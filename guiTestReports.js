const fs = require("fs");
const path = require("path");
const { randomUUID } = require("crypto");

function createGuiTestReport({
    appName = "Application not specified",
    appDirectory = null,
    framework = "Not specified",
    browser = "Chromium",
    testFiles = [],
    startedAt = new Date().toISOString()
} = {}) {
    return {
        schemaVersion: 1,
        id: randomUUID(),
        appName,
        appDirectory,
        framework,
        browser,
        testFiles: testFiles.map(file => path.basename(String(file))),
        startedAt,
        finishedAt: null,
        status: "running",
        plannedTests: 0,
        total: 0,
        passed: 0,
        failed: 0,
        skipped: 0,
        interrupted: 0,
        passPercentage: 0,
        durationMs: 0,
        tests: [],
        errors: []
    };
}

function getFailureExplanation(test) {
    const diagnostic = test.diagnostics || {};
    const errorMessage = test.errors?.map(error => error.message).filter(Boolean).join("\n") || "";
    const evidence = `${diagnostic.summary || ""}\n${errorMessage}`;
    const category = diagnostic.category;

    if (category === "browser_infrastructure_failure") {
        return { category: "infrastructure_issue", likelyCause: diagnostic.summary,
            confidence: "Medium", investigation: "Check browser logs, Docker exit status and resource limits, and whether the test explicitly closed its page or browser." };
    }
    if (category === "selector_ambiguity") {
        return { category: "selector_or_timing_issue", likelyCause: diagnostic.summary,
            confidence: "High", investigation: "Verify accessible names and scope the locator to the intended control." };
    }

    if (category === "connection_failure" || category === "navigation_failure" || /ERR_CONNECTION_REFUSED|ECONNREFUSED|proxy connection/i.test(evidence)) {
        return {
            category: "startup_or_connection_issue",
            likelyCause: "The browser could not connect to the application or its test endpoint.",
            confidence: "High",
            investigation: "Check the local app startup log, selected port, Docker-to-host connectivity, and allowed network destinations."
        };
    }
    if (category === "application_runtime_error" || /APP_PAGE_ERROR:/.test(evidence)) {
        return {
            category: "application_bug",
            likelyCause: "An uncaught application error was recorded while the test ran.",
            confidence: "Medium",
            investigation: "Inspect the recorded page error and reproduce the same interaction in the app. The error is evidence of a runtime failure, but does not by itself establish its root cause."
        };
    }
    if (category === "actionability_failure") {
        return {
            category: "selector_or_timing_issue",
            likelyCause: "The locator matched an element, but Playwright could not interact with it because it was obstructed or not actionable.",
            confidence: "High",
            investigation: "Inspect the matched element and visible associated controls; confirm the test interacts with the same control a user can reach."
        };
    }
    if (category === "locator_timeout") {
        return {
            category: "selector_or_timing_issue",
            likelyCause: "A locator did not resolve before the timeout. Available evidence cannot distinguish a wrong selector from missing setup or timing.",
            confidence: "Low",
            investigation: "Compare the locator with the rendered page and source, verify preceding setup actions, and check whether the control appears asynchronously."
        };
    }
    if (category === "assertion_mismatch") {
        return {
            category: "unknown",
            likelyCause: "An assertion did not match the observed state; evidence does not establish whether the expectation or application behavior is wrong.",
            confidence: "Low",
            investigation: "Compare the failed expectation with the intended behavior and inspect the application state captured at failure."
        };
    }
    return {
        category: "unknown",
        likelyCause: "Available execution evidence is insufficient to identify the cause.",
        confidence: "Low",
        investigation: "Review the Playwright error, execution output, app startup logs, and reproduce the test before attributing the failure."
    };
}

function updateReportCounts(report) {
    report.passed = report.tests.filter(test => test.status === "passed").length;
    report.failed = report.tests.filter(test => test.status === "failed").length;
    report.skipped = report.tests.filter(test => test.status === "skipped").length;
    report.interrupted = report.tests.filter(test => test.status === "interrupted").length;
    report.total = report.tests.length;
    report.passPercentage = report.total
        ? Math.round((report.passed / report.total) * 1000) / 10
        : 0;
}

function isGuiTestReportEvent(event) {
    return Boolean(
        event &&
        typeof event === "object" &&
        ["run_started", "test_started", "test_finished", "test_error", "runner_error", "run_complete", "runner_container_exit"].includes(event.type)
    );
}

