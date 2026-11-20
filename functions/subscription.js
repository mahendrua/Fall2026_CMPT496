"use strict";

// Test-only subscription logic for Checkpoint Pro. This module has no Firebase
// dependency so it can be unit-tested directly; index.js adapts it to Firestore.
//
// Stored shape (users/{uid}.subscription), and nothing else:
//   plan        "checkpoint_pro_monthly"
//   status      "active"
//   activatedAt Date (Firestore Timestamp when stored)
//   expiresAt   Date (Firestore Timestamp when stored)
//   test        true
//
// Expiry is never written back. A subscription counts as active only while
// the server clock is before expiresAt, so no renewal is ever simulated.

const PLAN_ID = "checkpoint_pro_monthly";

class SubscriptionError extends Error {
    constructor(code, message) {
        super(message);
        this.name = "SubscriptionError";
        this.code = code;
    }
}

function addOneMonth(date) {
    const result = new Date(date.getTime());
    const day = result.getUTCDate();
    result.setUTCDate(1);
    result.setUTCMonth(result.getUTCMonth() + 1);
    const lastDay = new Date(Date.UTC(result.getUTCFullYear(), result.getUTCMonth() + 1, 0)).getUTCDate();
    result.setUTCDate(Math.min(day, lastDay));
    return result;
}

function isValidDate(value) {
    return value instanceof Date && Number.isFinite(value.getTime());
}

// Turns a stored record into the entitlement the app acts on. Anything that
// is not a well-formed, unexpired Pro subscription is not active.
function evaluateSubscription(subscription, now) {
    if (subscription === undefined || subscription === null) {
        return { plan: "free", status: "free", active: false, activatedAt: null, expiresAt: null, test: false };
    }

    const wellFormed =
        typeof subscription === "object" &&
        subscription.plan === PLAN_ID &&
        typeof subscription.status === "string" &&
        isValidDate(subscription.activatedAt) &&
        isValidDate(subscription.expiresAt) &&
        subscription.test === true;
    if (!wellFormed) {
        return { plan: "free", status: "unverified", active: false, activatedAt: null, expiresAt: null, test: false };
    }

    const status = subscription.status !== "active"
        ? "inactive"
        : subscription.expiresAt.getTime() > now.getTime() ? "active" : "expired";
    return {
        plan: PLAN_ID,
        status,
        active: status === "active",
        activatedAt: subscription.activatedAt.toISOString(),
        expiresAt: subscription.expiresAt.toISOString(),
        test: true
    };
}

function requireUid(uid) {
    if (typeof uid !== "string" || !/^[A-Za-z0-9_-]{1,128}$/.test(uid)) {
        throw new SubscriptionError("unauthenticated", "Sign in to Checkpoint to continue.");
    }
}

// The operation takes no input. Rejecting any payload means card details or a
// client-chosen plan or expiry can never be accepted or stored here.
function requireEmptyPayload(data) {
    const empty = data === undefined || data === null ||
        (typeof data === "object" && !Array.isArray(data) && Object.keys(data).length === 0);
    if (!empty) {
        throw new SubscriptionError("invalid-argument", "This operation does not accept any request data.");
    }
}

// store.runTransaction(work) runs work({ getSubscription(uid), setSubscription(uid, value) })
// atomically, retrying on contention. Two simultaneous checkouts therefore
// serialize: the first creates the subscription, the second finds it active.
async function activateTestSubscription({ uid, data, testPaymentsEnabled, store, now }) {
    if (testPaymentsEnabled !== true) {
        throw new SubscriptionError("failed-precondition", "Test checkout is disabled on this server.");
    }
    requireUid(uid);
    requireEmptyPayload(data);

    return store.runTransaction(async transaction => {
        const current = now();
        const existing = evaluateSubscription(await transaction.getSubscription(uid), current);
        if (existing.active) {
            return { created: false, subscription: existing };
        }

        const record = {
            plan: PLAN_ID,
            status: "active",
            activatedAt: current,
            expiresAt: addOneMonth(current),
            test: true
        };
        transaction.setSubscription(uid, record);
        return { created: true, subscription: evaluateSubscription(record, current) };
    });
}

async function getEntitlement({ uid, data, testPaymentsEnabled, store, now }) {
    requireUid(uid);
    requireEmptyPayload(data);
    const subscription = await store.getSubscription(uid);
    const current = now();
    return {
        subscription: evaluateSubscription(subscription, current),
        serverTime: current.toISOString(),
        testCheckoutAvailable: testPaymentsEnabled === true
    };
}

module.exports = {
    PLAN_ID,
    SubscriptionError,
    activateTestSubscription,
    addOneMonth,
    evaluateSubscription,
    getEntitlement
};
