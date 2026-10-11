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
 * With --interactive instead of <out_dir>, nothing is checked: the app opens on
 * the demo results (optionally a named state, e.g. --interactive injection)
 * and stays open for you to use. Close the window to stop it.
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
const [manifestPath, outArg, interactiveState] = process.argv.slice(2);
const interactive = outArg === "--interactive";
const outDir = interactive ? null : outArg;
if (!manifestPath || !outArg) {
    console.error("Usage: electron verify_screen.js <manifest.json> (<out_dir> | --interactive [state])");
    process.exit(2);
}
const demo = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
if (outDir) fs.mkdirSync(outDir, { recursive: true });

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

// Consistency checks started from the screen get the offline oracle
// (backend_shim.py), which logs every model created and call made.
process.env.CR_SHIM_MODELS = "oracle";
const modelLog = path.join(demo.app, "MODEL_LOG.jsonl");
const modelEvents = () => (fs.existsSync(modelLog)
    ? fs.readFileSync(modelLog, "utf8").trim().split("\n").filter(Boolean).map(line => JSON.parse(line))
    : []);

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

// --- Evidence, verdicts and review decisions ----------------------------------
const DETAIL = `document.querySelector('#viewDisplayOutputBox .cr-detail')`;

async function detailFacts() {
    return js(`(() => {
        const d = ${DETAIL};
        if (!d) return null;
        const t = sel => [...d.querySelectorAll(sel)].map(n => n.textContent);
        return {
            id: d.dataset.finding,
            text: d.textContent,
            sections: t('.cr-section-title'),
            chips: t('.cr-chip'),
            claims: t('.cr-claim'),
            evidence: t('.cr-evidence'),
            gutters: t('.cr-evidence .cr-gutter'),
            current: t('.cr-current'),
            checked: (d.querySelector('.cr-radio input:checked') || {}).value,
            note: (d.querySelector('.cr-note-input') || {}).value,
            saveDisabled: (d.querySelector('.cr-save') || {}).disabled,
            dirty: (d.querySelector('.cr-dirty') || {}).textContent,
            feedback: (d.querySelector('.cr-feedback') || {}).textContent,
        };
    })()`);
}

async function artifactsChecked() {
    await until(`!${DETAIL}.textContent.includes('Checking current content')`, "current content states");
}

async function clickTab(label) {
    await js(`[...document.querySelectorAll('.cr-tab')].find(b => b.textContent.startsWith(${JSON.stringify(label)})).click()`);
    await sleep(100);
}

