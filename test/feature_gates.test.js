const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const { FEATURES, PAID_PAGES, featureForPage } = require("../subscription_state");
const { PAID_FEATURES } = require("../entitlement_service");

const root = path.join(__dirname, "..");
const rendererJs = fs.readFileSync(path.join(root, "renderer.js"), "utf8");
const indexHtml = fs.readFileSync(path.join(root, "index.html"), "utf8");

const pageIds = [...indexHtml.matchAll(/<div id="([A-Za-z]+)" class="page\b/g)].map(match => match[1]);

function handlerBody(marker) {
    const start = rendererJs.indexOf(marker);
    assert.notEqual(start, -1, `${marker} not found in renderer.js`);
    return rendererJs.slice(start, start + 600);
}

test("the UI and the main process name the same two paid feature groups", () => {
    assert.deepEqual(Object.keys(FEATURES).sort(), Object.keys(PAID_FEATURES).sort());
});

test("every GUI and GitHub page is a paid page, and every paid page exists", () => {
    const featurePages = pageIds.filter(id => /^(gui|github)/i.test(id));
    assert.deepEqual(featurePages.sort(), Object.keys(PAID_PAGES).sort());
    for (const id of featurePages) assert.ok(featureForPage(id), `${id} must be gated`);
});

test("free pages stay free: sign-in, home, Upgrade, API keys, FAQ, local operations, insights", () => {
    for (const id of ["homePage", "upgradePage", "apiPage", "faqAndSupportPage", "analysisPage", "codebasePipelinePage", "mainViewPage"]) {
        assert.ok(pageIds.includes(id), `${id} exists`);
        assert.equal(featureForPage(id), null, `${id} must not be gated`);
    }
    assert.equal(featureForPage("constructor"), null);
});

test("showPage refuses a locked paid page before showing anything", () => {
    const body = handlerBody("function showPage(pageId) {");
    assert.match(body, /^function showPage\(pageId\) \{\s*const paidFeature = window\.CheckpointSubscription\.featureForPage\(pageId\);\s*if \(paidFeature && !ensurePaidFeature\(paidFeature\)\) return false;/);
});

test("no code reveals a paid page without going through showPage", () => {
    for (const id of Object.keys(PAID_PAGES)) {
        assert.doesNotMatch(
            rendererJs,
            new RegExp(`getElementById\\(["']${id}["']\\)\\.classList\\.(remove|toggle)\\(["']hidden`),
            `${id} must only be shown by showPage`
        );
    }
});

test("paid controls inside free pages are gated", () => {
    assert.match(handlerBody('getElementById("connectGithubBtn")'), /ensurePaidFeature\("github"\)/);
    assert.match(handlerBody('getElementById("githubRepositoryForm")'), /ensurePaidFeature\("github"\)/);
    assert.match(handlerBody('getElementById("viewGuiTestingReportsBtn")'), /ensurePaidFeature\("gui"\)/);
    assert.match(handlerBody("async function refreshGuiTestingReports() {"), /ensurePaidFeature\("gui"\)/);
});

test("entry points show a Pro badge while locked", () => {
    for (const [id, feature] of [["guiTestCasesBtn", "gui"], ["connectGithubBtn", "github"], ["viewGuiTestingReportsBtn", "gui"]]) {
        assert.match(indexHtml, new RegExp(`id="${id}"[^>]*data-paid-feature="${feature}"`));
    }
});

test("losing access while on a paid screen leaves it", () => {
    const listener = handlerBody('window.addEventListener("checkpoint-subscription-changed"');
    assert.match(listener, /featureForPage\(currentPageId\)/);
    assert.match(listener, /ensurePaidFeature\(pageFeature, "homePage"\)/);
});

test("the gate reads shared entitlement state rather than a local paid flag", () => {
    const gate = handlerBody("function paidFeatureUnlocked(feature) {");
    assert.match(gate, /window\.checkpointSubscription\?\.hasFeature\(feature\) === true/);
    assert.doesNotMatch(rendererJs, /\b(isPaid|isPro|paidUser|proUser)\s*=/);
});
