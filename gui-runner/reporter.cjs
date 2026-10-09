function send(type, data = {}) {
    process.stdout.write(`GUI_EVENT:${JSON.stringify({ type, ...data })}\n`);
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
        send("test_started", {
            testId: test.id,
            title: test.titlePath().filter(Boolean).join(" › "),
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
        send("test_finished", {
            testId: test.id,
            title: test.titlePath().filter(Boolean).join(" › "),
            file: test.location.file.split(/[\\/]/).pop(),
            status,
            duration: result.duration,
            errors,
            output
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
