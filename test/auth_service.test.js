const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const {
    getFirebaseConfig,
    readLocalProfile,
    saveLocalProfile,
    verifyFirebaseIdToken
} = require("../auth_service");

function createTemporaryDirectory(t) {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), "checkpoint-auth-"));
    t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
    return directory;
}

function createTestToken(claims = {}) {
    const payload = Buffer.from(JSON.stringify({
        exp: Math.floor(Date.now() / 1000) + 3600,
        ...claims
    })).toString("base64url");
    return `header.${payload}.signature`;
}

test("reports a missing Firebase config and reads the supplied web app settings", t => {
    const directory = createTemporaryDirectory(t);
    const bundledConfigPath = path.join(directory, "firebase-config.json");
    const missing = getFirebaseConfig(directory, bundledConfigPath);
    assert.equal(missing.configured, false);
    assert.equal(missing.configPath, bundledConfigPath);

    fs.writeFileSync(bundledConfigPath, JSON.stringify({
        apiKey: "  project-web-key ",
        authDomain: "checkpoint.example.firebaseapp.com",
        projectId: "checkpoint-example",
        appId: "1:123:web:abc",
        measurementId: "not-used"
    }));
    const configured = getFirebaseConfig(directory);
    assert.equal(configured.configured, true);
    assert.deepEqual(configured.config, {
        apiKey: "project-web-key",
        authDomain: "checkpoint.example.firebaseapp.com",
        projectId: "checkpoint-example",
        appId: "1:123:web:abc",
        measurementId: "not-used"
    });
});

test("loads the Checkpoint Firebase Web app configuration", t => {
    const directory = createTemporaryDirectory(t);
    const result = getFirebaseConfig(directory, path.join(__dirname, "..", "firebase-config.json"));
    assert.equal(result.configured, true);
    assert.equal(result.config.projectId, "checkpoint-cmpt497");
    assert.equal(result.config.authDomain, "checkpoint-cmpt497.firebaseapp.com");
    assert.equal(result.config.measurementId, "G-JF0H4DHHPY");
});

test("rejects malformed or incomplete Firebase configuration", t => {
    const directory = createTemporaryDirectory(t);
    const configPath = path.join(directory, "firebase-config.json");
    fs.writeFileSync(configPath, "{");
    assert.throws(() => getFirebaseConfig(directory, path.join(directory, "not-installed.json")), /not valid JSON/);

    fs.writeFileSync(configPath, JSON.stringify({ apiKey: "only-one-value" }));
    assert.throws(
        () => getFirebaseConfig(directory, path.join(directory, "not-installed.json")),
        /apiKey, authDomain, projectId, appId/
    );
});

test("prefers a user configuration override over the bundled Firebase settings", t => {
    const directory = createTemporaryDirectory(t);
    const bundledPath = path.join(directory, "bundled.json");
    const userConfigPath = path.join(directory, "firebase-config.json");
    fs.writeFileSync(bundledPath, JSON.stringify({
        apiKey: "bundled-key",
        authDomain: "checkpoint.example.firebaseapp.com",
        projectId: "checkpoint-example",
        appId: "1:123:web:abc"
    }));
    fs.writeFileSync(userConfigPath, JSON.stringify({
        apiKey: "local-override-key",
        authDomain: "local.example.firebaseapp.com",
        projectId: "local-example",
        appId: "1:456:web:def"
    }));

    const result = getFirebaseConfig(directory, bundledPath);
    assert.equal(result.configPath, userConfigPath);
    assert.equal(result.config.apiKey, "local-override-key");
});

test("stores a validated local profile by UID without password fields", t => {
    const directory = createTemporaryDirectory(t);
    const profile = saveLocalProfile(directory, {
        uid: "firebase-user-123",
        fullName: "  Checkpoint Tester ",
        email: "tester@example.com",
        professionalRole: "Software Tester / QA Engineer",
        accountCreatedAt: "2026-10-27T00:00:00.000Z",
        password: "must-not-be-saved"
    });

    assert.deepEqual(profile, {
        uid: "firebase-user-123",
        fullName: "Checkpoint Tester",
        email: "tester@example.com",
        professionalRole: "Software Tester / QA Engineer",
        accountCreatedAt: "2026-10-27T00:00:00.000Z"
    });
    assert.deepEqual(readLocalProfile(directory, "firebase-user-123"), profile);
    assert.doesNotMatch(
        fs.readFileSync(path.join(directory, "profiles", "firebase-user-123.json"), "utf8"),
        /password|must-not-be-saved/i
    );
    assert.equal(readLocalProfile(directory, "different-user"), null);
});

test("rejects invalid roles and UID path traversal", t => {
    const directory = createTemporaryDirectory(t);
    const profile = {
        uid: "valid-user",
        fullName: "Checkpoint Tester",
        email: "tester@example.com",
        professionalRole: "Administrator"
    };
    assert.throws(() => saveLocalProfile(directory, profile), /valid professional role/);
    assert.throws(() => saveLocalProfile(directory, { ...profile, uid: "../other" }), /profile is invalid/);
    assert.throws(() => readLocalProfile(directory, "../other"), /profile is invalid/);
});

test("verifies the Firebase token with Identity Toolkit and returns its account identity", async () => {
    const originalFetch = global.fetch;
    const token = createTestToken();
    let requestBody;
    let requestUrl;
    global.fetch = async (url, options) => {
        requestUrl = url;
        requestBody = JSON.parse(options.body);
        return {
            ok: true,
            json: async () => ({
                users: [{
                    localId: "firebase-user-123",
                    email: "tester@example.com",
                    displayName: "Checkpoint Tester",
                    createdAt: "1793059200000"
                }]
            })
        };
    };

    try {
        const identity = await verifyFirebaseIdToken(token, "firebase-web-key");
        assert.match(requestUrl, /accounts:lookup\?key=firebase-web-key$/);
        assert.equal(requestBody.idToken, token);
        assert.equal(identity.uid, "firebase-user-123");
        assert.equal(identity.email, "tester@example.com");
        assert.equal(identity.displayName, "Checkpoint Tester");
        assert.equal(identity.creationTime, "2026-10-27T00:00:00.000Z");
    } finally {
        global.fetch = originalFetch;
    }
});

test("rejects unverifiable and expired Firebase tokens", async () => {
    const originalFetch = global.fetch;
    global.fetch = async () => ({ ok: false });
    try {
        await assert.rejects(
            verifyFirebaseIdToken(createTestToken(), "firebase-web-key"),
            /session could not be verified/
        );
    } finally {
        global.fetch = originalFetch;
    }

    global.fetch = async () => ({
        ok: true,
        json: async () => ({
            users: [{ localId: "firebase-user-123", email: "tester@example.com" }]
        })
    });
    try {
        await assert.rejects(
            verifyFirebaseIdToken(createTestToken({ exp: Math.floor(Date.now() / 1000) - 1 }), "firebase-web-key"),
            /session has expired/
        );
    } finally {
        global.fetch = originalFetch;
    }
});
