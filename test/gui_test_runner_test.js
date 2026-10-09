const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const {
    GuiTestRunner,
    createProxyConfig,
    normalizeAllowedHosts,
    validateGuiTestFiles
} = require("../guiTestRunner");

function fileRecord(directory, name, content) {
    const filePath = path.join(directory, name);
    fs.writeFileSync(filePath, content);
    return {
        path: filePath,
        relativePath: name,
        stat: fs.lstatSync(filePath)
    };
}

test("accepts JavaScript and TypeScript Playwright test files", () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), "gui-test-validation-"));
    try {
        const files = [
            fileRecord(directory, "login.spec.js", "test('login', async () => {});"),
            fileRecord(directory, "profile.test.ts", "test('profile', async () => {});")
        ];

        assert.deepEqual(
            validateGuiTestFiles(files).map(file => file.relativePath),
            ["login.spec.js", "profile.test.ts"]
        );
    } finally {
        fs.rmSync(directory, { recursive: true, force: true });
    }
});

test("rejects unsupported, empty, oversized, and duplicate-path files", () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), "gui-test-validation-"));
    try {
        assert.throws(
            () => validateGuiTestFiles([fileRecord(directory, "test.py", "test")]),
            /Unsupported file/
        );
        assert.throws(
            () => validateGuiTestFiles([fileRecord(directory, "empty.spec.js", "")]),
            /is empty/
        );
        assert.throws(
            () => validateGuiTestFiles([fileRecord(directory, "large.spec.ts", Buffer.alloc(2 * 1024 * 1024 + 1))]),
            /2 MB per-file limit/
        );
        const duplicate = fileRecord(directory, "duplicate.js", "test");
        assert.throws(
            () => validateGuiTestFiles([duplicate], new Set(["duplicate.js"])),
            /duplicate or invalid path/
        );
    } finally {
        fs.rmSync(directory, { recursive: true, force: true });
    }
});

test("normalizes allowed hostnames and limits proxy destinations", () => {
    assert.deepEqual(
        normalizeAllowedHosts(["example.com", "app.example.net:5173"]),
        [
            { hostname: "example.com", ports: [80, 443], includeSubdomains: false },
            { hostname: "app.example.net", ports: [80, 443, 5173], includeSubdomains: false }
        ]
    );
    assert.deepEqual(
        normalizeAllowedHosts(["host.docker.internal:3000"]),
        [{ hostname: "host.docker.internal", ports: [3000], includeSubdomains: false }]
    );

    const config = createProxyConfig(["*.example.com", "host.docker.internal:3000"]);
    const exactConfig = createProxyConfig(["example.com"]);
    assert.match(config, /http_access deny private_destination !docker_host/);
    assert.match(config, /allowed_domain_0 dstdomain example\.com \.example\.com/);
    assert.match(config, /allowed_port_1 port 3000/);
    assert.match(config, /http_access deny all/);
    assert.match(exactConfig, /allowed_domain_0 dstdomain example\.com\n/);
    assert.doesNotMatch(exactConfig, /\.example\.com/);
});

test("rejects unsafe or malformed allowed host entries", () => {
    assert.throws(() => normalizeAllowedHosts([]), /at least one/);
    assert.throws(() => normalizeAllowedHosts(["localhost"]), /invalid or blocked/);
    assert.throws(() => normalizeAllowedHosts(["127.0.0.1"]), /invalid or blocked/);
    assert.throws(() => normalizeAllowedHosts(["example.com:70000"]), /between 1 and 65535/);
    assert.throws(() => normalizeAllowedHosts(["*.host.docker.internal"]), /invalid or blocked/);
    assert.throws(() => normalizeAllowedHosts(["example.com; http://evil.test"]), /Invalid allowed host/);
});

test("keeps uploaded test helpers in a shared relative layout", async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), "gui-test-upload-"));
    const specs = path.join(directory, "specs");
    const helpers = path.join(directory, "helpers");
    fs.mkdirSync(specs);
    fs.mkdirSync(helpers);
    const specPath = path.join(specs, "login.spec.ts");
    const helperPath = path.join(helpers, "session.ts");
    fs.writeFileSync(specPath, "import './test';");
    fs.writeFileSync(helperPath, "export const session = true;");

    const runner = new GuiTestRunner({
        appRoot: directory,
        resourcesPath: directory,
        isPackaged: false,
        onEvent: () => {}
    });
    try {
        runner.addFiles([specPath]);
        const files = runner.addFiles([helperPath]);

        assert.deepEqual(files.map(file => file.name), ["specs/login.spec.ts", "helpers/session.ts"]);
        const storedHelper = Array.from(runner.uploadedFiles.values())
            .find(file => file.relativePath === "helpers/session.ts");
        assert.equal(storedHelper.content.toString(), "export const session = true;");
    } finally {
        await runner.dispose();
        fs.rmSync(directory, { recursive: true, force: true });
    }
});
