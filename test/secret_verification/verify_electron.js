/*
 * verify_electron.js
 *
 * T-079 (US-034): drives the real application (main.js, preload.js,
 * index.html, renderer.js) through a full pipeline run on the ten-secret
 * fixture, with the backend started offline (offline_backend.py: the real
 * main.py, the offline model), and checks what the window was sent and shows.
 *
 *   node_modules\.bin\electron test\secret_verification\verify_electron.js <manifest.json> <out_dir>
 *
 * run_electron_check.py writes the manifest and runs this. Only the
 * backend's start-up is changed. Every message main.js sends the renderer is
 * recorded, the Complete screen and the Consistency Review screen are read
 * from the DOM, and all of it is checked for the planted values. The report
 * holds counts and labels only; screenshots go next to it. main.js clears
 * the repo's error_log.json when it starts; the developer's copy is put back
 * on exit.
 */

const path = require("path");
const fs = require("fs");
const childProcess = require("child_process");
const { app, BrowserWindow } = require("electron");

const REPO = path.resolve(__dirname, "..", "..");
const [manifestPath, outDir] = process.argv.slice(-2);
const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
fs.mkdirSync(outDir, { recursive: true });

// --- The planted values (raw, from the workspace; never written out) --------
const needles = JSON.parse(fs.readFileSync(manifest.needles, "utf8"));
const forms = needles.flatMap(n => [n.value, ...n.fragments].flatMap(v => [
    [n.case_id, v],
    [n.case_id, JSON.stringify(v).slice(1, -1)],
]));
const leaked = text => [...new Set(forms.filter(([, v]) => text.includes(v)).map(([id]) => id))].sort();

// --- Start the backend offline ----------------------------------------------
const realSpawn = childProcess.spawn;
const children = [];
childProcess.spawn = (command, args, options) => {
    if (Array.isArray(args) && args.some(a => String(a).endsWith("main.py"))) {
        command = manifest.python;
        args = ["-u", manifest.backend, "--app", manifest.app, "--capture", manifest.capture,
                "--codebase", manifest.codebase, "--seed", String(manifest.seed)];
        options = { ...options, cwd: manifest.app, env: manifest.env };
    }
    const child = realSpawn(command, args, options);
    children.push(child);
    return child;
};

const errorLog = path.join(REPO, "error_log.json");
const savedErrorLog = fs.existsSync(errorLog) ? fs.readFileSync(errorLog) : null;
process.on("exit", () => {
    children.forEach(child => { try { child.kill(); } catch (_) { /* already gone */ } });
    if (savedErrorLog !== null) fs.writeFileSync(errorLog, savedErrorLog);
});

const realLoadFile = BrowserWindow.prototype.loadFile;
BrowserWindow.prototype.loadFile = function (file, options) {
    return realLoadFile.call(this, path.isAbsolute(file) ? file : path.join(REPO, file), options);
};

require(path.join(REPO, "main.js"));

// --- Helpers ----------------------------------------------------------------
const results = [];
let win;

function expect(condition, label, detail) {
    results.push({ ok: Boolean(condition), label, detail: condition ? undefined : detail });
    console.log(`${condition ? "PASS" : "FAIL"}  ${label}${condition || detail === undefined ? "" : "  -> " + JSON.stringify(detail)}`);
}

const js = code => win.webContents.executeJavaScript(code, true);
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

async function until(code, label, timeout) {
    const start = Date.now();
    while (Date.now() - start < timeout) {
        if (await js(code)) return true;
        await sleep(250);
    }
    expect(false, `timed out waiting: ${label}`);
    return false;
}

async function shot(name) {
    await sleep(300);
    const image = await win.webContents.capturePage();
    fs.writeFileSync(path.join(outDir, `${name}.png`), image.toPNG());
}

