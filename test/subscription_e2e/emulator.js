/*
 * emulator.js
 *
 * Helpers for the Checkpoint Pro end-to-end checks. Everything here talks to
 * the local Firebase Emulator Suite only (see firebase.json); each URL is
 * built from LOCAL_HOSTS and checked before any request, so these helpers can
 * never reach or reset the real Firebase project.
 */
"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const { callFunction, getEmulatorSettings } = require("../../entitlement_service");

const REPO = path.resolve(__dirname, "..", "..");
const PROJECT_ID = JSON.parse(fs.readFileSync(path.join(REPO, ".firebaserc"), "utf8")).projects.default;
const LOCAL_HOSTS = Object.freeze({
    auth: "http://127.0.0.1:9099",
    firestore: "http://127.0.0.1:8080",
    functions: "http://127.0.0.1:5001"
});
const FIRESTORE_DOCS = `${LOCAL_HOSTS.firestore}/v1/projects/${PROJECT_ID}/databases/(default)/documents`;
// The Firestore emulator treats "Bearer owner" as an admin that bypasses rules.
const ADMIN = { Authorization: "Bearer owner" };

const realFetch = globalThis.fetch;

function assertLocal(url) {
    const { hostname } = new URL(url);
    if (hostname !== "127.0.0.1") throw new Error(`Refusing to contact a non-emulator host: ${hostname}`);
}

async function request(url, { method = "GET", headers = {}, body } = {}) {
    assertLocal(url);
    const response = await realFetch(url, {
        method,
        headers: { "Content-Type": "application/json", ...headers },
        body: body === undefined ? undefined : JSON.stringify(body)
    });
    const text = await response.text();
    let json = null;
    try { json = text ? JSON.parse(text) : null; } catch { /* not JSON */ }
    return { status: response.status, ok: response.ok, json, text };
}

async function emulatorsRunning() {
    const checks = await Promise.all(Object.values(LOCAL_HOSTS).map(host =>
        realFetch(host).then(() => true, () => false)
    ));
    return checks.every(Boolean);
}

// Wipes emulator data only (both URLs are the emulators' own admin endpoints).
async function resetEmulators() {
    await request(`${LOCAL_HOSTS.firestore}/emulator/v1/projects/${PROJECT_ID}/databases/(default)/documents`, { method: "DELETE" });
    await request(`${LOCAL_HOSTS.auth}/emulator/v1/projects/${PROJECT_ID}/accounts`, { method: "DELETE" });
}

function newAccount(label) {
    const id = crypto.randomBytes(4).toString("hex");
    return { email: `${label}-${id}@checkpoint.test`, password: `Pw-${crypto.randomBytes(9).toString("base64url")}`, name: `Test ${label}` };
}

async function signUp(account) {
    const result = await request(`${LOCAL_HOSTS.auth}/identitytoolkit.googleapis.com/v1/accounts:signUp?key=emulator`, {
        method: "POST",
        body: { email: account.email, password: account.password, returnSecureToken: true }
    });
    if (!result.ok) throw new Error(`Emulator sign-up failed: ${result.text}`);
    await request(`${LOCAL_HOSTS.auth}/identitytoolkit.googleapis.com/v1/accounts:update?key=emulator`, {
        method: "POST",
        body: { idToken: result.json.idToken, displayName: account.name, returnSecureToken: true }
    });
    return signIn(account);
}

async function signIn(account) {
    const result = await request(`${LOCAL_HOSTS.auth}/identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=emulator`, {
        method: "POST",
        body: { email: account.email, password: account.password, returnSecureToken: true }
    });
    if (!result.ok) throw new Error(`Emulator sign-in failed: ${result.text}`);
    return { ...account, uid: result.json.localId, idToken: result.json.idToken };
}

const functionOptions = () => ({ projectId: PROJECT_ID, emulators: getEmulatorSettings({ CHECKPOINT_FIREBASE_EMULATORS: "true" }, false) });
const callAs = (user, name) => callFunction(name, user.idToken, functionOptions());

// Firestore REST value helpers.
const stringValue = value => ({ stringValue: value });
const timestampValue = date => ({ timestampValue: new Date(date).toISOString() });
const mapValue = fields => ({ mapValue: { fields } });

function userDocUrl(uid, fieldPaths = []) {
    const mask = fieldPaths.map(field => `updateMask.fieldPaths=${encodeURIComponent(field)}`).join("&");
    return `${FIRESTORE_DOCS}/users/${encodeURIComponent(uid)}${mask ? `?${mask}` : ""}`;
}

const getUserDoc = (uid, headers = ADMIN) => request(userDocUrl(uid), { headers });
const patchUserDoc = (uid, fields, headers) =>
    request(userDocUrl(uid, Object.keys(fields)), { method: "PATCH", headers, body: { fields } });
const deleteUserDoc = (uid, headers) => request(userDocUrl(uid), { method: "DELETE", headers });
const listUserDocs = () => request(`${FIRESTORE_DOCS}/users?pageSize=300`, { headers: ADMIN });

// Moves a subscription's expiry into the past, as if a month had gone by.
function expireSubscription(uid) {
    return request(userDocUrl(uid, ["subscription.expiresAt"]), {
        method: "PATCH",
        headers: ADMIN,
        body: { fields: { subscription: mapValue({ expiresAt: timestampValue(Date.now() - 60 * 1000) }) } }
    });
}

module.exports = {
    ADMIN,
    LOCAL_HOSTS,
    PROJECT_ID,
    REPO,
    callAs,
    deleteUserDoc,
    emulatorsRunning,
    expireSubscription,
    getUserDoc,
    listUserDocs,
    mapValue,
    newAccount,
    patchUserDoc,
    resetEmulators,
    signIn,
    signUp,
    stringValue,
    timestampValue
};