async function evidenceAndReview() {
    // --- Evidence of a mismatch
    await openScreen(cb("findings"));
    await chooseScope("Selected files");
    await artifactsChecked();
    let d = await detailFacts();
    expect(d && ["Conflicting claims", "Evidence used by this check", "Uncertainty", "Artifacts and current content", "Your review"]
        .every(s => d.sections.includes(s)), "finding detail shows claims, evidence, uncertainty, artifacts and review", d && d.sections);
    expect(d.claims.length === 2 && /What the code does/.test(d.claims[0]) && /What the artifact says/.test(d.claims[1]), "source and generated claims side by side");
    expect(d.claims[1].includes("Returns true only when the item is unused"), "generated claim excerpt shown", d.claims[1]);
    expect(/Intended behaviour: unknown/.test(d.text), "intended behaviour shown as unknown");
    expect(d.evidence.some(e => e.includes("Inferred by reading the code (not executed)") && e.includes("return daysSincePurchase <= 30;")),
        "evidence origin and stored excerpt shown", d.evidence);
    expect(d.gutters.some(g => g.split("\n")[0] === "7"), "stored evidence carries its line numbers", d.gutters);
    expect(/Intended behaviour: no confirmed requirement/.test(d.text), "uncertainty listed");
    expect(d.chips.includes("Unchanged since the check"), "current content checked and unchanged", d.chips);
    await shot("08-finding-evidence");

    await clickTab("Verdicts");
    let text = await js(`document.querySelector('.cr-tabbed').textContent`);
    expect(/Consistent/.test(text) && /Inconsistent/.test(text) && /day 30|30 or fewer|30/.test(text), "verdicts tab shows every verdict with its reason");
    const rows = await js(`document.querySelectorAll('.cr-tabbed tbody tr').length`);
    expect(rows === 3, "one verdict row per assessed artifact", rows);
    await shot("09-verdicts");
    await clickTab("Findings");

    // --- Review: draft kept, single save, persisted
    const first = d.id;
    await js(`(() => { const r = ${DETAIL}.querySelector('.cr-radio input[value="reviewed"]'); r.click(); return true; })()`);
    await js(`(() => { const t = ${DETAIL}.querySelector('.cr-note-input'); t.focus(); t.value = 'Checked with the team; intent still unknown.';
        t.dispatchEvent(new Event('input', { bubbles: true })); return true; })()`);
    d = await detailFacts();
    expect(d.checked === "reviewed" && d.dirty === "Unsaved changes" && d.saveDisabled === false, "edited review shows as unsaved", d);
    const focused = await js(`document.activeElement && document.activeElement.classList.contains('cr-note-input')`);
    expect(focused, "typing a note keeps focus in the note");

    const second = (await facts()).rowIds.find(id => id !== first);
    await js(`document.querySelector('.cr-finding[data-id="${second}"]').click()`);
    await artifactsChecked();
    let rowsText = await js(`document.querySelector('.cr-finding[data-id="${first}"]').textContent`);
    expect(/Unsaved/.test(rowsText), "the edited finding is marked unsaved in the list");
    expect(/1 review not saved yet/.test((await facts()).all), "header counts unsaved reviews");
    await js(`document.querySelector('.cr-finding[data-id="${first}"]').click()`);
    d = await detailFacts();
    expect(d.note === "Checked with the team; intent still unknown." && d.checked === "reviewed", "switching findings keeps the draft", d);

    const resultPath = path.join(demo.app, "agent", "consistency_output", demo.states.findings, "consistency_result_selected.json");
    const resultBefore = fs.readFileSync(resultPath, "utf8");
    await js(`(() => { window.__crSaves = 0; const real = backendCall;
        backendCall = (command, args) => { if (command === 'save_consistency_review') window.__crSaves++; return real(command, args); };
        window.ConsistencyReviewScreen._state.api = { call: backendCall }; return true; })()`);
    await js(`(() => { const b = ${DETAIL}.querySelector('.cr-save'); b.click(); b.click(); return true; })()`);
    await until(`/Saved at/.test((${DETAIL}.querySelector('.cr-feedback') || {}).textContent || '')`, "save feedback");
    const saves = await js(`window.__crSaves`);
    expect(saves === 1, "a double click saves once", saves);
    d = await detailFacts();
    expect(d.chips.includes("Reviewed") && d.dirty === "" && d.saveDisabled === true, "saved review shown as saved", d);
    expect(/Reviewed or dismissed records your decision only. It does not mean the code/.test(d.text), "review controls disclaim correctness");
    expect(fs.readFileSync(resultPath, "utf8") === resultBefore, "saving a review leaves the saved result untouched");
    const reviews = JSON.parse(fs.readFileSync(path.join(path.dirname(resultPath), "reviews.json"), "utf8"));
    expect(reviews.reviews[first] && reviews.reviews[first].note === "Checked with the team; intent still unknown.", "review written to reviews.json");
    await js(`${DETAIL}.querySelector('.cr-review').scrollIntoView({ block: 'center' })`);
    await shot("10-review-saved");

    await js(`[...document.querySelectorAll('.cr-button')].find(b => b.textContent === 'Reload').click()`);
    await until(LOADED, "reload");
    await js(`document.querySelector('.cr-finding[data-id="${first}"]').click()`);
    d = await detailFacts();
    expect(d.checked === "reviewed" && d.note === "Checked with the team; intent still unknown.", "review restored after reload", d);

    await openScreen(cb("clean"));
    await openScreen(cb("findings"));
    await chooseScope("Selected files");
    await js(`document.querySelector('.cr-finding[data-id="${first}"]').click()`);
    d = await detailFacts();
    expect(d.checked === "reviewed", "review restored after switching project and back");
    const cleanReviews = path.join(demo.app, "agent", "consistency_output", demo.states.clean, "reviews.json");
    expect(!fs.existsSync(cleanReviews), "no review written for another project");

    // Results stay apart by scope; a review belongs to the finding (same
    // category and content), so the same finding shows it in either scope.
    const selectedCheck = (await facts()).fields.Check;
    await chooseScope("Whole project");
    let full = await facts();
    expect(full.fields.Check !== selectedCheck && full.fields.Result === "Whole project", "whole-project result is its own check");
    await js(`document.querySelector('.cr-finding[data-id="${first}"]').click()`);
    d = await detailFacts();
    expect(d.checked === "reviewed" && full.counts["Unresolved reviews"] === 1, "the same finding shows its review in the other scope", full.counts);
    await chooseScope("Selected files");

    // --- Keyboard selection
    await js(`document.querySelector('.cr-finding[data-id="${second}"]').focus()`);
    for (const type of ["keyDown", "char", "keyUp"]) win.webContents.sendInputEvent({ type, keyCode: type === "char" ? "\r" : "Enter" });
    await sleep(300);
    d = await detailFacts();
    expect(d.id === second, "a finding can be opened with the keyboard", d.id);

    // --- A failed save keeps the draft and says why
    await openScreen(cb("partial_findings"));
    await artifactsChecked();
    await js(`(() => { const r = ${DETAIL}.querySelector('.cr-radio input[value="dismissed"]'); r.click(); return true; })()`);
    const partialPath = path.join(demo.app, "agent", "consistency_output", demo.states.partial_findings, "consistency_result_selected.json");
    const partial = JSON.parse(fs.readFileSync(partialPath, "utf8"));
    partial.check_id = "replaced-elsewhere";
    fs.writeFileSync(partialPath, JSON.stringify(partial));
    await js(`${DETAIL}.querySelector('.cr-save').click()`);
    await until(`/Not saved/.test((${DETAIL}.querySelector('.cr-feedback') || {}).textContent || '')`, "save error");
    d = await detailFacts();
    expect(/changed since this screen loaded it/.test(d.feedback) && d.checked === "dismissed" && d.dirty === "Unsaved changes",
        "a refused save explains itself and keeps the draft", d);
    await shot("11-save-refused");

    // --- Skipped and undetermined
    await openScreen(cb("skipped"));
    await clickTab("Skipped");
    text = await js(`document.querySelector('.cr-tabbed').textContent`);
    expect(/Folder-level rule for src/.test(text) && /not assessed/.test(text), "skipped tab explains the folder-level rule");
    await shot("12-skipped");
    await openScreen(cb("undetermined"));
    await clickTab("Undetermined");
    text = await js(`document.querySelector('.cr-tabbed').textContent`);
    expect(/could not decide/.test(text) && /Summary/.test(text), "undetermined tab lists the comparison and its reason");
    await openScreen(cb("older"));
    await clickTab("Verdicts");
    text = await js(`document.querySelector('.cr-tabbed').textContent`);
    expect(/This older result does not record the verdict/.test(text), "older result: verdicts shown as not recorded, not as none");

    // --- Changed and missing content
    await openScreen(cb("skipped"));
    await artifactsChecked();
    d = await detailFacts();
    expect(d.chips.includes("Changed since the check"), "edited source marked changed", d.chips);
    expect(d.evidence.some(e => e.includes("age >= 18")), "stored evidence still shows the original code");
    await js(`[...${DETAIL}.querySelectorAll('.cr-current .cr-button')].find(b => b.textContent === 'Show current content').click()`);
    d = await detailFacts();
    expect(d.current.some(c => /changed, not the evidence used/.test(c) && c.includes("age >= 21")), "current content shown separately and labelled", d.current);
    await js(`[...${DETAIL}.querySelectorAll('.cr-artifact')].find(a => a.textContent.includes('Changed since')).scrollIntoView({ block: 'start' })`);
    await shot("13-changed-content");

    await openScreen(cb("missing_source"));
    await artifactsChecked();
    d = await detailFacts();
    expect(d.chips.includes("No longer exists") && d.evidence.some(e => e.includes("return price + price * 0.10m;")),
        "deleted source: reported missing, stored evidence kept", d.chips);

    // --- Untrusted text stays text
    await openScreen(cb("injection"));
    await artifactsChecked();
    const injection = await js(`(() => {
        const s = document.querySelector('#viewDisplayOutputBox .cr-screen');
        return { injected: window.__crInjected, elements: s.querySelectorAll('img, script, iframe, object').length,
                 literal: s.textContent.includes('<img src=x'), note: (s.querySelector('.cr-note-input') || {}).value || '' };
    })()`);
    expect(injection.injected === undefined && injection.elements === 0, "HTML in titles, claims and notes is not interpreted", injection);
    expect(injection.literal && injection.note.includes("<script>"), "the HTML is shown as literal text", injection);
    await clickTab("Verdicts");
    const verdictInjection = await js(`(() => ({ injected: window.__crInjected,
        elements: document.querySelectorAll('#viewDisplayOutputBox img, #viewDisplayOutputBox script').length }))()`);
    expect(verdictInjection.injected === undefined && verdictInjection.elements === 0, "HTML in verdict reasons is not interpreted");
    await clickTab("Findings");
    await shot("14-injection-as-text");
}

