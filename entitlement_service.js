"use strict";

// Main-process view of the signed-in account's Checkpoint Pro entitlement.
// The only source of truth is the getEntitlement Cloud Function (see
// functions/). Nothing the renderer sends can mark an account as paid.

const PRO_PRICE_LABEL = "$19.99/month";
const ENTITLEMENT_MAX_AGE_MS = 5 * 60 * 1000;
const FUNCTION_TIMEOUT_MS = 15 * 1000;
const DEFAULT_FUNCTIONS_REGION = "us-central1";
const EMULATOR_HOSTS = Object.freeze({
    auth: "http://127.0.0.1:9099",
    functions: "http://127.0.0.1:5001"
});

const PAID_FEATURES = Object.freeze({
    gui: "GUI testing",
    github: "GitHub integration"
});

// Every IPC channel that performs a paid operation. main.js must register each
// of these with registerPaidHandler (enforced by test/entitlement_service.test.js).
// Stop, cancel and clear actions stay free so a run can always be shut down.
const PAID_IPC_CHANNELS = Object.freeze({
    "github:get-branches": "github",
    "github:list-pull-requests": "github",
    "github:get-pull-request": "github",
    "github:get-pull-request-file": "github",
    "github:import": "github",
    "start-local-gui-app": "gui",
    "select-gui-test-files": "gui",
    "start-gui-tests": "gui",
    "start-generated-gui-tests": "gui",
    "repair-generated-gui-test": "gui",
    "diagnose-gui-event-stream": "gui",
    "list-gui-testing-reports": "gui",
    "get-gui-testing-report": "gui",
    "export-gui-testing-report-pdf": "gui"
});

// Python backend commands sent through execute-command that belong to a paid feature.
const PAID_BACKEND_COMMANDS = Object.freeze({
    generate_gui_test_cases: "gui",
    repair_gui_test_case: "gui",
    analyze_pull_request: "github"
});

const PAYMENT_REQUIRED_PREFIX = "Checkpoint Pro required:";

class PaymentRequiredError extends Error {
    constructor(feature) {
        super(`${PAYMENT_REQUIRED_PREFIX} ${PAID_FEATURES[feature] || "This feature"} is part of Checkpoint Pro (${PRO_PRICE_LABEL}). Open Upgrade to unlock it.`);
        this.name = "PaymentRequiredError";
        this.code = "PAYMENT_REQUIRED";
        this.feature = feature;
    }
}

class SubscriptionServiceError extends Error {
    constructor(status, message) {
        super(message);
        this.name = "SubscriptionServiceError";
        this.code = status;
    }
}

function paidFeatureForChannel(channel) {
    if (!Object.hasOwn(PAID_IPC_CHANNELS, channel)) {
        throw new Error(`${channel} is not a paid IPC channel.`);
    }
    return PAID_IPC_CHANNELS[channel];
}

function paidFeatureForCommand(command) {
    return typeof command === "string" && Object.hasOwn(PAID_BACKEND_COMMANDS, command)
        ? PAID_BACKEND_COMMANDS[command]
        : null;
}

// Emulators are opt-in for development only and never used by a packaged app.
function getEmulatorSettings(env = process.env, isPackaged = false) {
    if (isPackaged || !/^(1|true|yes)$/i.test(String(env.CHECKPOINT_FIREBASE_EMULATORS || ""))) return null;
    return { ...EMULATOR_HOSTS };
}

function functionUrl(name, { projectId, region = DEFAULT_FUNCTIONS_REGION, emulators = null }) {
    if (!/^[a-z0-9-]{4,40}$/.test(projectId || "") || !/^[a-z0-9-]{3,40}$/.test(region) || !/^[A-Za-z]{1,64}$/.test(name)) {
        throw new Error("The Firebase project settings for Checkpoint subscriptions are invalid.");
    }
    return emulators
        ? `${emulators.functions}/${projectId}/${region}/${name}`
        : `https://${region}-${projectId}.cloudfunctions.net/${name}`;
}

// Calls a Firebase callable function with the user's ID token. The request
// body is always {"data": null}: neither subscription operation takes input.
async function callFunction(name, idToken, options) {
    if (typeof idToken !== "string" || !idToken) {
        throw new SubscriptionServiceError("UNAUTHENTICATED", "Sign in to Checkpoint to continue.");
    }
    const fetchImpl = options.fetchImpl || fetch;
    let response;
    try {
        response = await fetchImpl(functionUrl(name, options), {
            method: "POST",
            headers: { "Content-Type": "application/json", Authorization: `Bearer ${idToken}` },
            body: JSON.stringify({ data: null }),
            signal: AbortSignal.timeout(options.timeoutMs || FUNCTION_TIMEOUT_MS)
        });
    } catch {
        throw new SubscriptionServiceError("UNAVAILABLE", "Could not reach the Checkpoint subscription service.");
    }

    let body;
    try {
        body = await response.json();
    } catch {
        throw new SubscriptionServiceError("INTERNAL", "The Checkpoint subscription service returned an invalid response.");
    }
    if (body?.error) {
        const status = typeof body.error.status === "string" ? body.error.status : "INTERNAL";
        const message = typeof body.error.message === "string" && body.error.message.length < 300
            ? body.error.message
            : "The Checkpoint subscription service rejected the request.";
        throw new SubscriptionServiceError(status, message);
    }
    if (!response.ok || !body || !Object.hasOwn(body, "result")) {
        throw new SubscriptionServiceError("INTERNAL", "The Checkpoint subscription service returned an invalid response.");
    }
    return body.result;
}

