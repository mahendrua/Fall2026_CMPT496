// redaction_summary.test.js
//
// What the Complete screen says about a run's redactions (US-034):
//   node --test test/other_tests/redaction_summary.test.js

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("path");

const { LABEL, NOTE, NOT_RECORDED, describe } = require(path.join(__dirname, "..", "..", "redaction_summary.js"));

test("a recorded count is shown with its categories, in reading order", () => {
    const shown = describe({
        run_id: "20261028-200000-abc123", calls: 52, total: 134,
        by_category: { TOKEN: 32, API_KEY: 12, PASSWORD: 53, PRIVATE_KEY: 2, AUTHORIZATION: 35 },
    });

    assert.equal(shown.recorded, true);
    assert.equal(shown.value, "134");
    assert.equal(shown.detail, "53 passwords, 12 API keys, 32 tokens, 35 authorization values, 2 private keys");
});

test("an explicit zero is shown as 0, not as missing", () => {
    const shown = describe({ run_id: "r", calls: 7, total: 0, by_category: {} });

    assert.equal(shown.recorded, true);
    assert.equal(shown.value, "0");
    assert.equal(shown.detail, "");
});

test("no count, or an older record without one, is shown as not recorded, never as 0", () => {
    for (const missing of [undefined, null, {}, { run_id: "r", calls: 3 }, { total: null }, "12", 12]) {
        const shown = describe(missing);
        assert.equal(shown.recorded, false, JSON.stringify(missing));
        assert.equal(shown.value, NOT_RECORDED);
        assert.equal(shown.detail, "");
    }
});

test("a malformed count is not shown as a number", () => {
    for (const total of [-1, 1.5, "7", NaN]) {
        assert.equal(describe({ total, by_category: {} }).value, NOT_RECORDED, String(total));
    }
});

test("categories it does not know are added up, not dropped", () => {
    const shown = describe({ total: 5, by_category: { PASSWORD: 2, NEW_KIND: 3, BROKEN: "x" } });

    assert.equal(shown.detail, "2 passwords, 3 other");
});

test("the label and note say what is counted and what is not", () => {
    assert.equal(LABEL, "Secret occurrences redacted from AI requests");
    assert.match(NOTE, /counts each time/);
    assert.match(NOTE, /Not a count of unique secrets/);
    assert.match(NOTE, /logs and saved outputs .* not counted here/);
    assert.equal(describe({ total: 1, by_category: { PASSWORD: 1 } }).note, NOTE);
});

test("one of a kind is written in the singular", () => {
    assert.equal(describe({ total: 3, by_category: { PASSWORD: 2, TOKEN: 1 } }).detail, "2 passwords, 1 token");
});
