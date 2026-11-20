const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const {
    EntitlementCache,
    PAID_BACKEND_COMMANDS,
    PAID_IPC_CHANNELS,
    PaymentRequiredError,
    callFunction,
    functionUrl,
    getEmulatorSettings,
    paidFeatureForChannel,
    paidFeatureForCommand
} = require("../entitlement_service");

const mainJs = fs.readFileSync(path.join(__dirname, "..", "main.js"), "utf8");
const preloadJs = fs.readFileSync(path.join(__dirname, "..", "preload.js"), "utf8");

const activeResult = (expiresAt = "2026-12-20T12:00:00.000Z") => ({
    subscription: {
        plan: "checkpoint_pro_monthly",
        status: "active",
        active: true,
        activatedAt: "2026-11-20T12:00:00.000Z",
        expiresAt,
        test: true
    },
    serverTime: "2026-11-20T12:00:00.000Z",
    testCheckoutAvailable: true
});
const freeResult = {
    subscription: { plan: "free", status: "free", active: false, activatedAt: null, expiresAt: null, test: false },
    testCheckoutAvailable: false
};

function clock(iso) {
    let current = Date.parse(iso);
    return { now: () => current, advance: ms => { current += ms; } };
}

test("an account with no subscription data is free and locked", () => {
    const cache = new EntitlementCache();
    cache.record("user-a", freeResult);
    const view = cache.view("user-a");
    assert.equal(view.active, false);
    assert.equal(view.plan, "free");
    assert.deepEqual(view.features, { gui: false, github: false });
    assert.throws(() => cache.assertFeature("user-a", "gui"), PaymentRequiredError);
    assert.throws(() => cache.assertFeature("user-a", "github"), PaymentRequiredError);
});

test("an unknown account (nothing cached yet) is locked", () => {
    const cache = new EntitlementCache();
    assert.equal(cache.view("user-a").status, "unknown");
    assert.throws(() => cache.assertFeature("user-a", "gui"), /Checkpoint Pro required/);
});

test("a verified active subscription unlocks both feature groups", () => {
    const time = clock("2026-11-20T12:00:00Z");
    const cache = new EntitlementCache({ now: time.now });
    cache.record("user-a", activeResult());
    assert.deepEqual(cache.view("user-a").features, { gui: true, github: true });
    assert.doesNotThrow(() => cache.assertFeature("user-a", "gui"));
    assert.doesNotThrow(() => cache.assertFeature("user-a", "github"));
});

test("access never carries over to a different account", () => {
    const cache = new EntitlementCache({ now: clock("2026-11-20T12:00:00Z").now });
    cache.record("user-a", activeResult());
    assert.equal(cache.view("user-b").active, false);
    assert.throws(() => cache.assertFeature("user-b", "gui"), PaymentRequiredError);
    cache.clear();
    assert.throws(() => cache.assertFeature("user-a", "gui"), PaymentRequiredError);
});

test("expiry locks both feature groups even before the next server check", () => {
    const time = clock("2026-12-20T11:59:00Z");
    const cache = new EntitlementCache({ now: time.now });
    cache.record("user-a", activeResult("2026-12-20T12:00:00.000Z"));
    assert.equal(cache.view("user-a").active, true);
    time.advance(60 * 1000);
    const view = cache.view("user-a");
    assert.equal(view.active, false);
    assert.equal(view.status, "expired");
    assert.throws(() => cache.assertFeature("user-a", "github"), PaymentRequiredError);
});

test("a stale entitlement is not trusted until re-verified", () => {
    const time = clock("2026-11-20T12:00:00Z");
    const cache = new EntitlementCache({ now: time.now, maxAgeMs: 1000 });
    cache.record("user-a", activeResult());
    time.advance(1000);
    assert.equal(cache.isFresh("user-a"), false);
    assert.equal(cache.view("user-a").status, "unverified");
    assert.throws(() => cache.assertFeature("user-a", "gui"), PaymentRequiredError);
});

test("an unverifiable or malformed response leaves paid features locked", () => {
    const cache = new EntitlementCache({ now: clock("2026-11-20T12:00:00Z").now });
    for (const result of [null, {}, { subscription: { active: true } }, { subscription: { status: "active", active: true, expiresAt: "soon" } }]) {
        cache.record("user-a", result);
        assert.equal(cache.view("user-a").active, false);
        assert.equal(cache.view("user-a").verified, false);
    }
    cache.record("user-a", activeResult());
    cache.recordFailure("user-a");
    assert.equal(cache.view("user-a").active, false);
    assert.throws(() => cache.assertFeature("user-a", "gui"), PaymentRequiredError);
});

