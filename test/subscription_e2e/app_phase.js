/*
 * app_phase.js
 *
 * One launch of the real Checkpoint app (main.js, preload.js, index.html,
 * renderer.js, upgrade.js, auth.bundle.js) against the Firebase emulators.
 * Started by run.js; not meant to be run by hand.
 *
 *   SUB_E2E_CONFIG  JSON file: { phase, userData, resultPath, accounts }
 *   SUB_E2E_CARD    the dummy card number to type (kept out of every file)
 *
 * Phases: "purchase" (new account, gates, checkout), "restart" (persistence,
 * account switch, expiry) and "disabled" (server test mode off).
 *
 * The Python backend is replaced with an idle process: every paid backend
 * command must be refused before it would reach Python. Every request the
 * main process or the page makes, and every console line, is recorded so
 * run.js can check the card number never left the page.
 */
"use strict";

const path = require("path");
const fs = require("fs");
const childProcess = require("child_process");
const { app, BrowserWindow, session } = require("electron");
const E = require("./emulator"); // captures the real fetch before it is wrapped below

const config = JSON.parse(fs.readFileSync(process.env.SUB_E2E_CONFIG, "utf8"));
const CARD = process.env.SUB_E2E_CARD || "";
const REPO = E.REPO;

process.env.CHECKPOINT_FIREBASE_EMULATORS = "true";
app.setPath("userData", config.userData);

// --- Recording -------------------------------------------------------------
const captures = { mainRequests: [], rendererRequests: [], consoleLines: [] };
const appFetch = globalThis.fetch;
globalThis.fetch = (input, init = {}) => {
    captures.mainRequests.push({
        url: String(input?.url || input),
        method: init.method || "GET",
        body: typeof init.body === "string" ? init.body : ""
    });
    return appFetch(input, init);
};
for (const level of ["log", "info", "warn", "error", "debug"]) {
    const original = console[level].bind(console);
    console[level] = (...args) => {
        captures.consoleLines.push(`[main:${level}] ${args.map(arg => (arg instanceof Error ? arg.stack : typeof arg === "string" ? arg : JSON.stringify(arg))).join(" ")}`);
        original(...args);
    };
}

// --- Idle stand-in for the Python backend ------------------------------------
const children = [];
const realSpawn = childProcess.spawn;
childProcess.spawn = (command, args = [], options = {}) => {
    const isBackend = /[\\/]main(\.exe)?$/i.test(String(command)) || args.some(arg => /main\.py$/i.test(String(arg)));
    const child = isBackend
        ? realSpawn(process.execPath, ["-e", "process.stdin.resume()"], { ...options, env: { ...process.env, ELECTRON_RUN_AS_NODE: "1" } })
        : realSpawn(command, args, options);
    children.push(child);
    return child;
};

// main.js clears the repo's error_log.json when the backend starts; put it back.
const errorLog = path.join(REPO, "error_log.json");
const savedErrorLog = fs.existsSync(errorLog) ? fs.readFileSync(errorLog) : null;
process.on("exit", () => {
    children.forEach(child => { try { child.kill(); } catch { /* already gone */ } });
    if (savedErrorLog !== null) fs.writeFileSync(errorLog, savedErrorLog);
});

const realLoadFile = BrowserWindow.prototype.loadFile;
BrowserWindow.prototype.loadFile = function (file, options) {
    return realLoadFile.call(this, path.isAbsolute(file) ? file : path.join(REPO, file), options);
};

let win = null;
app.on("browser-window-created", (_event, window) => {
    if (win) return;
    win = window;
    win.webContents.on("console-message", (event, level, message) => {
        captures.consoleLines.push(`[renderer] ${event?.message ?? message}`);
    });
});
app.whenReady().then(() => {
    session.defaultSession.webRequest.onBeforeRequest((details, callback) => {
        const body = (details.uploadData || []).map(part => (part.bytes ? Buffer.from(part.bytes).toString("utf8") : "")).join("");
        captures.rendererRequests.push({ url: details.url, method: details.method, body });
        callback({});
    });
});

require(path.join(REPO, "main.js"));

// --- Helpers -----------------------------------------------------------------
const results = [];
const J = JSON.stringify;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const js = code => win.webContents.executeJavaScript(code, true);

function expect(condition, label, detail) {
    results.push({ ok: Boolean(condition), label, detail: condition ? undefined : detail });
    console.log(`${condition ? "PASS" : "FAIL"}  [${config.phase}] ${label}${condition || detail === undefined ? "" : "  -> " + J(detail)}`);
}

