const path = require("path");

module.exports = {
    testDir: "/opt/gui-runner/tests",
    testMatch: "**/*.{js,ts}",
    testIgnore: ["node_modules/**"],
    timeout: 30000,
    globalTimeout: 30 * 60 * 1000,
    workers: 1,
    fullyParallel: false,
    retries: 0,
    reporter: [[path.join(__dirname, "reporter.cjs")]],
    outputDir: "/tmp/playwright-results",
    use: {
        headless: false,
        proxy: {
            server: process.env.HTTPS_PROXY,
            bypass: "localhost,127.0.0.1,::1"
        },
        launchOptions: {
            args: ["--no-sandbox"]
        }
    },
    projects: [{
        name: "chromium",
        use: { browserName: "chromium" }
    }]
};
