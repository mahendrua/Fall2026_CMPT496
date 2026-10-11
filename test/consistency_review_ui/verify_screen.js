/*
 * verify_screen.js
 *
 * Drives the real application (main.js, preload.js, index.html, renderer.js
 * and the Python backend) against isolated saved results and checks the
 * Consistency Review screen. Screenshots and a report go to <out_dir>.
 *
 *   .venv\Scripts\python.exe test\consistency_review_ui\make_demo.py <demo_dir>
 *   node_modules\.bin\electron test\consistency_review_ui\verify_screen.js <demo_dir>\manifest.json <out_dir>
 *
 * Only the backend's start-up is changed: main.py is started through
 * backend_shim.py, which points APP_DIR at the demo folder and makes model
 * creation fail (leaving a MODEL_REQUESTED file). main.js clears the repo's
 * error_log.json when it starts; the developer's copy is put back on exit.
 */
const path = require("path");
const fs = require("fs");
const childProcess = require("child_process");
const { app, BrowserWindow } = require("electron");

const REPO = path.resolve(__dirname, "..", "..");
const [manifestPath, outDir] = process.argv.slice(2);
if (!manifestPath || !outDir) {
    console.error("Usage: electron verify_screen.js <manifest.json> <out_dir>");
    process.exit(2);
}
const demo = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
fs.mkdirSync(outDir, { recursive: true });

