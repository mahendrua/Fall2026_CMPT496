"""
backend/run_report_html.py

The run report as one HTML file (US-049, T-165): Overview, Folders, Business
rules (with evidence), Tests and Diagram tabs, searchable, linked to each
other (a test links to its rule, a rule to its tests, a class in the diagram
to its details).

Everything is inside the one file -- styles, script, the run's data and the
diagram -- so it opens in any browser without Checkpoint or a network.
"""

import json
import re
from html import escape


def render(data, diagram=None):
    """
    The report page for one run.

    @param data What run_report_data.collect returned.
    @param diagram What run_report_diagram.build_diagram returned, or None.
    """
    payload = dict(data)
    diagram = diagram or {}
    payload["diagram"] = {key: value for key, value in diagram.items() if key != "svg"}

    # Inside <script>, "</script>" or "<!--" in the data would end the block early.
    data_json = json.dumps(payload).replace("<", "\\u003c")

    svg = re.sub(r"<\?[^>]*\?>", "", diagram.get("svg") or "")

    parts = {
        "TITLE": escape(f"{data.get('codebase', 'Project')} - Checkpoint run report"),
        "CODEBASE": escape(str(data.get("codebase", "Project"))),
        "DATA": data_json,
        "SVG": svg,
    }

    # One pass, so a marker that happens to appear in the data is left alone.
    return re.sub(r"@@(TITLE|CODEBASE|DATA|SVG)@@", lambda match: parts[match.group(1)], TEMPLATE)


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>@@TITLE@@</title>
<style>
:root {
  --bg: #f5f6f8; --panel: #ffffff; --text: #1c2333; --muted: #5b677d; --line: #e0e4eb;
  --accent: #2f55d4; --accent-soft: #e9eefc; --code-bg: #f3f5f8;
  --ok: #17703d; --ok-bg: #e3f4ea; --bad: #b3261e; --bad-bg: #fdebea;
  --warn: #8a5300; --warn-bg: #fff1d6; --none: #556074; --none-bg: #edf0f4;
  --flash: #fff4c2;
  color-scheme: light;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #12151b; --panel: #1b1f27; --text: #e4e8ef; --muted: #9aa5b8; --line: #2c323d;
    --accent: #8fa8ff; --accent-soft: #232c45; --code-bg: #151920;
    --ok: #6fd39a; --ok-bg: #173323; --bad: #ff8a80; --bad-bg: #3a1c1b;
    --warn: #f4c06a; --warn-bg: #3a2c12; --none: #a9b3c4; --none-bg: #262b35;
    --flash: #3d3816;
    color-scheme: dark;
  }
}
* { box-sizing: border-box; }
html { scroll-padding-top: 120px; }
body { margin: 0; background: var(--bg); color: var(--text);
  font: 15px/1.55 system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
h2 { font-size: 17px; margin: 0 0 10px; }
h4 { font-size: 13px; margin: 16px 0 6px; color: var(--muted); text-transform: uppercase; letter-spacing: .04em; }
p { margin: 0 0 8px; }
.muted { color: var(--muted); }
.mono, code, pre { font-family: ui-monospace, "Cascadia Mono", Consolas, "Courier New", monospace; }

header { position: sticky; top: 0; z-index: 20; background: var(--panel); border-bottom: 1px solid var(--line); }
.head { max-width: 1180px; margin: 0 auto; padding: 14px 20px 0; }
.title-row { display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px 14px; }
.title-row h1 { font-size: 20px; margin: 0; }
.title-row .sub { color: var(--muted); font-size: 13px; }
nav { display: flex; gap: 4px; margin-top: 10px; overflow-x: auto; }
nav a { padding: 8px 12px; border-bottom: 2px solid transparent; color: var(--muted); font-weight: 600; font-size: 14px; white-space: nowrap; }
nav a:hover { color: var(--text); text-decoration: none; }
nav a[aria-current="page"] { color: var(--accent); border-bottom-color: var(--accent); }
nav .n { font-weight: 500; font-size: 12px; color: var(--muted); margin-left: 4px; }

main { max-width: 1180px; margin: 0 auto; padding: 20px; }
main.wide { max-width: none; }
section[hidden] { display: none; }
.card { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 18px 20px; margin-bottom: 16px; }
.lead { font-size: 15.5px; max-width: 80ch; }

.chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
.chip { background: var(--accent-soft); color: var(--text); border-radius: 999px; padding: 2px 10px; font-size: 13px; }

.tiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px; margin-bottom: 16px; }
.tile { display: block; background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 14px 16px; color: var(--text); }
.tile:hover { border-color: var(--accent); text-decoration: none; }
.tile-label { font-size: 13px; color: var(--muted); font-weight: 600; }
.tile-value { font-size: 24px; font-weight: 700; margin: 2px 0; font-variant-numeric: tabular-nums; }
.tile-sub { font-size: 13px; color: var(--muted); }