// --- New check panel ---------------------------------------------------------
const PANEL = `document.querySelector('#cr-check-panel')`;

async function panelFacts() {
    return js(`(() => {
        const p = ${PANEL};
        if (!p) return null;
        const start = p.querySelector('.cr-start');
        return {
            text: p.textContent,
            startDisabled: start.disabled,
            startText: start.textContent,
            blocked: (p.querySelector('.cr-blocked') || {}).textContent || '',
            files: [...p.querySelectorAll('.cr-pick-path')].map(n => n.textContent),
            plan: (p.querySelector('.cr-plan') || {}).textContent || '',
            message: (p.querySelector('.cr-feedback') || {}).textContent || '',
            methods: [...p.querySelectorAll('.cr-pick select option')].map(o => o.textContent),
        };
    })()`);
}

async function recordCommands() {
    await js(`(() => { window.__crCommands = []; const real = window.__crRealCall || backendCall; window.__crRealCall = real;
        backendCall = (command, args) => { window.__crCommands.push(command); return real(command, args); };
        if (window.ConsistencyReviewScreen._state.api) window.ConsistencyReviewScreen._state.api = { call: backendCall };
        return true; })()`);
}

const commandsSent = () => js(`window.__crCommands.slice()`);

async function openPanel() {
    await js(`[...document.querySelectorAll('.cr-header .cr-button')].find(b => b.textContent === 'New check').click()`);
    await until(`!!${PANEL} && !${PANEL}.textContent.includes('Listing the files')`, "check panel");
}

