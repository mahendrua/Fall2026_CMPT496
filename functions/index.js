"use strict";

// Trusted backend for Checkpoint's test-only subscription. Firebase verifies
// the caller's ID token before these handlers run, so request.auth.uid is the
// signed-in account. The Admin SDK bypasses Firestore rules; clients cannot.
//
// Request data is never logged: neither handler accepts any.

const { onCall, HttpsError } = require("firebase-functions/v2/https");
const { defineBoolean } = require("firebase-functions/params");
const logger = require("firebase-functions/logger");
const { initializeApp } = require("firebase-admin/app");
const { getFirestore, Timestamp } = require("firebase-admin/firestore");
const {
    SubscriptionError,
    activateTestSubscription,
    getEntitlement
} = require("./subscription");

// Off unless explicitly enabled. For the emulator, set it in functions/.env.local.
const testPaymentsEnabled = defineBoolean("CHECKPOINT_TEST_PAYMENTS", {
    default: false,
    description: "Allow the test-only Checkpoint Pro checkout. Never enable in production."
});

initializeApp();
const db = getFirestore();
const userDocument = uid => db.collection("users").doc(uid);

function fromStored(subscription) {
    if (!subscription || typeof subscription !== "object") return subscription ?? null;
    const toDate = value => (value instanceof Timestamp ? value.toDate() : value);
    return { ...subscription, activatedAt: toDate(subscription.activatedAt), expiresAt: toDate(subscription.expiresAt) };
}

function toStored(subscription) {
    return {
        plan: subscription.plan,
        status: subscription.status,
        activatedAt: Timestamp.fromDate(subscription.activatedAt),
        expiresAt: Timestamp.fromDate(subscription.expiresAt),
        test: subscription.test
    };
}

const store = {
    async getSubscription(uid) {
        const snapshot = await userDocument(uid).get();
        return fromStored(snapshot.get("subscription"));
    },
    runTransaction(work) {
        return db.runTransaction(async transaction => work({
            async getSubscription(uid) {
                const snapshot = await transaction.get(userDocument(uid));
                return fromStored(snapshot.get("subscription"));
            },
            setSubscription(uid, subscription) {
                // mergeFields replaces only the subscription map and keeps every
                // other field (such as profile data) on the account record.
                transaction.set(userDocument(uid), { subscription: toStored(subscription) }, { mergeFields: ["subscription"] });
            }
        }));
    }
};

const serverNow = () => Timestamp.now().toDate();

function toHttpsError(error) {
    if (error instanceof SubscriptionError) return new HttpsError(error.code, error.message);
    logger.error("Subscription operation failed", { name: error?.name, code: error?.code });
    return new HttpsError("internal", "The subscription service could not complete the request.");
}

exports.activateTestSubscription = onCall({ region: "us-central1" }, async request => {
    try {
        const result = await activateTestSubscription({
            uid: request.auth?.uid,
            data: request.data,
            testPaymentsEnabled: testPaymentsEnabled.value(),
            store,
            now: serverNow
        });
        logger.info("Test subscription checkout", { uid: request.auth.uid, created: result.created });
        return result;
    } catch (error) {
        throw toHttpsError(error);
    }
});

exports.getEntitlement = onCall({ region: "us-central1" }, async request => {
    try {
        return await getEntitlement({
            uid: request.auth?.uid,
            data: request.data,
            testPaymentsEnabled: testPaymentsEnabled.value(),
            store,
            now: serverNow
        });
    } catch (error) {
        throw toHttpsError(error);
    }
});
