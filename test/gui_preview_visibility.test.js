const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { inspectDisplay } = require('../gui-runner/displayDiagnostics.cjs');

test('preview policy permits only the required IPv4 loopback HTTP frame origin', () => {
    const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
    const policy = html.match(/http-equiv="Content-Security-Policy" content="([^"]+)"/)[1];
    const frames = policy.split(';').map(s => s.trim()).find(s => s.startsWith('frame-src ')).split(/\s+/).slice(1);
    assert.deepEqual(frames, ["'self'", 'data:', 'http://127.0.0.1:*']);
    assert.match(policy, /script-src 'self';/);
});

test('normal mode stays fast and demo mode affects only browser slowMo', () => {
    const configPath = require.resolve('../gui-runner/playwright.config.cjs');
    const previous = process.env.GUI_DEMO_MODE;
    try {
        for (const [value, expected] of [['0', 0], ['1', 600], ['invalid', 0]]) {
            process.env.GUI_DEMO_MODE = value;
            delete require.cache[configPath];
            const config = require(configPath);
            assert.equal(config.use.launchOptions.slowMo, expected);
            assert.equal(config.use.headless, false);
            assert.equal(config.use.screenshot, 'only-on-failure');
            assert.equal(config.retries, 0);
        }
    } finally {
        if (previous === undefined) delete process.env.GUI_DEMO_MODE;
        else process.env.GUI_DEMO_MODE = previous;
        delete require.cache[configPath];
    }
});

test('display diagnostics distinguish visible, minimized, headless, wrong and unavailable displays', async () => {
    assert.equal((await inspectDisplay({ display: ':42' })).code, 'incorrect_display');
    for (const [windowInfo, expected] of [
        ['WM_CLASS = "chromium", "Chromium"\n_NET_WM_STATE =', 'browser_visible'],
        ['WM_CLASS = "chromium", "Chromium"\n_NET_WM_STATE = _NET_WM_STATE_HIDDEN', 'browser_hidden']
    ]) {
        const result = await inspectDisplay({ display: ':99', run: async (_cmd, args) => ({ stdout: args[0] === '-root' ? '0x123' : windowInfo }) });
        assert.equal(result.code, expected);
    }
    for (const [processes, expected] of [['chromium --headless', 'browser_headless'], ['', 'no_browser_window']]) {
        const result = await inspectDisplay({ display: ':99', run: async cmd => ({ stdout: cmd === 'ps' ? processes : '' }) });
        assert.equal(result.code, expected);
    }
    assert.equal((await inspectDisplay({ display: ':99', run: async () => { throw new Error('cannot open display'); } })).code, 'display_unavailable');
});