async function newCheckPanel() {
    const before = modelEvents().length;
    await recordCommands();

    // Opening the screen and the panel never starts a check.
    await openScreen(cb("clean"));
    await openPanel();
    let p = await panelFacts();
    let sent = await commandsSent();
    expect(!sent.includes("check_consistency") && sent.includes("get_consistency_targets"), "opening the panel lists files, starts nothing", sent);
    expect(/uses the AI model and API key selected in Manage API Keys/.test(p.text) && /quota/.test(p.text), "quota notice shown");
    expect(/The whole codebase: 1 file with generated artifacts/.test(p.plan) && /Up to 1 model request/.test(p.plan), "whole-codebase plan shown", p.plan);
    expect(!/agent\/|_output|SUMMARY|RULE_|TEST_/.test(p.text), "no internal handles or storage paths shown", p.text);
    expect(p.startDisabled === false && p.startText === "Check Consistency", "start available for a valid scope");
    await shot("15-new-check-panel");

    // Selected files: nothing chosen cannot start; choose a file and a method.
    await js(`${PANEL}.querySelector('input[value="selected"]').click()`);
    p = await panelFacts();
    expect(p.startDisabled && /Select at least one file/.test(p.blocked), "an empty selection cannot start", p.blocked);
    expect(p.files.includes("src/Pricing.cs"), "files with artifacts are listed", p.files);
    await js(`${PANEL}.querySelector('.cr-pick input[type=checkbox]').click()`);
    await until(`${PANEL}.querySelectorAll('.cr-pick select option').length > 1`, "method list");
    p = await panelFacts();
    expect(p.methods.includes("Whole file") && p.methods.includes("Pricing.Discount"), "methods offered for the selected file", p.methods);
    await js(`(() => { const s = ${PANEL}.querySelector('.cr-pick select'); s.value = 'Pricing.Discount';
        s.dispatchEvent(new Event('change')); return true; })()`);
    p = await panelFacts();
    expect(/1 selected file:/.test(p.plan) && /src\/Pricing.cs \(method Pricing.Discount\)/.test(p.plan), "selected method shown in the plan", p.plan);
    await shot("16-selected-method");

    // Switching scope back and forth starts nothing.
    await js(`${PANEL}.querySelector('input[value="full"]').click()`);
    await js(`${PANEL}.querySelector('input[value="selected"]').click()`);
    sent = await commandsSent();
    expect(!sent.includes("check_consistency") && modelEvents().length === before, "changing scope starts nothing");

    // Unsaved reviews block the start until saved or discarded.
    await openScreen(cb("findings"));
    await js(`(() => { const r = ${DETAIL}.querySelector('.cr-radio input[value="dismissed"]'); r.click(); return true; })()`);
    await openPanel();
    p = await panelFacts();
    expect(p.startDisabled && /Save or discard your 1 unsaved review first/.test(p.blocked), "unsaved reviews block a new check", p.blocked);
    await js(`[...${PANEL}.querySelectorAll('.cr-button')].find(b => /^Discard/.test(b.textContent)).click()`);
    p = await panelFacts();
    expect(!p.startDisabled, "discarding the unsaved review allows the check", p.blocked);

    // A codebase without outputs cannot start a check.
    await openScreen(cb("no_outputs"));
    await openPanel();
    p = await panelFacts();
    expect(p.startDisabled && /has generated summaries, rules or tests/.test(p.blocked), "no outputs: start disabled with the reason", p.blocked);
    await shot("17-no-outputs");

    // Starting: one click, one check; a second click while running does nothing.
    await openScreen(cb("clean"));
    await openPanel();
    await recordCommands();
    const runBefore = modelEvents().filter(e => e.event === "call").length;
    await js(`(() => { const b = ${PANEL}.querySelector('.cr-start'); b.click(); b.click(); return true; })()`);
    p = await panelFacts();
    expect(p.startDisabled && p.startText === "Checking…", "start disabled while the check runs", p.startText);
    await until(`!${PANEL} || !${PANEL}.querySelector('.cr-start').disabled || /completed|incomplete|failed|did not run/.test(${PANEL}.textContent)`, "check finished", 60000);
    await until(LOADED, "result after check", 30000);
    sent = await commandsSent();
    const calls = modelEvents().filter(e => e.event === "call").length - runBefore;
    expect(sent.filter(c => c === "check_consistency").length === 1 && calls === 1, "a double click runs one check with one model call", [sent, calls]);
    const f = await facts();
    expect(f.state === "clean" && f.fields.Result === "Whole project", "the new whole-codebase result is shown", [f.state, f.fields.Result]);
    p = await panelFacts();
    expect(/Consistency check completed: no mismatches found in the assessed artifacts/.test(p.message), "completion message shown", p.message);
    await shot("18-check-completed");
}

