(function (root, factory) {
    if (typeof module === "object" && module.exports) module.exports = factory;
    else root.createPreviewConnection = factory;
})(globalThis, function ({ createClient, onStatus, setTimer = setTimeout, clearTimer = clearTimeout,
    retryDelays = [250, 500, 1000, 2000, 4000], timeoutMs = 8000 }) {
    let client, timer, attempts = 0, stopped = false, everConnected = false;
    const clear = () => { if (timer != null) clearTimer(timer); timer = null; };
    function dispose() {
        const old = client;
        client = null;
        if (old) old.disconnect();
    }
    function retry(reason) {
        clear();
        dispose();
        if (stopped) return;
        if (attempts >= retryDelays.length) {
            stopped = true;
            onStatus(`Browser preview recovery failed: ${reason}. Test execution continues.`);
            return;
        }
        onStatus(`Reconnecting to browser preview... ${reason}`);
        timer = setTimer(connect, retryDelays[attempts++]);
    }
    function connect() {
        if (stopped) return;
        clear();
        let current;
        try { current = createClient(); } catch (error) { retry(error.message); return; }
        client = current;
        timer = setTimer(() => retry("Preview connection timed out"), timeoutMs);
        current.addEventListener("connect", () => {
            if (client !== current || stopped) return;
            clear();
            onStatus(everConnected ? "Browser preview successfully reconnected" : "Connected to browser preview");
            everConnected = true;
            // Bound repeated short-lived connections as well as failed handshakes.
            timer = setTimer(() => { attempts = 0; timer = null; }, 10000);
        });
        current.addEventListener("disconnect", () => {
            if (client === current && !stopped) retry("Browser disconnected or WebSocket connection refused");
        });
        current.addEventListener("securityfailure", () => {
            if (client !== current || stopped) return;
            stopped = true;
            clear();
            dispose();
            onStatus("Could not authenticate to the browser preview. Test execution continues.");
        });
    }
    connect();
    return { finish(message = "Preview session ended normally") {
        stopped = true;
        clear();
        dispose();
        onStatus(message);
    } };
});