async function until(code, label, timeout = 25000) {
    const start = Date.now();
    while (Date.now() - start < timeout) {
        if (await js(code).catch(() => false)) return true;
        await sleep(150);
    }
    expect(false, `timed out waiting: ${label}`, await js(`document.getElementById('loginError')?.textContent || ''`).catch(() => ""));
    return false;
}

// Runs an electronAPI call in the page and reports how it settled.
const ipc = expression => js(`(async () => {
    try { return { ok: true, value: await (${expression}) }; }
    catch (error) { return { ok: false, message: String(error && error.message || error) }; }
})()`);

const READY = `!!window.checkpointSubscription && document.getElementById('authScreen').classList.contains('hidden')
    && window.checkpointSubscription.getState().phase === 'ready'`;
const visible = id => js(`!document.getElementById(${J(id)}).classList.contains('hidden')`);
const features = () => js(`({ gui: window.checkpointSubscription.hasFeature('gui'), github: window.checkpointSubscription.hasFeature('github'),
    status: window.checkpointSubscription.getState().view?.status, badges: document.querySelectorAll('.pro-lock-badge').length })`);

async function waitForSignInForm() {
    await until(`!!document.getElementById('signInBtn') && !!window.checkpointSubscription`, "page loaded");
    await sleep(1500); // Firebase Auth initialises asynchronously and exposes no ready flag.
}

async function signIn(account) {
    await js(`(() => {
        document.getElementById('loginEmail').value = ${J(account.email)};
        document.getElementById('loginPassword').value = ${J(account.password)};
        document.getElementById('signInBtn').click(); return true; })()`);
    return until(READY, `signed in as ${account.label}`);
}

async function signOut() {
    await js(`document.getElementById('logoutBtn').click()`);
    return until(`!document.getElementById('authScreen').classList.contains('hidden')
        && window.checkpointSubscription.getState().phase === 'signed-out'`, "signed out");
}

async function register(account) {
    await js(`(() => {
        document.getElementById('showRegistrationBtn').click();
        document.getElementById('registrationName').value = ${J(account.name)};
        document.getElementById('registrationEmail').value = ${J(account.email)};
        document.getElementById('registrationPassword').value = ${J(account.password)};
        document.getElementById('registrationConfirmPassword').value = ${J(account.password)};
        document.getElementById('professionalRole').value = 'Student';
        document.getElementById('createAccountBtn').click(); return true; })()`);
    return until(READY, `registered ${account.label}`);
}

const PAID_CALLS = [
    ["github:get-branches", "window.electronAPI.getGithubBranches('')"],
    ["github:list-pull-requests", "window.electronAPI.listGithubPullRequests({})"],
    ["github:get-pull-request", "window.electronAPI.getGithubPullRequest({})"],
    ["github:get-pull-request-file", "window.electronAPI.getGithubPullRequestFile({})"],
    ["github:import", "window.electronAPI.importGithubRepository({ requestId: crypto.randomUUID() })"],
    ["start-local-gui-app", "window.electronAPI.startLocalGuiApp({ appDirectory: '' })"],
    ["start-gui-tests", "window.electronAPI.startGuiTests([], [])"],
    ["start-generated-gui-tests", "window.electronAPI.startGeneratedGuiTests({ testFiles: [] })"],
    ["repair-generated-gui-test", "window.electronAPI.repairGeneratedGuiTest({})"],
    ["diagnose-gui-event-stream", "window.electronAPI.diagnoseGuiEventStream('http://127.0.0.1:9/')"],
    ["list-gui-testing-reports", "window.electronAPI.listGuiTestingReports()"],
    ["get-gui-testing-report", "window.electronAPI.getGuiTestingReport('none')"],
    ["export-gui-testing-report-pdf", "window.electronAPI.exportGuiTestingReportPdf('none')"]
];
const PAID_COMMANDS = ["generate_gui_test_cases", "repair_gui_test_case", "analyze_pull_request"];

async function expectPaidOperationsRefused(context) {
    for (const [channel, call] of PAID_CALLS) {
        const outcome = await ipc(call);
        expect(!outcome.ok && /Checkpoint Pro required/.test(outcome.message), `${context}: ${channel} is refused by the main process`, outcome);
    }
    for (const command of PAID_COMMANDS) {
        const outcome = await ipc(`window.electronAPI.executeCommand(${J(command)}, { paid: true, plan: 'pro' })`);
        expect(outcome.ok && outcome.value?.code === "PAYMENT_REQUIRED" && outcome.value?.accepted === false,
            `${context}: backend command ${command} is refused before reaching Python`, outcome);
    }
}

