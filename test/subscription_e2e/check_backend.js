/*
 * check_backend.js
 *
 * Checks the deployed-to-emulator backend directly: Firestore rules on the
 * users/{uid} record and the activateTestSubscription / getEntitlement
 * callables. Called by run.js with the test-mode state the server reports.
 */
"use strict";

const E = require("./emulator");

const FUNCTION_URL = name => `${E.LOCAL_HOSTS.functions}/${E.PROJECT_ID}/us-central1/${name}`;

async function rawCall(name, idToken, data) {
    const url = FUNCTION_URL(name);
    const headers = idToken ? { Authorization: `Bearer ${idToken}` } : {};
    const fetchResult = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json", ...headers }, body: JSON.stringify({ data }) });
    return fetchResult.json();
}

async function runBackendChecks({ testMode, cardMarker, expect }) {
    // --- Accounts start free -------------------------------------------------
    const fresh = await E.signUp(E.newAccount("backend-new"));
    const freshEntitlement = await E.callAs(fresh, "getEntitlement");
    expect(freshEntitlement.subscription.status === "free" && freshEntitlement.subscription.active === false,
        "backend: a new account with no record is free", freshEntitlement.subscription);
    expect(freshEntitlement.testCheckoutAvailable === testMode,
        `backend: getEntitlement reports test checkout ${testMode ? "available" : "unavailable"}`, freshEntitlement.testCheckoutAvailable);

    const existing = await E.signUp(E.newAccount("backend-existing"));
    const asExisting = { Authorization: `Bearer ${existing.idToken}` };
    const profileCreate = await E.patchUserDoc(existing.uid, {
        fullName: E.stringValue(existing.name),
        professionalRole: E.stringValue("Software Tester / QA Engineer")
    }, asExisting);
    expect(profileCreate.status === 200, "rules: a user can create their own record with profile fields", profileCreate.status);
    const existingEntitlement = await E.callAs(existing, "getEntitlement");
    expect(existingEntitlement.subscription.status === "free", "backend: an existing record without subscription data is free", existingEntitlement.subscription);

    // --- Rules: users cannot touch subscription fields -------------------------
    const profileUpdate = await E.patchUserDoc(existing.uid, { professionalRole: E.stringValue("Team Lead") }, asExisting);
    expect(profileUpdate.status === 200, "rules: a user can still update their own profile fields", profileUpdate.status);

    const forgedSubscription = E.mapValue({
        plan: E.stringValue("checkpoint_pro_monthly"),
        status: E.stringValue("active"),
        activatedAt: E.timestampValue(Date.now()),
        expiresAt: E.timestampValue(Date.now() + 365 * 86400000),
        test: { booleanValue: true }
    });
    const selfGrantUpdate = await E.patchUserDoc(existing.uid, { subscription: forgedSubscription }, asExisting);
    expect(selfGrantUpdate.status === 403, "rules: a user cannot add subscription fields to their record", selfGrantUpdate.status);
    const selfGrantCreate = await E.patchUserDoc(fresh.uid, { subscription: forgedSubscription }, { Authorization: `Bearer ${fresh.idToken}` });
    expect(selfGrantCreate.status === 403, "rules: a user cannot create their record with subscription fields", selfGrantCreate.status);
    const otherWrite = await E.patchUserDoc(existing.uid, { fullName: E.stringValue("Intruder") }, { Authorization: `Bearer ${fresh.idToken}` });
    expect(otherWrite.status === 403, "rules: a user cannot write another user's record", otherWrite.status);
    const otherRead = await E.getUserDoc(existing.uid, { Authorization: `Bearer ${fresh.idToken}` });
    expect(otherRead.status === 403, "rules: a user cannot read another user's record", otherRead.status);
    const deleteOwn = await E.deleteUserDoc(existing.uid, asExisting);
    expect(deleteOwn.status === 403, "rules: a user cannot delete their record", deleteOwn.status);
    const stillFree = await E.callAs(existing, "getEntitlement");
    expect(stillFree.subscription.status === "free", "backend: rejected writes left the account free", stillFree.subscription);

    // --- Unauthenticated and payload-carrying calls --------------------------------
    const anonymous = await rawCall("activateTestSubscription", null, null);
    expect(anonymous.error?.status === (testMode ? "UNAUTHENTICATED" : "FAILED_PRECONDITION"),
        "backend: checkout without a sign-in token is rejected", anonymous.error);
    const withCard = await rawCall("activateTestSubscription", fresh.idToken, { cardNumber: cardMarker });
    expect(Boolean(withCard.error) && !withCard.result, "backend: checkout carrying card data is rejected", withCard.error?.status);
    const afterCard = await E.getUserDoc(fresh.uid);
    expect(afterCard.status === 404, "backend: a rejected checkout writes nothing", afterCard.status);

    if (!testMode) {
        let disabledError = null;
        try { await E.callAs(fresh, "activateTestSubscription"); } catch (error) { disabledError = error; }
        expect(disabledError?.code === "FAILED_PRECONDITION", "backend: checkout fails while test mode is disabled", disabledError?.code);
        const noDoc = await E.getUserDoc(fresh.uid);
        expect(noDoc.status === 404, "backend: disabled checkout created no subscription", noDoc.status);
        return;
    }

    // --- Checkout: idempotent, server time, one month ------------------------------
    const buyer = await E.signUp(E.newAccount("backend-buyer"));
    const before = Date.now();
    const results = await Promise.all(Array.from({ length: 5 }, () => E.callAs(buyer, "activateTestSubscription")));
    const created = results.filter(result => result.created).length;
    expect(created === 1, "backend: five simultaneous checkouts create exactly one subscription", results.map(r => r.created));
    expect(new Set(results.map(r => r.subscription.expiresAt)).size === 1, "backend: every simultaneous checkout reports the same expiry");
    const retry = await E.callAs(buyer, "activateTestSubscription");
    expect(retry.created === false && retry.subscription.expiresAt === results[0].subscription.expiresAt,
        "backend: a retry neither duplicates nor extends the subscription", retry);

    const stored = await E.getUserDoc(buyer.uid);
    const fields = stored.json?.fields?.subscription?.mapValue?.fields || {};
    expect(JSON.stringify(Object.keys(fields).sort()) === JSON.stringify(["activatedAt", "expiresAt", "plan", "status", "test"]),
        "firestore: only plan, status, activatedAt, expiresAt and test are stored", Object.keys(fields));
    expect(fields.test?.booleanValue === true && fields.plan?.stringValue === "checkpoint_pro_monthly",
        "firestore: the record is marked as a test subscription", fields);
    const activatedAt = Date.parse(fields.activatedAt?.timestampValue);
    const expiresAt = new Date(fields.expiresAt?.timestampValue);
    const expectedExpiry = new Date(activatedAt);
    expectedExpiry.setUTCMonth(expectedExpiry.getUTCMonth() + 1);
    expect(Math.abs(activatedAt - before) < 60000, "firestore: activation time comes from the server clock", fields.activatedAt);
    expect(Math.abs(expiresAt - expectedExpiry) < 4 * 86400000 && expiresAt > activatedAt + 27 * 86400000,
        "firestore: the subscription lasts one month", [fields.activatedAt, fields.expiresAt]);

    const active = await E.callAs(buyer, "getEntitlement");
    expect(active.subscription.active === true, "backend: the buyer is entitled after checkout", active.subscription);

    // --- Expiry --------------------------------------------------------------------
    await E.expireSubscription(buyer.uid);
    const expired = await E.callAs(buyer, "getEntitlement");
    expect(expired.subscription.status === "expired" && expired.subscription.active === false,
        "backend: an expired subscription is not active", expired.subscription);
    const renewed = await E.callAs(buyer, "activateTestSubscription");
    expect(renewed.created === true && Date.parse(renewed.subscription.expiresAt) > Date.now(),
        "backend: after expiry a new test purchase starts a new month (no automatic renewal)", renewed);
}

module.exports = { runBackendChecks };
