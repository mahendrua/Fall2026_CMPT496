const { spawn } = require("child_process");
const net = require("net");
const path = require("path");
const { WebSocket, WebSocketServer } = require("ws");

const history = [];
const clients = new Set();
const server = new WebSocketServer({ host: "0.0.0.0", port: 3001, maxPayload: 64 * 1024 });
let testProcess = null;
let shuttingDown = false;
let proxyReady = false;
let viewerReady = false;
let testsStarted = false;

function broadcast(event) {
    const payload = JSON.stringify(event);
    history.push(event);
    if (history.length > 300) history.shift();

    for (const client of clients) {
        if (client.readyState === WebSocket.OPEN) client.send(payload);
    }
}

server.on("connection", (socket, request) => {
    if (request.url !== `/${process.env.GUI_EVENT_TOKEN}`) {
        socket.close(1008);
        return;
    }
    clients.add(socket);
    for (const event of history) {
        if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(event));
    }
    socket.on("message", message => {
        if (message.toString() === "viewer_ready") {
            viewerReady = true;
            startPlaywrightTests();
        }
    });
    socket.on("close", () => clients.delete(socket));
});

function stop(exitCode = 1) {
    if (shuttingDown) return;
    shuttingDown = true;
    if (testProcess && !testProcess.killed) testProcess.kill("SIGTERM");
    for (const client of clients) client.close();
    server.close(() => process.exit(exitCode));
    setTimeout(() => process.exit(exitCode), 1500).unref();
}

function waitForEgressProxy() {
    const proxyUrl = process.env.HTTPS_PROXY;
    const proxyHost = new URL(proxyUrl).hostname;
    const proxyPort = Number(new URL(proxyUrl).port);
    const deadline = Date.now() + 30000;

    return new Promise((resolve, reject) => {
        const attempt = () => {
            const socket = net.createConnection({ host: proxyHost, port: proxyPort });
            socket.once("connect", () => {
                socket.destroy();
                resolve();
            });
            socket.once("error", () => {
                socket.destroy();
                if (Date.now() >= deadline) {
                    reject(new Error("The allowlist proxy did not become available."));
                } else {
                    setTimeout(attempt, 250);
                }
            });
        };
        attempt();
    });
}

process.on("SIGTERM", () => stop(143));
process.on("SIGINT", () => stop(130));

function startPlaywrightTests() {
    if (!proxyReady || !viewerReady || testsStarted) return;
    testsStarted = true;
    broadcast({ type: "runner_ready", message: "Playwright browser is starting." });

    try {
        const cli = require.resolve("@playwright/test/cli");
        const selectedFiles = JSON.parse(process.env.GUI_SELECTED_FILES || "[]");
        const args = [
            cli,
            "test",
            ...selectedFiles.map(file => path.posix.join("/opt/gui-runner/tests", file)),
            `--config=${path.join(__dirname, "playwright.config.cjs")}`
        ];
        const testEnvironment = { ...process.env };
        delete testEnvironment.GUI_EVENT_TOKEN;
        testProcess = spawn(process.execPath, args, {
            cwd: "/opt/gui-runner",
            env: testEnvironment,
            stdio: ["ignore", "pipe", "pipe"]
        });

        const consume = stream => {
            let pending = "";
            stream.on("data", chunk => {
                pending += chunk.toString();
                const lines = pending.split(/\r?\n/);
                pending = lines.pop();
                for (const line of lines) {
                    if (!line) continue;
                    if (line.startsWith("GUI_EVENT:")) {
                        try {
                            broadcast(JSON.parse(line.slice("GUI_EVENT:".length)));
                        } catch (error) {
                            broadcast({ type: "runner_log", message: `Could not parse runner event: ${error.message}` });
                        }
                    } else {
                        broadcast({ type: "runner_log", message: line.slice(0, 4000) });
                    }
                }
            });
            stream.on("end", () => {
                if (pending) broadcast({ type: "runner_log", message: pending.slice(0, 4000) });
            });
        };

        consume(testProcess.stdout);
        consume(testProcess.stderr);
        testProcess.on("error", error => {
            broadcast({ type: "runner_error", message: `Could not start Playwright: ${error.message}` });
        });
        testProcess.on("close", code => {
            if (!history.some(event => event.type === "run_complete")) {
                broadcast({
                    type: "run_complete",
                    status: code === 0 ? "passed" : "failed",
                    total: 0,
                    passed: 0,
                    failed: code === 0 ? 0 : 1,
                    skipped: 0
                });
            }
            setTimeout(() => stop(code === 0 ? 0 : 1), 10000);
        });
    } catch (error) {
        broadcast({ type: "runner_error", message: `Could not launch Playwright: ${error.message}` });
        broadcast({ type: "run_complete", status: "failed", total: 0, passed: 0, failed: 1, skipped: 0 });
        setTimeout(() => stop(1), 10000);
    }
}

server.on("listening", () => {
    broadcast({ type: "runner_ready", message: "Waiting for the allowlist proxy." });
    waitForEgressProxy()
        .then(() => {
            proxyReady = true;
            broadcast({ type: "runner_ready", message: "Waiting for the application preview to connect." });
            startPlaywrightTests();
        })
        .catch(error => {
            broadcast({ type: "runner_error", message: error.message });
            broadcast({ type: "run_complete", status: "failed", total: 0, passed: 0, failed: 1, skipped: 0 });
            setTimeout(() => stop(1), 10000);
        });
    setTimeout(() => {
        if (viewerReady || testsStarted) return;
        broadcast({ type: "runner_error", message: "The GUI preview did not connect in time." });
        broadcast({ type: "run_complete", status: "failed", total: 0, passed: 0, failed: 1, skipped: 0 });
        setTimeout(() => stop(1), 10000);
    }, 30000);
});
