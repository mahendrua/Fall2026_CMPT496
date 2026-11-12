(function exposeGuiEventConnection(root, createConnection) {
    if (typeof module === "object" && module.exports) {
        module.exports = createConnection;
    } else {
        root.GuiEventConnection = createConnection;
    }
})(globalThis, function createGuiEventConnection({
    url,
    WebSocketImpl = WebSocket,
    onOpen = () => {},
    onMessage = () => {},
    onStatus = () => {},
    onFailure = () => {},
    retryDelays = [250, 500, 1000, 1500, 2000, 2000],
    connectionTimeoutMs = 4000,
    setTimer = setTimeout,
    clearTimer = clearTimeout
}) {
    let socket = null;
    let retryTimer = null;
    let connectionTimer = null;
    let stableTimer = null;
    let retryIndex = 0;
    let stopped = false;
    let connected = false;
    let lastFailure = "the event server did not accept a WebSocket connection";

    const clearTimers = () => {
        if (retryTimer !== null) clearTimer(retryTimer);
        if (connectionTimer !== null) clearTimer(connectionTimer);
        if (stableTimer !== null) clearTimer(stableTimer);
        retryTimer = null;
        connectionTimer = null;
        stableTimer = null;
    };

    const closeSocket = () => {
        const currentSocket = socket;
        socket = null;
        if (currentSocket && currentSocket.readyState < 2) {
            try {
                currentSocket.close();
            } catch (error) {
                onStatus(`Could not close the GUI event socket cleanly: ${error.message}`);
            }
        }
    };

    const fail = message => {
        if (stopped) return;
        stopped = true;
        connected = false;
        clearTimers();
        closeSocket();
        onFailure(message);
    };

    const scheduleRetry = reason => {
        if (stopped) return;
        lastFailure = reason;
        if (retryIndex >= retryDelays.length) {
            fail(`Could not connect to the GUI test event stream after ${retryIndex + 1} attempts: ${lastFailure}.`);
            return;
        }
        const delay = retryDelays[retryIndex++];
        onStatus(`GUI test event stream connection failed (${lastFailure}); retrying in ${delay} ms (${retryIndex}/${retryDelays.length}).`);
        retryTimer = setTimer(() => {
            retryTimer = null;
            connect();
        }, delay);
    };

    const connect = () => {
        if (stopped || socket) return;
        let currentSocket;
        try {
            currentSocket = new WebSocketImpl(url);
        } catch (error) {
            scheduleRetry(`WebSocket initialization failed: ${error.message}`);
            return;
        }
        socket = currentSocket;
        let opened = false;
        connectionTimer = setTimer(() => {
            if (socket !== currentSocket || opened || stopped) return;
            connectionTimer = null;
            lastFailure = `connection timed out after ${connectionTimeoutMs} ms`;
            closeSocket();
            scheduleRetry(lastFailure);
        }, connectionTimeoutMs);

        currentSocket.addEventListener("open", () => {
            if (socket !== currentSocket || stopped) return;
            opened = true;
            connected = true;
            if (connectionTimer !== null) clearTimer(connectionTimer);
            connectionTimer = null;
            stableTimer = setTimer(() => {
                stableTimer = null;
                retryIndex = 0;
            }, 5000);
            onStatus("Connected to the GUI test event stream.");
            try {
                onOpen(currentSocket);
            } catch (error) {
                currentSocket.close();
                socket = null;
                scheduleRetry(`could not initialize the event stream: ${error.message}`);
            }
        });
        currentSocket.addEventListener("message", event => {
            if (socket === currentSocket && !stopped) onMessage(event);
        });
        currentSocket.addEventListener("error", event => {
            if (socket !== currentSocket || stopped) return;
            const detail = event?.message ? `WebSocket error: ${event.message}` : "WebSocket transport error";
            lastFailure = detail;
            onStatus(`GUI test event stream reported a ${detail.toLowerCase()}.`);
            if (opened) {
                if (stableTimer !== null) clearTimer(stableTimer);
                stableTimer = null;
                socket = null;
                connected = false;
                currentSocket.close();
                scheduleRetry(detail);
            }
        });
        currentSocket.addEventListener("close", event => {
            if (socket !== currentSocket || stopped) return;
            socket = null;
            if (connectionTimer !== null) clearTimer(connectionTimer);
            connectionTimer = null;
            if (stableTimer !== null) clearTimer(stableTimer);
            stableTimer = null;
            const closeDetail = event?.code
                ? `socket closed unexpectedly (code ${event.code}${event.reason ? `: ${event.reason}` : ""})`
                : "socket closed unexpectedly without a close code";
            if (!opened) {
                scheduleRetry(lastFailure || closeDetail);
            } else {
                connected = false;
                scheduleRetry(closeDetail);
            }
        });
    };

    connect();

    return {
        finish() {
            if (stopped) return;
            stopped = true;
            connected = false;
            clearTimers();
            closeSocket();
        },
        notifyRunnerExit(message) {
            if (!stopped) fail(message || "The Playwright runner exited before the event stream connected.");
        },
        stopWithError(message) {
            if (!stopped) fail(message || "The GUI test event stream could not be established.");
        },
        cancel() {
            if (stopped) return;
            stopped = true;
            connected = false;
            clearTimers();
            closeSocket();
        },
        isConnected() {
            return connected;
        }
    };
});
