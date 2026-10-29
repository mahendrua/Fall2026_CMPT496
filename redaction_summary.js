/*
 * redaction_summary.js
 *
 * The Complete screen's line about secrets (US-034): how many secret
 * occurrences the run that just finished had replaced in its AI requests.
 * The number comes from the backend, from that run's own record (the totals
 * last_run_log.json saves); nothing is counted here. A secret sent in
 * several requests counts in each, so it is not a number of unique secrets,
 * and what logs and saved outputs had cleaned is not part of it.
 *
 * Loaded by index.html before renderer.js; tested with node --test.
 */
(function (root, factory) {
    const api = factory();

    if (typeof module !== "undefined" && module.exports) {
        module.exports = api;
    } else {
        root.RedactionSummary = api;
    }
})(globalThis, function () {
    const LABEL = "Secret occurrences redacted from AI requests";

    const NOTE = "Counted per AI request: a secret sent in several requests counts each time. "
        + "Not a count of unique secrets; logs and saved outputs are cleaned too, but not counted here.";

    const NOT_RECORDED = "Not recorded";

    // The backend's category names, in reading order: [name, one, several].
    const CATEGORIES = [
        ["PASSWORD", "password", "passwords"],
        ["API_KEY", "API key", "API keys"],
        ["TOKEN", "token", "tokens"],
        ["AUTHORIZATION", "authorization value", "authorization values"],
        ["PRIVATE_KEY", "private key", "private keys"],
    ];

    const isCount = value => Number.isInteger(value) && value >= 0;

    /**
     * What to show for a run's redactions ({run_id, calls, total, by_category}
     * from the backend). Anything missing or malformed is "Not recorded",
     * never 0; an explicit total of 0 is shown as 0.
     */
    function describe(redactions) {
        if (!redactions || typeof redactions !== "object" || !isCount(redactions.total)) {
            return { label: LABEL, value: NOT_RECORDED, recorded: false, detail: "", note: NOTE };
        }

        const counts = redactions.by_category && typeof redactions.by_category === "object"
            ? redactions.by_category
            : {};
        const known = new Set(CATEGORIES.map(([name]) => name));

        const parts = CATEGORIES
            .filter(([name]) => isCount(counts[name]) && counts[name] > 0)
            .map(([name, one, several]) => `${counts[name]} ${counts[name] === 1 ? one : several}`);

        const other = Object.entries(counts)
            .filter(([name, count]) => !known.has(name) && isCount(count) && count > 0)
            .reduce((sum, [, count]) => sum + count, 0);
        if (other) parts.push(`${other} other`);

        return {
            label: LABEL,
            value: String(redactions.total),
            recorded: true,
            detail: parts.join(", "),
            note: NOTE,
        };
    }

    return { LABEL, NOTE, NOT_RECORDED, describe };
});