// --- The run ------------------------------------------------------------------
app.whenReady().then(async () => {
    const sent = [];
    const facts = {};
    try {
        const start = Date.now();
        while (!(win = BrowserWindow.getAllWindows()[0]) && Date.now() - start < 20000) await sleep(100);
        if (win.webContents.isLoading()) await new Promise(resolve => win.webContents.once("did-finish-load", resolve));
        win.setSize(1440, 960);

        // Everything main.js hands the renderer, as it hands it.
        const realSend = win.webContents.send.bind(win.webContents);
        win.webContents.send = (channel, ...args) => {
            sent.push({ channel, text: JSON.stringify(args) });
            return realSend(channel, ...args);
        };

        // The Run Operations button, as a user would press it.
        await js(`(() => { selectedCodebasePath = ${JSON.stringify(manifest.codebase)};
            document.getElementById('codebaseAnalysisPipelineBtn').click(); return true; })()`);
        const done = await until(`(() => activeRequestId === null && !pipelineStarting
            && /complete/i.test(document.getElementById('loadingTitle').textContent))()`, "the pipeline to finish", 900000);
        expect(done, "the full pipeline ran from the Run Operations button");

        const overlay = await js(`document.getElementById('loadingOverlay').textContent`);
        await shot("complete_screen");
        facts.complete_chars = overlay.length;
        expect(/Full pipeline completed/.test(overlay), "the Complete screen says the pipeline completed");
        expect(leaked(overlay).length === 0, "the Complete screen shows no planted value", leaked(overlay));

        // The redaction count (US-034): shown, labelled, and the number the
        // backend sent for this very run.
        const count = await js(`(() => ({
            hidden: document.getElementById('redactionSummary').classList.contains('hidden'),
            value: document.getElementById('redactionSummaryValue').textContent,
            label: document.getElementById('redactionSummaryLabel').textContent,
            note: document.getElementById('redactionSummaryNote').textContent }))()`);
        const pipelineResponse = sent
            .filter(m => m.channel === "backend-response")
            .map(m => JSON.parse(m.text)[0])
            .find(r => r && r.command === "full_pipeline" && r.success && r.result);
        const redactions = pipelineResponse && pipelineResponse.result.redactions;
        facts.redaction_count_shown = count.value;
        facts.redaction_run_id = redactions ? redactions.run_id : null;
        expect(!count.hidden && count.label === "Secret occurrences redacted from AI requests",
            "the Complete screen shows the redaction count with its label", count);
        expect(redactions && String(redactions.total) === count.value && Number(count.value) > 0,
            "the count shown is the one the backend sent for this run", { shown: count.value });
        expect(/counts each time/.test(count.note), "the count says repeated occurrences count separately");

        // The run report the Complete screen's Open Report button opens:
        // the same count, for the same run.
        const reportPath = pipelineResponse && pipelineResponse.result.run_report;
        if (reportPath) {
            const page = new BrowserWindow({ show: false, width: 1440, height: 960 });
            await page.loadFile(reportPath);
            await sleep(800);
            await page.webContents.executeJavaScript(`(() => { const card = document.getElementById('b-secrets');
                if (card) card.scrollIntoView({ block: 'center' }); return true; })()`, true);
            await sleep(300);
            fs.writeFileSync(path.join(outDir, "run_report_business.png"), (await page.webContents.capturePage()).toPNG());
            // The page opens on the business view; the developer view is
            // built when its button is pressed, as a reader would.
            const shown = await page.webContents.executeJavaScript(`(async () => {
                const tiles = sel => [...document.querySelectorAll(sel + ' .tile')]
                    .find(t => t.textContent.includes('Secret occurrences redacted from AI requests'));
                const business = tiles('#tab-b-overview');
                const card = (document.getElementById('b-secrets') || {}).textContent || '';
                document.querySelector('.view-switch button[data-view="developer"]').click();
                await new Promise(resolve => setTimeout(resolve, 500));
                const developer = tiles('#tab-overview');
                const facts = document.querySelector('#run .facts');
                return { tile: business ? business.textContent : null, developer: developer ? developer.textContent : null,
                         card, facts: facts ? facts.textContent : '', text: document.body.textContent }; })()`, true);
            fs.writeFileSync(path.join(outDir, "run_report.png"), (await page.webContents.capturePage()).toPNG());
            page.destroy();
            facts.report_tile = shown.tile ? shown.tile.replace(/\s+/g, " ").trim() : null;
            expect(shown.tile && redactions && shown.tile.includes(String(redactions.total)),
                "the run report's business view shows the same count", facts.report_tile);
            expect(shown.developer && redactions && shown.developer.includes(String(redactions.total)),
                "the run report's developer view shows the same count");
            expect(/counts each time/.test(shown.card), "the business view explains how it is counted");
            expect(redactions && shown.facts.includes(redactions.run_id),
                "the run report is about the same run");
            expect(/counts each time/.test(shown.facts), "the run report explains how it is counted");
            expect(leaked(shown.text).length === 0, "the run report shows no planted value", leaked(shown.text));
        } else {
            expect(false, "the pipeline produced a run report");
        }

        // Consistency Review, opened as from View Insights.
        await js(`(() => { document.getElementById('loadingOverlay').classList.add('hidden');
            showPage('mainViewPage'); document.getElementById('viewConsistencyBtn').click(); return true; })()`);
        await until(`(() => { const s = document.querySelector('#viewDisplayOutputBox .cr-screen');
            return !!s && s.dataset.state !== 'loading'; })()`, "the review screen", 30000);
        await js(`(() => { document.querySelectorAll('.cr-details').forEach(d => d.open = true); return true; })()`);
        await sleep(500);
        const review = await js(`(() => { const s = document.querySelector('#viewDisplayOutputBox .cr-screen');
            return { state: s.dataset.state, text: s.textContent,
                     findings: [...s.querySelectorAll('.cr-finding-title')].map(n => n.textContent) }; })()`);
        await shot("consistency_review");
        facts.review_state = review.state;
        facts.review_findings = review.findings.length;
        expect(review.findings.length >= 1, "the review screen lists the run's findings", review.findings.length);
        expect(/\[REDACTED_[A-Z_]+_\d+\]/.test(review.text), "the review screen shows placeholders where secrets were");
        expect(leaked(review.text).length === 0, "the review screen shows no planted value", leaked(review.text));

        facts.messages = sent.length;
        const backendMessages = sent.filter(m => m.channel === "backend-response");
        facts.backend_messages = backendMessages.length;
        expect(backendMessages.length > 50, "the window was sent the backend's progress and responses", backendMessages.length);
        const inMessages = leaked(sent.map(m => m.text).join("\n"));
        expect(inMessages.length === 0, "no message sent to the window holds a planted value", inMessages);
    } catch (error) {
        expect(false, "verification crashed", String(error && error.message || error));
    }

    const failed = results.filter(r => !r.ok);
    fs.writeFileSync(path.join(outDir, "electron_report.json"),
        JSON.stringify({ results, failed: failed.length, facts }, null, 2));
    console.log(`\n${results.length - failed.length} passed, ${failed.length} failed.`);
    process.exitCode = failed.length ? 1 : 0;
    app.quit();
});
