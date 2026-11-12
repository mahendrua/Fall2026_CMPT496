const { chromium } = require('/opt/gui-runner/node_modules/@playwright/test');
(async () => {
    const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
    try {
        const page = await browser.newPage();
        page.on('console', message => console.log('PREVIEW_CONSOLE:', message.text()));
        page.on('pageerror', error => console.error('PREVIEW_PAGE_ERROR:', error.message));
        await page.addInitScript(() => {
            const Native = window.WebSocket;
            function CaptureSocket(...args) { const socket = new Native(...args); window.previewSocket = socket; return socket; }
            CaptureSocket.prototype = Native.prototype;
            Object.setPrototypeOf(CaptureSocket, Native);
            window.WebSocket = CaptureSocket;
        });
        await page.goto('http://127.0.0.1:6080/gui-preview.html#password=verify');
        await page.waitForFunction(() => document.querySelector('#status').textContent === 'Connected to browser preview', null, { timeout: 5000 }).catch(async error => {
            console.error('PREVIEW_STATUS:', await page.locator('#status').textContent()); throw error;
        });
        const canvas = await page.locator('canvas').evaluate(c => ({ width: c.width, height: c.height }));
        if (!canvas.width || !canvas.height) throw new Error('Preview has no framebuffer');
        await page.evaluate(() => window.previewSocket.close(4000, 'Controlled temporary interruption'));
        await page.waitForFunction(() => document.querySelector('#status').textContent === 'Browser preview successfully reconnected');
        console.log('PREVIEW_VERIFIED: framebuffer visible and real WebSocket reconnected');
    } finally { await browser.close(); }
})().catch(error => { console.error('PREVIEW_FAILED:', error); process.exitCode = 1; });
