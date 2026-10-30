const { diagnoseResponse } = require('./connectivity.cjs');
function send(type, data) { process.stdout.write(`GUI_EVENT:${JSON.stringify({ type, ...data })}\n`); }

module.exports = async function appPreflight(config, hooks = {}) {
    const emit = hooks.emit || send;
    if (process.env.GUI_APP_PREFLIGHT !== '1') return;
    const url = process.env.BASE_URL;
    const framework = process.env.GUI_APP_FRAMEWORK || '';
    let parsed;
    try { parsed = new URL(url); } catch { throw new Error('APP_PREFLIGHT: BASE_URL is missing or invalid.'); }
    if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password) throw new Error('APP_PREFLIGHT: BASE_URL must be an HTTP(S) URL without embedded credentials.');
    emit('app_preflight', { status: 'checking', message: 'Checking Docker browser access to the application...' });
    const chromium = hooks.chromium || require('@playwright/test').chromium;
    const use = config.projects[0].use;
    let browser;
    let lastDiagnosis;
    const deadline = Date.now() + 45000;
    try {
        browser = await chromium.launch({ ...use.launchOptions, headless: false });
        const context = await browser.newContext({ proxy: use.proxy });
        const page = await context.newPage();
        let hmrReady = null;
        page.on('websocket', socket => {
            const socketUrl = new URL(socket.url());
            if (framework !== 'next' || !/^\/_next\/(?:webpack-)?hmr/.test(socketUrl.pathname)) return;
            hmrReady = new Promise(resolve => {
                let timer;
                const done = value => { clearTimeout(timer); resolve(value); };
                socket.on('framereceived', () => done(null));
                socket.on('socketerror', () => done({ category: 'development_websocket_failure', message: 'The Next.js development WebSocket failed. Check the exact host/port proxy Upgrade rules and the development server origin log. If it reports a blocked origin, add only "host.docker.internal" to allowedDevOrigins in next.config.js; do not disable origin checks.' }));
                timer = setTimeout(() => done({ category: 'development_websocket_failure', message: 'The Next.js development WebSocket did not initialize within five seconds. Check proxy Upgrade support and the server development-origin allowlist.' }), 5000);
            });
        });
        let assetFailures = [];
        page.on('response', response => {
            const asset = new URL(response.url());
            if (asset.origin !== parsed.origin || response.status() < 400 || response.request().resourceType() !== 'script') return;
            if (framework === 'next' && asset.pathname.startsWith('/_next/')) {
                assetFailures.push({ category: 'development_asset_blocked', message: 'Next.js returned an error for a required browser script. Check development-origin and authentication/middleware rules. If its log reports blocked cross-origin requests, add only "host.docker.internal" to allowedDevOrigins in next.config.js. Checkpoint will not bypass that origin check or modify your configuration.' });
            } else if (/\/(?:@vite|@fs|src)\//.test(asset.pathname)) {
                assetFailures.push({ category: 'development_asset_blocked', message: 'The development server rejected a required browser script. Check its explicit hostname/origin allowlist and compile log before running assertions.' });
            }
        });
        while (Date.now() < deadline) {
            assetFailures = [];
            hmrReady = null;
            try {
                const response = await page.goto(url, { waitUntil: 'load', timeout: Math.min(12000, deadline - Date.now()) });
                // Do not serialize page content into logs: it can include credentials or private data.
                const body = (await page.locator('body').innerText({ timeout: 3000 })).slice(0, 32768);
                const html = await page.locator('vite-error-overlay, #__next_error__, [data-nextjs-dialog-header]').count();
                const hmrFailure = hmrReady ? await hmrReady : null;
                lastDiagnosis = hmrFailure || assetFailures[0] || diagnoseResponse({ status: response?.status() || 0, body: html ? body + '<vite-error-overlay' : body, framework });
                if (lastDiagnosis.category === 'accessible') {
                    emit('app_preflight', { status: 'verified', category: 'accessible', message: 'Application accessible from Playwright. Opening live browser tests...' });
                    return;
                }
            } catch (error) { lastDiagnosis = diagnoseResponse({ error: error.message, framework }); }
            if (!['connection_refused', 'connection_timeout', 'application_startup_error'].includes(lastDiagnosis.category)) break;
            emit('app_preflight', { status: 'retrying', category: lastDiagnosis.category, message: 'Application is not ready from Docker yet; retrying within the startup deadline.' });
            await new Promise(resolve => setTimeout(resolve, 500));
        }
        emit('runner_error', { category: lastDiagnosis?.category || 'navigation_failure', message: `Application preflight failed: ${lastDiagnosis?.message || 'No usable response.'}` });
        throw new Error(`APP_PREFLIGHT: ${lastDiagnosis?.category}: ${lastDiagnosis?.message}`);
    } catch (error) {
        if (!lastDiagnosis) emit('runner_error', { category: 'browser_startup_failure', message: 'Application preflight could not launch its browser. Check the Docker browser/display logs.' });
        throw error;
    } finally { if (browser) await browser.close(); }
};