test("callFunction sends only the ID token and an empty payload", async () => {
    const calls = [];
    const fetchImpl = async (url, init) => {
        calls.push({ url, init });
        return { ok: true, json: async () => ({ result: freeResult }) };
    };
    const result = await callFunction("getEntitlement", "id-token-value", { projectId: "checkpoint-cmpt497", fetchImpl });
    assert.deepEqual(result, freeResult);
    assert.equal(calls[0].url, "https://us-central1-checkpoint-cmpt497.cloudfunctions.net/getEntitlement");
    assert.equal(calls[0].init.headers.Authorization, "Bearer id-token-value");
    assert.equal(calls[0].init.body, JSON.stringify({ data: null }));
});

test("callFunction surfaces callable errors with their status", async () => {
    const fetchImpl = async () => ({
        ok: false,
        json: async () => ({ error: { status: "FAILED_PRECONDITION", message: "Test checkout is disabled on this server." } })
    });
    await assert.rejects(
        callFunction("activateTestSubscription", "token", { projectId: "checkpoint-cmpt497", fetchImpl }),
        error => error.code === "FAILED_PRECONDITION" && /disabled/.test(error.message)
    );
    await assert.rejects(
        callFunction("getEntitlement", "token", { projectId: "checkpoint-cmpt497", fetchImpl: async () => { throw new Error("offline"); } }),
        error => error.code === "UNAVAILABLE"
    );
    await assert.rejects(callFunction("getEntitlement", "", { projectId: "checkpoint-cmpt497" }), error => error.code === "UNAUTHENTICATED");
});

test("emulators are opt-in and never used by a packaged app", () => {
    assert.equal(getEmulatorSettings({}, false), null);
    assert.equal(getEmulatorSettings({ CHECKPOINT_FIREBASE_EMULATORS: "true" }, true), null);
    const emulators = getEmulatorSettings({ CHECKPOINT_FIREBASE_EMULATORS: "1" }, false);
    assert.equal(
        functionUrl("getEntitlement", { projectId: "checkpoint-cmpt497", emulators }),
        "http://127.0.0.1:5001/checkpoint-cmpt497/us-central1/getEntitlement"
    );
});

test("main.js registers every paid IPC channel through registerPaidHandler", () => {
    for (const channel of Object.keys(PAID_IPC_CHANNELS)) {
        const escaped = channel.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
        assert.match(mainJs, new RegExp(`registerPaidHandler\\(\\s*"${escaped}"`), `${channel} must be gated`);
        assert.doesNotMatch(mainJs, new RegExp(`(registerProtectedHandler|ipcMain\\.handle)\\(\\s*"${escaped}"`), `${channel} must not bypass the gate`);
    }
    assert.throws(() => paidFeatureForChannel("auth:get-profile"), /not a paid IPC channel/);
});

test("every renderer-exposed GitHub and GUI channel is paid, except stop/cancel/clear", () => {
    const exposed = [...preloadJs.matchAll(/ipcRenderer\.(?:invoke|send)\("([^"]+)"/g)].map(match => match[1]);
    const freeCleanup = new Set(["github:cancel-import", "stop-local-gui-app", "stop-gui-tests", "clear-gui-tests", "gui-test-report-event"]);
    const paidLooking = exposed.filter(channel => /^github:|gui/.test(channel) && !freeCleanup.has(channel));
    assert.deepEqual(paidLooking.filter(channel => !Object.hasOwn(PAID_IPC_CHANNELS, channel)), []);
});

test("execute-command gates the paid backend commands", () => {
    assert.equal(paidFeatureForCommand("generate_gui_test_cases"), "gui");
    assert.equal(paidFeatureForCommand("repair_gui_test_case"), "gui");
    assert.equal(paidFeatureForCommand("analyze_pull_request"), "github");
    assert.equal(paidFeatureForCommand("full_pipeline"), null);
    assert.equal(paidFeatureForCommand("toString"), null);
    assert.deepEqual(Object.keys(PAID_BACKEND_COMMANDS).sort(), ["analyze_pull_request", "generate_gui_test_cases", "repair_gui_test_case"]);
    const executeCommand = mainJs.slice(mainJs.indexOf('"execute-command"'), mainJs.indexOf('"cancel-command"'));
    assert.match(executeCommand, /paidFeatureForCommand\(request\?\.command\)[\s\S]*requirePaidFeature\(paidFeature\)[\s\S]*stdin\.write/);
});

test("checkout IPC ignores renderer arguments and clears entitlements on sign-out and account switch", () => {
    assert.match(preloadJs, /activateTestSubscription: \(\) => ipcRenderer\.invoke\("subscription:activate-test"\)/);
    assert.match(mainJs, /registerProtectedHandler\("subscription:activate-test", async \(\) =>/);
    const signOut = mainJs.slice(mainJs.indexOf('ipcMain.handle("auth:sign-out"'), mainJs.indexOf('ipcMain.handle("auth:get-profile"'));
    assert.match(signOut, /entitlements\.clear\(\)/);
    const establish = mainJs.slice(mainJs.indexOf('ipcMain.handle("auth:establish"'), mainJs.indexOf('ipcMain.handle("auth:sign-out"'));
    assert.match(establish, /authSession\?\.uid !== identity\.uid\)\s*\{\s*entitlements\.clear\(\)/);
});