// --- Phases ------------------------------------------------------------------
async function purchasePhase() {
    const buyer = config.accounts.buyer;
    await waitForSignInForm();
    await register(buyer);

    let f = await features();
    expect(f.status === "free" && !f.gui && !f.github, "a newly registered account starts free", f);
    expect(f.badges === 3, "all three GUI/GitHub entry points show a Pro badge", f.badges);

    // Routes and entry points
    await js(`document.getElementById('guiTestCasesBtn').click()`);
    expect(await visible("upgradePage") && !(await visible("guiTestPage")), "GUI Test Cases opens Upgrade instead of the GUI page");
    expect(/GUI testing is part of Checkpoint Pro/.test(await js(`document.getElementById('upgradeLockedMessage').textContent`)),
        "the Upgrade screen explains GUI testing is a Pro feature");
    for (const page of ["guiTestPage", "githubAnalysisModePage", "githubPullRequestExplorerPage", "githubCodeComparisonPage"]) {
        const shown = await js(`showPage(${J(page)})`);
        expect(shown === false && !(await visible(page)) && await visible("upgradePage"), `direct navigation to ${page} is refused`);
    }
    expect(/GitHub integration is part of Checkpoint Pro/.test(await js(`document.getElementById('upgradeLockedMessage').textContent`)),
        "the Upgrade screen explains GitHub integration is a Pro feature");
    await js(`showPage('codebasePipelinePage'); document.getElementById('connectGithubBtn').click()`);
    expect(!(await visible("githubConnectPanel")) && await visible("upgradePage"), "Connect Repository is locked");
    await js(`showPage('mainViewPage'); document.getElementById('viewGuiTestingReportsBtn').click()`);
    expect(!(await visible("viewGuiReportsBtns")) && await visible("upgradePage"), "Insights GUI Testing reports are locked");

    // Operations, including with a tampered client-side flag
    await expectPaidOperationsRefused("free account");
    await js(`window.__realHasFeature = window.checkpointSubscription.hasFeature; window.checkpointSubscription.hasFeature = () => true; true`);
    const tampered = await ipc("window.electronAPI.listGuiTestingReports()");
    expect(!tampered.ok && /Checkpoint Pro required/.test(tampered.message), "a client that claims to be paid is still refused", tampered);
    await js(`window.checkpointSubscription.hasFeature = window.__realHasFeature; delete window.__realHasFeature; true`);

    // Free workflows keep working
    for (const page of ["analysisPage", "mainViewPage", "apiPage", "faqAndSupportPage", "codebasePipelinePage", "homePage"]) {
        expect(await js(`showPage(${J(page)})`) === true && await visible(page), `free page ${page} still opens`);
    }
    const keys = await ipc("window.electronAPI.listApiKeys()");
    expect(keys.ok, "free IPC (API key list) still works", keys);
    const profile = await ipc("window.electronAPI.getUserProfile()");
    expect(profile.ok && profile.value?.professionalRole === "Student", "account profile is still saved and readable", profile);

    // Checkout
    await js(`document.getElementById('upgradeBtn').click()`);
    await until(`!document.getElementById('upgradeCheckout').classList.contains('hidden')`, "checkout shown");
    const pageText = await js(`document.getElementById('upgradePage').textContent`);
    expect(/\$19\.99\s*\/month/.test(pageText), "Upgrade shows $19.99/month");
    expect(/Test mode — no charge/.test(pageText), "checkout is labelled Test mode — no charge");
    await js(`document.getElementById('upgradeDummyCardBtn').click()`);
    expect(await js(`document.getElementById('upgradeCardNumber').value`) === "4242 4242 4242 4242", "Use dummy card fills a dummy number");
    await js(`(() => { document.getElementById('upgradeCardNumber').value = ${J(CARD)}; return true; })()`);
    const callsBefore = captures.mainRequests.filter(r => r.url.endsWith("/activateTestSubscription")).length;
    await js(`(() => { const b = document.getElementById('upgradeConfirmBtn'); b.click(); b.click(); return true; })()`);
    await until(`window.checkpointSubscription.hasFeature('gui') && window.checkpointSubscription.hasFeature('github')`, "Pro unlocked");
    const activationCalls = captures.mainRequests.filter(r => r.url.endsWith("/activateTestSubscription")).length - callsBefore;
    expect(activationCalls === 1, "a double-click sends one checkout request", activationCalls);
    expect(await js(`document.getElementById('upgradeCardNumber').value`) === "", "the card field is cleared after checkout");
    expect(/You were not charged/.test(await js(`document.getElementById('upgradeSuccessMessage').textContent`)), "success message says no charge");

    f = await features();
    expect(f.gui && f.github && f.badges === 0, "checkout unlocks both feature groups without signing out", f);
    expect(await js(`showPage('guiTestPage')`) === true && await visible("guiTestPage"), "the GUI page opens after checkout");
    const reports = await ipc("window.electronAPI.listGuiTestingReports()");
    expect(reports.ok && reports.value?.success === true, "GUI operations run after checkout", reports);
    const branches = await ipc("window.electronAPI.getGithubBranches('')");
    expect(!branches.ok && !/Checkpoint Pro required/.test(branches.message), "GitHub operations pass the gate after checkout", branches);
    const command = await ipc("window.electronAPI.executeCommand('analyze_pull_request', { pull_request: {}, files: [] })");
    expect(command.ok && command.value?.accepted === true, "pull-request analysis is accepted after checkout", command);
    await js(`showPage('homePage')`);
}

