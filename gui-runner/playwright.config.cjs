const path = require("path");

const secureOrigin = process.env.GUI_SECURE_ORIGIN;
const secureOriginArgument = secureOrigin
    ? `--unsafely-treat-insecure-origin-as-secure=${secureOrigin}`
    : null;

module.exports = {
    globalSetup: path.join(__dirname, 'appPreflight.cjs'),
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
        baseURL: process.env.BASE_URL,
        screenshot: "only-on-failure",
        headless: false,
        proxy: {
            server: process.env.HTTPS_PROXY,
            bypass: "localhost,127.0.0.1,::1"
        },
        launchOptions: {
            slowMo: process.env.GUI_DEMO_MODE === "1" ? 600 : 0,
            args: ["--no-sandbox", ...(secureOriginArgument ? [secureOriginArgument] : [])]
        }
    },
    projects: [{
        name: "chromium",
        use: { browserName: "chromium" }
    }]
};
