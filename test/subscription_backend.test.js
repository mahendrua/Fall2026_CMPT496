const assert = require("node:assert/strict");
const test = require("node:test");
const {
    PLAN_ID,
    SubscriptionError,
    activateTestSubscription,
    addOneMonth,
    evaluateSubscription,
    getEntitlement
} = require("../functions/subscription");

// In-memory stand-in for Firestore: transactions run one at a time, which is
// the guarantee Firestore's retrying transactions give the real handler.
function createStore(initial = {}) {
    const records = new Map(Object.entries(initial));
    let queue = Promise.resolve();
    let writes = 0;
    return {
        records,
        get writes() { return writes; },
        async getSubscription(uid) {
            return records.get(uid) ?? null;
        },
        runTransaction(work) {
            const run = queue.then(async () => {
                const pending = new Map();
                const result = await work({
                    getSubscription: async uid => records.get(uid) ?? null,
                    setSubscription: (uid, value) => pending.set(uid, value)
                });
                for (const [uid, value] of pending) {
                    records.set(uid, value);
                    writes += 1;
                }
                return result;
            });
            queue = run.catch(() => {});
            return run;
        }
    };
}

const fixedNow = value => () => new Date(value);

test("an account with no subscription record is free", async () => {
    const result = await getEntitlement({
        uid: "newUser1",
        store: createStore(),
        now: fixedNow("2026-11-20T12:00:00Z"),
        testPaymentsEnabled: false
    });
    assert.equal(result.subscription.status, "free");
    assert.equal(result.subscription.active, false);
    assert.equal(result.testCheckoutAvailable, false);
});

test("activation stores only the needed fields and grants one month from server time", async () => {
    const store = createStore();
    const result = await activateTestSubscription({
        uid: "buyer1",
        data: {},
        testPaymentsEnabled: true,
        store,
        now: fixedNow("2026-11-20T12:00:00Z")
    });
    assert.equal(result.created, true);
    assert.deepEqual(Object.keys(store.records.get("buyer1")).sort(), ["activatedAt", "expiresAt", "plan", "status", "test"]);
    assert.equal(result.subscription.plan, PLAN_ID);
    assert.equal(result.subscription.active, true);
    assert.equal(result.subscription.test, true);
    assert.equal(result.subscription.activatedAt, "2026-11-20T12:00:00.000Z");
    assert.equal(result.subscription.expiresAt, "2026-12-20T12:00:00.000Z");
});

test("double submissions and retries create exactly one subscription", async () => {
    const store = createStore();
    const request = { uid: "buyer2", data: null, testPaymentsEnabled: true, store, now: fixedNow("2026-11-20T12:00:00Z") };
    const results = await Promise.all([activateTestSubscription(request), activateTestSubscription(request)]);
    const retry = await activateTestSubscription({ ...request, now: fixedNow("2026-11-25T12:00:00Z") });

    assert.deepEqual(results.map(result => result.created).sort(), [false, true]);
    assert.equal(retry.created, false);
    assert.equal(store.writes, 1);
    assert.equal(retry.subscription.expiresAt, "2026-12-20T12:00:00.000Z", "a retry must not extend access");
});

test("a subscription is expired once server time reaches expiresAt", () => {
    const record = {
        plan: PLAN_ID,
        status: "active",
        activatedAt: new Date("2026-10-01T00:00:00Z"),
        expiresAt: new Date("2026-11-01T00:00:00Z"),
        test: true
    };
    assert.equal(evaluateSubscription(record, new Date("2026-10-31T23:59:59Z")).active, true);
    const expired = evaluateSubscription(record, new Date("2026-11-01T00:00:00Z"));
    assert.equal(expired.status, "expired");
    assert.equal(expired.active, false);
});

test("an expired subscription can be purchased again without renewal side effects", async () => {
    const store = createStore({
        buyer3: {
            plan: PLAN_ID,
            status: "active",
            activatedAt: new Date("2026-09-01T00:00:00Z"),
            expiresAt: new Date("2026-10-01T00:00:00Z"),
            test: true
        }
    });
    const result = await activateTestSubscription({
        uid: "buyer3", testPaymentsEnabled: true, store, now: fixedNow("2026-11-20T00:00:00Z")
    });
    assert.equal(result.created, true);
    assert.equal(result.subscription.expiresAt, "2026-12-20T00:00:00.000Z");
});

test("malformed or unrecognised records cannot be verified and stay locked", () => {
    const now = new Date("2026-11-20T00:00:00Z");
    const future = new Date("2027-01-01T00:00:00Z");
    const cases = [
        "paid",
        { plan: "enterprise", status: "active", activatedAt: now, expiresAt: future, test: true },
        { plan: PLAN_ID, status: "active", activatedAt: now, expiresAt: "2027-01-01", test: true },
        { plan: PLAN_ID, status: "active", activatedAt: now, expiresAt: future, test: false },
        { plan: PLAN_ID, status: "active", activatedAt: now, expiresAt: new Date(NaN), test: true }
    ];
    for (const record of cases) {
        const result = evaluateSubscription(record, now);
        assert.equal(result.active, false);
        assert.equal(result.status, "unverified");
    }
    assert.equal(
        evaluateSubscription({ plan: PLAN_ID, status: "canceled", activatedAt: now, expiresAt: future, test: true }, now).active,
        false
    );
});

test("checkout is rejected when test mode is disabled or not explicitly true", async () => {
    for (const flag of [false, undefined, "true", 1]) {
        const store = createStore();
        await assert.rejects(
            activateTestSubscription({ uid: "buyer4", testPaymentsEnabled: flag, store, now: fixedNow("2026-11-20T00:00:00Z") }),
            error => error instanceof SubscriptionError && error.code === "failed-precondition"
        );
        assert.equal(store.writes, 0);
    }
});

test("checkout requires a verified UID", async () => {
    for (const uid of [undefined, null, "", "../users/other", "a".repeat(129)]) {
        await assert.rejects(
            activateTestSubscription({ uid, testPaymentsEnabled: true, store: createStore(), now: fixedNow("2026-11-20T00:00:00Z") }),
            error => error.code === "unauthenticated"
        );
    }
});

test("checkout rejects any payload, so card details or client-chosen terms are never accepted", async () => {
    const payloads = [
        { cardNumber: "4242424242424242" },
        { plan: PLAN_ID, expiresAt: "2099-01-01" },
        { paid: true },
        ["4242"],
        "4242424242424242"
    ];
    for (const data of payloads) {
        const store = createStore();
        await assert.rejects(
            activateTestSubscription({ uid: "buyer5", data, testPaymentsEnabled: true, store, now: fixedNow("2026-11-20T00:00:00Z") }),
            error => error.code === "invalid-argument"
        );
        assert.equal(store.writes, 0);
    }
});

test("one month clamps to the last day of shorter months", () => {
    assert.equal(addOneMonth(new Date("2026-01-31T08:00:00Z")).toISOString(), "2026-02-28T08:00:00.000Z");
    assert.equal(addOneMonth(new Date("2028-01-31T08:00:00Z")).toISOString(), "2028-02-29T08:00:00.000Z");
    assert.equal(addOneMonth(new Date("2026-12-15T08:00:00Z")).toISOString(), "2027-01-15T08:00:00.000Z");
});
