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

    if (/browser.*disconnected|browser.*closed|Target.*closed|page crashed|browser.*crash/i.test(combined)) {
        return { category: "browser_infrastructure_failure", locator,
            summary: "The browser or page closed unexpectedly; investigate a crash, resource exhaustion, or test-initiated closure before blaming the application." };
    }
    if (/strict mode violation/i.test(combined)) {
        return { category: "selector_ambiguity", locator,
            summary: "The locator matched multiple elements. Verify its accessible name and scope against the interface." };
    }
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
    if (/page\.goto:.*(?:Timeout|ERR_|Invalid URL)|Cannot navigate to invalid URL/i.test(combined)) {
        return { category: "navigation_failure", locator,
            summary: "Navigation failed. Check BASE_URL, application readiness, route and network configuration before attributing an application defect." };
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
    if (/expect\(/i.test(combined) && /locator resolved to (?:<|[1-9])/i.test(combined)) {
        return { category: "assertion_mismatch", locator,
            summary: "The locator resolved, but the observed state did not satisfy the expectation. Verify the requirement before attributing an application defect." };
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
        let screenshot = null;
        for (const attachment of result.attachments || []) {
            if (attachment.contentType !== "image/png") continue;
            try {
                const fs = require("node:fs");
                if (attachment.path && fs.statSync(attachment.path).size > 256 * 1024) continue;
                const data = attachment.body || fs.readFileSync(attachment.path);
                if (data.length <= 256 * 1024) screenshot = `data:image/png;base64,${data.toString("base64")}`;
            } catch { /* Missing evidence must not interrupt reporting. */ }
            if (screenshot) break;
        }
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
            screenshot,
            assumptions: [...(test.annotations || []), ...(result.annotations || [])]
                .filter(annotation => annotation.type === "assumption").map(annotation => annotation.description),
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
