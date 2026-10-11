/*
 * consistency_review.js
 *
 * The Consistency Review screen inside View Insights. It reads saved checks
 * through the backend and saves review decisions; it never runs a check or
 * calls a model. Everything shown comes from saved results or the codebase
 * and is untrusted text: it is only ever set with textContent, never parsed
 * as HTML.
 *
 *     ConsistencyReviewScreen.open(container, api, codebasePath)
 *
 * api.call(command, args) returns a promise of the backend's response.
 *
 * Backend commands used:
 *     get_consistency_result     the saved full or selected check
 *     get_consistency_artifact   current state of one artifact of a finding
 *     save_consistency_review    a finding's review status and note
 *     get_consistency_targets    files a new check can cover (no model)
 *     get_consistency_methods    methods of one file a check can be limited to
 *     check_consistency          runs a check; only ever on the Check Consistency button
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
        tab: "findings",
        // Kept for the whole session so a note is never dropped by switching
        // findings, scopes or views. Keyed by codebase, check and finding.
        drafts: new Map(),
        saving: new Set(),
        feedback: new Map(),
        artifacts: new Map(),
        showCurrent: new Set(),
        panel: newPanel(),
        // The check that is running, if any. Kept apart from the view so
        // switching codebase or view never loses it or misplaces its result.
        operation: null,
        outcomes: new Map(),       // codebase name -> { result, op, shown }
        handoff: null,             // { expected, shown } check IDs after a check
        ticker: null,
        visible: false,            // false once another Insights view takes the output box
    };

    /* The "New check" panel. Nothing in it starts a check except the start button. */
    function newPanel() {
        return {
            open: false,
            scope: "full",
            selected: new Map(),         // path -> "" (whole file) or a method
            targets: null,               // { loading } | { data } | { error }
            methods: new Map(),          // path -> { loading } | { data } | { error }
            filter: "",
            message: null,               // { kind, text }
        };
    }

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
        Object.entries(p.props || {}).forEach(([k, v]) => { node[k] = v; });
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
        return el("section", { class: "cr-section" }, el("h4", { class: "cr-section-title", text: title }), ...children);
    }

    function list(items, render) {
        return el("ul", { class: "cr-list" }, items.map(item => el("li", null, render(item))));
    }

    /* Code or artifact text, kept as typed, with optional line numbers. */
    function codeBlock(text, firstLine, label, prose) {
        const lines = String(text === null || text === undefined ? "" : text).split("\n");
        const gutter = firstLine
            ? el("span", { class: "cr-gutter", attrs: { "aria-hidden": "true" },
                text: lines.map((_, i) => firstLine + i).join("\n") })
            : null;
        return el("pre", { class: `cr-code ${firstLine ? "numbered" : ""} ${prose ? "prose" : ""}`, attrs: { "aria-label": label || null, tabindex: "0" } },
            gutter, el("code", { text: lines.join("\n") }));
    }

    // ---------------------------------------------------------------
    // Loading
    // ---------------------------------------------------------------

    function open(container, api, codebasePath) {
        const switched = codebasePath !== state.codebasePath;
        state.container = container;
        state.visible = true;
        state.api = api;
        if (switched) {
            state.codebasePath = codebasePath;
            state.scope = "full";
            state.panel = newPanel();
            resetView();
        }
        state.preferAvailable = switched;
        if (!codebasePath) {
            state.response = null;
            container.replaceChildren(el("div", { class: "cr-screen" },
                el("h2", { class: "summary-header", text: "Consistency Review" }),
                el("p", { class: "cr-muted", text: "Select a codebase first from the pipeline page." }),
                runningNotice()));
            return Promise.resolve();
        }

        // A check of this codebase finished while it was not on screen: show its result.
        const finished = state.outcomes.get(nameOf(codebasePath));
        if (finished && !finished.shown && finished.result.scope) {
            finished.shown = true;
            state.scope = finished.result.scope;
            state.preferAvailable = false;
            resetView();
            return load(finished.result.checkId);
        }
        return load();
    }

    function resetView() {
        state.response = null;
        state.filters = { text: "", file: "", category: "", status: "" };
        state.page = 1;
        state.selectedId = null;
        state.tab = "findings";
    }

    /* Loads the saved check of the current scope. expected is the check ID a just-finished check returned. */
    async function load(expected) {
        const token = ++state.loadToken;
        state.response = null;
        state.handoff = null;
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
        const shown = resultOf();
        state.handoff = typeof expected === "string" ? { expected, shown: shown ? shown.check_id : null } : null;
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
    // Review drafts
    // ---------------------------------------------------------------

    function draftKey(finding) {
        const result = resultOf();
        return `${nameOf(state.codebasePath)}|${result ? result.check_id : ""}|${finding.id}`;
    }

    function draftOf(finding) {
        const saved = finding.review || {};
        return state.drafts.get(draftKey(finding)) || { status: saved.status || "unresolved", note: saved.note || "" };
    }

    function setDraft(finding, change) {
        const draft = Object.assign({}, draftOf(finding), change);
        if (M.isDirty(finding.review, draft)) state.drafts.set(draftKey(finding), draft);
        else state.drafts.delete(draftKey(finding));
        state.feedback.delete(draftKey(finding));
        return draft;
    }

    function unsavedCount() {
        const prefix = `${nameOf(state.codebasePath)}|${(resultOf() || {}).check_id || ""}|`;
        return [...state.drafts.keys()].filter(k => k.startsWith(prefix)).length;
    }

    async function save(finding) {
        const key = draftKey(finding);
        if (state.saving.has(key)) return;              // one save at a time per finding
        const draft = draftOf(finding);
        const result = resultOf();

        state.saving.add(key);
        state.feedback.set(key, { kind: "pending", text: "Saving…" });
        render();

        let response;
        try {
            response = await state.api.call("save_consistency_review", {
                codebase: state.codebasePath,
                scope: state.scope,
                check_id: result.check_id,
                finding_id: finding.id,
                status: draft.status,
                note: draft.note,
            });
        } catch (error) {
            response = { success: false, error: error && error.message ? error.message : String(error) };
        }
        state.saving.delete(key);

        if (response && response.success && response.saved) {
            finding.review = response.saved.review;
            state.drafts.delete(key);
            state.feedback.set(key, { kind: "good", text: `Saved at ${response.saved.review.updated_at || "now"}.` });
        } else {
            state.feedback.set(key, {
                kind: "bad",
                text: `Not saved: ${(response && response.error) || "the backend did not answer."} Your changes are still here.`,
            });
        }
        render();
    }

    // ---------------------------------------------------------------
    // Current artifact content
    // ---------------------------------------------------------------

    function artifactKey(finding, index) {
        return `${draftKey(finding)}|${index}`;
    }

    function fetchArtifacts(finding) {
        const result = resultOf();
        finding.artifacts.forEach((_, index) => {
            const key = artifactKey(finding, index);
            if (state.artifacts.has(key)) return;
            state.artifacts.set(key, { loading: true });
            state.api.call("get_consistency_artifact", {
                codebase: state.codebasePath,
                scope: state.scope,
                check_id: result.check_id,
                finding_id: finding.id,
                artifact_index: index,
            }).then(response => {
                state.artifacts.set(key, response && response.success
                    ? { artifact: response.artifact }
                    : { error: (response && response.error) || "Current content could not be read." });
                render();
            }, error => {
                state.artifacts.set(key, { error: String(error && error.message || error) });
                render();
            });
        });
    }

    // ---------------------------------------------------------------
    // Rendering
    // ---------------------------------------------------------------

    /* Re-render, keeping focus, cursor and scroll position. */
    function render() {
        // Another Insights view now owns the output box: draw nothing there.
        if (!state.container || !state.visible) return;

        const active = document.activeElement;
        const focusKey = active && state.container.contains(active) ? active.getAttribute("data-focus") : null;
        const selection = focusKey && "selectionStart" in active
            ? [active.selectionStart, active.selectionEnd] : null;
        const scroll = state.container.scrollTop;

        state.container.replaceChildren(screen());

        state.container.scrollTop = scroll;
        if (focusKey) {
            const again = state.container.querySelector(`[data-focus="${CSS.escape(focusKey)}"]`);
            if (again) {
                again.focus({ preventScroll: true });
                if (selection && "setSelectionRange" in again) {
                    try { again.setSelectionRange(selection[0], selection[1]); } catch (_) { /* not text */ }
                }
            }
        }
    }

    function screen() {
        const status = M.describe(state.response);
        const review = reviewData();
        const result = resultOf();

        return el("div", { class: "cr-screen", attrs: { "data-state": status.kind } },
            header(review),
            runningNotice(),
            outcomeNotice(),
            state.panel.open ? checkPanel() : null,
            handoffNotice(),
            el("div", { class: `cr-banner cr-banner-${status.kind}`, attrs: { role: "status" } },
                el("strong", { class: "cr-banner-title", text: status.title }),
                status.message ? el("p", { text: status.message }) : null),
            result ? overview(review, result) : null,
            result ? problems(result, status.kind) : null,
            result ? tabs(result) : null);
    }

    function header(review) {
        const available = (review && review.available_scopes) || [];
        const scopes = ["full", "selected"].map(scope => el("button", {
            class: `cr-scope ${scope === state.scope ? "active" : ""}`,
            text: M.SCOPE_LABELS[scope] + (available.includes(scope) ? "" : " (none saved)"),
            attrs: { type: "button", "aria-pressed": scope === state.scope ? "true" : "false" },
            on: { click: () => setScope(scope) },
        }));
        const unsaved = unsavedCount();

        return el("header", { class: "cr-header" },
            el("div", null,
                el("h2", { class: "summary-header", text: "Consistency Review" }),
                el("p", { class: "cr-muted", text: `Codebase: ${(review && review.codebase) || nameOf(state.codebasePath)}` }),
                unsaved ? el("p", { class: "cr-unsaved", text: `${M.plural(unsaved, "review")} not saved yet` }) : null),
            el("div", { class: "cr-header-actions", attrs: { role: "group", "aria-label": "Result scope" } },
                ...scopes,
                el("button", { class: "cr-button", text: "Reload", attrs: { type: "button" }, on: { click: () => load() } }),
                el("button", {
                    class: `cr-button ${state.panel.open ? "active-toggle" : ""}`,
                    text: state.panel.open ? "Close new check" : "New check",
                    attrs: { type: "button", "aria-expanded": state.panel.open ? "true" : "false",
                             "aria-controls": "cr-check-panel", "data-focus": "panel-toggle" },
                    on: { click: togglePanel },
                })));
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
    // Tabs: findings, verdicts, skipped, undetermined
    // ---------------------------------------------------------------

    function tabs(result) {
        const legacy = (reviewData().missing_fields || []);
        const all = [
            ["findings", `Findings (${result.findings.length})`],
            ["verdicts", `Verdicts (${legacy.includes("assessments") ? "not recorded" : result.assessments.length})`],
            ["skipped", `Skipped (${legacy.includes("skipped") ? "not recorded" : result.skipped.length})`],
            ["undetermined", `Undetermined (${legacy.includes("undetermined") ? "not recorded" : result.undetermined.length})`],
        ];
        const bar = el("div", { class: "cr-tabs", attrs: { role: "tablist", "aria-label": "Result details" } },
            all.map(([id, label]) => el("button", {
                class: `cr-tab ${state.tab === id ? "active" : ""}`,
                text: label,
                attrs: { type: "button", role: "tab", "aria-selected": state.tab === id ? "true" : "false",
                         "data-focus": `tab-${id}` },
                on: { click: () => { state.tab = id; render(); } },
            })));

        let body;
        if (state.tab === "verdicts") body = verdicts(result, legacy.includes("assessments"));
        else if (state.tab === "skipped") body = skipped(result, legacy.includes("skipped"));
        else if (state.tab === "undetermined") body = undetermined(result, legacy.includes("undetermined"));
        else body = result.findings.length ? browser(result)
            : el("p", { class: "cr-muted", text: "No findings in this result. See the other tabs for what was assessed, skipped or undetermined." });

        return el("div", { class: "cr-tabbed" }, bar, el("div", { attrs: { role: "tabpanel" } }, body));
    }

    function notRecorded(what) {
        return el("p", { class: "cr-note", text: `This older result does not record ${what}.` });
    }

    function verdicts(result, legacy) {
        if (legacy) return notRecorded("the verdict for each artifact");
        if (!result.assessments.length) return el("p", { class: "cr-muted", text: "No verdicts were recorded in this check." });

        const rows = result.assessments.map(a => [
            el("span", null, M.handleOf(a.artifact), el("div", { class: "cr-muted", text: M.location(a.artifact) })),
            M.location(a.scope),
            el("span", null, chip(M.VERDICT_LABELS[a.verdict] || a.verdict, verdictKind(a)),
                a.accepted === false ? el("div", { class: "cr-error", text: `Not used: ${a.problem}` }) : null),
            a.reason,
        ]);
        return el("div", null,
            el("p", { class: "cr-muted", text: "Each verdict is the model's judgement of one generated artifact against the code "
                + "it was compared with, with the short reason it gave. A verdict does not establish what the code should do." }),
            table(["Artifact", "Compared with", "Verdict", "Reason"], rows));
    }

    function verdictKind(a) {
        if (a.accepted === false) return "bad";
        return { consistent: "good", inconsistent: "warn", not_applicable: "dismissed", insufficient_evidence: "warn" }[a.verdict];
    }

    function skipped(result, legacy) {
        if (legacy) return notRecorded("which artifacts were skipped");
        if (!result.skipped.length) return el("p", { class: "cr-muted", text: "Nothing that was linked was skipped." });
        return el("div", null,
            el("p", { class: "cr-muted", text: "These artifacts were linked to the code but never assessed, so no verdict exists for them." }),
            table(["Artifact", "Would have been compared with", "Why it was skipped"], result.skipped.map(s => [
                el("span", null, M.handleOf(s.artifact), el("div", { class: "cr-muted", text: M.location(s.artifact) })),
                s.scope ? M.location(s.scope) : "—",
                s.reason,
            ])));
    }

    function undetermined(result, legacy) {
        if (legacy) return notRecorded("undetermined comparisons");
        if (!result.undetermined.length) return el("p", { class: "cr-muted", text: "Every assessed comparison reached a verdict." });
        return el("div", null,
            el("p", { class: "cr-muted", text: "The checker compared these but could not decide whether they agree with the code." }),
            table(["Artifact", "Why it is undetermined"], result.undetermined.map(u => [
                el("span", null, M.handleOf(u.artifact), el("div", { class: "cr-muted", text: M.location(u.artifact) })),
                u.reason,
            ])));
    }

    function table(headers, rows) {
        const t = el("table", { class: "usage-table cr-table" });
        const head = t.createTHead().insertRow();
        headers.forEach(h => head.appendChild(el("th", { text: h })));
        const body = t.createTBody();
        rows.forEach(cells => {
            const row = body.insertRow();
            cells.forEach(cell => {
                const td = row.insertCell();
                if (cell instanceof Node) td.appendChild(cell);
                else td.textContent = cell === null || cell === undefined ? "" : String(cell);
            });
        });
        return t;
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
            el("div", { class: "cr-detail-pane" }, selected ? detail(selected) : el("p", { class: "cr-muted",
                text: filtered.length ? "Select a finding to see its evidence." : "No finding matches these filters." })));
    }

    function filters(options) {
        const select = (label, key, values, labels) => el("label", { class: "cr-filter" },
            el("span", { text: label }),
            el("select", {
                attrs: { "data-focus": `filter-${key}` },
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
                     "data-focus": "search" },
            props: { value: state.filters.text },
            on: { input: e => { state.filters.text = e.target.value; state.page = 1; render(); } },
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
        const unsaved = state.drafts.has(draftKey(finding));
        return el("button", {
            class: `cr-finding ${selected ? "selected" : ""}`,
            attrs: { type: "button", role: "option", "aria-selected": selected ? "true" : "false", "data-id": finding.id,
                     "data-focus": `finding-${finding.id}` },
            on: { click: () => select(finding.id) },
        },
        el("span", { class: "cr-finding-top" },
            chip(M.CATEGORY_LABELS[finding.category] || finding.category, "category"),
            chip(M.REVIEW_LABELS[status], status),
            unsaved ? chip("Unsaved", "bad") : null),
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

    // ---------------------------------------------------------------
    // Finding detail: claims, evidence, uncertainty, artifacts, review
    // ---------------------------------------------------------------

    function detail(finding) {
        fetchArtifacts(finding);
        const status = (finding.review && finding.review.status) || "unresolved";
        const sourceClaims = finding.claims.filter(c => c.artifact.kind === "source");
        const otherClaims = finding.claims.filter(c => c.artifact.kind !== "source");

        return el("article", { class: "cr-detail", attrs: { "aria-label": "Selected finding", "data-finding": finding.id } },
            el("div", { class: "cr-finding-top" },
                chip(M.CATEGORY_LABELS[finding.category] || finding.category, "category"),
                chip(M.REVIEW_LABELS[status], status)),
            el("h3", { class: "cr-detail-title", text: finding.title }),
            el("p", { class: "cr-muted", text: M.sourceLocation(finding) }),
            el("p", { class: "cr-explanation", text: finding.explanation }),
            el("p", { class: "cr-intended", text: intendedText(finding) }),

            section("Conflicting claims",
                el("div", { class: "cr-claims" },
                    el("div", { class: "cr-claim-column" }, sourceClaims.map(c => claim(finding, c, "What the code does"))),
                    el("div", { class: "cr-claim-column" }, otherClaims.map(c => claim(finding, c, "What the artifact says"))))),

            section("Evidence used by this check",
                el("p", { class: "cr-muted", text: "Stored with the check. If the code or artifact has changed since, the stored "
                    + "evidence below is still what the check was based on." }),
                finding.evidence.length ? finding.evidence.map(e => evidence(e)) : el("p", { class: "cr-muted", text: "No evidence recorded." })),

            section("Uncertainty",
                finding.uncertainty.length ? list(finding.uncertainty, text => el("span", { text }))
                    : el("p", { class: "cr-muted", text: "None recorded." })),

            section("Artifacts and current content", artifacts(finding)),

            reviewForm(finding));
    }

    function intendedText(finding) {
        if (finding.intended_behaviour === "matches_requirement") return "Intended behaviour: matches a confirmed requirement.";
        if (finding.intended_behaviour === "conflicts_with_requirement") return "Intended behaviour: conflicts with a confirmed requirement.";
        return "Intended behaviour: unknown. No confirmed requirement exists, so it is not known whether the code or the "
            + "generated artifact reflects what is intended.";
    }

    function claim(finding, c, heading) {
        const ref = c.artifact;
        return el("div", { class: "cr-claim" },
            el("div", { class: "cr-claim-head" },
                el("strong", { text: heading }), " ", chip(M.handleOf(ref), ref.kind === "source" ? "category" : "")),
            el("p", { class: "cr-muted", text: M.location(ref) }),
            el("p", { text: c.statement }),
            // The model's quotation, not whole lines, so it is not numbered.
            c.excerpt ? codeBlock(c.excerpt, null, `Excerpt from ${M.handleOf(ref)}`, isProse(ref)) : null,
            provenance(ref));
    }

    /* Summaries and rules are prose and wrap; code keeps its exact layout. */
    function isProse(ref) {
        return ref && (ref.kind === "summary" || ref.kind === "business_rule");
    }

    function provenance(ref) {
        if (ref.kind !== "business_rule" || !(ref.provenance || []).length) return null;
        return el("div", { class: "cr-provenance" },
            el("span", { class: "cr-muted", text: "Rule provenance:" }),
            list(ref.provenance, p => el("span", null,
                chip(p.origin, ""), " ",
                (p.source_file_paths || []).length ? p.source_file_paths.join(", ") : "no source files recorded (folder-level)")));
    }

    function evidence(e) {
        const ref = e.artifact;
        return el("div", { class: "cr-evidence" },
            el("div", { class: "cr-claim-head" },
                chip(M.ORIGIN_LABELS[e.origin] || e.origin, e.origin === "code_inspection" ? "warn" : "good")),
            el("p", { text: e.description }),
            e.excerpt ? codeBlock(e.excerpt, ref && ref.kind === "source" ? ref.line_start : null,
                `Stored evidence from ${ref ? M.location(ref) : "the check"}`) : null);
    }

    function artifacts(finding) {
        return el("div", { class: "cr-artifacts" }, finding.artifacts.map((ref, index) => {
            const key = artifactKey(finding, index);
            const current = state.artifacts.get(key);
            return el("div", { class: "cr-artifact" },
                el("div", { class: "cr-claim-head" },
                    el("strong", { text: M.KIND_LABELS[ref.kind] || ref.kind }), " ", chip(M.handleOf(ref), ""), " ",
                    currentChip(current)),
                el("p", { class: "cr-muted", text: M.location(ref) || "No location recorded" }),
                ref.status !== "resolved" || ref.detail ? el("p", { class: "cr-muted", text: `${ref.status}: ${ref.detail || ""}` }) : null,
                provenance(ref),
                currentContent(current, key));
        }));
    }

    function currentChip(current) {
        if (!current || current.loading) return chip("Checking current content…", "");
        if (current.error) return chip("Current content unavailable", "bad");
        return {
            unchanged: chip("Unchanged since the check", "good"),
            changed: chip("Changed since the check", "warn"),
            missing: chip("No longer exists", "bad"),
            unavailable: chip("Current content cannot be matched", "warn"),
        }[current.artifact.state] || chip(current.artifact.state, "");
    }

    function currentContent(current, key) {
        if (!current || current.loading) return null;
        if (current.error) return el("p", { class: "cr-error", text: current.error });

        const a = current.artifact;
        const note = {
            changed: "This artifact has changed since the check. The content below is the current version, shown only for "
                + "comparison; the stored evidence above is what the check used.",
            missing: `It no longer exists: ${a.current.detail || "not found"}. Only the stored evidence remains.`,
            unavailable: `The current artifact cannot be matched to the stored one: ${a.current.detail || a.current.status}.`,
        }[a.state];

        const open = state.showCurrent.has(key);
        return el("div", { class: "cr-current" },
            note ? el("p", { class: "cr-note", text: note }) : null,
            a.content !== null && a.content !== undefined ? el("button", {
                class: "cr-button",
                text: open ? "Hide current content" : "Show current content",
                attrs: { type: "button", "aria-expanded": open ? "true" : "false", "data-focus": `current-${key}` },
                on: { click: () => { if (open) state.showCurrent.delete(key); else state.showCurrent.add(key); render(); } },
            }) : null,
            open && a.content !== null && a.content !== undefined
                ? el("div", null,
                    el("p", { class: "cr-muted", text: a.state === "changed" ? "Current content (changed, not the evidence used)" : "Current content" }),
                    codeBlock(a.content, a.lines ? a.lines[0] : null, "Current content", isProse(a.current)))
                : null);
    }

    function reviewForm(finding) {
        const key = draftKey(finding);
        const draft = draftOf(finding);
        const dirty = M.isDirty(finding.review, draft);
        const saving = state.saving.has(key);
        const feedback = state.feedback.get(key);
        const name = `cr-review-${finding.id}`;

        const radios = Object.entries(M.REVIEW_LABELS).map(([value, label]) => el("label", { class: "cr-radio" },
            el("input", {
                attrs: { type: "radio", name, value, "data-focus": `status-${finding.id}-${value}` },
                props: { checked: draft.status === value, disabled: saving },
                on: { change: () => { setDraft(finding, { status: value }); render(); } },
            }),
            el("span", { text: label })));

        const note = el("textarea", {
            class: "cr-note-input",
            attrs: { rows: "3", maxlength: "4000", "aria-label": "Review note (optional)", "data-focus": `note-${finding.id}`,
                     placeholder: "Optional note, e.g. who was asked and what is still unknown" },
            props: { value: draft.note, disabled: saving },
            on: {
                // Updated in place so typing never re-renders the screen.
                input: e => {
                    const now = setDraft(finding, { note: e.target.value });
                    const form = e.target.closest(".cr-review");
                    const unsaved = M.isDirty(finding.review, now);
                    form.querySelector(".cr-save").disabled = !unsaved;
                    form.querySelector(".cr-dirty").textContent = unsaved ? "Unsaved changes" : "";
                    const msg = form.querySelector(".cr-feedback");
                    if (msg) msg.textContent = "";
                },
            },
        });

        const saved = finding.review && finding.review.updated_at
            ? `Last saved ${finding.review.updated_at}.` : "No review saved yet.";

        return el("section", { class: "cr-section cr-review", attrs: { "aria-label": "Review decision" } },
            el("h4", { class: "cr-section-title", text: "Your review" }),
            el("p", { class: "cr-muted", text: "Reviewed or dismissed records your decision only. It does not mean the code, "
                + "summary, rule or test is correct, and it changes nothing in them or in this finding." }),
            el("div", { class: "cr-radios", attrs: { role: "radiogroup", "aria-label": "Review status" } }, radios),
            note,
            el("div", { class: "cr-review-actions" },
                el("button", {
                    class: "cr-button primary cr-save",
                    text: saving ? "Saving…" : "Save review",
                    attrs: { type: "button", "data-focus": `save-${finding.id}` },
                    props: { disabled: saving || !dirty },
                    on: { click: () => save(finding) },
                }),
                el("span", { class: "cr-dirty", text: dirty && !saving ? "Unsaved changes" : "" }),
                el("span", { class: "cr-muted", text: saved })),
            feedback ? el("p", { class: `cr-feedback cr-feedback-${feedback.kind}`, attrs: { role: "status" }, text: feedback.text }) : null);
    }

    // ---------------------------------------------------------------
    // New check: scope selection and start
    // ---------------------------------------------------------------

    function togglePanel() {
        state.panel.open = !state.panel.open;
        if (state.panel.open && !state.panel.targets) loadTargets();
        render();
    }

    async function loadTargets() {
        const panel = state.panel;
        panel.targets = { loading: true };
        render();
        let response;
        try {
            response = await state.api.call("get_consistency_targets", { codebase: state.codebasePath });
        } catch (error) {
            response = { success: false, error: String(error && error.message || error) };
        }
        if (panel !== state.panel) return;                 // another codebase was opened meanwhile
        panel.targets = response && response.success
            ? { data: response.targets }
            : { error: (response && response.error) || "The files could not be listed." };
        render();
    }

    async function loadMethods(path) {
        const panel = state.panel;
        if (panel.methods.has(path)) return;
        panel.methods.set(path, { loading: true });
        render();
        let response;
        try {
            response = await state.api.call("get_consistency_methods", { codebase: state.codebasePath, path });
        } catch (error) {
            response = { success: false, error: String(error && error.message || error) };
        }
        if (panel !== state.panel) return;
        panel.methods.set(path, response && response.success
            ? { data: response.methods }
            : { error: (response && response.error) || "Methods could not be listed." });
        render();
    }

    function checkPanel() {
        const panel = state.panel;
        const name = nameOf(state.codebasePath);
        const targets = panel.targets || {};
        const files = (targets.data && targets.data.files) || [];
        const request = M.checkRequest(panel);
        const plan = request.error ? null : M.describeCheck(request, files);
        const unsaved = M.unsavedFor(state.drafts.keys(), name);

        const op = state.operation;
        const running = Boolean(op);
        let blocked = null;
        if (op && op.codebasePath === state.codebasePath) blocked = "A check of this codebase is running.";
        else if (op) blocked = `A check of ${op.codebase} is running. Start another when it has finished.`;
        else if (targets.loading) blocked = "Listing the files that can be checked…";
        else if (targets.error) blocked = targets.error;
        else if (!files.length) blocked = "No file of this codebase has generated summaries, rules or tests to check. "
            + "Run the pipeline first.";
        else if (request.error) blocked = request.error;
        else if (unsaved) blocked = `Save or discard your ${M.plural(unsaved, "unsaved review")} first: a new check `
            + "replaces the saved result, and unsaved notes would no longer match a finding.";

        const scopeRadio = (value, label) => el("label", { class: "cr-radio" },
            el("input", {
                attrs: { type: "radio", name: "cr-check-scope", value, "data-focus": `check-scope-${value}` },
                props: { checked: panel.scope === value, disabled: running },
                on: { change: () => { panel.scope = value; panel.message = null; render(); } },
            }),
            el("span", { text: label }));

        return el("section", { class: "cr-check-panel", attrs: { id: "cr-check-panel", "aria-label": "New consistency check" } },
            el("h3", { class: "cr-section-title", text: `Check consistency of ${name}` }),
            el("p", { class: "cr-muted", text: "Compares the code with the summaries, business rules and unit tests already "
                + "generated for it. Nothing is regenerated and the pipeline does not run." }),
            el("p", { class: "cr-quota", text: "This uses the AI model and API key selected in Manage API Keys. Each file "
                + "checked is a model request that counts toward that key's quota and usage (see AI Usage)." }),
            outputsNotes(targets.data),
            el("div", { class: "cr-radios", attrs: { role: "radiogroup", "aria-label": "What to check" } },
                scopeRadio("full", "Whole codebase"),
                scopeRadio("selected", "Selected files")),
            panel.scope === "selected" ? filePicker(files, targets) : null,
            plan ? el("div", { class: "cr-plan" },
                el("strong", { text: "Will check: " }), plan.scope,
                plan.items.length ? list(plan.items, text => el("span", { text })) : null,
                el("p", { class: "cr-muted", text: M.requestsText(plan.requests) })) : null,
            unsaved && !running ? el("div", { class: "cr-actions" },
                el("button", {
                    class: "cr-button", text: `Discard ${M.plural(unsaved, "unsaved review")}`,
                    attrs: { type: "button" },
                    on: { click: discardUnsaved },
                })) : null,
            el("div", { class: "cr-actions" },
                el("button", {
                    class: "cr-button primary cr-start",
                    text: running ? "Checking…" : "Check Consistency",
                    attrs: { type: "button", "data-focus": "check-start" },
                    props: { disabled: Boolean(blocked) },
                    on: { click: startCheck },
                }),
                blocked && !(op && op.codebasePath === state.codebasePath)
                    ? el("span", { class: "cr-muted cr-blocked", text: blocked }) : null),
            panel.message ? el("p", { class: `cr-feedback cr-feedback-${panel.message.kind}`, attrs: { role: "status" },
                text: panel.message.text }) : null);
    }

    function outputsNotes(data) {
        if (!data) return null;
        const missing = [];
        if (!data.outputs.summaries) missing.push("No file summaries exist yet, so summaries will not be compared.");
        if (!data.outputs.rules) missing.push("No validated business rules exist yet, so rules and their tests will not be compared.");
        if (!data.outputs.tests) missing.push("No generated unit tests exist yet, so tests will not be compared.");
        if (!missing.length) return null;
        return el("div", { class: "cr-note" },
            el("strong", { text: "The check will be incomplete. " }),
            missing.join(" "));
    }

    function filePicker(files, targets) {
        const panel = state.panel;
        if (targets.loading) return el("p", { class: "cr-muted", text: "Listing files…" });
        if (targets.error || !files.length) return null;

        const needle = panel.filter.trim().toLowerCase();
        const shown = files.filter(f => !needle || f.path.toLowerCase().includes(needle));

        return el("div", { class: "cr-picker" },
            el("input", {
                class: "cr-search",
                attrs: { type: "search", placeholder: "Filter files", "aria-label": "Filter files", "data-focus": "picker-filter" },
                props: { value: panel.filter, disabled: Boolean(state.operation) },
                on: { input: e => { panel.filter = e.target.value; render(); } },
            }),
            el("div", { class: "cr-picker-list", attrs: { role: "group", "aria-label": "Files to check" } },
                shown.map(file => fileRow(file))),
            shown.length < files.length ? el("p", { class: "cr-muted", text: `${shown.length} of ${files.length} files shown.` }) : null);
    }

    function fileRow(file) {
        const panel = state.panel;
        const checked = panel.selected.has(file.path);

        let method = null;
        if (checked && file.methods_supported) {
            const methods = panel.methods.get(file.path) || { loading: true };
            if (methods.data && methods.data.supported) {
                method = el("select", {
                    attrs: { "aria-label": `Method to check in ${file.path}`, "data-focus": `method-${file.path}` },
                    props: { disabled: Boolean(state.operation) },
                    on: { change: e => { panel.selected.set(file.path, e.target.value); panel.message = null; render(); } },
                },
                el("option", { text: "Whole file", attrs: { value: "" } }),
                methods.data.methods.map(m => el("option", {
                    text: m.overloaded ? `${m.symbol} (overloaded: check the whole file)` : m.symbol,
                    attrs: { value: m.symbol, disabled: m.overloaded, selected: panel.selected.get(file.path) === m.symbol },
                })));
            } else if (methods.data) {
                method = el("span", { class: "cr-muted", text: methods.data.reason || "Whole file only." });
            } else if (methods.error) {
                method = el("span", { class: "cr-muted", text: `Whole file only: ${methods.error}` });
            } else {
                method = el("span", { class: "cr-muted", text: "Listing methods…" });
            }
        }

        return el("div", { class: `cr-pick ${checked ? "selected" : ""}` },
            el("label", { class: "cr-pick-label" },
                el("input", {
                    attrs: { type: "checkbox", "data-focus": `pick-${file.path}` },
                    props: { checked, disabled: Boolean(state.operation) },
                    on: { change: e => togglePick(file, e.target.checked) },
                }),
                el("span", { class: "cr-pick-path", text: file.path }),
                el("span", { class: "cr-muted", text: M.artifactsText(file) })),
            method);
    }

    function togglePick(file, checked) {
        const panel = state.panel;
        if (checked) {
            panel.selected.set(file.path, "");
            if (file.methods_supported) loadMethods(file.path);
        } else {
            panel.selected.delete(file.path);
        }
        panel.message = null;
        render();
    }

    function discardUnsaved() {
        const prefix = `${nameOf(state.codebasePath)}|`;
        [...state.drafts.keys()].filter(k => k.startsWith(prefix)).forEach(k => {
            state.drafts.delete(k);
            state.feedback.delete(k);
        });
        render();
    }

    async function startCheck() {
        const panel = state.panel;
        const request = M.checkRequest(panel);
        if (state.operation || request.error) return;        // one check at a time, app-wide

        const files = (panel.targets && panel.targets.data && panel.targets.data.files) || [];
        const op = M.newOperation({
            codebasePath: state.codebasePath,
            codebase: nameOf(state.codebasePath),
            request,
            total: request.scope === "full" ? files.length : request.targets.length,
        });
        op.plan = M.describeCheck(request, files);
        state.operation = op;
        panel.message = null;
        startTicker();
        render();

        let response;
        try {
            response = await state.api.call(
                "check_consistency",
                { codebase: op.codebasePath, scope: request.scope, targets: request.targets },
                { onEvent: event => { M.applyEvent(op, event); refreshRunning(); } },
            );
        } catch (error) {
            response = { success: false, error: String(error && error.message || error) };
        }

        op.done = true;
        op.response = response;
        op.finishedAt = Date.now();
        state.operation = null;
        stopTicker();

        const result = M.outcome(response);
        const here = state.codebasePath === op.codebasePath && isShowing();
        state.outcomes.set(op.codebase, { result, op, shown: here });

        if (here && result.scope) {
            state.scope = result.scope;
            resetView();
            await load(result.checkId);                     // the exact result of this check
        } else {
            render();
        }
    }

    function isShowing() {
        return Boolean(state.container && state.visible);
    }

    function startTicker() {
        stopTicker();
        state.ticker = setInterval(() => {
            const op = state.operation;
            const node = state.container && state.container.querySelector(".cr-elapsed");
            if (op && node) node.textContent = M.formatElapsed(Date.now() - op.startedAt);
        }, 1000);
    }

    function stopTicker() {
        if (state.ticker) clearInterval(state.ticker);
        state.ticker = null;
    }

    /* Redraws only the running card, so typing elsewhere on the screen is not disturbed. */
    function refreshRunning() {
        const node = state.container && state.container.querySelector(".cr-running");
        if (!node || !isShowing()) return;
        const fresh = runningNotice();
        if (fresh) node.replaceWith(fresh);
    }

    function runningNotice() {
        const op = state.operation;
        if (!op) return null;

        if (op.codebasePath !== state.codebasePath) {
            return el("div", { class: "cr-running cr-running-elsewhere", attrs: { role: "status" } },
                el("strong", { text: `A consistency check of ${op.codebase} is running.` }),
                " Other results load when it finishes, because the app's backend runs one task at a time.");
        }

        const usage = M.usageText(op.usage);
        const latest = op.messages.slice(-6);
        return el("section", { class: "cr-running", attrs: { "aria-label": "Running check", "aria-busy": "true" } },
            el("div", { class: "cr-running-head" },
                el("span", { class: "cr-spinner", attrs: { "aria-hidden": "true" } }),
                el("strong", { text: `Checking ${op.codebase}` }), " ",
                el("span", { class: "cr-muted", text: op.plan ? op.plan.scope : "" })),
            op.plan && op.plan.items.length ? list(op.plan.items, text => el("span", { text })) : null,
            el("p", null,
                el("span", { text: `${M.filesText(op)} ` }),
                el("span", { class: "cr-muted", text: "Elapsed " }),
                el("span", { class: "cr-elapsed", text: M.formatElapsed(Date.now() - op.startedAt) }),
                usage ? el("span", { class: "cr-muted", text: ` · ${usage} so far` }) : null),
            op.waits ? el("p", { class: "cr-note", text: `The AI provider asked to wait ${M.plural(op.waits, "time")}; `
                + "the request is retried within the existing limit, never resent by this screen." }) : null,
            latest.length ? el("ul", { class: "cr-progress", attrs: { "aria-live": "polite" } },
                latest.map(m => el("li", { class: m.wait ? "cr-progress-wait" : "", text: m.text }))) : null,
            el("p", { class: "cr-muted", text: "A running check cannot be stopped without losing its result: it ends after "
                + "every selected file has been tried. Other actions in the app wait until then." }));
    }

    function outcomeNotice() {
        const name = nameOf(state.codebasePath);
        const parts = [];

        // Checks of other codebases that finished while this one is shown.
        state.outcomes.forEach((o, codebase) => {
            if (codebase !== name && !o.shown) {
                parts.push(el("p", { class: `cr-feedback cr-feedback-${o.result.kind}`, attrs: { role: "status" },
                    text: `The check of ${codebase} finished: ${o.result.text} Select ${codebase} and open `
                        + "Consistency Review to see it." }));
            }
        });

        const mine = state.outcomes.get(name);
        if (mine && mine.shown) {
            const usage = M.usageText(mine.op.usage);
            parts.push(el("div", { class: `cr-outcome cr-outcome-${mine.result.kind}`, attrs: { role: "status" } },
                el("strong", { text: "Latest check from this session: " }),
                el("span", { text: mine.result.text }),
                usage ? el("span", { class: "cr-muted", text: ` (${usage}; see AI Usage for the full record)` }) : null,
                el("button", {
                    class: "cr-button", text: "Dismiss", attrs: { type: "button" },
                    on: { click: () => { state.outcomes.delete(name); render(); } },
                })));
        }
        return parts.length ? el("div", { class: "cr-outcomes" }, parts) : null;
    }

    function handoffNotice() {
        const h = state.handoff;
        if (!h || !M.handoffMismatch(h.expected, h.shown)) return null;
        return el("p", { class: "cr-note", text: `This is not the result of the check that just ran (${h.expected}). `
            + `${h.shown ? `The saved result is now ${h.shown}.` : "No saved result was found for it."}` });
    }

    window.ConsistencyReviewScreen = {
        open,
        // Called when another Insights view is opened in the same output box.
        hide: () => { state.visible = false; },
        reload: load,
        hasUnsavedReviews: () => state.drafts.size > 0,
        _state: state,
    };
})();
