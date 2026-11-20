const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const {
    cleanErrorMessage,
    createSubscriptionState,
    isPaymentRequiredError
} = require("../subscription_state");

const root = path.join(__dirname, "..");
const upgradeJs = fs.readFileSync(path.join(root, "upgrade.js"), "utf8");
const indexHtml = fs.readFileSync(path.join(root, "index.html"), "utf8");

const NOW = Date.parse("2026-11-20T12:00:00Z");
const freeView = { plan: "free", status: "free", active: false, verified: true, expiresAt: null, testCheckoutAvailable: true, features: { gui: false, github: false } };
const proView = { plan: "checkpoint_pro_monthly", status: "active", active: true, verified: true, test: true, expiresAt: "2026-12-20T12:00:00.000Z", testCheckoutAvailable: true, features: { gui: true, github: true } };

function createBridge(overrides = {}) {
    const calls = [];
    return {
        calls,
        getSubscription: async (...args) => { calls.push(["get", args]); return freeView; },
        refreshSubscription: async (...args) => { calls.push(["refresh", args]); return freeView; },
        activateTestSubscription: async (...args) => { calls.push(["activate", args]); return proView; },
        ...overrides
    };
}

test("a signed-in free account has no paid features", async () => {
    const state = createSubscriptionState(createBridge(), { now: () => NOW });
    assert.equal(state.hasFeature("gui"), false);
    await state.load();
    assert.equal(state.getState().phase, "ready");
    assert.equal(state.hasFeature("gui"), false);
    assert.equal(state.hasFeature("github"), false);
});

test("checkout unlocks both feature groups without signing out", async () => {
    const bridge = createBridge();
    const state = createSubscriptionState(bridge, { now: () => NOW });
    await state.load();
    await state.activate();
    assert.equal(state.hasFeature("gui"), true);
    assert.equal(state.hasFeature("github"), true);
    assert.equal(state.hasFeature("toString"), false);
});

test("checkout sends no arguments, so card input cannot reach the main process", async () => {
    const bridge = createBridge();
    const state = createSubscriptionState(bridge, { now: () => NOW });
    await state.activate();
    assert.deepEqual(bridge.calls.find(([name]) => name === "activate"), ["activate", []]);
});

test("double clicks share one activation call", async () => {
    let release;
    const bridge = createBridge({
        activateTestSubscription: (...args) => {
            bridge.calls.push(["activate", args]);
            return new Promise(resolve => { release = () => resolve(proView); });
        }
    });
    const state = createSubscriptionState(bridge, { now: () => NOW });
    const first = state.activate();
    const second = state.activate();
    assert.equal(first, second);
    release();
    await first;
    assert.equal(bridge.calls.filter(([name]) => name === "activate").length, 1);
});

test("an expired view locks both feature groups", async () => {
    let now = Date.parse("2026-12-20T11:59:59Z");
    const state = createSubscriptionState(createBridge({ getSubscription: async () => proView }), { now: () => now });
    await state.load();
    assert.equal(state.hasFeature("gui"), true);
    now = Date.parse("2026-12-20T12:00:00Z");
    assert.equal(state.hasFeature("gui"), false);
    assert.equal(state.hasFeature("github"), false);
});

test("sign-out clears access, and a late reply for the old account is discarded", async () => {
    let release;
    const state = createSubscriptionState(createBridge({
        getSubscription: () => new Promise(resolve => { release = () => resolve(proView); })
    }), { now: () => NOW });
    const pending = state.load();
    state.reset();
    release();
    await pending;
    assert.equal(state.getState().phase, "signed-out");
    assert.equal(state.hasFeature("gui"), false);
});

test("a subscription that cannot be verified leaves features locked", async () => {
    const state = createSubscriptionState(createBridge({
        getSubscription: async () => { throw new Error("Error invoking remote method 'subscription:get': Error: Could not reach the Checkpoint subscription service."); }
    }), { now: () => NOW });
    await state.load();
    assert.equal(state.getState().phase, "error");
    assert.equal(state.getState().error, "Could not reach the Checkpoint subscription service.");
    assert.equal(state.hasFeature("gui"), false);
});

test("payment-required errors from the main process are recognised", () => {
    assert.equal(isPaymentRequiredError(new Error("Error invoking remote method 'github:get-branches': Error: Checkpoint Pro required: GitHub integration is part of Checkpoint Pro")), true);
    assert.equal(isPaymentRequiredError("Checkpoint Pro required: GUI testing"), true);
    assert.equal(isPaymentRequiredError(new Error("Network error")), false);
    assert.equal(cleanErrorMessage(new Error(""), "fallback"), "fallback");
});

test("the Upgrade screen shows the price and labels checkout as test mode", () => {
    assert.match(indexHtml, /id="upgradePage"/);
    assert.match(indexHtml, /\$19\.99<\/strong><span>\/month/);
    assert.match(indexHtml, /Test mode — no charge/);
    assert.match(indexHtml, /id="upgradeDummyCardBtn"[^>]*>Use dummy card</);
    assert.match(indexHtml, /id="upgradeCardNumber"[^>]*autocomplete="off"/);
    const checkout = indexHtml.slice(indexHtml.indexOf('id="upgradeCheckout"'), indexHtml.indexOf('id="upgradeSuccessMessage"'));
    assert.doesNotMatch(checkout, /<form|name="/, "the card field must not be part of a submittable form");
});

test("upgrade.js never sends, logs or stores the card number", () => {
    const cardReads = [...upgradeJs.matchAll(/cardInput\.value(?!\s*=)/g)];
    assert.equal(cardReads.length, 1, "the card value is read once, only to check it is not empty");
    assert.match(upgradeJs, /const hasCard = cardInput\.value\.trim\(\)\.length > 0;\s*clearCard\(\);/);
    assert.doesNotMatch(upgradeJs, /console\.\w+\([^)]*card/i);
    assert.doesNotMatch(upgradeJs, /localStorage|sessionStorage|indexedDB|document\.cookie/);
    assert.doesNotMatch(upgradeJs, /activateTestSubscription\([^)]/);
    assert.doesNotMatch(upgradeJs, /electronAPI\.\w+\([^)]*card/i);
});
