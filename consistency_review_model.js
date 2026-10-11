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
                    + "Running a check from this screen is not available yet.",
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
                return `From run ${result.run_id}, the most recent logged run of this codebase.`;
            case "earlier_run":
                return `From an earlier run (${result.run_id}). The most recent logged run of this codebase is `
                    + `${latest.run_id}; this result is not from it.`;
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
        plural,
    };
});
