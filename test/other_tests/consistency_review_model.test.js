const test = require("node:test");
const assert = require("node:assert/strict");

const M = require("../../consistency_review_model");

function source(path, line, symbol) {
    return { kind: "source", path, symbol: symbol || null, line_start: line, line_end: line };
}

function finding(id, { category = "source_summary", path = "src/A.cs", status = "unresolved", title = "T" } = {}) {
    return {
        id,
        category,
        title,
        explanation: `Explanation of ${id}`,
        claims: [{ artifact: source(path, 7) }, { artifact: { kind: "summary", path } }],
        review: { status },
    };
}

function loaded(result, extra = {}) {
    return { success: true, review: { codebase: "Shop", scope: "full", available: true, result, ...extra } };
}

function result(fields = {}) {
    return {
        status: "completed", findings: [], artifacts_checked: [], assessments: [], skipped: [],
        undetermined: [], missing_artifacts: [], unresolved_links: [], errors: [], notes: [], ...fields,
    };
}

test("a completed check with no findings never claims the code is correct", () => {
    const state = M.describe(loaded(result({ artifacts_checked: [source("a.cs"), { kind: "summary" }, { kind: "unit_test" }] })));
    assert.equal(state.kind, "clean");
    assert.equal(state.title, "No mismatches found in assessed artifacts");
    assert.match(state.message, /compared 2 generated artifacts/);
    assert.match(state.message, /does not show that the code is correct/);
    assert.doesNotMatch(state.title + state.message, /verified|all tests pass/i);
});

test("an incomplete check is never presented as clean, with or without findings", () => {
    const empty = M.describe(loaded(result({ status: "partial", undetermined: [{}], errors: ["x"] })));
    assert.equal(empty.kind, "partial");
    assert.match(empty.title, /^Incomplete check/);
    assert.match(empty.message, /1 error, 1 undetermined comparison/);

    const some = M.describe(loaded(result({ status: "partial", findings: [finding("a")], missing_artifacts: [{}] })));
    assert.equal(some.title, "Incomplete check: 1 mismatch found so far");
});

test("failed, missing, unreadable and errored loads are told apart", () => {
    assert.equal(M.describe(null).kind, "loading");
    assert.equal(M.describe(loaded(result({ status: "failed", errors: ["boom"] }))).kind, "failed");
    assert.equal(M.describe({ success: true, review: { codebase: "Shop", scope: "selected", available: false,
        result: result({ status: "not_run" }) } }).kind, "no_check");
    assert.equal(M.describe({ success: true, review: { scope: "full", available: false, problem: "unreadable",
        error: "Could not read" } }).kind, "unreadable");
    const error = M.describe({ success: false, error: "Backend not running" });
    assert.equal(error.kind, "error");
    assert.equal(error.message, "Backend not running");
});

test("findings are described as disagreements, not as defects", () => {
    const state = M.describe(loaded(result({ findings: [finding("a"), finding("b")] })));
    assert.equal(state.title, "2 mismatches found");
    assert.match(state.message, /Which of the two is right is not known/);
});

test("a result from an earlier run is labelled as such", () => {
    const review = { run_relation: "earlier_run", result: { run_id: "r1" }, latest_run: { run_id: "r2" } };
    assert.equal(M.runLabel(review),
        "From an earlier run (r1). The most recent logged run of this codebase is r2; this result is not from it.");
    assert.match(M.runLabel({ run_relation: "same_run", result: { run_id: "r2" } }), /most recent logged run/);
    assert.match(M.runLabel({ run_relation: "no_run", result: {} }), /not as part of a pipeline run/);
    assert.equal(M.runLabel({ run_relation: "later_run", result: { run_id: "r3" }, latest_run: { run_id: "r2" } }),
        "From run r3, a check run on its own after the most recent logged run (r2). The artifacts it compared were generated earlier.");
    assert.match(M.runLabel({ run_relation: "unknown", result: { run_id: "r1" } }), /current is unknown/);
});

test("coverage counts compared artifacts, verdicts, skips and errors separately", () => {
    const cov = M.coverage(result({
        artifacts_checked: [source("a.cs"), { kind: "summary" }],
        assessments: [{ verdict: "consistent" }, { verdict: "inconsistent", accepted: false }],
        skipped: [{}, {}], undetermined: [{}], errors: ["e"], missing_artifacts: [{}], unresolved_links: [{}, {}],
    }));
    assert.deepEqual(cov, {
        compared: 1, sources: 1, assessed: 2, verdicts: { consistent: 1, inconsistent: 1 }, rejectedVerdicts: 1,
        skipped: 2, undetermined: 1, missing: 1, unresolved: 2, errors: 1,
    });
});

test("findings filter by file, category, review status and text", () => {
    const all = [
        finding("a", { path: "src/A.cs", category: "source_summary", title: "Boundary off by one" }),
        finding("b", { path: "src/B.cs", category: "source_test", status: "reviewed" }),
        finding("c", { path: "src/A.cs", category: "source_test", status: "dismissed" }),
    ];
    const ids = filters => M.filterFindings(all, filters).map(f => f.id);
    assert.deepEqual(ids({ file: "src/A.cs" }), ["a", "c"]);
    assert.deepEqual(ids({ category: "source_test" }), ["b", "c"]);
    assert.deepEqual(ids({ status: "unresolved" }), ["a"]);
    assert.deepEqual(ids({ text: "boundary" }), ["a"]);
    assert.deepEqual(ids({ text: "src/b" }), ["b"]);
    assert.deepEqual(M.filterOptions(all), { files: ["src/A.cs", "src/B.cs"], categories: ["source_summary", "source_test"] });
});

test("findings are paged and the page is clamped", () => {
    const items = Array.from({ length: 60 }, (_, i) => i);
    assert.deepEqual(M.paginate(items, 1).items, items.slice(0, 25));
    assert.equal(M.paginate(items, 3).items.length, 10);
    assert.equal(M.paginate(items, 9).page, 3);
    assert.equal(M.paginate([], 1).pages, 1);
});

test("an unsaved review is detected, ignoring blank notes", () => {
    assert.equal(M.isDirty({ status: "unresolved", note: null }, { status: "unresolved", note: "  " }), false);
    assert.equal(M.isDirty({ status: "unresolved", note: null }, { status: "reviewed", note: "" }), true);
    assert.equal(M.isDirty({ status: "reviewed", note: "ok" }, { status: "reviewed", note: "ok " }), false);
    assert.equal(M.isDirty(undefined, { status: "unresolved", note: "new" }), true);
});

test("locations name the file, symbol and lines", () => {
    assert.equal(M.location({ path: "src/A.cs", symbol: "A.M", line_start: 3, line_end: 9 }), "src/A.cs · A.M · lines 3–9");
    assert.equal(M.location({ path: "src/A.cs", line_start: 4, line_end: 4 }), "src/A.cs · line 4");
    assert.equal(M.sourceLocation({ claims: [] }), "Source location not recorded");
    assert.equal(M.handleOf({ kind: "business_rule", rule_id: 3 }), "Rule 3");
    assert.equal(M.handleOf({ kind: "unit_test", test_id: "UT_3", rule_id: 3 }), "UT_3");
});