function parseSubscription(value) {
    const statuses = ["free", "active", "expired", "inactive", "unverified"];
    if (!value || typeof value !== "object" || !statuses.includes(value.status) || typeof value.active !== "boolean") {
        return null;
    }
    const expiresAt = value.expiresAt === null ? null : Date.parse(value.expiresAt);
    if (value.active && !Number.isFinite(expiresAt)) return null;
    return {
        plan: typeof value.plan === "string" ? value.plan : "free",
        status: value.status,
        active: value.active === true && value.status === "active",
        activatedAt: typeof value.activatedAt === "string" ? value.activatedAt : null,
        expiresAt: Number.isFinite(expiresAt) ? new Date(expiresAt).toISOString() : null,
        test: value.test === true
    };
}

// Cached entitlement for exactly one UID. An entry is trusted only for
// ENTITLEMENT_MAX_AGE_MS and only while expiresAt is still in the future,
// so expiry locks features even before the next server check.
class EntitlementCache {
    constructor({ now = Date.now, maxAgeMs = ENTITLEMENT_MAX_AGE_MS } = {}) {
        this.now = now;
        this.maxAgeMs = maxAgeMs;
        this.entry = null;
    }

    clear() {
        this.entry = null;
    }

    record(uid, result) {
        const subscription = parseSubscription(result?.subscription);
        this.entry = subscription
            ? { uid, subscription, testCheckoutAvailable: result.testCheckoutAvailable === true, verified: true, verifiedAt: this.now() }
            : { uid, subscription: null, testCheckoutAvailable: false, verified: false, verifiedAt: this.now() };
    }

    recordFailure(uid) {
        const previous = this.entry?.uid === uid ? this.entry : null;
        this.entry = {
            uid,
            subscription: null,
            testCheckoutAvailable: previous?.testCheckoutAvailable === true,
            verified: false,
            verifiedAt: this.now()
        };
    }

    isFresh(uid) {
        return this.entry?.uid === uid && this.entry.verified && this.now() - this.entry.verifiedAt < this.maxAgeMs;
    }

    hasPaidAccess(uid) {
        const subscription = this.entry?.uid === uid && this.entry.verified ? this.entry.subscription : null;
        return Boolean(
            subscription?.active &&
            this.isFresh(uid) &&
            Date.parse(subscription.expiresAt) > this.now()
        );
    }

    // What the renderer is told. It drives the UI only; enforcement uses hasPaidAccess.
    view(uid) {
        const entry = this.entry?.uid === uid ? this.entry : null;
        const subscription = entry?.subscription;
        const active = this.hasPaidAccess(uid);
        let status = entry ? (entry.verified ? subscription.status : "unverified") : "unknown";
        if (status === "active" && !active) {
            status = Date.parse(subscription.expiresAt) <= this.now() ? "expired" : "unverified";
        }
        return {
            plan: active ? subscription.plan : "free",
            status,
            active,
            verified: Boolean(entry?.verified),
            activatedAt: subscription?.activatedAt ?? null,
            expiresAt: subscription?.expiresAt ?? null,
            test: subscription?.test === true,
            testCheckoutAvailable: entry?.testCheckoutAvailable === true,
            priceLabel: PRO_PRICE_LABEL,
            features: Object.fromEntries(Object.keys(PAID_FEATURES).map(feature => [feature, active]))
        };
    }

    assertFeature(uid, feature) {
        if (!Object.hasOwn(PAID_FEATURES, feature) || !this.hasPaidAccess(uid)) {
            throw new PaymentRequiredError(feature);
        }
    }
}

module.exports = {
    DEFAULT_FUNCTIONS_REGION,
    ENTITLEMENT_MAX_AGE_MS,
    EntitlementCache,
    PAID_BACKEND_COMMANDS,
    PAID_FEATURES,
    PAID_IPC_CHANNELS,
    PAYMENT_REQUIRED_PREFIX,
    PRO_PRICE_LABEL,
    PaymentRequiredError,
    SubscriptionServiceError,
    callFunction,
    functionUrl,
    getEmulatorSettings,
    paidFeatureForChannel,
    paidFeatureForCommand
};
