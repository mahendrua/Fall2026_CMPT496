const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const {
    applyGuiTestEvent,
    buildGuiReportHtml,
    createGuiTestReport,
    finalizeGuiTestReport,
    getGuiTestReport,
    isGuiTestReportEvent,
    listGuiTestReports,
    saveGuiTestReport
} = require("../guiTestReports");

function temporaryReportsDirectory() {
    return fs.mkdtempSync(path.join(os.tmpdir(), "checkpoint-gui-reports-"));
}

test("preserves assumptions and bounded PNG evidence while rejecting unsafe image URLs", () => {
    const report = createGuiTestReport();
    const screenshot = "data:image/png;base64,iVBORw0KGgo=";
    applyGuiTestEvent(report, { type: "test_finished", testId: "evidence", title: "counter", status: "failed",
        screenshot, assumptions: ["The increment is unspecified"], errors: [{ message: "Expected 1, received 2" }],
        diagnostics: { category: "assertion_mismatch" } });
    const html = buildGuiReportHtml(report);
    assert.match(html, /Unconfirmed assumptions/);
    assert.ok(html.includes(screenshot));
    assert.match(html, /Expected 1, received 2/);
    applyGuiTestEvent(report, { type: "test_finished", testId: "unsafe", title: "unsafe", status: "failed",
        screenshot: 'javascript:alert(1)' });
    assert.equal(report.tests[1].screenshot, null);
});

test("builds factual pass, failure, and skipped summaries with evidence-based explanations", () => {
    const report = createGuiTestReport({
        appName: "TodoMVC",
        framework: "Svelte",
        testFiles: ["add.spec.js"]
    });
    applyGuiTestEvent(report, { type: "run_started", total: 3 });
    applyGuiTestEvent(report, {
        type: "test_started",
        testId: "pass",
        title: "adds a task",
        purpose: "Confirm a task can be added.",
        expectedResult: "The task appears in the todo list."
    });
    applyGuiTestEvent(report, {
        type: "test_finished",
        testId: "pass",
        title: "adds a task",
        status: "passed",
        duration: 210,
        purpose: "Confirm a task can be added.",
        expectedResult: "The task appears in the todo list."
    });
    applyGuiTestEvent(report, {
        type: "test_finished",
        testId: "failed",
        title: "connects to app",
        status: "failed",
        duration: 300,
        errors: [{ message: "page.goto: net::ERR_CONNECTION_REFUSED" }],
        diagnostics: { category: "connection_failure", summary: "Could not connect." }
    });
    applyGuiTestEvent(report, {
        type: "test_finished",
        testId: "skip",
        title: "optional flow",
        status: "skipped",
        duration: 0
    });
    finalizeGuiTestReport(report, "failed", null, new Date(Date.parse(report.startedAt) + 2500));

    assert.equal(report.appName, "TodoMVC");
    assert.equal(report.framework, "Svelte");
    assert.equal(report.browser, "Chromium");
    assert.equal(report.total, 3);
    assert.equal(report.passed, 1);
    assert.equal(report.failed, 1);
    assert.equal(report.skipped, 1);
    assert.equal(report.passPercentage, 33.3);
    assert.equal(report.durationMs, 2500);
    assert.match(report.tests[0].actualResult, /assertions passed/);
    assert.equal(report.tests[0].purpose, "Confirm a task can be added.");
    assert.equal(report.tests[1].failureExplanation.category, "startup_or_connection_issue");
    assert.equal(report.tests[1].failureExplanation.confidence, "High");
    assert.equal(report.tests[2].purpose, null);
    assert.equal(report.tests[2].expectedResult, null);
});

test("calculates a fully passing run", () => {
    const report = createGuiTestReport();
    applyGuiTestEvent(report, { type: "test_finished", testId: "one", title: "first", status: "passed", duration: 10 });
    applyGuiTestEvent(report, { type: "test_finished", testId: "two", title: "second", status: "passed", duration: 20 });
    finalizeGuiTestReport(report, "passed");

    assert.equal(report.total, 2);
    assert.equal(report.passed, 2);
    assert.equal(report.failed, 0);
    assert.equal(report.passPercentage, 100);
});

