const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

// The Python backend saves its results under backendOutputDir: the app folder
// when running from source, the backend's own folder in the installed app.
// Reading results from __dirname works from source but finds nothing once the
// app is installed, so main.js must read them from backendOutputDir.
const mainJs = fs.readFileSync(path.join(__dirname, "..", "..", "main.js"), "utf8");

test("main.js reads backend results from backendOutputDir, not the app folder", () => {
    const fromAppFolder = mainJs.match(
        /path\.join\(\s*__dirname\s*,\s*["'](agent|run_reports|run_logs)["']/g
    ) || [];

    assert.deepEqual(fromAppFolder, []);
});