// --- Run --------------------------------------------------------------------
async function openInteractive() {
    const start = Date.now();
    while (!(win = BrowserWindow.getAllWindows()[0]) && Date.now() - start < 20000) await sleep(100);
    if (win.webContents.isLoading()) await new Promise(resolve => win.webContents.once("did-finish-load", resolve));
    const state = interactiveState || "findings";
    console.log("Demo codebases (paste one into the codebase path on the Run Operations page to switch):");
    Object.entries(demo.states).forEach(([name, codebase]) => console.log(`  ${name.padEnd(17)} ${demo.codebases[codebase]}`));
    await openScreen(cb(state));
    console.log(`\nShowing "${state}". Model creation is disabled; close the window to stop.`);
}

app.whenReady().then(async () => {
    if (interactive) {
        await openInteractive();
        return;
    }
    try {
        const start = Date.now();
        while (!(win = BrowserWindow.getAllWindows()[0]) && Date.now() - start < 20000) await sleep(100);
        if (win.webContents.isLoading()) await new Promise(resolve => win.webContents.once("did-finish-load", resolve));
        win.setSize(1440, 960);

        await screenStates();
        await evidenceAndReview();
        const created = modelEvents().length;
        expect(created === 0, "browsing results created no model", created);
        await newCheckPanel();

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