test("accepts reporter lifecycle events and produces the completed mixed-run totals", () => {
    const report = createGuiTestReport();
    const events = [
        { type: "run_started", total: 4 },
        ...[1, 2, 3].map(testId => ({
            type: "test_finished",
            testId: `passed-${testId}`,
            title: `passed test ${testId}`,
            status: "passed"
        })),
        {
            type: "test_finished",
            testId: "failed",
            title: "failed test",
            status: "failed",
            errors: [{ message: "Assertion failed." }]
        }
    ];

    for (const event of events) {
        assert.equal(isGuiTestReportEvent(event), true);
        applyGuiTestEvent(report, event);
    }
    applyGuiTestEvent(report, { type: "run_complete", status: "failed", total: 4, passed: 3, failed: 1 });
    finalizeGuiTestReport(report, "failed");

    assert.equal(report.plannedTests, 4);
    assert.equal(report.total, 4);
    assert.equal(report.passed, 3);
    assert.equal(report.failed, 1);
    assert.equal(report.tests.length, 4);
    assert.equal(isGuiTestReportEvent({ type: "runner_container_exit" }), true);
});

test("calculates a fully failing run without attributing unsupported causes", () => {
    const report = createGuiTestReport();
    for (const testId of ["one", "two"]) {
        applyGuiTestEvent(report, {
            type: "test_finished",
            testId,
            title: `failed ${testId}`,
            status: "failed",
            duration: 30,
            errors: [{ message: "Assertion failed with no further context." }],
            diagnostics: { category: "unknown", summary: "Unknown failure." }
        });
    }
    finalizeGuiTestReport(report, "failed");

    assert.equal(report.total, 2);
    assert.equal(report.passed, 0);
    assert.equal(report.failed, 2);
    assert.equal(report.passPercentage, 0);
    assert.equal(report.tests.every(test => test.failureExplanation.category === "unknown"), true);
    assert.equal(report.tests.every(test => test.failureExplanation.confidence === "Low"), true);
});

test("preserves partial results and marks unfinished runs interrupted after restart", () => {
    const directory = temporaryReportsDirectory();
    try {
        const report = createGuiTestReport({ appName: "Interrupted app" });
        applyGuiTestEvent(report, { type: "run_started", total: 4 });
        applyGuiTestEvent(report, {
            type: "test_started",
            testId: "active",
            title: "unfinished test"
        });
        saveGuiTestReport(directory, report);

        const loaded = listGuiTestReports(directory)[0];
        assert.equal(loaded.status, "interrupted");
        assert.equal(loaded.total, 1);
        assert.equal(loaded.interrupted, 1);
        assert.equal(loaded.tests[0].actualResult, "The run ended before Playwright reported a result for this test.");
        assert.equal(getGuiTestReport(directory, report.id).status, "interrupted");
    } finally {
        fs.rmSync(directory, { recursive: true, force: true });
    }
});

test("reports startup failures and escapes report content for PDF output", () => {
    const report = createGuiTestReport({ appName: "<script>alert(1)</script>" });
    applyGuiTestEvent(report, {
        type: "runner_error",
        message: "Application startup failed: port unavailable"
    });
    finalizeGuiTestReport(report, "startup_failed");
    const html = buildGuiReportHtml(report);

    assert.equal(report.errors.length, 1);
    assert.match(html, /&lt;script&gt;/);
    assert.doesNotMatch(html, /<script>alert/);
    assert.match(html, /startup_failed/);
});

test("keeps separate report files for separate runs", () => {
    const directory = temporaryReportsDirectory();
    try {
        const first = createGuiTestReport({ appName: "TodoMVC" });
        const second = createGuiTestReport({ appName: "TodoMVC" });
        saveGuiTestReport(directory, first);
        saveGuiTestReport(directory, second);
        assert.equal(listGuiTestReports(directory).length, 2);
        assert.notEqual(first.id, second.id);
    } finally {
        fs.rmSync(directory, { recursive: true, force: true });
    }
});