function applyGuiTestEvent(report, event) {
    if (!isGuiTestReportEvent(event)) return;

    if (event.type === "run_started") {
        report.plannedTests = Number(event.total) || 0;
    } else if (event.type === "test_started") {
        if (!report.tests.some(test => test.testId === event.testId)) {
            report.tests.push({
                testId: event.testId,
                name: event.title || "Untitled test",
                file: event.file || null,
                purpose: event.purpose || null,
                expectedResult: event.expectedResult || null,
                status: "running",
                startedAt: new Date().toISOString(),
                durationMs: null,
                actualResult: "Test is running.",
                errors: [],
                output: [],
                diagnostics: null
            });
        }
    } else if (event.type === "test_finished") {
        let test = report.tests.find(item => item.testId === event.testId);
        if (!test) {
            test = {
                testId: event.testId,
                name: event.title || "Untitled test",
                file: event.file || null,
                purpose: event.purpose || null,
                expectedResult: event.expectedResult || null
            };
            report.tests.push(test);
        }
        test.name = event.title || test.name;
        test.file = event.file || test.file;
        test.purpose = event.purpose || test.purpose || null;
        test.expectedResult = event.expectedResult || test.expectedResult || null;
        test.status = event.status || "failed";
        test.durationMs = Number(event.duration) || 0;
        test.actualResult = test.status === "passed"
            ? "The test completed and all Playwright assertions passed."
            : test.status === "skipped"
                ? "Playwright skipped this test."
                : (event.errors || []).map(error => error.message).filter(Boolean)[0]
                    || "The test failed; no error message was provided.";
        test.errors = Array.isArray(event.errors) ? event.errors : [];
        test.output = Array.isArray(event.output) ? event.output : [];
        test.diagnostics = event.diagnostics || null;
        test.assumptions = Array.isArray(event.assumptions) ? event.assumptions.map(String) : [];
        test.screenshot = typeof event.screenshot === "string" && event.screenshot.length <= 350000
            && /^data:image\/png;base64,[A-Za-z0-9+/=]+$/.test(event.screenshot) ? event.screenshot : null;
        if (test.status === "failed") test.failureExplanation = getFailureExplanation(test);
    } else if (event.type === "test_error" || event.type === "runner_error") {
        report.errors.push({
            message: String(event.message || "GUI test runner error"),
            stack: event.stack ? String(event.stack) : null,
            recordedAt: new Date().toISOString()
        });
    }

    updateReportCounts(report);
}

function finalizeGuiTestReport(report, status = "completed", message = null, now = new Date()) {
    if (report.status !== "running") return report;

    if (message) {
        report.errors.push({
            message: String(message),
            stack: null,
            recordedAt: now.toISOString()
        });
    }
    for (const test of report.tests) {
        if (test.status === "running") {
            test.status = "interrupted";
            test.actualResult = "The run ended before Playwright reported a result for this test.";
        }
    }
    report.status = status;
    report.finishedAt = now.toISOString();
    report.durationMs = Math.max(0, Date.parse(report.finishedAt) - Date.parse(report.startedAt));
    updateReportCounts(report);
    return report;
}