async function restartPhase() {
    const { buyer, existing } = config.accounts;
    await until(READY, "session restored after restart", 30000);
    let f = await features();
    expect(f.gui && f.github, "Pro access persists after restarting the app", f);

    await signOut();
    f = await features();
    expect(!f.gui && !f.github && f.badges === 3, "signing out clears cached access in the page", f);

    await signIn(existing);
    f = await features();
    expect(f.status === "free" && !f.gui && !f.github, "an existing account without subscription data is free", f);
    await expectPaidOperationsRefused("after switching accounts");

    await signOut();
    await signIn(buyer);
    f = await features();
    expect(f.gui && f.github, "the buyer still has Pro after switching back", f);

    await js(`showPage('guiTestPage')`);
    await E.expireSubscription(config.accounts.buyerUid);
    await js(`window.checkpointSubscription.load({ force: true })`);
    await until(`window.checkpointSubscription.getState().phase === 'ready'`, "refreshed after expiry");
    f = await features();
    expect(f.status === "expired" && !f.gui && !f.github, "expiration locks both feature groups", f);
    expect(await visible("upgradePage") && !(await visible("guiTestPage")), "an open GUI page is left when the subscription expires");
    expect(/expired on/.test(await js(`document.getElementById('upgradePlanDetail').textContent`)), "Upgrade says the subscription expired");
    await expectPaidOperationsRefused("expired subscription");
}

async function disabledPhase() {
    await waitForSignInForm();
    await signIn(config.accounts.existing);
    await js(`document.getElementById('upgradeBtn').click()`);
    await until(`window.checkpointSubscription.getState().phase === 'ready'`, "subscription loaded");
    await sleep(300);
    expect(!(await visible("upgradeCheckout")), "the mock checkout is hidden while server test mode is off");
    expect(/test mode is turned off/.test(await js(`document.getElementById('upgradePlanDetail').textContent`)), "Upgrade explains checkout is unavailable");
    const direct = await ipc("window.electronAPI.activateTestSubscription()");
    expect(!direct.ok && /disabled/i.test(direct.message), "a direct checkout call fails while test mode is off", direct);
    const f = await features();
    expect(!f.gui && !f.github, "the account stays free", f);
    await expectPaidOperationsRefused("test mode off");
}

const PHASES = { purchase: purchasePhase, restart: restartPhase, disabled: disabledPhase };

async function main() {
    const timeout = setTimeout(() => finish(new Error("phase timed out")), 180000);
    try {
        while (!win) await sleep(100);
        await new Promise(resolve => (win.webContents.isLoading() ? win.webContents.once("did-finish-load", resolve) : resolve()));
        await PHASES[config.phase]();
        clearTimeout(timeout);
        finish();
    } catch (error) {
        clearTimeout(timeout);
        finish(error);
    }
}

let finished = false;
function finish(error) {
    if (finished) return;
    finished = true;
    if (error) expect(false, `phase error: ${error.message}`);
    fs.writeFileSync(config.resultPath, JSON.stringify({ phase: config.phase, results, captures }, null, 2));
    setTimeout(() => app.exit(results.every(r => r.ok) ? 0 : 1), 5000).unref();
    app.quit();
}

app.whenReady().then(main);