table { border-collapse: collapse; width: 100%; font-size: 14px; }
th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
th { color: var(--muted); font-weight: 600; font-size: 13px; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
.table-wrap { overflow-x: auto; }
dl.facts { display: grid; grid-template-columns: max-content 1fr; gap: 4px 16px; margin: 0 0 14px; font-size: 14px; }
dl.facts dt { color: var(--muted); }
dl.facts dd { margin: 0; overflow-wrap: anywhere; }
ul.notes { margin: 0; padding-left: 20px; }
ul.notes li { margin-bottom: 4px; }

.pill { display: inline-block; border-radius: 999px; padding: 1px 9px; font-size: 12px; font-weight: 700; white-space: nowrap; }
.s-proven, .s-passed, .s-success { background: var(--ok-bg); color: var(--ok); }
.s-error, .s-failed { background: var(--bad-bg); color: var(--bad); }
.s-dropped, .s-rejected { background: var(--warn-bg); color: var(--warn); }
.s-not-run, .s-running { background: var(--none-bg); color: var(--none); }
.badge { display: inline-block; border: 1px solid var(--line); border-radius: 6px; padding: 0 6px; font-size: 12px; color: var(--muted); white-space: nowrap; }
.badge.warn { border-color: var(--warn); color: var(--warn); }

.toolbar { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin-bottom: 14px; }
.search { flex: 1 1 260px; min-width: 0; padding: 9px 12px; font: inherit; border: 1px solid var(--line); border-radius: 8px; background: var(--panel); color: var(--text); }
.search:focus { outline: 2px solid var(--accent); outline-offset: -1px; }
.filters { display: flex; flex-wrap: wrap; gap: 4px; }
.filters button { font: inherit; font-size: 13px; padding: 6px 11px; border: 1px solid var(--line); background: var(--panel); color: var(--text); border-radius: 999px; cursor: pointer; }
.filters button[aria-pressed="true"] { background: var(--accent); border-color: var(--accent); color: #fff; }
.count-line { font-size: 13px; color: var(--muted); margin: -4px 0 10px; }

details.item { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; margin-bottom: 8px; }
details.item > summary { list-style: none; cursor: pointer; padding: 11px 14px; display: flex; flex-wrap: wrap; gap: 6px 10px; align-items: baseline; }
details.item > summary::-webkit-details-marker { display: none; }
details.item > summary::before { content: "\25B8"; color: var(--muted); width: 10px; flex: none; }
details.item[open] > summary::before { content: "\25BE"; }
details.item[open] > summary { border-bottom: 1px solid var(--line); }
.item-title { flex: 1 1 400px; min-width: 0; }
.item-body { padding: 4px 16px 16px 34px; }
.id { font-weight: 700; color: var(--muted); font-variant-numeric: tabular-nums; }
.flash { animation: flash 1.8s ease-out; }
@keyframes flash { from { background: var(--flash); } to { background: var(--panel); } }

pre.code { background: var(--code-bg); border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px; margin: 6px 0 10px; overflow-x: auto; font-size: 12.5px; line-height: 1.45; white-space: pre; max-height: 480px; }
/* Evidence snippets often come as one long line; wrap them rather than cut them off. */
pre.code.wrap { white-space: pre-wrap; overflow-wrap: anywhere; }
.evidence-file { font-weight: 600; font-size: 13.5px; margin-top: 10px; }
ul.links { list-style: none; padding: 0; margin: 0; }
ul.links li { padding: 4px 0; display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; }
.dots { display: inline-flex; gap: 3px; }
.dot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; }
.dot.s-passed { background: var(--ok); } .dot.s-failed { background: var(--bad); }
.dot.s-dropped { background: var(--warn); } .dot.s-not-run { background: var(--none); }

.split { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 16px; }
@media (max-width: 760px) { .split { grid-template-columns: 1fr; } }

details.folder { margin-left: calc(var(--depth, 0) * 22px); }
.folder-path { color: var(--muted); font-size: 13px; }
details.file { border-top: 1px solid var(--line); padding: 6px 0; }
details.file > summary { cursor: pointer; font-weight: 600; font-size: 14px; }
details.file > div { padding: 6px 0 4px 18px; }
ul.types { margin: 6px 0; padding-left: 18px; font-size: 14px; }
.kind { font-size: 12px; color: var(--muted); }

#tab-diagram { display: grid; grid-template-columns: 1fr 340px; gap: 14px; }
#tab-diagram[hidden] { display: none; }
#tab-diagram .tools { grid-column: 1 / -1; }
.viewport { position: relative; height: calc(100vh - 210px); min-height: 420px; overflow: hidden; border: 1px solid var(--line); border-radius: 10px; background: #fff; cursor: grab; touch-action: none; }
.viewport.dragging { cursor: grabbing; }
.canvas { position: absolute; left: 0; top: 0; transform-origin: 0 0; }
.canvas svg { display: block; }
.canvas a.sel > rect, .canvas a.sel > path:first-of-type { stroke: #e8590c !important; stroke-width: 3px !important; }
.zoom-label { font-size: 13px; color: var(--muted); min-width: 48px; text-align: right; font-variant-numeric: tabular-nums; }
.tools button { font: inherit; font-size: 14px; padding: 6px 12px; border: 1px solid var(--line); background: var(--panel); color: var(--text); border-radius: 8px; cursor: pointer; }
.tools button:hover { border-color: var(--accent); }
aside.panel { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 16px; height: calc(100vh - 210px); min-height: 420px; overflow-y: auto; font-size: 14px; }
aside.panel h3 { margin: 0 0 4px; font-size: 17px; overflow-wrap: anywhere; }
aside.panel ul { padding-left: 18px; margin: 4px 0; }
@media (max-width: 900px) {
  #tab-diagram { grid-template-columns: 1fr; }
  aside.panel { height: auto; min-height: 0; }
}
footer { max-width: 1180px; margin: 0 auto; padding: 0 20px 30px; font-size: 13px; color: var(--muted); }
</style>
</head>
<body>
<header>
  <div class="head">
    <div class="title-row">
      <h1>@@CODEBASE@@</h1>
      <span class="sub" id="run-line">Checkpoint run report</span>
    </div>
    <nav id="tabs">
      <a href="#overview" data-tab="overview">Overview</a>
      <a href="#folders" data-tab="folders">Folders <span class="n" id="n-folders"></span></a>
      <a href="#rules" data-tab="rules">Business rules <span class="n" id="n-rules"></span></a>
      <a href="#tests" data-tab="tests">Tests <span class="n" id="n-tests"></span></a>
      <a href="#diagram" data-tab="diagram">Diagram <span class="n" id="n-types"></span></a>
    </nav>
  </div>
</header>
<main id="main">
  <section id="tab-overview"></section>
  <section id="tab-folders" hidden></section>
  <section id="tab-rules" hidden></section>
  <section id="tab-tests" hidden></section>
  <section id="tab-diagram" hidden>
    <div class="tools toolbar">
      <button type="button" id="zoom-out" title="Zoom out">&minus;</button>
      <button type="button" id="zoom-in" title="Zoom in">+</button>
      <button type="button" id="zoom-fit">Fit</button>
      <button type="button" id="zoom-100">100%</button>
      <span class="zoom-label" id="zoom-label"></span>
      <input class="search" id="find-type" list="type-names" placeholder="Find a class (press Enter)">
      <datalist id="type-names"></datalist>
      <span class="muted" id="diagram-stats"></span>
    </div>
    <div class="viewport" id="viewport"><div class="canvas" id="canvas">@@SVG@@</div></div>
    <aside class="panel" id="panel"></aside>
  </section>
</main>
<footer id="footer"></footer>
<script type="application/json" id="report-data">@@DATA@@</script>
<script>
(function () {
  "use strict";

  var DATA = JSON.parse(document.getElementById("report-data").textContent);
  var COUNTS = DATA.overview.counts;
  var DIAGRAM = DATA.diagram || {};
  var TESTS = DATA.tests.unit.tests.concat(DATA.tests.integration.tests);
  var RULES = DATA.rules;
  var TABS = ["overview", "folders", "rules", "tests", "diagram"];
  var HAS_SVG = !!document.querySelector("#canvas svg");

  var rulesById = new Map(RULES.map(function (r) { return [String(r.id), r]; }));
  var testsByName = new Map(TESTS.map(function (t) { return [t.name, t]; }));
  var typesById = new Map((DIAGRAM.types || []).map(function (t) { return [t.id, t]; }));

  // ---------- small helpers ----------

  function el(tag, attrs) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (key) {
      var value = attrs[key];
      if (value === null || value === undefined || value === false) return;
      if (key === "class") node.className = value;
      else if (key === "style") node.setAttribute("style", value);
      else if (key.slice(0, 2) === "on") node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? "" : value);
    });
    for (var i = 2; i < arguments.length; i++) add(node, arguments[i]);
    return node;
  }
  function add(node, child) {
    if (child === null || child === undefined || child === false) return;
    if (Array.isArray(child)) { child.forEach(function (c) { add(node, c); }); return; }
    node.append(child instanceof Node ? child : String(child));
  }
  function fmt(n) { return n === null || n === undefined ? "–" : Number(n).toLocaleString("en-US"); }
  function plural(n, one, many) { return fmt(n) + " " + (n === 1 ? one : (many || one + "s")); }
  function duration(seconds) {
    if (seconds === null || seconds === undefined) return "–";
    var s = Math.round(seconds), m = Math.floor(s / 60);
    return m ? m + " min " + (s % 60) + " s" : s + " s";
  }
  function when(stamp) {
    if (!stamp) return "–";
    var d = new Date(stamp);
    return isNaN(d) ? stamp : d.toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" });
  }
  var LABELS = { proven: "Proven", rejected: "Rejected", error: "Error", passed: "Passed", failed: "Failed",
                 dropped: "Dropped", "not run": "Not run", success: "Success", running: "Running" };
  function cls(status) { return "s-" + String(status || "").replace(/\s+/g, "-"); }
  function pill(status) { return el("span", { class: "pill " + cls(status) }, LABELS[status] || status || "–"); }
  function hashFor(kind, key) { return "#" + kind + "-" + encodeURIComponent(key); }
  function text() { return Array.prototype.join.call(arguments, " ").toLowerCase(); }

  var STAGES = {
    build_database: "Read the code into the database",
    generate_file_summaries: "Summarise each file",
    build_summary_database: "Store the summaries",
    generate_directory_summaries: "Summarise each folder",
    validate_business_rules: "Check the business rules",
    generate_unit_tests: "Write and run unit tests",
    generate_integration_tests: "Write and run integration tests",
    generate_all_uml: "Draw UML diagrams"
  };

  // Imports are shown exactly as the test agent saved them, whatever the language.
  function importsOf(test) {
    return (test.imports || []).map(function (i) { return String(i).trim(); }).filter(Boolean);
  }

  // ---------- header ----------

  (function header() {
    var run = DATA.run;
    var line = run ? "Run " + when(run.started_at) + " · " + duration(run.elapsed_seconds) : "Checkpoint run report";
    document.getElementById("run-line").textContent = line;
    document.getElementById("n-folders").textContent = fmt(COUNTS.folders);
    document.getElementById("n-rules").textContent = fmt(COUNTS.rules);
    document.getElementById("n-tests").textContent = fmt(TESTS.length);
    document.getElementById("n-types").textContent = DIAGRAM.types && DIAGRAM.types.length ? fmt(DIAGRAM.types.length) : "";
    add(document.getElementById("footer"), [
      "Made by Checkpoint on " + when(DATA.generated_at) + " from the outputs of this run. ",
      "Everything is inside this one file, so it opens without Checkpoint."
    ]);
  })();

  // ---------- Overview ----------

  function tile(label, value, sub, href) {
    return el("a", { class: "tile", href: href },
      el("div", { class: "tile-label" }, label),
      el("div", { class: "tile-value" }, value),
      sub ? el("div", { class: "tile-sub" }, sub) : null);
  }

  function testTile(label, section, total, passed, failed, dropped) {
    if (!total) return tile(label, "None", "no tests were written", "#tests");
    if (!section.checked) return tile(label, fmt(total) + " written", "not compiled or run in this run", "#tests");
    return tile(label, fmt(passed) + " of " + fmt(total) + " passed",
      fmt(failed) + " failed · " + fmt(dropped) + " dropped", "#tests");
  }

  function buildOverview() {
    var root = document.getElementById("tab-overview");
    var run = DATA.run;
    var tokens = run ? run.tokens : null;

    add(root, el("div", { class: "card" },
      el("h2", null, "What this project is"),
      DATA.overview.summary
        ? el("p", { class: "lead" }, DATA.overview.summary)
        : el("p", { class: "muted" }, "No whole-project summary was found for this run."),
      DATA.overview.responsibilities.length
        ? el("div", { class: "chips" }, DATA.overview.responsibilities.map(function (r) { return el("span", { class: "chip" }, r); }))
        : null));

    var ruleSub = fmt(COUNTS.rules) + " found · " + fmt(COUNTS.rules_rejected) + " rejected" +
      (COUNTS.rules_error ? " · " + fmt(COUNTS.rules_error) + " error" : "");

    add(root, el("div", { class: "tiles" },
      tile("Business rules", fmt(COUNTS.rules_proven) + " proven", ruleSub, "#rules"),
      testTile("Unit tests", DATA.tests.unit, COUNTS.unit_tests, COUNTS.unit_passed, COUNTS.unit_failed, COUNTS.unit_dropped),
      testTile("Integration tests", DATA.tests.integration, COUNTS.integration_tests, COUNTS.integration_passed,
               COUNTS.integration_failed, COUNTS.integration_dropped),
      tile("Code read", plural(COUNTS.files, "file"), plural(COUNTS.folders, "folder") + " · " + plural(COUNTS.types, "class", "classes"), "#folders"),
      tile("AI tokens", tokens ? fmt(tokens.total_tokens) : "–",
           tokens ? fmt(tokens.thinking_tokens) + " thinking · " + plural(tokens.calls, "AI call") : "no run log for this run", "#run"),
      tile("Run time", run ? duration(run.elapsed_seconds) : "–", run ? "finished " + when(run.finished_at) : "no run log for this run", "#run")));

    if (DATA.notes.length) {
      add(root, el("div", { class: "card" },
        el("h2", null, "Good to know about this report"),
        el("ul", { class: "notes" }, DATA.notes.map(function (n) { return el("li", null, n); }))));
    }

    if (run) {
      var rows = run.stages.map(function (s) {
        return el("tr", null,
          el("td", null, STAGES[s.stage] || s.stage),
          el("td", null, pill(s.status)),
          el("td", { class: "num" }, duration(s.elapsed_seconds)),
          el("td", { class: "num" }, fmt(s.calls)),
          el("td", { class: "num" }, fmt(s.total_tokens)),
          el("td", { class: "num" }, s.thinking_tokens === null || s.thinking_tokens === undefined ? "–" : fmt(s.thinking_tokens)));
      });
      add(root, el("div", { class: "card", id: "run" },
        el("h2", null, "The run"),
        el("dl", { class: "facts" },
          el("dt", null, "Status"), el("dd", null, pill(run.status), run.error ? " " + run.error : ""),
          el("dt", null, "Started"), el("dd", null, when(run.started_at)),
          el("dt", null, "Finished"), el("dd", null, when(run.finished_at)),
          el("dt", null, "Run id"), el("dd", { class: "mono" }, run.run_id || "–"),
          el("dt", null, "AI calls"), el("dd", null, fmt(tokens.calls) + (tokens.failed_calls ? " (" + fmt(tokens.failed_calls) + " failed)" : "") +
            (tokens.waits ? " · waited " + duration(tokens.waited_seconds) + " for rate limits" : ""))),
        el("div", { class: "table-wrap" }, el("table", null,
          el("thead", null, el("tr", null, el("th", null, "Step"), el("th", null, "Status"), el("th", { class: "num" }, "Time"),
            el("th", { class: "num" }, "AI calls"), el("th", { class: "num" }, "Tokens"), el("th", { class: "num" }, "of which thinking"))),
          el("tbody", null, rows)))));
    }
  }

  // ---------- shared list filtering ----------

  function makeFilter(root, opts) {
    // opts: items [{node, status, kind, text}], statuses [[value,label]], kinds, placeholder, noun
    var state = { q: "", status: "all", kind: "all" };
    var search = el("input", { class: "search", type: "search", placeholder: opts.placeholder });
    var countLine = el("div", { class: "count-line" });
    var toolbar = el("div", { class: "toolbar" }, search);

    function group(name, values) {
      var box = el("div", { class: "filters", role: "group" });
      values.forEach(function (pair) {
        var n = pair[0] === "all" ? opts.items.length
          : opts.items.filter(function (i) { return i[name] === pair[0]; }).length;
        if (pair[0] !== "all" && !n) return;
        add(box, el("button", { type: "button", "aria-pressed": String(pair[0] === "all"), "data-value": pair[0],
          onclick: function () {
            state[name] = pair[0];
            Array.prototype.forEach.call(box.children, function (b) { b.setAttribute("aria-pressed", String(b.dataset.value === pair[0])); });
            apply();
          } }, pair[1] + " (" + fmt(n) + ")"));
      });
      return box;
    }
    var groups = {};
    if (opts.kinds) { groups.kind = group("kind", opts.kinds); add(toolbar, groups.kind); }
    if (opts.statuses) { groups.status = group("status", opts.statuses); add(toolbar, groups.status); }

    function apply() {
      var words = state.q.toLowerCase().split(/\s+/).filter(Boolean);
      var shown = 0;
      opts.items.forEach(function (item) {
        var ok = (state.status === "all" || item.status === state.status) &&
                 (state.kind === "all" || item.kind === state.kind) &&
                 words.every(function (w) { return item.text.indexOf(w) >= 0; });
        item.node.hidden = !ok;
        if (ok) shown++;
      });
      countLine.textContent = shown === opts.items.length
        ? "Showing all " + plural(shown, opts.noun)
        : "Showing " + fmt(shown) + " of " + plural(opts.items.length, opts.noun);
    }
    search.addEventListener("input", function () { state.q = search.value; apply(); });

    function reset() {
      search.value = ""; state.q = "";
      Object.keys(groups).forEach(function (name) {
        state[name] = "all";
        Array.prototype.forEach.call(groups[name].children, function (b) { b.setAttribute("aria-pressed", String(b.dataset.value === "all")); });
      });
      apply();
    }

    add(root, [toolbar, countLine]);
    apply();
    return { search: search, reset: reset };
  }

  var filters = {};

  // ---------- Business rules ----------

  function testDots(names) {
    if (!names.length) return null;
    return el("span", { class: "dots", title: names.length + " test(s)" },
      names.map(function (n) { var t = testsByName.get(n); return el("span", { class: "dot " + cls(t ? t.status : "") }); }));
  }

  function ruleCard(r) {
    var body = el("div", { class: "item-body" });
    add(body, el("p", { class: "muted" }, "Folder: ", r.folder || "–",
      r.source_files.length ? " · Found in: " + r.source_files.join(", ") : ""));

    if (r.status === "proven") {
      add(body, [el("h4", null, "Why Checkpoint believes it"), el("p", null, r.reasoning || "–")]);
      var files = Object.keys(r.evidence || {});
      if (files.length) {
        add(body, el("h4", null, "Evidence from the code"));
        files.forEach(function (file) {
          add(body, el("div", { class: "evidence-file mono" }, file));
          (r.evidence[file] || []).forEach(function (snippet) { add(body, el("pre", { class: "code wrap" }, snippet)); });
        });
      }
    } else {
      add(body, [el("h4", null, r.status === "error" ? "What went wrong" : "Why it was rejected"), el("p", null, r.reason || "–")]);
    }

    add(body, el("h4", null, "Tests for this rule"));
    if (r.tests.length) {
      add(body, el("ul", { class: "links" }, r.tests.map(function (name) {
        var t = testsByName.get(name);
        return el("li", null, el("a", { href: hashFor("test", name), class: "mono" }, name), t ? pill(t.status) : null,
          t && t.kind === "integration" ? el("span", { class: "muted" }, t.title) : null);
      })));
    } else {
      add(body, el("p", { class: "muted" }, r.status === "proven" ? "No test was written for this rule." : "Rejected rules get no tests."));
    }

    return el("details", { class: "item", id: "rule-" + r.id },
      el("summary", null, el("span", { class: "id" }, "#" + r.id), pill(r.status), el("span", { class: "item-title" }, r.rule), testDots(r.tests)),
      body);
  }

  function buildRules() {
    var root = document.getElementById("tab-rules");
    if (!RULES.length) {
      add(root, el("div", { class: "card muted" }, "No business rules were found for this run."));
      return;
    }
    var list = el("div");
    var items = RULES.map(function (r) {
      var node = ruleCard(r);
      add(list, node);
      return { node: node, status: r.status,
        text: text("#" + r.id, r.rule, r.reasoning, r.reason, r.folder, r.source_files.join(" "), Object.keys(r.evidence || {}).join(" "), r.tests.join(" ")) };
    });
    filters.rules = makeFilter(root, { items: items, noun: "rule", placeholder: "Search rules, evidence files, folders… (press / to jump here)",
      statuses: [["all", "All"], ["proven", "Proven"], ["rejected", "Rejected"], ["error", "Error"]] });
    add(root, list);
  }

  // ---------- Tests ----------

  function sectionCard(label, section, tests) {
    var by = function (s) { return tests.filter(function (t) { return t.status === s; }).length; };
    var lines = [];
    if (!tests.length) lines.push(el("p", { class: "muted" }, "No tests of this kind were written."));
    else if (!section.checked) lines.push(el("p", null, plural(tests.length, "test") + " written, but they were not compiled or run in this run."));
    else {
      lines.push(el("p", null, section.message));
      lines.push(el("p", { class: "muted" }, "By test file: " + fmt(by("passed")) + " passed, " + fmt(by("failed")) + " failed, " +
        fmt(by("dropped")) + " dropped" + (by("not run") ? ", " + fmt(by("not run")) + " not run" : "") +
        ". A test file can hold more than one test method, so the counts above can be higher."));
      if (section.project_error) lines.push(el("p", { class: "s-error" }, "The test project did not build: " + section.project_error));
    }
    return el("div", { class: "card" }, el("h2", null, label), lines);
  }

  function testCard(t) {
    var body = el("div", { class: "item-body" });
    if (t.description) add(body, el("p", null, t.description));

    add(body, el("h4", null, t.rule_ids.length === 1 ? "Checks this rule" : "Checks these rules"));
    if (t.rule_ids.length) {
      add(body, el("ul", { class: "links" }, t.rule_ids.map(function (id) {
        var r = rulesById.get(String(id));
        return el("li", null, el("a", { href: hashFor("rule", id) }, "#" + id), r ? pill(r.status) : null,
          el("span", null, r ? r.rule : "(rule not found in this run's rules)"));
      })));
    } else add(body, el("p", { class: "muted" }, "No rule is linked to this test."));

    if (t.failed_methods.length) {
      add(body, [el("h4", null, "Failing test methods"),
        el("ul", null, t.failed_methods.map(function (m) { return el("li", { class: "mono" }, m); }))]);
    }
    if (t.reason) add(body, [el("h4", null, t.status === "dropped" ? "Why it was dropped" : "Note"), el("p", null, t.reason)]);
    if (t.repaired) add(body, el("p", { class: "muted" }, "It did not compile at first; the AI repaired it."));
    var imports = importsOf(t);
    if (imports.length) add(body, [el("h4", null, "Imports"), el("pre", { class: "code" }, imports.join("\n"))]);
    add(body, [el("h4", null, "Test code"), el("pre", { class: "code" }, t.code || "–")]);

    return el("details", { class: "item", id: "test-" + t.name },
      el("summary", null, el("span", { class: "id mono" }, t.name), pill(t.status),
        t.repaired ? el("span", { class: "badge" }, "repaired") : null,
        el("span", { class: "badge" }, t.kind),
        el("span", { class: "item-title" }, t.title)),
      body);
  }

  function buildTests() {
    var root = document.getElementById("tab-tests");
    add(root, el("div", { class: "split" },
      sectionCard("Unit tests", DATA.tests.unit, DATA.tests.unit.tests),
      sectionCard("Integration tests", DATA.tests.integration, DATA.tests.integration.tests)));
    if (!TESTS.length) return;

    var list = el("div");
    var items = TESTS.map(function (t) {
      var node = testCard(t);
      add(list, node);
      var ruleText = t.rule_ids.map(function (id) { var r = rulesById.get(String(id)); return "#" + id + " " + (r ? r.rule : ""); }).join(" ");
      return { node: node, status: t.status, kind: t.kind,
        text: text(t.name, t.title, t.description || "", t.reason, t.failed_methods.join(" "), ruleText) };
    });
    filters.tests = makeFilter(root, { items: items, noun: "test", placeholder: "Search tests, rules, failing methods…",
      kinds: [["all", "All kinds"], ["unit", "Unit"], ["integration", "Integration"]],
      statuses: [["all", "All"], ["passed", "Passed"], ["failed", "Failed"], ["dropped", "Dropped"], ["not run", "Not run"]] });
    add(root, list);
  }

  // ---------- Folders ----------

  function comparePaths(a, b) {
    if (a === b) return 0;
    if (a === ".") return -1;
    if (b === ".") return 1;
    var x = a.toLowerCase().split("/"), y = b.toLowerCase().split("/");
    for (var i = 0; i < Math.min(x.length, y.length); i++) {
      if (x[i] !== y[i]) return x[i] < y[i] ? -1 : 1;
    }
    return x.length - y.length;
  }

  function typeId(file, type) {
    var base = String(type.name || "").split(/[<\[(]/)[0].trim().split(".").pop();
    var found = (DIAGRAM.types || []).filter(function (t) {
      return t.folder === file.folder && String(t.name).split(/[<\[(]/)[0].trim().split(".").pop() === base;
    })[0];
    return found ? found.id : null;
  }

  function fileBlock(f) {
    var types = f.types.map(function (t) {
      var id = typeId(f, t);
      return el("li", null, el("strong", null, t.name), " ", el("span", { class: "kind" }, t.kind),
        t.description ? " — " + t.description : "",
        id && HAS_SVG ? [" ", el("a", { href: "#type-" + id }, "show in diagram")] : null);
    });
    return el("details", { class: "file", id: "file-" + f.path },
      el("summary", null, f.path.split("/").pop(), " ",
        el("span", { class: "kind" }, f.types.length ? plural(f.types.length, "class", "classes") : ""),
        f.old ? [" ", el("span", { class: "badge warn" }, "older than this run")] : null),
      el("div", null,
        el("p", { class: "folder-path mono" }, f.path),
        el("p", null, f.summary || "–"),
        types.length ? el("ul", { class: "types" }, types) : null,
        f.business_rules.length ? [el("h4", null, "Rules noticed in this file"),
          el("ul", null, f.business_rules.map(function (r) { return el("li", null, r); }))] : null,
        f.dependencies.length ? [el("h4", null, "Uses"), el("div", { class: "chips" },
          f.dependencies.map(function (d) { return el("span", { class: "chip mono" }, d); }))] : null));
  }

  function buildFolders() {
    var root = document.getElementById("tab-folders");
    var folders = new Map(DATA.folders.map(function (f) { return [f.path, f]; }));
    var filesByFolder = new Map();
    DATA.files.forEach(function (f) {
      if (!folders.has(f.folder)) {
        folders.set(f.folder, { path: f.folder, name: f.folder.split("/").pop(), purpose: "", responsibilities: [],
          observed_rules: [], inferred_rules: [], old: false, missing: true });
      }
      if (!filesByFolder.has(f.folder)) filesByFolder.set(f.folder, []);
      filesByFolder.get(f.folder).push(f);
    });
    if (!folders.size) {
      add(root, el("div", { class: "card muted" }, "No folder or file summaries were found for this run."));
      return;
    }

    var list = el("div");
    var items = Array.from(folders.values()).sort(function (a, b) { return comparePaths(a.path, b.path); }).map(function (f) {
      var files = (filesByFolder.get(f.path) || []).sort(function (a, b) { return comparePaths(a.path, b.path); });
      var depth = f.path === "." ? 0 : f.path.split("/").length;
      var rules = (f.observed_rules || []).concat(f.inferred_rules || []);
      var body = el("div", { class: "item-body" },
        f.missing ? el("p", { class: "muted" }, "No summary was written for this folder.") : el("p", null, f.purpose || "–"),
        (f.responsibilities || []).length ? el("div", { class: "chips" }, f.responsibilities.map(function (r) { return el("span", { class: "chip" }, r); })) : null,
        rules.length ? el("details", { class: "file" }, el("summary", null, "Rules noticed in this folder (" + rules.length + ")"),
          el("div", null, el("ul", null, rules.map(function (r) { return el("li", null, r); })))) : null,
        files.length ? [el("h4", null, plural(files.length, "file")), files.map(fileBlock)] : null);
      var node = el("details", { class: "item folder", id: "folder-" + f.path, style: "--depth:" + Math.min(depth, 8), open: depth <= 1 },
        el("summary", null, el("strong", null, f.path === "." ? DATA.codebase : f.name),
          el("span", { class: "folder-path mono" }, f.path === "." ? "project root" : f.path),
          el("span", { class: "muted" }, files.length ? plural(files.length, "file") : ""),
          f.old ? el("span", { class: "badge warn" }, "older than this run") : null),
        body);
      add(list, node);
      return { node: node, text: text(f.path, f.name, f.purpose, (f.responsibilities || []).join(" "), rules.join(" "),
        files.map(function (x) { return x.path + " " + x.summary + " " + x.types.map(function (t) { return t.name; }).join(" "); }).join(" ")) };
    });
    filters.folders = makeFilter(root, { items: items, noun: "folder", placeholder: "Search folders, files, classes…" });
    add(root, list);
  }

  // ---------- Diagram ----------

  var view = { x: 0, y: 0, k: 1 }, fitted = false;
  var viewport = document.getElementById("viewport");
  var canvas = document.getElementById("canvas");
  var svg = canvas.querySelector("svg");

  function svgSize() {
    var box = svg.viewBox && svg.viewBox.baseVal;
    if (box && box.width) return { w: box.width, h: box.height };
    return { w: svg.clientWidth || 800, h: svg.clientHeight || 600 };
  }
  function applyView() {
    canvas.style.transform = "translate(" + view.x + "px," + view.y + "px) scale(" + view.k + ")";
    document.getElementById("zoom-label").textContent = Math.round(view.k * 100) + "%";
  }
  function zoomAt(k, cx, cy) {
    k = Math.max(0.05, Math.min(6, k));
    view.x = cx - (cx - view.x) * (k / view.k);
    view.y = cy - (cy - view.y) * (k / view.k);
    view.k = k;
    applyView();
  }
  function fit() {
    var size = svgSize(), w = viewport.clientWidth, h = viewport.clientHeight;
    if (!w || !h) return;
    view.k = Math.min(w / size.w, h / size.h, 1) * 0.96;
    view.x = (w - size.w * view.k) / 2;
    view.y = (h - size.h * view.k) / 2;
    applyView();
  }
  function centerOn(node) {
    var box = node.getBBox();
    var k = Math.max(view.k, 0.9);
    view.k = k;
    view.x = viewport.clientWidth / 2 - (box.x + box.width / 2) * k;
    view.y = viewport.clientHeight / 2 - (box.y + box.height / 2) * k;
    applyView();
  }

  function typeLink(id) {
    var t = typesById.get(id);
    return t ? el("a", { href: "#type-" + id }, t.name) : id;
  }

  function showType(id) {
    var t = typesById.get(id);
    var panel = document.getElementById("panel");
    panel.textContent = "";
    if (!t) { add(panel, el("p", { class: "muted" }, "That class is not in this diagram.")); return; }

    var out = DIAGRAM.edges.filter(function (e) { return e.source === id; });
    var inc = DIAGRAM.edges.filter(function (e) { return e.target === id; });
    var KIND = { inheritance: "Inherits from", implements: "Implements", association: "Uses" };
    var BACK = { inheritance: "Inherited by", implements: "Implemented by", association: "Used by" };

    add(panel, [
      el("h3", null, t.name),
      el("p", { class: "muted" }, t.kind + " in ", el("a", { href: hashFor("folder", t.folder) }, t.folder === "." ? "the project root" : t.folder)),
      el("p", null, t.description || "–"),
      el("h4", null, t.files.length === 1 ? "File" : "Files"),
      el("ul", null, t.files.map(function (f) { return el("li", null, el("a", { href: hashFor("file", f), class: "mono" }, f)); }))
    ]);
    ["inheritance", "implements", "association"].forEach(function (kind) {
      var a = out.filter(function (e) { return e.kind === kind; }), b = inc.filter(function (e) { return e.kind === kind; });
      if (a.length) add(panel, [el("h4", null, KIND[kind]), el("ul", null, a.map(function (e) { return el("li", null, typeLink(e.target)); }))]);
      if (b.length) add(panel, [el("h4", null, BACK[kind]), el("ul", null, b.map(function (e) { return el("li", null, typeLink(e.source)); }))]);
    });
    if (t.enum_values.length) add(panel, [el("h4", null, "Values"), el("p", { class: "mono" }, t.enum_values.join(", "))]);
    if (t.properties.length) {
      add(panel, [el("h4", null, "Properties (" + t.properties.length + ")"), el("ul", null, t.properties.map(function (p) {
        return el("li", null, el("span", { class: "mono" }, (p.visibility ? p.visibility + " " : "") + (p.is_static ? "static " : "") +
          (p.type ? p.type + " " : "") + p.name), p.description ? el("div", { class: "muted" }, p.description) : null);
      }))]);
    }
    if (t.methods.length) {
      add(panel, [el("h4", null, "Methods (" + t.methods.length + ")"), el("ul", null, t.methods.map(function (m) {
        var sig = (m.visibility ? m.visibility + " " : "") + (m.is_static ? "static " : "") + (m.return_type && !m.is_constructor ? m.return_type + " " : "") +
          m.name + "(" + (m.parameters || []).join(", ") + ")";
        return el("li", null, el("span", { class: "mono" }, sig), m.description ? el("div", { class: "muted" }, m.description) : null);
      }))]);
    }

    Array.prototype.forEach.call(canvas.querySelectorAll("a.sel"), function (a) { a.classList.remove("sel"); });
    var node = canvas.querySelector('a[href="#type-' + id + '"]');
    if (node) { node.classList.add("sel"); centerOn(node); }
  }

  function buildDiagram() {
    var stats = document.getElementById("diagram-stats");
    var types = DIAGRAM.types || [];
    var folders = new Set(types.map(function (t) { return t.folder; }));
    stats.textContent = types.length ? plural(types.length, "class", "classes") + " in " + plural(folders.size, "folder") + " · " +
      plural((DIAGRAM.edges || []).length, "link") +
      (DIAGRAM.skipped_relationships ? " (" + fmt(DIAGRAM.skipped_relationships) + " to library or unclear classes not drawn)" : "") : "";

    var datalist = document.getElementById("type-names");
    types.forEach(function (t) { add(datalist, el("option", { value: t.name + " — " + (t.folder === "." ? "root" : t.folder) })); });

    var panel = document.getElementById("panel");
    if (!svg) {
      document.querySelector("#tab-diagram .tools").hidden = true;
      viewport.hidden = true;
      add(panel, [el("h3", null, "No diagram for this run"), el("p", null, DIAGRAM.error || "The diagram was not made."),
        DIAGRAM.puml ? [el("p", { class: "muted" }, "The diagram text is below; paste it into any PlantUML viewer to draw it."),
          el("pre", { class: "code" }, DIAGRAM.puml)] : null]);
      document.getElementById("tab-diagram").style.gridTemplateColumns = "1fr";
      return;
    }
    add(panel, [el("h3", null, "Whole-project class diagram"),
      el("p", null, "Every class Checkpoint found, grouped by folder. Click a class to see what it does, its file and what it is linked to."),
      el("p", { class: "muted" }, "Scroll to zoom, drag to move. Arrows with a hollow triangle mean “inherits from”; plain arrows mean “uses”.")]);

    // Name each class in its tooltip instead of the link text.
    Array.prototype.forEach.call(canvas.querySelectorAll('a[href^="#type-"]'), function (a) {
      var t = typesById.get(a.getAttribute("href").slice(6));
      if (!t) return;
      a.setAttribute("title", t.name);
      a.setAttributeNS("http://www.w3.org/1999/xlink", "xlink:title", t.name);
      a.removeAttribute("target");
    });

    var drag = null, moved = false;
    viewport.addEventListener("wheel", function (e) {
      e.preventDefault();
      var r = viewport.getBoundingClientRect();
      zoomAt(view.k * Math.exp(-e.deltaY * 0.0015), e.clientX - r.left, e.clientY - r.top);
    }, { passive: false });
    viewport.addEventListener("pointerdown", function (e) {
      drag = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y }; moved = false;
    });
    window.addEventListener("pointermove", function (e) {
      if (!drag) return;
      var dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      if (!moved && Math.abs(dx) + Math.abs(dy) > 4) { moved = true; viewport.classList.add("dragging"); }
      if (moved) { view.x = drag.vx + dx; view.y = drag.vy + dy; applyView(); }
    });
    window.addEventListener("pointerup", function () { drag = null; viewport.classList.remove("dragging"); });
    canvas.addEventListener("click", function (e) {
      var a = e.target.closest && e.target.closest("a");
      if (!a) return;
      e.preventDefault();
      if (!moved) location.hash = a.getAttribute("href");
    });

    function zoomCenter(f) { zoomAt(view.k * f, viewport.clientWidth / 2, viewport.clientHeight / 2); }
    document.getElementById("zoom-in").onclick = function () { zoomCenter(1.25); };
    document.getElementById("zoom-out").onclick = function () { zoomCenter(0.8); };
    document.getElementById("zoom-fit").onclick = fit;
    document.getElementById("zoom-100").onclick = function () { zoomAt(1, viewport.clientWidth / 2, viewport.clientHeight / 2); };

    var find = document.getElementById("find-type");
    find.addEventListener("keydown", function (e) {
      if (e.key !== "Enter") return;
      var q = find.value.split(" — ")[0].trim().toLowerCase();
      if (!q) return;
      var hit = types.filter(function (t) { return t.name.toLowerCase() === q; })[0] ||
                types.filter(function (t) { return t.name.toLowerCase().indexOf(q) >= 0; })[0];
      if (hit) location.hash = "#type-" + hit.id;
    });
  }

  // ---------- tabs and links ----------

  var built = {};
  function showTab(name) {
    if (!built[name]) {
      built[name] = true;
      ({ overview: buildOverview, folders: buildFolders, rules: buildRules, tests: buildTests, diagram: buildDiagram })[name]();
    }
    TABS.forEach(function (t) { document.getElementById("tab-" + t).hidden = t !== name; });
    Array.prototype.forEach.call(document.querySelectorAll("#tabs a"), function (a) {
      if (a.dataset.tab === name) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
    document.getElementById("main").classList.toggle("wide", name === "diagram");
    document.getElementById("footer").hidden = name === "diagram";  // the diagram fills the window
    if (name === "diagram" && svg && !fitted) { fitted = true; fit(); }
  }

  function reveal(node, filter) {
    if (!node) return;
    var hiddenBy = node;
    while (hiddenBy && !hiddenBy.hidden) hiddenBy = hiddenBy.parentElement;
    if (hiddenBy && filter) filter.reset();
    for (var p = node; p; p = p.parentElement) if (p.tagName === "DETAILS") p.open = true;
    node.scrollIntoView({ block: "start" });
    var target = node.tagName === "DETAILS" ? node : node.closest("details") || node;
    target.classList.remove("flash"); void target.offsetWidth; target.classList.add("flash");
  }

  function route() {
    var hash = decodeURIComponent(location.hash.slice(1));
    if (!hash || TABS.indexOf(hash) >= 0) { showTab(hash || "overview"); window.scrollTo(0, 0); return; }
    var dash = hash.indexOf("-"), kind = hash.slice(0, dash), key = hash.slice(dash + 1);
    if (kind === "rule") { showTab("rules"); reveal(document.getElementById("rule-" + key), filters.rules); }
    else if (kind === "test") { showTab("tests"); reveal(document.getElementById("test-" + key), filters.tests); }
    else if (kind === "folder") { showTab("folders"); reveal(document.getElementById("folder-" + key), filters.folders); }
    else if (kind === "file") { showTab("folders"); reveal(document.getElementById("file-" + key), filters.folders); }
    else if (kind === "type") { showTab("diagram"); showType(key); }
    else if (hash === "run") { showTab("overview"); reveal(document.getElementById("run")); }
    else showTab("overview");
  }

  window.addEventListener("hashchange", route);
  document.addEventListener("keydown", function (e) {
    if (e.key !== "/" || /^(INPUT|TEXTAREA)$/.test(document.activeElement.tagName)) return;
    var open = TABS.filter(function (t) { return !document.getElementById("tab-" + t).hidden; })[0];
    var box = open === "diagram" ? document.getElementById("find-type") : filters[open] && filters[open].search;
    if (box) { e.preventDefault(); box.focus(); }
  });
  route();
})();
</script>
</body>
</html>
"""