// --- Start the backend through the shim -------------------------------------
const realSpawn = childProcess.spawn;
const children = [];
childProcess.spawn = (command, args, options) => {
    if (Array.isArray(args) && args.some(a => String(a).endsWith("main.py"))) {
        args = ["-u", path.join(__dirname, "backend_shim.py"), demo.app];
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

// main.js loads "index.html" relative to the app path, which is this folder
// when main.js is not the entry point; resolve it against the repo instead.
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

async function until(code, label, timeout = 20000) {
    const start = Date.now();
    while (Date.now() - start < timeout) {
        if (await js(code)) return true;
        await sleep(100);
    }
    expect(false, `timed out waiting: ${label}`);
    return false;
}

const LOADED = `(() => { const s = document.querySelector('#viewDisplayOutputBox .cr-screen');
    return !!s && s.dataset.state !== 'loading'; })()`;

async function openScreen(codebase) {
    await js(`(() => { selectedCodebasePath = ${JSON.stringify(codebase || "")}; showPage('mainViewPage');
        document.getElementById('viewConsistencyBtn').click(); return true; })()`);
    await sleep(150);
    await until(LOADED, `screen for ${codebase}`);
}

async function chooseScope(label) {
    await js(`[...document.querySelectorAll('.cr-scope')].find(b => b.textContent.startsWith(${JSON.stringify(label)})).click()`);
    await sleep(150);
    await until(LOADED, `scope ${label}`);
}

async function facts() {
    return js(`(() => {
        const s = document.querySelector('#viewDisplayOutputBox .cr-screen');
        const text = sel => [...s.querySelectorAll(sel)].map(n => n.textContent);
        return {
            state: s.dataset.state,
            banner: (s.querySelector('.cr-banner-title') || {}).textContent,
            bannerText: (s.querySelector('.cr-banner') || {}).textContent,
            counts: Object.fromEntries([...s.querySelectorAll('.cr-count')].map(c =>
                [c.querySelector('.cr-count-label').textContent, Number(c.querySelector('.cr-count-value').textContent)])),
            fields: Object.fromEntries([...s.querySelectorAll('.cr-field')].map(f =>
                [f.querySelector('dt').textContent, f.querySelector('dd').textContent])),
            rows: text('.cr-finding-title'),
            rowIds: [...s.querySelectorAll('.cr-finding')].map(r => r.dataset.id),
            details: text('.cr-details > summary'),
            openDetails: [...s.querySelectorAll('.cr-details')].filter(d => d.open).map(d => d.querySelector('summary').textContent),
            notes: text('.cr-note'),
            all: s.textContent,
        };
    })()`);
}

async function shot(name) {
    await sleep(200);
    const image = await win.webContents.capturePage();
    fs.writeFileSync(path.join(outDir, `${name}.png`), image.toPNG());
}

// --- Scenarios --------------------------------------------------------------
const cb = name => demo.codebases[demo.states[name]];

async function screenStates() {
    await openScreen("");
    let f = await js(`document.querySelector('#viewDisplayOutputBox').textContent`);
    expect(f.includes("Select a codebase first"), "no codebase selected: asks for one");

    await openScreen(cb("no_check"));
    f = await facts();
    expect(f.state === "no_check", "no saved check: no_check state", f.state);
    expect(f.rows.length === 0 && !/No mismatches/.test(f.all), "no saved check: no findings and no clean claim");
    await shot("01-no-check");

    await openScreen(cb("clean"));
    f = await facts();
    expect(f.state === "clean", "completed, no findings: clean state", f.state);
    expect(f.banner === "No mismatches found in assessed artifacts", "clean headline is about assessed artifacts", f.banner);
    expect(/does not show that the code is correct/.test(f.bannerText), "clean state disclaims correctness");
    expect(!/verified|code is correct\b(?! or)/i.test(f.banner), "clean headline makes no correctness claim", f.banner);
    expect(f.fields.Result === "Selected files", "opened the scope that has a result", f.fields.Result);
    expect(f.counts["Generated artifacts compared"] === 3, "clean state shows what was compared", f.counts);
    await shot("02-clean");

    await openScreen(cb("findings"));
    f = await facts();
    expect(f.state === "findings" && f.fields.Result === "Whole project", "findings: whole-project result shown first", [f.state, f.fields.Result]);
    expect(/earlier run \(demo-run-1\).*demo-run-2; this result is not from it/.test(f.fields.Run), "result from an earlier run is labelled", f.fields.Run);
    expect(f.rows.length === 2 && f.counts.Findings === 2 && f.counts["Unresolved reviews"] === 2, "findings listed with counts", f.counts);
    const fullIds = f.rowIds;
    await shot("03-findings-full");

    await chooseScope("Selected files");
    f = await facts();
    expect(f.fields.Result === "Selected files" && f.fields.Selection === "src/Refunds.cs", "selected scope shows its selection", f.fields);
    expect(f.fields.Check !== undefined, "check identity shown", f.fields);
    const selectedCheck = f.fields.Check;
    await chooseScope("Whole project");
    f = await facts();
    expect(f.fields.Check !== selectedCheck, "full and selected results are different checks", [f.fields.Check, selectedCheck]);
    expect(JSON.stringify(f.rowIds) === JSON.stringify(fullIds), "same finding IDs for the same content in either scope");

    await js(`(() => { const s = [...document.querySelectorAll('.cr-filter')].find(l => l.textContent.startsWith('Category')).querySelector('select');
        s.value = 'source_test'; s.dispatchEvent(new Event('change')); return true; })()`);
    f = await facts();
    expect(f.rows.length === 1, "category filter narrows the list", f.rows);
    await js(`(() => { const s = document.querySelector('.cr-search'); s.value = 'zzz-no-match'; s.dispatchEvent(new Event('input')); return true; })()`);
    f = await facts();
    expect(f.rows.length === 0 && /No finding matches these filters/.test(f.all), "search with no match says so");

    await openScreen(cb("partial_clean"));
    f = await facts();
    expect(f.state === "partial" && /^Incomplete check: no mismatches found in what was assessed/.test(f.banner), "partial without findings is not clean", f.banner);
    expect(f.openDetails.some(t => t.startsWith("Missing or unresolved artifacts")), "partial: missing artifacts listed and open", f.openDetails);
    await shot("04-partial-no-findings");

    await openScreen(cb("partial_findings"));
    f = await facts();
    expect(f.state === "partial" && f.rows.length === 2, "partial with findings lists them", [f.state, f.rows.length]);
    expect(f.openDetails.some(t => t.startsWith("Errors")) && /did not assess RULE_1/.test(f.all), "partial: errors visible", f.openDetails);
    await shot("05-partial-findings");

    await openScreen(cb("failed"));
    f = await facts();
    expect(f.state === "failed" && f.rows.length === 0, "failed check: failed state", f.state);
    expect(/No conclusion can be drawn/.test(f.bannerText) && f.openDetails.some(t => t.startsWith("Errors")), "failed check explains itself");
    await shot("06-failed");

    await openScreen(cb("unreadable"));
    f = await facts();
    expect(f.state === "unreadable" && /could not be read/.test(f.banner), "corrupt result: unreadable state", f.banner);
    await shot("07-unreadable");

    await openScreen(cb("older"));
    f = await facts();
    expect(f.notes.some(n => /earlier version and does not record: undetermined, assessments, skipped/.test(n)), "older result: missing fields named", f.notes);

    await openScreen(cb("skipped"));
    f = await facts();
    expect(f.counts.Skipped === 1, "skipped folder-level rule counted", f.counts);

    // Switching projects shows only that project's result.
    await openScreen(cb("findings"));
    const before = await facts();
    await openScreen(cb("clean"));
    const after = await facts();
    expect(after.rows.length === 0 && !after.rowIds.some(id => before.rowIds.includes(id)), "switching project does not carry findings over");
    expect(/misleading_name_accurate/.test(after.all) && !/ignored_parameter/.test(after.all), "switching project changes the codebase shown");
}

// --- Run --------------------------------------------------------------------
app.whenReady().then(async () => {
    try {
        const start = Date.now();
        while (!(win = BrowserWindow.getAllWindows()[0]) && Date.now() - start < 20000) await sleep(100);
        if (win.webContents.isLoading()) await new Promise(resolve => win.webContents.once("did-finish-load", resolve));
        win.setSize(1440, 960);

        await screenStates();
        if (typeof module.exports.extra === "function") await module.exports.extra({ js, until, openScreen, chooseScope, facts, shot, expect, cb, demo, sleep });

        expect(!fs.existsSync(path.join(demo.app, "MODEL_REQUESTED")), "no model was requested");
    } catch (error) {
        expect(false, "verification crashed", String(error && error.stack || error));
    }

    const failed = results.filter(r => !r.ok);
    fs.writeFileSync(path.join(outDir, "report.json"), JSON.stringify({ results, failed: failed.length }, null, 2));
    console.log(`\n${results.length - failed.length} passed, ${failed.length} failed. Screenshots and report.json in ${outDir}`);
    process.exitCode = failed.length ? 1 : 0;
    app.quit();
});

module.exports = {};
