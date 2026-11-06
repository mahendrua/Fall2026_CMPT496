const assert = require("node:assert/strict");
const fs = require("node:fs");
const net = require("node:net");
const path = require("node:path");
const test = require("node:test");
const createGuiEventConnection = require("../guiEventConnection");
const { probeLoopbackPort } = require("../guiTestRunner");

const delay = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));

async function waitFor(predicate, timeoutMs = 500) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
        if (predicate()) return;
        await delay(2);
    }
    assert.fail("Timed out waiting for the GUI event connection state.");
}

function createFakeSocketClass(onConstruct) {
    return class FakeSocket {
        static attempts = 0;

        constructor(url) {
            this.url = url;
            this.readyState = 0;
            this.listeners = new Map();
            this.closed = false;
            FakeSocket.attempts += 1;
            onConstruct?.(this, FakeSocket.attempts);
        }

        addEventListener(type, listener) {
            const listeners = this.listeners.get(type) || [];
            listeners.push(listener);
            this.listeners.set(type, listeners);
        }

        emit(type, event = {}) {
            for (const listener of this.listeners.get(type) || []) listener(event);
        }

        open() {
            this.readyState = 1;
            this.emit("open");
        }

        fail(code = 1006, reason = "") {
            this.readyState = 3;
            this.emit("close", { code, reason });
        }

        close() {
            this.closed = true;
            this.readyState = 3;
        }
    };
}

test("retries until a delayed event server becomes available", async () => {
    let opened = false;
    const FakeSocket = createFakeSocketClass((socket, attempt) => {
        setTimeout(() => {
            if (attempt < 3) {
                socket.emit("error", { message: "connect ECONNREFUSED" });
                socket.fail();
            } else {
                socket.open();
                opened = true;
            }
        }, 1);
    });
    const connection = createGuiEventConnection({
        url: "ws://127.0.0.1:49123/test-token",
        WebSocketImpl: FakeSocket,
        retryDelays: [1, 1, 1],
        connectionTimeoutMs: 50
    });

    await waitFor(() => opened);
    assert.equal(FakeSocket.attempts, 3);
    connection.finish();
});

test("reports connection refusal after bounded attempts", async () => {
    let failure = "";
    const FakeSocket = createFakeSocketClass(socket => {
        setTimeout(() => {
            socket.emit("error", { message: "connect ECONNREFUSED" });
            socket.fail();
        }, 1);
    });
    createGuiEventConnection({
        url: "ws://127.0.0.1:49123/test-token",
        WebSocketImpl: FakeSocket,
        retryDelays: [1],
        connectionTimeoutMs: 50,
        onFailure: message => { failure = message; }
    });

    await waitFor(() => Boolean(failure));
    assert.match(failure, /after 2 attempts/);
    assert.match(failure, /ECONNREFUSED/);
});

test("stops reconnecting when Docker reports that the runner exited", async () => {
    let failure = "";
    const sockets = [];
    const FakeSocket = createFakeSocketClass(socket => sockets.push(socket));
    const connection = createGuiEventConnection({
        url: "ws://127.0.0.1:49123/test-token",
        WebSocketImpl: FakeSocket,
        retryDelays: [1, 1, 1],
        connectionTimeoutMs: 50,
        onFailure: message => { failure = message; }
    });

    connection.notifyRunnerExit("Runner exited with code 1.");
    await waitFor(() => Boolean(failure));
    await delay(10);
    assert.equal(FakeSocket.attempts, 1);
    assert.equal(sockets[0].closed, true);
    assert.equal(failure, "Runner exited with code 1.");
});

test("reports WebSocket connection timeouts and closes failed sockets", async () => {
    let failure = "";
    const sockets = [];
    const activeTimers = new Set();
    const setTrackedTimer = (callback, milliseconds) => {
        const timer = setTimeout(() => {
            activeTimers.delete(timer);
            callback();
        }, milliseconds);
        activeTimers.add(timer);
        return timer;
    };
    const clearTrackedTimer = timer => {
        activeTimers.delete(timer);
        clearTimeout(timer);
    };
    const FakeSocket = createFakeSocketClass(socket => sockets.push(socket));
    createGuiEventConnection({
        url: "ws://127.0.0.1:49123/test-token",
        WebSocketImpl: FakeSocket,
        retryDelays: [1, 1],
        connectionTimeoutMs: 8,
        setTimer: setTrackedTimer,
        clearTimer: clearTrackedTimer,
        onFailure: message => { failure = message; }
    });

    await waitFor(() => Boolean(failure));
    assert.match(failure, /timed out after 8 ms/);
    assert.equal(FakeSocket.attempts, 3);
    assert.ok(sockets.every(socket => socket.closed));
    assert.equal(activeTimers.size, 0);
    await delay(10);
    assert.equal(FakeSocket.attempts, 3);
});

test("reconnects after an unexpected close and can then finish cleanly", async () => {
    const sockets = [];
    let messageReceived = false;
    const FakeSocket = createFakeSocketClass((socket, attempt) => {
        sockets.push(socket);
        setTimeout(() => {
            socket.open();
            if (attempt === 1) {
                setTimeout(() => socket.fail(1006, "connection reset"), 1);
            } else {
                setTimeout(() => socket.emit("message", { data: "run_complete" }), 1);
            }
        }, 1);
    });
    let connection;
    connection = createGuiEventConnection({
        url: "ws://127.0.0.1:49123/test-token",
        WebSocketImpl: FakeSocket,
        retryDelays: [1, 1],
        connectionTimeoutMs: 50,
        onMessage: () => {
            messageReceived = true;
            connection.finish();
        }
    });

    await waitFor(() => messageReceived);
    assert.equal(FakeSocket.attempts, 2);
    assert.equal(connection.isConnected(), false);
    assert.ok(
        sockets[0].closed || sockets[0].readyState === 3,
        "the first socket should have closed before the replacement connected"
    );
});

test("reports whether the published event port is reachable or refusing connections", async () => {
    const server = net.createServer();
    await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
    const openPort = server.address().port;
    try {
        assert.deepEqual(await probeLoopbackPort(openPort), {
            category: "websocket_handshake_or_policy",
            message: "The published event port is reachable. The failure is likely in the WebSocket handshake or renderer connection policy."
        });
    } finally {
        await new Promise(resolve => server.close(resolve));
    }

    const closedServer = net.createServer();
    await new Promise(resolve => closedServer.listen(0, "127.0.0.1", resolve));
    const closedPort = closedServer.address().port;
    await new Promise(resolve => closedServer.close(resolve));
    const refused = await probeLoopbackPort(closedPort);
    assert.equal(refused.category, "connection_refused");
    assert.match(refused.message, /ECONNREFUSED/);
});

test("allows the loopback WebSocket origin required by the dynamically published port", () => {
    const html = fs.readFileSync(path.join(__dirname, "..", "index.html"), "utf8");
    assert.match(html, /connect-src[^"]*ws:\/\/127\.0\.0\.1:\*/);
});
