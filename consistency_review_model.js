/*
 * consistency_review_model.js
 *
 * The Consistency Review screen's logic, without the DOM: what state a saved
 * result is in, how to describe it honestly, coverage counts, filtering and
 * paging of findings, and unsaved-review detection. Loaded by index.html
 * before consistency_review.js, and by node --test.
 */
(function (root, factory) {
    const api = factory();

    if (typeof module !== "undefined" && module.exports) {
        module.exports = api;
    } else {
        root.ConsistencyReviewModel = api;
    }
})(globalThis, function () {
    const PAGE_SIZE = 25;

    const CATEGORY_LABELS = {
        source_summary: "Source vs summary",
        source_rule: "Source vs business rule",
        source_test: "Source vs unit test",
        artifact_artifact: "Between generated artifacts",
        implementation_requirement: "Implementation vs confirmed requirement",
    };

    const REVIEW_LABELS = {
        unresolved: "Unresolved",
        reviewed: "Reviewed",
        dismissed: "Dismissed",
    };

    const SCOPE_LABELS = {
        full: "Whole project",
        selected: "Selected files",
    };

    const VERDICT_LABELS = {
        consistent: "Consistent",
        inconsistent: "Inconsistent",
        not_applicable: "Not applicable",
        insufficient_evidence: "Insufficient evidence",
    };

    const ORIGIN_LABELS = {
        code_inspection: "Inferred by reading the code (not executed)",
        execution: "Observed by running a test",
        human_confirmation: "Confirmed by a person",
    };

    const KIND_LABELS = {
        source: "Source",
        summary: "Summary",
        business_rule: "Business rule",
        unit_test: "Unit test",
        requirement: "Requirement",
    };

    function plural(count, one, many) {
        return `${count} ${count === 1 ? one : (many || one + "s")}`;
    }

    function handleOf(ref) {
        if (!ref) return "";
        if (ref.kind === "summary") return "Summary";
        if (ref.kind === "business_rule") return ref.rule_id == null ? "Business rules" : `Rule ${ref.rule_id}`;
        if (ref.kind === "unit_test") return ref.test_id || (ref.rule_id == null ? "Unit tests" : `Test for rule ${ref.rule_id}`);
        return KIND_LABELS[ref.kind] || ref.kind;
    }

    function location(ref) {
        if (!ref) return "";
        const place = ref.path || ref.directory || ref.artifact_file || "";
        const symbol = ref.symbol ? ` · ${ref.symbol}` : "";
        let lines = "";
        if (ref.line_start) {
            lines = ref.line_end && ref.line_end !== ref.line_start
                ? ` · lines ${ref.line_start}–${ref.line_end}`
                : ` · line ${ref.line_start}`;
        }
        return `${place}${symbol}${lines}`;
    }

    function sourceOf(finding) {
        const claim = (finding.claims || []).find(c => c.artifact && c.artifact.kind === "source");
        return claim ? claim.artifact : null;
    }

    function sourceLocation(finding) {
        return location(sourceOf(finding)) || "Source location not recorded";
    }

    function coverage(result) {
        const r = result || {};
        const verdicts = {};
        (r.assessments || []).forEach(a => {
            verdicts[a.verdict] = (verdicts[a.verdict] || 0) + 1;
        });
        return {
            compared: (r.artifacts_checked || []).filter(ref => ref.kind !== "source").length,
            sources: (r.artifacts_checked || []).filter(ref => ref.kind === "source").length,
            assessed: (r.assessments || []).length,
            verdicts,
            rejectedVerdicts: (r.assessments || []).filter(a => a.accepted === false).length,
            skipped: (r.skipped || []).length,
            undetermined: (r.undetermined || []).length,
            missing: (r.missing_artifacts || []).length,
            unresolved: (r.unresolved_links || []).length,
            errors: (r.errors || []).length,
        };
    }

    function reviewCounts(findings) {
        const counts = { unresolved: 0, reviewed: 0, dismissed: 0 };
        (findings || []).forEach(f => {
            const status = (f.review && f.review.status) || "unresolved";
            counts[status] = (counts[status] || 0) + 1;
        });
        return counts;
    }

    /*
     * The state of a loaded result, with an honest headline. A clean headline
     * is only possible for a completed check, and even then it describes the
     * artifacts that were compared, never the code itself.
     */
    function describe(response) {
        if (!response) {
            return { kind: "loading", title: "Loading saved check…", message: "" };
        }
        if (response.success === false) {
            return {
                kind: "error",
                title: "Could not load the saved check",
                message: response.error || "The backend did not return a result.",
            };
        }

        const review = response.review || {};
        const scope = SCOPE_LABELS[review.scope] || review.scope;

        if (review.problem === "unreadable") {
            return {
                kind: "unreadable",
                title: "The saved check could not be read",
                message: `${review.error || "The result file is damaged or in an unknown format."} Nothing from it is shown.`,
            };
        }

        const result = review.result || {};
        const findings = result.findings || [];
        const cov = coverage(result);

        if (!review.available || result.status === "not_run") {
            return {
                kind: "no_check",
                title: `No saved check (${scope})`,
                message: `No consistency check of this scope has been saved for ${review.codebase}. `
                    + "Use New check to run one.",
            };
        }

        if (result.status === "failed") {
            return {
                kind: "failed",
                title: "The check failed",
                message: `${plural(cov.errors, "error")} stopped the check. No conclusion can be drawn about the artifacts, `
                    + "including those not listed here.",
            };
        }

        if (result.status === "partial") {
            const reasons = [];
            if (cov.errors) reasons.push(plural(cov.errors, "error"));
            if (cov.missing) reasons.push(plural(cov.missing, "missing artifact"));
            if (cov.unresolved) reasons.push(plural(cov.unresolved, "unresolved link"));
            if (cov.undetermined) reasons.push(plural(cov.undetermined, "undetermined comparison"));
            return {
                kind: "partial",
                title: findings.length
                    ? `Incomplete check: ${plural(findings.length, "mismatch", "mismatches")} found so far`
                    : "Incomplete check: no mismatches found in what was assessed",
                message: `Some artifacts were not compared (${reasons.join(", ")}). `
                    + "Mismatches in those artifacts would not appear here.",
            };
        }

        if (!findings.length) {
            return {
                kind: "clean",
                title: "No mismatches found in assessed artifacts",
                message: `The checker compared ${plural(cov.compared, "generated artifact")} with the code and found no `
                    + "disagreement. This does not show that the code is correct or that its tests pass.",
            };
        }

        return {
            kind: "findings",
            title: `${plural(findings.length, "mismatch", "mismatches")} found`,
            message: "Each one is a disagreement between the code and a generated artifact. "
                + "Which of the two is right is not known.",
        };
    }

    /* Which run a result came from, said plainly. */
    function runLabel(review) {
        const result = (review && review.result) || {};
        const latest = review && review.latest_run;
        switch (review && review.run_relation) {
            case "same_run":
                // A check run from the app is a run of its own; the artifacts it compared are older.
                if (latest && latest.command === "check_consistency") {
                    return `From run ${result.run_id}, a consistency check run on its own (the most recent logged run of `
                        + "this codebase). The artifacts it compared were generated earlier.";
                }
                return `From run ${result.run_id}, the most recent logged run of this codebase.`;
            case "earlier_run":
                return `From an earlier run (${result.run_id}). The most recent logged run of this codebase is `
                    + `${latest.run_id}; this result is not from it.`;
            case "later_run":
                return `From run ${result.run_id}, a check run on its own after the most recent logged run (`
                    + `${latest.run_id}). The artifacts it compared were generated earlier.`;
            case "no_run":
                return "Run on its own, not as part of a pipeline run.";
            case "unknown":
                return `From run ${result.run_id}. The most recent run of this codebase is not logged, `
                    + "so whether this result is current is unknown.";
            default:
                return "";
        }
    }

    function filterOptions(findings) {
        const files = new Set();
        const categories = new Set();
        (findings || []).forEach(f => {
            const src = sourceOf(f);
            if (src && src.path) files.add(src.path);
            categories.add(f.category);
        });
        return { files: [...files].sort(), categories: [...categories].sort() };
    }

    function filterFindings(findings, filters) {
        const f = filters || {};
        const text = (f.text || "").trim().toLowerCase();
        return (findings || []).filter(finding => {
            const src = sourceOf(finding);
            const status = (finding.review && finding.review.status) || "unresolved";
            if (f.file && (!src || src.path !== f.file)) return false;
            if (f.category && finding.category !== f.category) return false;
            if (f.status && status !== f.status) return false;
            if (text) {
                const haystack = [finding.title, finding.explanation, src && src.path, src && src.symbol]
                    .filter(Boolean).join(" ").toLowerCase();
                if (!haystack.includes(text)) return false;
            }
            return true;
        });
    }

    function paginate(items, page, size) {
        const per = size || PAGE_SIZE;
        const pages = Math.max(1, Math.ceil((items || []).length / per));
        const current = Math.min(Math.max(1, page || 1), pages);
        return {
            items: (items || []).slice((current - 1) * per, current * per),
            page: current,
            pages,
            total: (items || []).length,
        };
    }

    const SUMMARY_STATES = {
        resolved: "summary",
        missing: "no summary",
        ambiguous: "two summaries (not compared)",
        stale: "summary out of date",
        unverified: "summary unreadable",
    };

    /* Plain description of what was generated for a file, for choosing what to check. */
    function artifactsText(file) {
        const parts = [SUMMARY_STATES[file.summary] || "summary " + file.summary];
        parts.push(plural(file.rules, "rule"));
        parts.push(plural(file.tests, "test"));
        return parts.join(" · ");
    }

    /* Files a model request would be made for: a usable summary or at least one rule. */
    function comparable(file) {
        return file.summary === "resolved" || file.rules > 0;
    }

    /*
     * The check request the panel describes, or an error saying why it
     * cannot start. selected maps a file path to "" (whole file) or a method.
     */
    function checkRequest(panel) {
        if (panel.scope === "full") return { scope: "full", targets: [] };
        const targets = [...panel.selected.entries()].map(([path, symbol]) => (symbol ? { path, symbol } : path));
        if (!targets.length) return { error: "Select at least one file to check." };
        return { scope: "selected", targets };
    }

    /* What will be checked and at most how many model requests it can make. */
    function describeCheck(request, files) {
        const byPath = new Map((files || []).map(f => [f.path, f]));
        if (request.scope === "full") {
            const all = files || [];
            const count = all.filter(comparable).length;
            return {
                scope: `The whole codebase: ${plural(all.length, "file")} with generated artifacts.`,
                items: [],
                requests: count,
            };
        }
        const items = request.targets.map(t => {
            const path = typeof t === "string" ? t : t.path;
            const what = typeof t === "string" ? "whole file" : `method ${t.symbol}`;
            return `${path} (${what})`;
        });
        const requests = request.targets.filter(t => {
            const file = byPath.get(typeof t === "string" ? t : t.path);
            return !file || comparable(file);
        }).length;
        return { scope: `${plural(request.targets.length, "selected file")}:`, items, requests };
    }

    function requestsText(count) {
        if (!count) return "No model requests: nothing selected has a summary or rule to compare.";
        return `Up to ${plural(count, "model request")} (one per file, plus at most one more for an answer that `
            + "cannot be read).";
    }

    /* Unsaved review drafts for a codebase (draft keys start with the codebase name). */
    function unsavedFor(draftKeys, codebase) {
        const prefix = `${codebase}|`;
        return [...(draftKeys || [])].filter(key => key.startsWith(prefix)).length;
    }

    // ---------------------------------------------------------------
    // A running check: only what the backend actually reports
    // ---------------------------------------------------------------

    const FILE_STARTED = /^Checking consistency: /;
    const PROVIDER_WAIT = /waiting \d+ s before retrying/i;
    const MAX_MESSAGES = 50;

    /* A check that has been started; total is how many files it covers, if known. */
    function newOperation({ codebasePath, codebase, request, total, now }) {
        return {
            codebasePath, codebase, request,
            total: Number.isInteger(total) ? total : null,
            startedAt: now === undefined ? Date.now() : now,
            started: 0, waits: 0, messages: [], usage: null,
            done: false, response: null,
        };
    }

    /* Folds one streamed backend event into the operation. */
    function applyEvent(op, event) {
        if (!event || typeof event !== "object") return op;
        if (event.type === "progress" && typeof event.stage === "string") {
            const wait = PROVIDER_WAIT.test(event.stage);
            if (FILE_STARTED.test(event.stage)) op.started += 1;
            if (wait) op.waits += 1;
            op.messages.push({ text: event.stage, wait });
            if (op.messages.length > MAX_MESSAGES) op.messages.splice(0, op.messages.length - MAX_MESSAGES);
        } else if (event.type === "token_usage" || event.type === "token_usage_stage") {
            const calls = Number(event.calls) || 0;
            const input = Number(event.input_tokens) || 0;
            const output = Number(event.output_tokens) || 0;
            op.usage = { calls, input, output, total: Number(event.total_tokens) || input + output };
        }
        return op;
    }

    function filesText(op) {
        if (!op.started) return "Preparing the comparison…";
        return op.total ? `Started ${op.started} of ${plural(op.total, "file")}.` : `Started ${plural(op.started, "file")}.`;
    }

    function usageText(usage) {
        if (!usage || !usage.calls) return null;
        return `${plural(usage.calls, "AI call")} · ${usage.total.toLocaleString("en-US")} tokens`;
    }

    function formatElapsed(ms) {
        const seconds = Math.max(0, Math.floor(ms / 1000));
        const minutes = Math.floor(seconds / 60);
        return minutes ? `${minutes} min ${String(seconds % 60).padStart(2, "0")} s` : `${seconds} s`;
    }

    /* How a finished check went, told apart: completed, incomplete, failed, or did not run. */
    function outcome(response) {
        if (response && response.success && response.result) {
            const r = response.result;
            const kind = r.status === "completed" ? "good" : r.status === "partial" ? "warn" : "bad";
            return { kind, status: r.status, text: r.warning || r.message, checkId: r.check_id, scope: r.scope,
                     codebase: r.codebase };
        }
        const error = (response && response.error) || "no answer from the backend.";
        return { kind: "bad", status: "not_run", text: `The check did not finish: ${error}`, checkId: null, scope: null };
    }

    /* True when the result shown is not the one the check produced. */
    function handoffMismatch(expectedCheckId, shownCheckId) {
        return Boolean(expectedCheckId) && expectedCheckId !== shownCheckId;
    }

    /* True when the form differs from what is saved (blank notes count as none). */
    function isDirty(saved, draft) {
        const savedStatus = (saved && saved.status) || "unresolved";
        const savedNote = ((saved && saved.note) || "").trim();
        return draft.status !== savedStatus || (draft.note || "").trim() !== savedNote;
    }

    return {
        PAGE_SIZE,
        CATEGORY_LABELS,
        REVIEW_LABELS,
        SCOPE_LABELS,
        VERDICT_LABELS,
        ORIGIN_LABELS,
        KIND_LABELS,
        handleOf,
        location,
        sourceOf,
        sourceLocation,
        coverage,
        reviewCounts,
        describe,
        runLabel,
        filterOptions,
        filterFindings,
        paginate,
        isDirty,
        artifactsText,
        comparable,
        checkRequest,
        describeCheck,
        requestsText,
        unsavedFor,
        newOperation,
        applyEvent,
        filesText,
        usageText,
        formatElapsed,
        outcome,
        handoffMismatch,
        plural,
    };
});
