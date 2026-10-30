function send(type, data = {}) {
    process.stdout.write(`GUI_EVENT:${JSON.stringify({ type, ...data })}\n`);
}

function diagnoseFailure(errors, output) {
    const errorText = (errors || []).map(error => error.message || "").join("\n");
    const outputText = (output || []).map(item => item.text || "").join("\n");
    const combined = `${errorText}\n${outputText}`.replace(/\u001b\[[0-?]*[ -/]*[@-~]/g, "");
    const locator = combined.match(/Locator:\s+([^\r\n]+)/)?.[1]
        || combined.match(/waiting for (locator\([^)]*\)[^\r\n]*)/)?.[1]
        || null;

    if (/APP_PAGE_ERROR:/.test(combined)) {
        return {
            category: "application_runtime_error",
            locator,
            summary: "The page reported an uncaught application error during this test."
        };
    }
    if (/net::ERR_(?:CONNECTION|NAME|ADDRESS|TUNNEL)|ECONNREFUSED|ENOTFOUND|EAI_AGAIN|proxy connection/i.test(combined)) {
        return {
            category: "connection_failure",
            locator,
            summary: "The browser could not connect to the app or a requested network destination."
        };
    }
    if (
        /locator resolved to <[^>]+>/i.test(combined)
        && /intercepts pointer events|not visible|not enabled|not stable/i.test(combined)
    ) {
        return {
            category: "actionability_failure",
            locator,
            summary: "The locator matched an element, but Playwright could not interact with it. This is not evidence of an incorrect selector."
        };
    }
    if (/locator resolved to 0 elements|waiting for locator|waiting for getBy(?:Role|Label|Text|Placeholder)/i.test(combined)) {
        return {
            category: "locator_timeout",
            locator,
            summary: "A locator did not resolve in time. This may be a selector mismatch, missing test precondition, or timing issue; the cause is not confirmed."
        };
    }
    if (/expect\(/i.test(combined) && /failed|Timeout/i.test(combined)) {
        return {
            category: "assertion_mismatch",
            locator,
            summary: "An expectation did not match the observed application state; this may indicate an app behavior issue or an unsupported test assumption."
        };
    }
    return {
        category: "unknown",
        locator,
        summary: "The test failed, but the available error does not identify the cause."
    };
}

class GuiReporter {
    constructor() {
        this.total = 0;
        this.passed = 0;
        this.failed = 0;
        this.skipped = 0;
    }

    onBegin(config, suite) {
        this.total = suite.allTests().length;
        send("run_started", { total: this.total });
    }

    onTestBegin(test) {
        const annotations = Object.fromEntries(
            (test.annotations || []).map(annotation => [annotation.type, annotation.description || ""])
        );
        send("test_started", {
            testId: test.id,
            title: test.title,
            purpose: annotations.purpose || null,
            expectedResult: annotations.expected || null,
            file: test.location.file.split(/[\\/]/).pop(),
            line: test.location.line
        });
    }

    onTestEnd(test, result) {
        const passed = result.status === test.expectedStatus;
        const status = result.status === "skipped"
            ? "skipped"
            : passed ? "passed" : "failed";

        if (status === "passed") this.passed += 1;
        else if (status === "skipped") this.skipped += 1;
        else this.failed += 1;

        const errors = (result.errors || []).map(error => ({
            message: String(error.message || "Test failed").slice(0, 8000),
            stack: String(error.stack || "").slice(0, 12000)
        }));
        const output = [
            ...(result.stdout || []).map(chunk => ({ stream: "stdout", text: String(chunk).slice(0, 8000) })),
            ...(result.stderr || []).map(chunk => ({ stream: "stderr", text: String(chunk).slice(0, 8000) }))
        ];
        const diagnostics = status === "failed" ? diagnoseFailure(errors, output) : null;
        const annotations = Object.fromEntries(
            [...(test.annotations || []), ...(result.annotations || [])]
                .map(annotation => [annotation.type, annotation.description || ""])
        );
        send("test_finished", {
            testId: test.id,
            title: test.title,
            purpose: annotations.purpose || null,
            expectedResult: annotations.expected || null,
            file: test.location.file.split(/[\\/]/).pop(),
            status,
            duration: result.duration,
            errors,
            output,
            diagnostics
        });
    }

    onError(error) {
        send("test_error", {
            message: String(error.message || error).slice(0, 8000),
            stack: String(error.stack || "").slice(0, 12000)
        });
    }

    onEnd(result) {
        send("run_complete", {
            status: result.status,
            total: this.total,
            passed: this.passed,
            failed: this.failed,
            skipped: this.skipped
        });
    }
}

module.exports = GuiReporter;
module.exports.diagnoseFailure = diagnoseFailure;
