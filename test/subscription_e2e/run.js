/*
 * run.js — end-to-end verification of the test-only Checkpoint Pro subscription.
 *
 * Needs the Firebase emulators running from the repo root:
 *   firebase emulators:start --only auth,firestore,functions
 *
 * Then:
 *   node test/subscription_e2e/run.js [out_dir]
 *
 * It runs against the emulators only (127.0.0.1), and it RESETS their data
 * first. What it checks depends on the server's test-mode flag:
 *   - test mode ON  (functions/.env.local has CHECKPOINT_TEST_PAYMENTS=true):
 *     backend + rules checks, then two app launches ("purchase", "restart")
 *   - test mode OFF (no .env.local): backend checks + one "disabled" launch
 * Run it once in each mode for the full set.
 *
 * Afterwards it scans every request, console line, file under the app's
 * user-data folder, the Firestore data and the emulator logs for the dummy
 * card number typed during checkout.
 */
"use strict";

const childProcess = require("node:child_process");
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const E = require("./emulator");
const { runBackendChecks } = require("./check_backend");

const outDir = path.resolve(process.argv[2] || path.join(__dirname, "runs", new Date().toISOString().replace(/[:.]/g, "-")));
const results = [];

function expect(condition, label, detail) {
    results.push({ ok: Boolean(condition), label, detail: condition ? undefined : detail });
    console.log(`${condition ? "PASS" : "FAIL"}  ${label}${condition || detail === undefined ? "" : "  -> " + JSON.stringify(detail)}`);
}

function writeLocalProfile(userData, account) {
    const directory = path.join(userData, "profiles");
    fs.mkdirSync(directory, { recursive: true });
    fs.writeFileSync(path.join(directory, `${account.uid}.json`), JSON.stringify({
        uid: account.uid,
        fullName: account.name,
        email: account.email,
        professionalRole: "Software Developer",
        accountCreatedAt: new Date(Date.now() - 30 * 86400000).toISOString()
    }, null, 2));
}

function runPhase(phase, userData, accounts, card) {
    const configPath = path.join(outDir, `${phase}-config.json`);
    const resultPath = path.join(outDir, `${phase}-result.json`);
    fs.writeFileSync(configPath, JSON.stringify({ phase, userData, resultPath, accounts }, null, 2));
    const electron = require("electron");
    const env = { ...process.env, SUB_E2E_CONFIG: configPath, SUB_E2E_CARD: card };
    delete env.ELECTRON_RUN_AS_NODE;
    console.log(`\n--- app launch: ${phase} ---`);
    const child = childProcess.spawnSync(electron, [path.join(__dirname, "app_phase.js")], {
        cwd: E.REPO, env, encoding: "utf8", timeout: 240000, maxBuffer: 64 * 1024 * 1024
    });
    const output = `${child.stdout || ""}\n${child.stderr || ""}`;
    fs.writeFileSync(path.join(outDir, `${phase}-output.log`), output);
    output.split("\n").filter(line => /^(PASS|FAIL)\s/.test(line)).forEach(line => console.log(line));
    if (!fs.existsSync(resultPath)) {
        expect(false, `app launch "${phase}" produced results`, child.error?.message || `exit ${child.status}`);
        return { results: [], captures: { mainRequests: [], rendererRequests: [], consoleLines: [] }, output };
    }
    const phaseResult = JSON.parse(fs.readFileSync(resultPath, "utf8"));
    results.push(...phaseResult.results.map(result => ({ ...result, label: `[${phase}] ${result.label}` })));
    return { ...phaseResult, output };
}

function cardForms(card) {
    const digits = card.replace(/\D/g, "");
    const forms = new Set([digits, card, digits.replace(/(\d{4})(?=\d)/g, "$1 "), digits.replace(/(\d{4})(?=\d)/g, "$1-")]);
    return [...forms].flatMap(form => [Buffer.from(form, "utf8"), Buffer.from(form, "utf16le")]);
}

function containsCard(buffer, forms) {
    return forms.some(form => buffer.includes(form));
}

function scanFiles(root, forms, skip = () => false) {
    const hits = [];
    if (!fs.existsSync(root)) return hits;
    const stack = [root];
    while (stack.length) {
        const current = stack.pop();
        for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
            const full = path.join(current, entry.name);
            if (entry.isDirectory()) stack.push(full);
            else if (!skip(full)) {
                try {
                    if (fs.statSync(full).size < 64 * 1024 * 1024 && containsCard(fs.readFileSync(full), forms)) hits.push(path.relative(root, full));
                } catch { /* locked or vanished file */ }
            }
        }
    }
    return hits;
}

