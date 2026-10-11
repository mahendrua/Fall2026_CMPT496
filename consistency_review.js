/*
 * consistency_review.js
 *
 * The Consistency Review screen inside View Insights. It reads saved checks
 * through the backend (get_consistency_result) and never runs a check or
 * calls a model. Everything shown comes from saved results or the codebase
 * and is untrusted text: it is only ever set with textContent, never parsed
 * as HTML.
 *
 *     ConsistencyReviewScreen.open(container, api, codebasePath)
 *
 * api.call(command, args) returns a promise of the backend's response.
 */
(function () {
    const M = window.ConsistencyReviewModel;

    const state = {
        container: null,
        api: null,
        codebasePath: "",
        scope: "full",
        response: null,
        loadToken: 0,
        filters: { text: "", file: "", category: "", status: "" },
        page: 1,
        selectedId: null,
        preferAvailable: false,
    };

    // ---------------------------------------------------------------
    // DOM helpers: text only, never innerHTML with data
    // ---------------------------------------------------------------

    function el(tag, props, ...children) {
        const node = document.createElement(tag);
        const p = props || {};
        if (p.class) node.className = p.class;
        if (p.text !== undefined && p.text !== null) node.textContent = String(p.text);
        Object.entries(p.attrs || {}).forEach(([k, v]) => {
            if (v !== undefined && v !== null && v !== false) node.setAttribute(k, v === true ? "" : String(v));
        });
        Object.entries(p.on || {}).forEach(([event, handler]) => node.addEventListener(event, handler));
        children.flat().forEach(child => {
            if (child === null || child === undefined || child === false) return;
            node.appendChild(child instanceof Node ? child : document.createTextNode(String(child)));
        });
        return node;
    }

    function chip(text, kind) {
        return el("span", { class: `cr-chip ${kind ? "cr-chip-" + kind : ""}`, text });
    }

    function field(label, value) {
        if (value === undefined || value === null || value === "") return null;
        return el("div", { class: "cr-field" },
            el("dt", { text: label }),
            el("dd", { text: value }));
    }

    function section(title, ...children) {
        return el("section", { class: "cr-section" }, el("h3", { class: "cr-section-title", text: title }), ...children);
    }

    function list(items, render) {
        return el("ul", { class: "cr-list" }, items.map(item => el("li", null, render(item))));
    }

    // ---------------------------------------------------------------
    // Loading
    // ---------------------------------------------------------------

    function open(container, api, codebasePath) {
        const switched = codebasePath !== state.codebasePath;
        state.container = container;
        state.api = api;
        if (switched) {
            state.codebasePath = codebasePath;
            state.scope = "full";
            resetView();
        }
        state.preferAvailable = switched;
        if (!codebasePath) {
            state.response = null;
            container.replaceChildren(el("div", { class: "cr-screen" },
                el("h2", { class: "summary-header", text: "Consistency Review" }),
                el("p", { class: "cr-muted", text: "Select a codebase first from the pipeline page." })));
            return Promise.resolve();
        }
        return load();
    }

    function resetView() {
        state.response = null;
        state.filters = { text: "", file: "", category: "", status: "" };
        state.page = 1;
        state.selectedId = null;
    }

    async function load() {
        const token = ++state.loadToken;
        state.response = null;
        render();

        let response;
        try {
            response = await state.api.call("get_consistency_result", {
                codebase: state.codebasePath,
                scope: state.scope,
            });
        } catch (error) {
            response = { success: false, error: error && error.message ? error.message : String(error) };
        }

        // A later load (another scope or codebase) replaces this one.
        if (token !== state.loadToken) return;

        // On first opening a codebase, show whichever scope has a saved result.
        const review = response && response.success && response.review;
        const other = state.scope === "full" ? "selected" : "full";
        if (state.preferAvailable && review && !review.available && !review.problem
            && (review.available_scopes || []).includes(other)) {
            state.preferAvailable = false;
            state.scope = other;
            return load();
        }
        state.preferAvailable = false;

        state.response = response;
        const findings = findingsOf();
        if (!findings.some(f => f.id === state.selectedId)) {
            state.selectedId = findings.length ? findings[0].id : null;
        }
        render();
    }

    function reviewData() {
        return (state.response && state.response.success && state.response.review) || null;
    }

    function resultOf() {
        const review = reviewData();
        return (review && review.available && review.result) || null;
    }

    function findingsOf() {
        const result = resultOf();
        return (result && result.findings) || [];
    }

    function setScope(scope) {
        if (scope === state.scope) return;
        state.scope = scope;
        resetView();
        load();
    }

    // ---------------------------------------------------------------
    // Rendering
    // ---------------------------------------------------------------

    function render() {
        if (!state.container) return;
        state.container.replaceChildren(screen());
    }

    function screen() {
        const status = M.describe(state.response);
        const review = reviewData();
        const result = resultOf();

        return el("div", { class: "cr-screen", attrs: { "data-state": status.kind } },
            header(review),
            el("div", { class: `cr-banner cr-banner-${status.kind}`, attrs: { role: "status" } },
                el("strong", { class: "cr-banner-title", text: status.title }),
                status.message ? el("p", { text: status.message }) : null),
            result ? overview(review, result) : null,
            result ? problems(result, status.kind) : null,
            result && result.findings.length ? browser(result) : null);
    }

    function header(review) {
        const available = (review && review.available_scopes) || [];
        const scopes = ["full", "selected"].map(scope => el("button", {
            class: `cr-scope ${scope === state.scope ? "active" : ""}`,
            text: M.SCOPE_LABELS[scope] + (available.includes(scope) ? "" : " (none saved)"),
            attrs: { type: "button", "aria-pressed": scope === state.scope ? "true" : "false" },
            on: { click: () => setScope(scope) },
        }));

        return el("header", { class: "cr-header" },
            el("div", null,
                el("h2", { class: "summary-header", text: "Consistency Review" }),
                el("p", { class: "cr-muted", text: `Codebase: ${(review && review.codebase) || nameOf(state.codebasePath)}` })),
            el("div", { class: "cr-header-actions", attrs: { role: "group", "aria-label": "Result scope" } },
                ...scopes,
                el("button", { class: "cr-button", text: "Reload", attrs: { type: "button" }, on: { click: load } })));
    }

    function nameOf(path) {
        const parts = String(path || "").split(/[\\/]/).filter(Boolean);
        return parts[parts.length - 1] || "";
    }

    function overview(review, result) {
        const cov = M.coverage(result);
        const counts = M.reviewCounts(result.findings);

        const identity = el("dl", { class: "cr-fields" },
            field("Result", M.SCOPE_LABELS[result.scope] || result.scope),
            field("Check", result.check_id),
            field("Run", M.runLabel(review)),
            field("Started", result.started_at),
            field("Finished", result.finished_at),
            field("Status", result.status),
            result.selection && result.selection.length ? field("Selection", result.selection.join(", ")) : null);

        const numbers = el("div", { class: "cr-counts" },
            count("Findings", result.findings.length),
            count("Unresolved reviews", counts.unresolved),
            count("Generated artifacts compared", cov.compared),
            count("Verdicts recorded", cov.assessed),
            count("Skipped", cov.skipped, cov.skipped ? "warn" : ""),
            count("Undetermined", cov.undetermined, cov.undetermined ? "warn" : ""),
            count("Errors", cov.errors, cov.errors ? "bad" : ""));

        const older = (review.missing_fields || []).length
            ? el("p", { class: "cr-note", text:
                `This result was saved by an earlier version and does not record: ${review.missing_fields.join(", ")}. `
                + "Those parts are shown as empty, not as nothing found." })
            : null;

        return el("div", { class: "cr-overview" }, identity, numbers, older);
    }

    function count(label, value, kind) {
        return el("div", { class: `cr-count ${kind ? "cr-count-" + kind : ""}` },
            el("span", { class: "cr-count-value", text: value }),
            el("span", { class: "cr-count-label", text: label }));
    }

    /* Errors and limitations stay visible; they are open by default when the check is incomplete. */
    function problems(result, kind) {
        const open = kind === "partial" || kind === "failed";
        const parts = [];

        if (result.errors.length) {
            parts.push(el("details", { class: "cr-details", attrs: { open } },
                el("summary", { text: `Errors (${result.errors.length})` }),
                list(result.errors, text => el("span", { class: "cr-error", text }))));
        }
        const missing = [...(result.missing_artifacts || []), ...(result.unresolved_links || [])];
        if (missing.length) {
            parts.push(el("details", { class: "cr-details", attrs: { open } },
                el("summary", { text: `Missing or unresolved artifacts (${missing.length})` }),
                list(missing, ref => el("span", null,
                    chip(ref.status, "warn"), " ", M.handleOf(ref), " ", el("span", { class: "cr-muted", text: M.location(ref) }),
                    ref.detail ? el("div", { class: "cr-muted", text: ref.detail }) : null))));
        }
        if ((result.notes || []).length) {
            parts.push(el("details", { class: "cr-details" },
                el("summary", { text: `Limitations and notes (${result.notes.length})` }),
                list(result.notes, text => el("span", { text }))));
        }
        return parts.length ? el("div", { class: "cr-problems" }, parts) : null;
    }

    // ---------------------------------------------------------------
    // Findings browser
    // ---------------------------------------------------------------

    function browser(result) {
        const options = M.filterOptions(result.findings);
        const filtered = M.filterFindings(result.findings, state.filters);
        const page = M.paginate(filtered, state.page);
        state.page = page.page;

        // The detail always shows a finding the filters let through.
        const selected = filtered.find(f => f.id === state.selectedId) || filtered[0] || null;
        if (selected) state.selectedId = selected.id;

        return el("div", { class: "cr-browser" },
            el("div", { class: "cr-list-pane" },
                filters(options),
                el("p", { class: "cr-muted", attrs: { "aria-live": "polite" },
                    text: `Showing ${page.items.length} of ${filtered.length} (${result.findings.length} in total)` }),
                el("div", { class: "cr-findings", attrs: { role: "listbox", "aria-label": "Findings" } },
                    page.items.map(findingRow)),
                page.pages > 1 ? pager(page) : null),
            el("div", { class: "cr-detail-pane" }, selected ? detail(selected, result) : el("p", { class: "cr-muted",
                text: filtered.length ? "Select a finding to see its evidence." : "No finding matches these filters." })));
    }

    function filters(options) {
        const select = (label, key, values, labels) => el("label", { class: "cr-filter" },
            el("span", { text: label }),
            el("select", {
                on: { change: e => { state.filters[key] = e.target.value; state.page = 1; render(); } },
            },
            el("option", { text: "All", attrs: { value: "" } }),
            values.map(v => el("option", {
                text: labels ? labels[v] || v : v,
                attrs: { value: v, selected: state.filters[key] === v },
            }))));

        const search = el("input", {
            class: "cr-search",
            attrs: { type: "search", placeholder: "Search titles and explanations", "aria-label": "Search findings",
                     value: state.filters.text },
            on: {
                input: e => {
                    state.filters.text = e.target.value;
                    state.page = 1;
                    const at = e.target.selectionStart;
                    render();
                    const again = state.container.querySelector(".cr-search");
                    if (again) { again.focus(); again.setSelectionRange(at, at); }
                },
            },
        });

        return el("div", { class: "cr-filters" },
            search,
            select("File", "file", options.files),
            select("Category", "category", options.categories, M.CATEGORY_LABELS),
            select("Review", "status", Object.keys(M.REVIEW_LABELS), M.REVIEW_LABELS));
    }

    function findingRow(finding) {
        const status = (finding.review && finding.review.status) || "unresolved";
        const selected = finding.id === state.selectedId;
        return el("button", {
            class: `cr-finding ${selected ? "selected" : ""}`,
            attrs: { type: "button", role: "option", "aria-selected": selected ? "true" : "false", "data-id": finding.id },
            on: { click: () => select(finding.id) },
        },
        el("span", { class: "cr-finding-top" },
            chip(M.CATEGORY_LABELS[finding.category] || finding.category, "category"),
            chip(M.REVIEW_LABELS[status], status)),
        el("span", { class: "cr-finding-title", text: finding.title }),
        el("span", { class: "cr-muted", text: M.sourceLocation(finding) }));
    }

    function pager(page) {
        const go = n => { state.page = n; render(); };
        return el("div", { class: "cr-pager" },
            el("button", { class: "cr-button", text: "Previous", attrs: { type: "button", disabled: page.page <= 1 },
                on: { click: () => go(page.page - 1) } }),
            el("span", { text: `Page ${page.page} of ${page.pages}` }),
            el("button", { class: "cr-button", text: "Next", attrs: { type: "button", disabled: page.page >= page.pages },
                on: { click: () => go(page.page + 1) } }));
    }

    function select(id) {
        state.selectedId = id;
        render();
    }

    function detail(finding) {
        const status = (finding.review && finding.review.status) || "unresolved";
        return el("article", { class: "cr-detail", attrs: { "aria-label": "Selected finding" } },
            el("div", { class: "cr-finding-top" },
                chip(M.CATEGORY_LABELS[finding.category] || finding.category, "category"),
                chip(M.REVIEW_LABELS[status], status)),
            el("h3", { class: "cr-detail-title", text: finding.title }),
            el("p", { class: "cr-muted", text: M.sourceLocation(finding) }),
            el("p", { class: "cr-explanation", text: finding.explanation }));
    }

    window.ConsistencyReviewScreen = {
        open,
        reload: load,
        _state: state,
    };
})();
