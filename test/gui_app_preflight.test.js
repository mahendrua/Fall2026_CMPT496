const test = require('node:test');
const assert = require('node:assert/strict');
const preflight = require('../gui-runner/appPreflight.cjs');

async function scenario({ status = 200, body = 'Application', assetStatus = 200, websocketError = false, framework = 'next' } = {}) {
    const previous = { flag: process.env.GUI_APP_PREFLIGHT, url: process.env.BASE_URL, framework: process.env.GUI_APP_FRAMEWORK };
    process.env.GUI_APP_PREFLIGHT = '1';process.env.BASE_URL = 'http://host.docker.internal:4321/';process.env.GUI_APP_FRAMEWORK = framework;
    const events = [], listeners = {};
    let closed = false, launch, context;
    try {
        const chromium = { launch: async options => {
            launch = options;
            return { close: async () => { closed = true; }, newContext: async options => {
                context = options;
                return { newPage: async () => ({
                    on: (name, fn) => { listeners[name] = fn; },
                    goto: async (_url, options) => {
                        assert.equal(options.waitUntil, 'load');
                        if (assetStatus >= 400) listeners.response({ url: () => 'http://host.docker.internal:4321/_next/static/test.js', status: () => assetStatus, request: () => ({ resourceType: () => 'script' }) });
                        if (websocketError) listeners.websocket({ url: () => 'ws://host.docker.internal:4321/_next/hmr', on: (name, fn) => { if (name === 'socketerror') queueMicrotask(fn); } });
                        return { status: () => status };
                    },
                    locator: selector => ({ innerText: async () => body, count: async () => 0 })
                }) };
            } };
        } };
        let error;
        try { await preflight({ projects: [{ use: { proxy: { server: 'http://proxy:3128', bypass: 'localhost' }, launchOptions: {} } }] }, { chromium, emit: (type, data) => events.push({ type, ...data }) }); }
        catch (caught) { error = caught; }
        return { events, closed, launch, context, error };
    } finally {
        for (const [key,value] of [['GUI_APP_PREFLIGHT',previous.flag], ['BASE_URL',previous.url], ['GUI_APP_FRAMEWORK',previous.framework]]) {
            if (value === undefined) delete process.env[key]; else process.env[key] = value;
        }
    }
}
test('browser preflight uses the real context proxy, accepts a login response and closes its browser', async () => {
    const result = await scenario({ status: 401, body: 'Sign in' });
    assert.equal(result.error, undefined);
    assert.equal(result.closed, true);
    assert.equal(result.launch.headless, false);
    assert.equal(result.launch.proxy, undefined);
    assert.equal(result.context.proxy.server, 'http://proxy:3128');
    assert.ok(result.events.some(event => event.status === 'verified'));
});
test('host-blocking HTML stops preflight before assertions without logging private page content', async () => {
    const result = await scenario({ status: 403, framework: 'vite', body: 'Blocked request. This host is not allowed. SECRET-CONTENT' });
    assert.match(result.error.message, /APP_PREFLIGHT/);
    assert.ok(result.events.some(event => event.category === 'host_blocked'));
    assert.ok(!JSON.stringify(result.events).includes('SECRET-CONTENT'));
    assert.ok(!result.events.some(event => event.status === 'verified'));
    assert.equal(result.closed, true);
});
test('failed development assets and HMR are infrastructure failures, not app assertion failures', async () => {
    for (const options of [{ assetStatus: 403 }, { websocketError: true }]) {
        const result = await scenario(options);
        assert.match(result.error.message, /APP_PREFLIGHT/);
        assert.ok(result.events.some(event => ['development_asset_blocked','development_websocket_failure'].includes(event.category)));
        assert.equal(result.closed, true);
    }
});
