const assert = require("node:assert/strict");
const test = require("node:test");
const { assertAuthenticatedRenderer, assertMainRenderer } = require("../ipc_auth");

function createWindow() {
    const mainFrame = {};
    const webContents = { id: 8, mainFrame };
    return {
        mainFrame,
        webContents,
        isDestroyed: () => false
    };
}

function createEvent(window, overrides = {}) {
    return {
        sender: window.webContents,
        senderFrame: window.mainFrame,
        ...overrides
    };
}

test("allows only the main Checkpoint renderer", () => {
    const window = createWindow();
    assert.doesNotThrow(() => assertMainRenderer(createEvent(window), window));
});

test("rejects a missing, destroyed, or non-main renderer", () => {
    const window = createWindow();
    assert.throws(() => assertMainRenderer(createEvent(window), null), /Checkpoint window/);
    assert.throws(() => assertMainRenderer(createEvent(window), {
        ...window,
        isDestroyed: () => true
    }), /Checkpoint window/);
    assert.throws(() => assertMainRenderer(createEvent(window, {
        sender: { id: 9 }
    }), window), /Checkpoint window/);
    assert.throws(() => assertMainRenderer(createEvent(window, {
        senderFrame: {}
    }), window), /Checkpoint window/);
});

test("allows protected operations only for an unexpired authenticated renderer session", () => {
    const window = createWindow();
    const session = { webContentsId: window.webContents.id, expiresAt: Date.now() + 60_000 };
    assert.doesNotThrow(() => assertAuthenticatedRenderer(createEvent(window), window, session));
});

test("rejects protected operations when signed out, expired, or authenticated in another window", () => {
    const window = createWindow();
    assert.throws(() => assertAuthenticatedRenderer(createEvent(window), window, null), /Sign in/);
    assert.throws(() => assertAuthenticatedRenderer(createEvent(window), window, {
        webContentsId: window.webContents.id,
        expiresAt: Date.now() - 1
    }), /Sign in/);
    assert.throws(() => assertAuthenticatedRenderer(createEvent(window), window, {
        webContentsId: window.webContents.id + 1,
        expiresAt: Date.now() + 60_000
    }), /Sign in/);
    assert.throws(() => assertAuthenticatedRenderer(createEvent(window, {
        senderFrame: {}
    }), window, {
        webContentsId: window.webContents.id,
        expiresAt: Date.now() + 60_000
    }), /Checkpoint window/);
});
