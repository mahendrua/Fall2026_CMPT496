const assert = require("node:assert/strict");
const fs = require("node:fs");
const net = require("node:net");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const {
    LocalGuiApp,
    createAppEnvironment,
    detectFramework,
    getPreferredPort,
    getLaunchOptions
} = require("../guiLocalApp");

test("selects dev before start and configures supported framework host ports", () => {
    assert.deepEqual(
        getLaunchOptions({
            scripts: { dev: "vite", start: "vite preview" },
            devDependencies: { vite: "^7.0.0" }
        }, "vite", 4173),
        {
            script: "dev",
            args: ["--", "--host", "0.0.0.0", "--port", "4173", "--strictPort"]
        }
    );
    assert.deepEqual(
        getLaunchOptions({
            scripts: { start: "next start" },
            dependencies: { next: "^15.0.0" }
        }, "next", 3000),
        {
            script: "start",
            args: ["--", "--hostname", "0.0.0.0", "--port", "3000"]
        }
    );
});

test("detects common frontend frameworks and their default ports", () => {
    const packages = [
        [{ scripts: { dev: "vite" } }, "vite", 5173],
        [{ dependencies: { next: "^15.0.0" } }, "next", 3000],
        [{ devDependencies: { "@angular/cli": "^20.0.0" } }, "angular", 4200],
        [{ dependencies: { "@vue/cli-service": "^5.0.0" } }, "vue-cli", 8080],
        [{ dependencies: { "react-scripts": "^5.0.0" } }, "create-react-app", 3000],
        [{ scripts: { server: "node server.js" } }, "node", 3000]
    ];

    for (const [packageJson, framework, port] of packages) {
        assert.equal(detectFramework(packageJson), framework);
        assert.equal(getPreferredPort(framework), port);
    }
});

test("uses a server script when dev and start are not defined", () => {
    assert.deepEqual(
        getLaunchOptions({
            scripts: { server: "node tests/server.js" },
            dependencies: { express: "^5.0.0" }
        }, "node", 5173),
        { script: "server", args: [] }
    );
});

test("requires a supported app script", () => {
    assert.throws(
        () => getLaunchOptions({ scripts: { test: "node test.js" } }, 3000),
        /must define a "dev", "start", or "server" script/
    );
});

test("passes only a reduced environment to the local app", () => {
    const previousSecret = process.env.GUI_TEST_SECRET;
    process.env.GUI_TEST_SECRET = "not-for-the-app";
    try {
        const environment = createAppEnvironment(3100, "vite");
        assert.equal(environment.HOST, "0.0.0.0");
        assert.equal(environment.PORT, "3100");
        assert.equal(environment.GUI_TEST_SECRET, undefined);
        assert.equal(environment.__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS, "host.docker.internal");
        assert.equal(createAppEnvironment(3100, "node").__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS, undefined);
    } finally {
        if (previousSecret === undefined) delete process.env.GUI_TEST_SECRET;
        else process.env.GUI_TEST_SECRET = previousSecret;
    }
});

test("starts and stops a local npm app", async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), "gui-local-app-"));
    const app = new LocalGuiApp();
    try {
        fs.writeFileSync(path.join(directory, "package.json"), JSON.stringify({
            scripts: {
                dev: "node server.js"
            }
        }));
        fs.writeFileSync(
            path.join(directory, "server.js"),
            "require('node:http').createServer((_req,res)=>res.end('ready')).listen(Number(process.env.PORT), process.env.HOST);"
        );

        const started = await app.start(directory);
        assert.ok(started.port >= getPreferredPort("node"));
        assert.equal(started.script, "dev");
        assert.ok(app.active);

        await app.stop();
        assert.equal(app.active, null);
        await assert.rejects(new Promise((resolve, reject) => {
            const socket = net.createConnection({ host: "127.0.0.1", port: started.port });
            socket.once("connect", () => {
                socket.destroy();
                resolve();
            });
            socket.once("error", reject);
        }));
    } finally {
        if (app.active) await app.stop();
        fs.rmSync(directory, { recursive: true, force: true });
    }
});
