const { execFile } = require("node:child_process");
const { promisify } = require("node:util");
const exec = promisify(execFile);

async function inspectDisplay({ display = process.env.DISPLAY, run = exec } = {}) {
    if (display !== ":99") return { code: "incorrect_display", message: `Chromium DISPLAY is ${display || "unset"}; preview captures :99.` };
    try {
        const { stdout } = await run("xprop", ["-root", "_NET_CLIENT_LIST"], { timeout: 2000 });
        const windows = stdout.match(/0x[0-9a-f]+/gi) || [];
        for (const id of windows) {
            const { stdout: info } = await run("xprop", ["-id", id, "WM_CLASS", "_NET_WM_STATE"], { timeout: 2000 });
            if (!/chromium|google-chrome/i.test(info)) continue;
            if (/_NET_WM_STATE_HIDDEN/.test(info)) return { code: "browser_hidden", message: "VNC display is ready, but the Chromium window is minimized or hidden." };
            return { code: "browser_visible", message: "Chromium window is visible on Xvfb :99; VNC captures that display." };
        }
        const { stdout: processes } = await run("ps", ["-eo", "args"], { timeout: 2000 });
        const headless = processes.split("\n").some(line => /chrome|chromium/i.test(line) && /--headless/.test(line));
        return headless
            ? { code: "browser_headless", message: "Chromium is running headless; it has no window on the VNC display. Check test-level browser overrides." }
            : { code: "no_browser_window", message: "Xvfb :99 is ready, but no Chromium window is visible yet. The browser may be starting, closed between tests, or not using this display." };
    } catch (error) {
        return { code: "display_unavailable", message: `Could not inspect Xvfb :99: ${error.message}` };
    }
}
module.exports = { inspectDisplay };