async function main() {
    if (!(await E.emulatorsRunning())) {
        console.error("The Firebase emulators are not running. From the repo root run:\n  firebase emulators:start --only auth,firestore,functions");
        process.exit(2);
    }
    fs.mkdirSync(outDir, { recursive: true });
    await E.resetEmulators();

    const probe = await E.signUp(E.newAccount("probe"));
    const testMode = (await E.callAs(probe, "getEntitlement")).testCheckoutAvailable === true;
    console.log(`Server test mode: ${testMode ? "ON" : "OFF"}\n`);

    // A random dummy number, so a match can only come from this run's checkout.
    const card = `9${crypto.randomInt(10 ** 7, 10 ** 8 - 1)}${crypto.randomInt(10 ** 6, 10 ** 7 - 1)}`;
    const forms = cardForms(card);

    console.log("--- backend and Firestore rules ---");
    // This deliberately sends card-like data to prove the server rejects it, so
    // it uses a different number from the one the leak scan looks for.
    await runBackendChecks({ testMode, cardMarker: "4000 0000 0000 0002", expect });

    const userData = path.join(outDir, "userData");
    fs.rmSync(userData, { recursive: true, force: true });
    fs.mkdirSync(userData, { recursive: true });

    const existing = { ...(await E.signUp(E.newAccount("existing"))), label: "existing account" };
    writeLocalProfile(userData, existing);
    await E.patchUserDoc(existing.uid, { fullName: E.stringValue(existing.name) }, { Authorization: `Bearer ${existing.idToken}` });
    const strip = account => ({ label: account.label, name: account.name, email: account.email, password: account.password });

    const phases = [];
    if (testMode) {
        const buyer = { ...E.newAccount("buyer"), label: "new buyer" };
        phases.push(runPhase("purchase", userData, { buyer: strip(buyer), existing: strip(existing) }, card));
        const buyerUid = (await E.signIn(buyer)).uid;
        const stored = await E.getUserDoc(buyerUid);
        expect(Boolean(stored.json?.fields?.subscription), "firestore: the app's checkout created the buyer's subscription", stored.status);
        phases.push(runPhase("restart", userData, { buyer: strip(buyer), buyerUid, existing: strip(existing) }, card));
    } else {
        phases.push(runPhase("disabled", userData, { existing: strip(existing) }, card));
    }

    // --- Card number leak scan --------------------------------------------------
    console.log("\n--- dummy card leak scan ---");
    for (const phase of phases) {
        const name = phase.phase || "app";
        const leakedRequests = [...phase.captures.mainRequests, ...phase.captures.rendererRequests]
            .filter(request => containsCard(Buffer.from(`${request.url}\n${request.body}`), forms));
        expect(leakedRequests.length === 0, `[${name}] the card number is in none of ${phase.captures.mainRequests.length + phase.captures.rendererRequests.length} recorded requests`,
            leakedRequests.map(request => request.url));
        const checkoutRequests = phase.captures.mainRequests.filter(request => request.url.endsWith("/activateTestSubscription"));
        expect(checkoutRequests.every(request => request.body === JSON.stringify({ data: null })), `[${name}] every checkout request body is {"data":null}`,
            checkoutRequests.map(request => request.body));
        const leakedLines = phase.captures.consoleLines.filter(line => containsCard(Buffer.from(line), forms));
        expect(leakedLines.length === 0, `[${name}] the card number is in none of ${phase.captures.consoleLines.length} console lines`, leakedLines.length);
        expect(!containsCard(Buffer.from(phase.output), forms), `[${name}] the card number is not in the app's stdout/stderr`);
    }
    const userDataHits = scanFiles(userData, forms);
    expect(userDataHits.length === 0, "the card number is in no file the app stored (profiles, session, Local Storage, IndexedDB, caches)", userDataHits);
    const firestoreDump = await E.listUserDocs();
    fs.writeFileSync(path.join(outDir, "firestore-users.json"), firestoreDump.text);
    expect(!containsCard(Buffer.from(firestoreDump.text), forms), `the card number is not in Firestore (${firestoreDump.json?.documents?.length ?? 0} user records)`);
    const logHits = ["firebase-debug.log", "firestore-debug.log", "ui-debug.log"]
        .map(name => path.join(E.REPO, name))
        .filter(file => fs.existsSync(file) && containsCard(fs.readFileSync(file), forms));
    expect(logHits.length === 0, "the card number is not in the emulator logs", logHits);

    // --- Report -----------------------------------------------------------------
    const failed = results.filter(result => !result.ok);
    const report = {
        ranAt: new Date().toISOString(),
        serverTestMode: testMode ? "on" : "off",
        passed: results.length - failed.length,
        failed: failed.length,
        results
    };
    fs.writeFileSync(path.join(outDir, "report.json"), JSON.stringify(report, null, 2));
    console.log(`\n${report.passed}/${results.length} checks passed (test mode ${report.serverTestMode}). Report: ${path.join(outDir, "report.json")}`);
    process.exit(failed.length ? 1 : 0);
}

main().catch(error => {
    console.error(error);
    process.exit(1);
});