function saveGuiTestReport(directory, report) {
    fs.mkdirSync(directory, { recursive: true });
    const filePath = path.join(directory, `${report.id}.json`);
    const temporaryPath = `${filePath}.tmp`;
    fs.writeFileSync(temporaryPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
    fs.renameSync(temporaryPath, filePath);
    return filePath;
}

function listGuiTestReports(directory, activeReportId = null) {
    if (!fs.existsSync(directory)) return [];
    return fs.readdirSync(directory)
        .filter(name => /^[0-9a-f-]+\.json$/i.test(name))
        .map(name => {
            const filePath = path.join(directory, name);
            let report;
            try {
                report = JSON.parse(fs.readFileSync(filePath, "utf8"));
            } catch (error) {
                throw new Error(`Could not read GUI report "${name}": ${error.message}`);
            }
            if (report.status === "running" && report.id !== activeReportId) {
                finalizeGuiTestReport(report, "interrupted", "Checkpoint closed before this GUI test run finished.");
                saveGuiTestReport(directory, report);
            }
            return report;
        })
        .sort((left, right) => String(right.startedAt).localeCompare(String(left.startedAt)));
}

function getGuiTestReport(directory, id, activeReportId = null) {
    if (!/^[0-9a-f-]{36}$/i.test(String(id || ""))) {
        throw new Error("The GUI report id is invalid.");
    }
    const filePath = path.join(directory, `${id}.json`);
    let report;
    try {
        report = JSON.parse(fs.readFileSync(filePath, "utf8"));
    } catch (error) {
        throw new Error(`Could not load GUI report: ${error.message}`);
    }
    if (report.status === "running" && report.id !== activeReportId) {
        finalizeGuiTestReport(report, "interrupted", "Checkpoint closed before this GUI test run finished.");
        saveGuiTestReport(directory, report);
    }
    return report;
}

function escapeHtml(value) {
    return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

function formatDuration(milliseconds) {
    const seconds = Math.max(0, Number(milliseconds) || 0) / 1000;
    return seconds < 60
        ? `${seconds.toFixed(1)} s`
        : `${Math.floor(seconds / 60)} min ${(seconds % 60).toFixed(0)} s`;
}

function buildGuiReportHtml(report) {
    const tests = (report.tests || []).map(test => {
        const failure = test.failureExplanation;
        const evidence = (test.errors || []).map(error => escapeHtml(error.message)).join("<br>");
        return `<article class="test">
            <h2>${escapeHtml(test.name)} <span class="status">${escapeHtml(test.status)}</span></h2>
            <p><b>Purpose:</b> ${escapeHtml(test.purpose || "Not provided by the test.")}</p>
            <p><b>Expected:</b> ${escapeHtml(test.expectedResult || "Not provided by the test.")}</p>
            ${test.assumptions?.length ? `<p><b>Unconfirmed assumptions:</b> ${test.assumptions.map(escapeHtml).join("; ")}</p>` : ""}
            <p><b>Actual:</b> ${escapeHtml(test.actualResult || "No result was reported.")}</p>
            ${test.screenshot ? `<img alt="Playwright failure screenshot" style="max-width:100%" src="${escapeHtml(test.screenshot)}">` : ""}
            <p><b>Duration:</b> ${escapeHtml(formatDuration(test.durationMs))}</p>
            ${failure ? `<p><b>Likely cause:</b> ${escapeHtml(failure.likelyCause)} (${escapeHtml(failure.confidence)} confidence)</p><p><b>Investigation:</b> ${escapeHtml(failure.investigation)}</p>` : ""}
            ${evidence ? `<p><b>Execution evidence:</b><br>${evidence}</p>` : ""}
        </article>`;
    }).join("\n");
    const date = report.startedAt ? new Date(report.startedAt).toLocaleString() : "Unknown";
    return `<!doctype html><html><head><meta charset="utf-8"><title>GUI Testing Report</title>
        <style>
        body{font:14px Arial,sans-serif;color:#182230;margin:32px}
        h1{margin-bottom:4px;color:#243b65}h2{font-size:17px}
        .summary{background:#f1f5fb;padding:14px;border-radius:8px;margin:18px 0}
        .test{border:1px solid #d6deea;border-radius:8px;padding:12px 16px;margin:12px 0;page-break-inside:avoid}
        .status{float:right;text-transform:uppercase;font-size:12px}
        p{line-height:1.45;margin:8px 0}
        </style></head><body>
        <h1>GUI Testing Report</h1>
        <div>${escapeHtml(report.appName)} · ${escapeHtml(date)}</div>
        <section class="summary">
        <b>Status:</b> ${escapeHtml(report.status)} · <b>Total:</b> ${report.total} ·
        <b>Passed:</b> ${report.passed} · <b>Failed:</b> ${report.failed} · <b>Skipped:</b> ${report.skipped}<br>
        <b>Pass rate:</b> ${report.passPercentage}% · <b>Duration:</b> ${escapeHtml(formatDuration(report.durationMs))}<br>
        <b>Framework:</b> ${escapeHtml(report.framework)} · <b>Browser:</b> ${escapeHtml(report.browser)}
        </section>${tests}</body></html>`;
}

module.exports = {
    applyGuiTestEvent,
    buildGuiReportHtml,
    createGuiTestReport,
    finalizeGuiTestReport,
    getFailureExplanation,
    getGuiTestReport,
    isGuiTestReportEvent,
    listGuiTestReports,
    saveGuiTestReport
};
