
// ============================================
// Backend State
// ============================================


let pathSubmitted = false;
let codebasePickerPreviousSelection = null;

let ranFullPipeline = false;

let summariesMade = false;
let sourcesMade = false;
let errorsMade = false;


// Currently selected codebase
let selectedCodebasePath = "";
let testResultsDashboardVersion = 0;
let activeTestResultsDashboard = null;
const pendingTestResultsPaths = new Set();


// Selections
let selectedRules = [];

// Backend response buffer
let backendOutputBuffer = "";

//loadingscreen 
let activeCommand = null;
let pipelineMode = false;
let operationReturnPage = "homePage";

let totalSteps = 1;
let currentStep = 0;

let currentStepProgress = 0;


let selectedValidatedRule = null;

let activeFileListView = {
    includePdfs: false,
    onlyPdfs: false,
    recursivePdfs: false
};

let insightFolderHistory = [];
let currentInsightFolderPath = null;
let insightRootPath = null;
let insightParentPath = null;
let suppressAutoEnterOnce = false;

//output
let pendingLine = '';
let lastLine = '';
let summaryPendingLine = '';

// Checks if the API key has been set
const envPath = ".env";

let hasAPI = false; // Flag to track if the API key has been set

//start of the code to check if the API key has been set and determineif the API button should be active or not
//activates the API button if the API key has not been set, otherwise it will be disabled -- also changes attributes within that page and enables the anaylsis button

window.electronAPI.hasAPIKey()
    .then((apiKeyExists) => {
        hasAPI = apiKeyExists;

        if (hasAPI) {

            showApiKeyPresent();

            const analysisBtnEl = document.getElementById("analysisBtn");
            if (analysisBtnEl) analysisBtnEl.classList.remove("unusable-btn");

            const apiKeyMsgEl = document.getElementById("apiKeyMsg");
            if (apiKeyMsgEl) apiKeyMsgEl.classList.remove("hidden");

            const replaceApiKeyBtnEl = document.getElementById("replaceApiKeyBtn");
            if (replaceApiKeyBtnEl) replaceApiKeyBtnEl.classList.remove("hidden");

            const keepApiKeyBtnEl = document.getElementById("keepApiKeyBtn");
            if (keepApiKeyBtnEl) keepApiKeyBtnEl.classList.remove("hidden");

            const submitApiKeyBtnEl = document.getElementById("submitApiKeyBtn");
            if (submitApiKeyBtnEl) submitApiKeyBtnEl.classList.add("hidden");

            const apiKeyInputEl = document.getElementById("apiKeyInput");
            if (apiKeyInputEl) apiKeyInputEl.classList.add("hidden");

            const apiBackBtnEl = document.getElementById("apiBackBtn");
            if (apiBackBtnEl) apiBackBtnEl.classList.add("hidden");
        }

    });

// ============================================
// Backend Communication Helpers
// ============================================

function showLoading(title, message) {

    const currentPage = document.querySelector(".page:not(.hidden)");
    if (currentPage?.id) {
        operationReturnPage = currentPage.id;
    }

    const overlay =
        document.getElementById("loadingOverlay");

    if (!overlay) return;

    overlay.classList.remove("token-report-mode");
    overlay.classList.remove("hidden");

    document.getElementById("loadingTitle").textContent = title;
    document.getElementById("loadingStepMessage").textContent = message;

    // show spinner & loading bar
    document.getElementById("loadingSpinner").classList.remove("hidden");
    document.getElementById("stepProgressContainer").classList.remove("hidden");

    // Hide pipeline progress by default
    document.getElementById("pipelineProgressContainer").classList.add("hidden");


    //hide ok button
    document.getElementById("loadingOkBtn").classList.add("hidden");
    document.getElementById("loadingCancelBtn").classList.remove("hidden");
    document.getElementById("loadingCancelBtn").disabled = false;

    // hide any report left over from a previous estimate
    const previousEstimate =
        document.getElementById("estimateOutput");

    if (previousEstimate) {
        previousEstimate.classList.add("hidden");
        previousEstimate.replaceChildren();
        previousEstimate.classList.remove("token-report-estimate", "token-report-calibration");
    }

    resetTokenMeter();

   
}


function updateStepLoading(message) {

    const messageElement =
        document.getElementById("loadingStepMessage");

    if (!messageElement) return;

    message = message.replace(
        /^\s*\[\s*progress\s*\]\s*/i,
        ""
    );

    messageElement.textContent = message;
}

function updatePipelineLoading(message) {

    const messageElement =
        document.getElementById("loadingPipelineMessage");

    if (!messageElement) return;

    message = message.replace(
        /^\s*\[\s*progress\s*\]\s*/i,
        ""
    );

    messageElement.textContent = message;
}





// ============================================
// Live token meter
// ============================================

function resetTokenMeter() {

    const meter =
        document.getElementById("tokenMeter");

    if (!meter) return;

    meter.classList.add("hidden");

    document.getElementById("tokenMeterValue")
        .textContent = "0";

    document.getElementById("tokenMeterStages")
        .textContent = "";

    showLoadingWarning(null);

}


// Shown on the Complete screen when a command finished but had to leave
// something out, e.g. UML diagrams skipped because the AI's diagram text was
// invalid. Without it a missing diagram looks like a program mistake.
function showLoadingWarning(text) {

    const warning =
        document.getElementById("loadingWarning");

    if (!warning) return;

    warning.textContent = text || "";
    warning.classList.toggle("hidden", !text);

}


function updateTokenMeter(response) {

    const meter =
        document.getElementById("tokenMeter");

    if (!meter) return;

    meter.classList.remove("hidden");

    const total = response.total_tokens ?? 0;

    document.getElementById("tokenMeterValue")
        .textContent = total.toLocaleString();

}

function createTokenReportElement(tagName, className, text) {
    const element = document.createElement(tagName);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
}

function renderTokenEstimateResult(result) {
    const report = document.getElementById("estimateOutput");
    if (!report) return;

    report.replaceChildren();
    report.classList.remove("token-report-calibration");
    report.classList.add("token-report-estimate");

    const heading = createTokenReportElement("div", "token-report-heading");
    heading.appendChild(createTokenReportElement("h3", "", "Token Estimate:"));
    heading.appendChild(createTokenReportElement(
        "span",
        "token-report-codebase-tag",
        result.codebase_name || getSelectedCodebaseName() || "Selected codebase"
    ));
    report.appendChild(heading);

    const chips = createTokenReportElement("div", "token-report-chips");
    if (Number.isFinite(result.files_after_exclusions)) {
        chips.appendChild(createTokenReportElement(
            "span", "token-report-chip", `📄 ${result.files_after_exclusions.toLocaleString()} Source Files`
        ));
    }
    if (Number.isFinite(result.directories)) {
        chips.appendChild(createTokenReportElement(
            "span", "token-report-chip", `📁 ${result.directories.toLocaleString()} Directories`
        ));
    }
    if (chips.childElementCount) report.appendChild(chips);

    if (Number.isFinite(result.excluded_files) && result.excluded_files > 0) {
        const warning = createTokenReportElement("div", "token-report-warning");
        warning.appendChild(createTokenReportElement("span", "token-report-warning-icon", "⚠"));
        warning.appendChild(createTokenReportElement(
            "p",
            "",
            `Skipping ${result.excluded_files.toLocaleString()} files in generated or vendored folders (node_modules, .venv, bin, obj), saving ${Number(result.excluded_tokens || 0).toLocaleString()} tokens.`
        ));
        report.appendChild(warning);
    }

    const table = createTokenReportElement("table", "token-report-table");
    const tableHead = document.createElement("thead");
    const headerRow = document.createElement("tr");
    headerRow.appendChild(createTokenReportElement("th", "", "Stage"));
    headerRow.appendChild(createTokenReportElement("th", "token-report-value-head", "Estimated input tokens"));
    tableHead.appendChild(headerRow);
    table.appendChild(tableHead);
    const tableBody = document.createElement("tbody");

    (Array.isArray(result.stages) ? result.stages : []).forEach(stage => {
        const row = document.createElement("tr");
        const name = createTokenReportElement("th", "token-report-stage-name", stage.stage || "Stage");
        name.scope = "row";
        if (stage.note) name.title = stage.note;
        const value = createTokenReportElement("td", "token-report-stage-value");

        if (stage.uses_ai === false) {
            value.appendChild(createTokenReportElement("span", "token-report-state token-report-state-free", "● free (no AI)"));
        } else if (stage.input_tokens === null || stage.input_tokens === undefined) {
            value.appendChild(createTokenReportElement("span", "token-report-state token-report-state-prior", "! needs a prior run"));
        } else {
            const low = Number(stage.input_tokens).toLocaleString();
            const high = Number.isFinite(stage.input_tokens_max)
                ? ` – ${Number(stage.input_tokens_max).toLocaleString()}`
                : "";
            value.appendChild(document.createTextNode(`${low}${high}`));
            if (stage.confidence === "range") {
                value.appendChild(createTokenReportElement("span", "token-report-range-badge", "range"));
            }
        }

        row.append(name, value);
        tableBody.appendChild(row);
    });

    if (Number.isFinite(result.total_input_low) && Number.isFinite(result.total_input_high)) {
        const row = document.createElement("tr");
        row.className = "token-report-total-row";
        const label = createTokenReportElement("th", "token-report-total-label", "Estimated input total");
        label.scope = "row";
        const value = createTokenReportElement(
            "td",
            "token-report-total-value",
            result.total_input_low === result.total_input_high
                ? `${result.total_input_low.toLocaleString()} tokens`
                : `${result.total_input_low.toLocaleString()} – ${result.total_input_high.toLocaleString()} tokens`
        );
        row.append(label, value);
        tableBody.appendChild(row);
    }

    table.appendChild(tableBody);
    report.appendChild(table);
    report.classList.remove("hidden");
}

function getTokenCalibrationStageMetrics(evidence, fileSummary) {
    const definitions = [
        { name: "build_database", label: "Build database", doesNotUseLlm: true },
        { name: "generate_file_summaries", label: "File summaries" },
        { name: "generate_directory_summaries", label: "Directory summaries" },
        { name: "validate_business_rules", label: "Business rule validation" },
    ];

    return definitions.map(stage => {
        if (stage.doesNotUseLlm) {
            return { ...stage, status: "no-llm", calls: null, totalInputTokens: null, avgTokensPerCall: null };
        }

        const evidenceEntry = evidence.find(item => item.stage === stage.name);
        const fileStage = stage.name === "generate_file_summaries" ? fileSummary : null;
        const source = fileStage && Number(fileStage.calls) > 0 ? fileStage : evidenceEntry;
        if (!source) {
            return { ...stage, status: "not-recorded", calls: null, totalInputTokens: null, avgTokensPerCall: null };
        }

        const rawCalls = source.calls;
        const calls = rawCalls !== null && rawCalls !== undefined && Number.isFinite(Number(rawCalls))
            ? Number(rawCalls)
            : null;
        const rawMissingUsage = source.missing_usage ?? source.calls_missing_usage ?? evidenceEntry?.missing_usage;
        const missingUsage = Number.isFinite(Number(rawMissingUsage)) ? Number(rawMissingUsage) : 0;
        const rawTotal = source.input_tokens;
        const hasTotal = rawTotal !== null && rawTotal !== undefined && Number.isFinite(Number(rawTotal));

        if (calls === null || calls <= 0) {
            return { ...stage, status: "not-recorded", calls: null, totalInputTokens: null, avgTokensPerCall: null };
        }
        if (missingUsage > 0) {
            return { ...stage, status: "incomplete", calls, totalInputTokens: null, avgTokensPerCall: null };
        }
        if (!hasTotal) {
            return { ...stage, status: "usage-unavailable", calls, totalInputTokens: null, avgTokensPerCall: null };
        }

        const totalInputTokens = Number(rawTotal);
        return {
            ...stage,
            status: "calibrated",
            calls,
            totalInputTokens,
            avgTokensPerCall: Number((totalInputTokens / calls).toFixed(1)),
        };
    });
}

function renderTokenCalibrationResult(result) {
    const report = document.getElementById("estimateOutput");
    if (!report) return;

    report.replaceChildren();
    report.classList.remove("token-report-estimate");
    report.classList.add("token-report-calibration");

    const evidence = Array.isArray(result.evidence) ? result.evidence : [];
    const stageMetrics = getTokenCalibrationStageMetrics(evidence, result.file_summary_actual);
    const hasRecordedBaseline = stageMetrics.some(stage => stage.status === "calibrated");

    const heading = createTokenReportElement("div", "token-calibration-heading");
    heading.appendChild(createTokenReportElement(
        "p",
        "token-calibration-subtitle",
        hasRecordedBaseline
            ? "Calibration analyzed past execution logs to establish baseline token usage per call for future cost estimation."
            : "Calibration checked past execution logs, but no complete token usage was available to establish a baseline."
    ));
    heading.appendChild(createTokenReportElement(
        "span",
        "token-report-codebase-tag",
        result.codebase_name || getSelectedCodebaseName() || "Selected codebase"
    ));
    report.appendChild(heading);

    const tablePanel = createTokenReportElement("section", "token-calibration-breakdown");
    tablePanel.appendChild(createTokenReportElement("h3", "token-calibration-breakdown-title", "Calibration stage breakdown"));
    const tableWrap = createTokenReportElement("div", "token-calibration-table-wrap");
    const table = createTokenReportElement("table", "token-calibration-table");
    const tableHead = document.createElement("thead");
    const headerRow = document.createElement("tr");
    ["Pipeline Stage", "LLM Calls", "Total Input Tokens", "Avg Tokens / Call", "Calibration Status"].forEach(label => {
        headerRow.appendChild(createTokenReportElement("th", "", label));
    });
    tableHead.appendChild(headerRow);
    table.appendChild(tableHead);

    const tableBody = document.createElement("tbody");
    stageMetrics.forEach(stage => {
        const row = document.createElement("tr");
        row.className = `token-calibration-row is-${stage.status}`;
        row.appendChild(createTokenReportElement("th", "token-calibration-stage-name", stage.label));

        let callsText = "Not recorded";
        let totalText = "Not recorded";
        let averageText = "Not recorded";
        let statusText = "Not recorded";
        let statusClass = "token-calibration-status-muted";

        if (stage.status === "no-llm") {
            callsText = "Not required";
            totalText = "Not required";
            averageText = "Not required";
            statusText = "No LLM calls required";
            statusClass = "token-calibration-status-muted";
        } else if (stage.status === "incomplete") {
            callsText = `${stage.calls.toLocaleString()} ${stage.calls === 1 ? "call" : "calls"}`;
            statusText = "Usage incomplete";
            statusClass = "token-calibration-status-warning";
        } else if (stage.status === "usage-unavailable") {
            callsText = `${stage.calls.toLocaleString()} ${stage.calls === 1 ? "call" : "calls"}`;
            statusText = "Usage unavailable";
            statusClass = "token-calibration-status-warning";
        } else if (stage.status === "calibrated") {
            callsText = `${stage.calls.toLocaleString()} ${stage.calls === 1 ? "call" : "calls"}`;
            totalText = stage.totalInputTokens.toLocaleString();
            averageText = `${stage.avgTokensPerCall.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 1 })} tokens/call`;
            statusText = "Calibrated";
            statusClass = "token-calibration-status-ready";
        }

        row.appendChild(createTokenReportElement("td", "token-calibration-numeric", callsText));
        row.appendChild(createTokenReportElement("td", "token-calibration-numeric", totalText));
        row.appendChild(createTokenReportElement("td", "token-calibration-numeric", averageText));
        const statusCell = document.createElement("td");
        statusCell.appendChild(createTokenReportElement("span", `token-calibration-status ${statusClass}`, statusText));
        row.appendChild(statusCell);
        tableBody.appendChild(row);
    });
    table.appendChild(tableBody);
    tableWrap.appendChild(table);
    tablePanel.appendChild(tableWrap);
    report.appendChild(tablePanel);

    const suggestions = result.suggestions && typeof result.suggestions === "object"
        ? Object.entries(result.suggestions)
        : [];
    if (suggestions.length) {
        const values = createTokenReportElement("div", "token-calibration-suggestions");
        values.appendChild(createTokenReportElement("h4", "", "Suggested values"));
        values.appendChild(createTokenReportElement(
            "p",
            "token-calibration-suggestion-note",
            "These values are not applied automatically. Future estimates continue using the configured constants until they are updated."
        ));
        suggestions.forEach(([constant, value]) => {
            const row = createTokenReportElement("div", "token-calibration-measured-row");
            row.appendChild(createTokenReportElement("code", "", constant));
            row.appendChild(createTokenReportElement("strong", "", Number(value).toLocaleString()));
            values.appendChild(row);
        });
        report.appendChild(values);
    }

    report.classList.remove("hidden");
}

function addTokenMeterStage(response) {

    const meter =
        document.getElementById("tokenMeter");

    const list =
        document.getElementById("tokenMeterStages");

    if (!meter || !list) return;

    meter.classList.remove("hidden");

    const used =
        (response.input_tokens ?? 0) + (response.output_tokens ?? 0);

    // The pipeline records itself as well as its stages, so showing its row
    // alongside them would look like double counting. Its figure is already
    // the running total above.
    if (response.stage === "full_pipeline") {

        document.getElementById("tokenMeterValue")
            .textContent = used.toLocaleString();

        return;
    }

    const row = document.createElement("div");
    row.className = "stage-row";

    const name = document.createElement("span");
    name.textContent = response.stage;

    const amount = document.createElement("span");
    amount.textContent =
        `${used.toLocaleString()} (${response.calls} calls)`;

    row.appendChild(name);
    row.appendChild(amount);
    list.appendChild(row);

}


function finishLoading(message = "Process completed successfully!") {

    document.getElementById("loadingTitle").textContent = "Complete";


    const stepMessage = document.getElementById("loadingStepMessage");

    if(stepMessage){
        stepMessage.textContent = message;
    }


    // hide spinner and progress bars
    document.getElementById("loadingSpinner").classList.add("hidden");
    document.getElementById("stepProgressContainer").classList.add("hidden");
    document.getElementById("pipelineProgressContainer").classList.add("hidden");
    document.getElementById("stepProgressTitle").classList.add("hidden");


    // show OK button
    document.getElementById("loadingOkBtn").classList.remove("hidden");
    document.getElementById("loadingCancelBtn").classList.add("hidden");
}

function hideLoading() {

    const overlay =
        document.getElementById("loadingOverlay");

    if (!overlay) return;

    overlay.classList.add("hidden");

    const cancelButton = document.getElementById("loadingCancelBtn");
    if (cancelButton) {
        cancelButton.classList.add("hidden");
        cancelButton.disabled = false;
    }
}

function returnToOperationPage() {
    const destination = document.getElementById(operationReturnPage)
        ? operationReturnPage
        : "homePage";
    operationReturnPage = "homePage";
    showPage(destination);
}

function showErrorPopup(message) {
    const popup = document.getElementById("errorPopup");
    const messageElement = document.getElementById("errorPopupMessage");

    if (!popup || !messageElement) return;

    messageElement.textContent = String(message || "An unexpected error occurred.");
    popup.classList.remove("hidden");
}

function hideErrorPopup() {
    const popup = document.getElementById("errorPopup");

    if (popup) popup.classList.add("hidden");
}

function updatePipleineProgressBar(percent){

    document.getElementById("pipelineProgressContainer").classList.remove("hidden");
    document.getElementById("stepProgressTitle").classList.remove("hidden");

    const bar =
        document.getElementById("loadingPipelineProgressBar");

    const text =
        document.getElementById("loadingPipelineProgressText");


    if(bar)
        bar.style.width = `${percent}%`;

    if(text)
        text.textContent = `${Math.round(percent)}%`;
}

function updateStepProgressBar(percent){

    const bar =
        document.getElementById("loadingStepProgressBar");

    const text =
        document.getElementById("loadingStepProgressText");


    if(bar)
        bar.style.width = `${percent}%`;

    if(text)
        text.textContent = `${Math.round(percent)}%`;
}


async function runBackendCommand(command,args={}){

    invalidateTestResultsDashboard();

    activeCommand = command;


    return await window.electronAPI.executeCommand(
        command,
        args
    );

}

async function refreshErrorLog() {
    const response = await window.electronAPI.getErrorLog();

    if (response?.success) {
        renderErrorPreview(response.errors);
    } else {
        renderTextPreview(response?.error || "Could not load errors.");
    }
}


async function runPreviewCommand(
    action,
    args = {}
){

    invalidateTestResultsDashboard();

    console.log(
        "Preview:",
        action,
        args
    );

    const response = await window.electronAPI.previewCommand(
        action,
        args
    );

    console.log("Preview response:", response);

    if (!response) {
        console.error("No preview response returned");
        return response;
    }

    if (!response.success) {
        console.error("Preview command failed:", response.error);
        return response;
    }

    if (response.files) {
        console.log("Rendering file buttons", response.files);
        renderViewFileButtons(response.files, activeFileListView, response.path);
    }

    if (response.type === "file-preview") {
        const preview = response.preview || {};

        // Clear existing output
        const outputBox = document.getElementById("viewDisplayOutputBox");
        if (outputBox) outputBox.innerHTML = "";

        if (preview.type === "pdf") {
            renderPdfPreview(preview.content);
        } else if (preview.type === "summary") {
            renderSummaryPreview(preview.content);
        } else if (preview.type === "source") {
            renderSourcePreview(preview.content);
        } else if (preview.type === "business_rules") {
            renderBusinessRulesPreview(preview.content);
        } else if (preview.type === "integration_tests") {
            renderIntegrationTestsPreview(preview.content);
        } else {
            // Unknown or plain text — try to pretty-print JSON when possible
            renderJsonPreview(preview.content);
        }
    }

    return response;
}


function renderViewSummaryButtons(collections) {

    const container =
        document.getElementById("viewSummariesBtns");

    container.innerHTML = "";

    container.classList.remove("hidden");

    collections.forEach(collection => {

        const button =
            document.createElement("button");

        button.className =
            "btn-secondary";

        button.textContent =
            collection;

        button.addEventListener("click", () => {

            const codebaseName = getSelectedCodebaseName();

            if (!codebaseName) {
                renderTextPreview("Select a codebase first from the pipeline page.");
                return;
            }

            const summaryPath = [
                "agent",
                "file_summary_agent_output",
                codebaseName,
                `${collection}.json`
            ].join("/");

            runPreviewCommand(
                "open_file",
                {
                    path: summaryPath
                }
            );

        });

        container.appendChild(button);

    });

}

//this function will display the buttons when the files btn is clicked
function setActiveFileListView(options = {}) {
    activeFileListView = {
        includePdfs: Boolean(options.includePdfs),
        onlyPdfs: Boolean(options.onlyPdfs),
        recursivePdfs: Boolean(options.recursivePdfs)
    };
}

function resetInsightFolderNavigation(rootPath) {
    // Clear history and wait for the renderer response to provide
    // the canonical (absolute) path. We avoid storing the raw
    // incoming rootPath because it may be relative.
    insightFolderHistory = [];
    currentInsightFolderPath = null;
    insightRootPath = null;
    insightParentPath = null;
}

function normalizePath(p) {
    if (!p) return p;
    return p.replace(/\\/g, "/").replace(/\/+$|\\+$/g, "").toLowerCase();
}

function getSelectedCodebaseName() {
    if (!selectedCodebasePath) return null;
    const parts = selectedCodebasePath.split(/[\\/]/).filter(Boolean);
    return parts.length ? parts[parts.length - 1] : null;
}

function updateCodebaseSummary() {
    const codebaseName = getSelectedCodebaseName();

    const homeCodebaseName = document.getElementById("homeActiveCodebaseName");
    const homeCodebaseProject = document.getElementById("homeActiveCodebaseProject");
    const homeActiveStatus = document.getElementById("homeActiveStatus");
    const homeActiveCodebase = document.getElementById("homeActiveCodebase");

    if (homeCodebaseName && homeCodebaseProject && homeActiveStatus && homeActiveCodebase) {
        const hasCodebase = Boolean(codebaseName);
        homeCodebaseName.textContent = codebaseName || "No codebase selected";
        homeCodebaseProject.classList.toggle("is-empty", !hasCodebase);
        homeActiveStatus.classList.toggle("hidden", !hasCodebase);
        homeActiveCodebase.setAttribute(
            "aria-label",
            hasCodebase ? `${codebaseName}, active codebase` : "No codebase selected"
        );
        homeCodebaseName.title = hasCodebase ? selectedCodebasePath : "";
    }

    document.querySelectorAll(".codebase-summary").forEach(summary => {
        const emptyMessage = summary.querySelector(".codebase-summary-empty");
        const details = summary.querySelector(".codebase-summary-details");

        if (!codebaseName) {
            emptyMessage.classList.remove("hidden");
            details.classList.add("hidden");
            return;
        }

        emptyMessage.classList.add("hidden");
        details.classList.remove("hidden");
        summary.querySelector("[data-summary-name]").textContent = codebaseName;
        summary.querySelector("[data-summary-language]").textContent = "Not detected";
        const pathElement = summary.querySelector("[data-summary-path]");
        pathElement.textContent = selectedCodebasePath;
        pathElement.title = selectedCodebasePath;
    });
}

function invalidateTestResultsDashboard() {
    testResultsDashboardVersion += 1;
    activeTestResultsDashboard = null;
}

function normalizeReportPath(value) {
    return String(value || "").replace(/\\/g, "/").replace(/\/+$/g, "").toLowerCase();
}

async function readTestResultsJson(filePath, load) {
    const normalizedPath = normalizeReportPath(filePath);
    pendingTestResultsPaths.add(normalizedPath);
    load.pendingPath = normalizedPath;

    try {
        const response = await window.electronAPI.previewCommand("open_file", { path: filePath });
        if (!response?.success) {
            return { data: null, error: response?.error || "Report unavailable." };
        }

        const content = response.preview?.content;
        const data = typeof content === "string" ? JSON.parse(content) : content;
        if (!data || typeof data !== "object" || Array.isArray(data)) {
            return { data: null, error: "Report has an unsupported format." };
        }

        return { data, error: "" };
    } catch (error) {
        return { data: null, error: error.message || "Report unavailable." };
    } finally {
        pendingTestResultsPaths.delete(normalizedPath);
        if (load.pendingPath === normalizedPath) load.pendingPath = null;
    }
}

function appendTestResultDetail(section, title, items, emptyMessage) {
    const details = document.createElement("details");
    details.className = "test-results-details";

    const summary = document.createElement("summary");
    summary.textContent = title;
    details.appendChild(summary);

    if (!items.length) {
        const message = document.createElement("p");
        message.textContent = emptyMessage;
        details.appendChild(message);
    } else {
        const list = document.createElement("ul");
        items.forEach(item => {
            const entry = document.createElement("li");
            entry.textContent = item;
            list.appendChild(entry);
        });
        details.appendChild(list);
    }

    section.appendChild(details);
}

function appendTestResultCard(container, title, value) {
    const card = document.createElement("div");
    card.className = "test-result-card";

    const label = document.createElement("span");
    label.className = "test-result-card-label";
    label.textContent = title;

    const count = document.createElement("strong");
    count.className = "test-result-card-value";
    count.textContent = value === null ? "Unavailable" : value.toLocaleString();

    card.append(label, count);
    container.appendChild(card);
}

function recordedTestCount(value) {
    return Number.isInteger(value) && value >= 0 ? value : null;
}

function renderTestResultsEmpty(codebaseName, message) {
    const output = document.getElementById("viewDisplayOutputBox");
    if (!output) return;

    output.replaceChildren();
    const dashboard = document.createElement("section");
    dashboard.className = "test-results-dashboard";

    const title = document.createElement("h2");
    title.className = "test-results-title";
    title.textContent = `Test Results${codebaseName ? `: ${codebaseName}` : ""}`;

    const empty = document.createElement("p");
    empty.className = "test-results-empty";
    empty.textContent = message;

    dashboard.append(title, empty);
    output.appendChild(dashboard);
}

function renderTestResultsDashboard(codebaseName, runLog, results) {
    const output = document.getElementById("viewDisplayOutputBox");
    if (!output) return;

    output.replaceChildren();
    const dashboard = document.createElement("section");
    dashboard.className = "test-results-dashboard";

    const title = document.createElement("h2");
    title.className = "test-results-title";
    title.textContent = `Test Results: ${codebaseName}`;

    const runLabel = document.createElement("p");
    runLabel.className = "test-results-run-id";
    runLabel.textContent = `Run ${runLog.run_id}`;
    dashboard.append(title, runLabel);

    results.forEach(({ kind, report, error }) => {
        const section = document.createElement("section");
        section.className = "test-results-kind";

        const heading = document.createElement("h3");
        heading.textContent = `${kind} Tests`;
        section.appendChild(heading);

        if (!report) {
            const unavailable = document.createElement("p");
            unavailable.className = "test-results-unavailable";
            unavailable.textContent = error || "No report was recorded for this run.";
            section.appendChild(unavailable);
            dashboard.appendChild(section);
            return;
        }

        const dropped = Array.isArray(report.dropped) ? report.dropped : null;
        const cards = document.createElement("div");
        cards.className = "test-result-cards";
        appendTestResultCard(cards, "Passed", recordedTestCount(report.passed));
        appendTestResultCard(cards, "Failed", recordedTestCount(report.failed));
        appendTestResultCard(cards, "Discarded", dropped ? dropped.length : null);
        section.appendChild(cards);

        const passedCount = recordedTestCount(report.passed);
        appendTestResultDetail(
            section,
            "Passed test names",
            [],
            passedCount === 0
                ? "No tests passed in this run."
                : "Passed test names were not recorded; compiled tests are not counted as passed."
        );

        const failedNames = Array.isArray(report.failed_tests)
            ? report.failed_tests.filter(name => typeof name === "string")
            : [];
        appendTestResultDetail(
            section,
            "Failed tests",
            failedNames,
            recordedTestCount(report.failed) === null
                ? "Failed test names are unavailable."
                : report.failed > 0
                    ? "Failed test names were not recorded."
                    : "No failed test names were recorded."
        );

        const droppedItems = dropped
            ? dropped.map(item => {
                const name = typeof item?.name === "string" ? item.name : "Unnamed test";
                const reason = typeof item?.reason === "string" ? item.reason : "Discard reason unavailable.";
                return `${name}: ${reason}`;
            })
            : [];
        appendTestResultDetail(
            section,
            "Discarded tests and reasons",
            droppedItems,
            dropped ? "No tests were discarded." : "Discarded test details were not recorded."
        );

        if (failedNames.length > 0) {
            const failureNote = document.createElement("p");
            failureNote.className = "test-results-note";
            failureNote.textContent = report.html_report
                ? `Failure messages are in the recorded HTML report: ${report.html_report}`
                : "Failure messages were not recorded in this JSON report.";
            section.appendChild(failureNote);
        }

        if (report.project_error) {
            const projectError = document.createElement("p");
            projectError.className = "test-results-error";
            projectError.textContent = `Test project error: ${report.project_error}`;
            section.appendChild(projectError);
        }

        dashboard.appendChild(section);
    });

    output.appendChild(dashboard);
}

async function loadTestResultsDashboard(codebasePath, codebaseName) {
    const load = {
        version: ++testResultsDashboardVersion,
        codebasePath,
        pendingPath: null,
    };
    activeTestResultsDashboard = load;
    renderTestResultsEmpty(codebaseName, "Loading the latest recorded test results...");

    const current = () => (
        activeTestResultsDashboard === load &&
        selectedCodebasePath === codebasePath &&
        testResultsDashboardVersion === load.version
    );

    const runResult = await readTestResultsJson("last_run_log.json", load);
    if (!current()) return;

    const runLog = runResult.data;
    if (!runLog?.run_id || runLog.status === "running" || runLog.codebase !== codebaseName) {
        renderTestResultsEmpty(codebaseName, "No test results are available for the current run.");
        return;
    }

    const reportSources = [
        { kind: "Unit", path: `agent/UT_agent_output/${codebaseName}/test_report.json` },
        { kind: "Integration", path: `agent/IT_agent_output/${codebaseName}/test_report.json` },
    ];
    const results = [];

    for (const source of reportSources) {
        const loaded = await readTestResultsJson(source.path, load);
        if (!current()) return;

        const report = loaded.data;
        const isCurrentRun = report?.run_id === runLog.run_id &&
            normalizeReportPath(report.codebase_path) === normalizeReportPath(codebasePath);
        results.push({
            kind: source.kind,
            report: isCurrentRun ? report : null,
            error: loaded.error || "No results were recorded for this run.",
        });
    }

    if (!current()) return;
    if (!results.some(result => result.report)) {
        renderTestResultsEmpty(codebaseName, "No test results were recorded for the current run and codebase.");
        return;
    }

    renderTestResultsDashboard(codebaseName, runLog, results);
}

function enterInsightFolder(targetPath) {

    const normTarget = normalizePath(targetPath);
    const normCurrent = normalizePath(currentInsightFolderPath);

    console.log("enterInsightFolder() target:", targetPath);
    console.log("  normTarget:", normTarget, "normCurrent:", normCurrent);

    if (normCurrent && normTarget && normCurrent !== normTarget) {
        insightFolderHistory.push(currentInsightFolderPath);
    }

    currentInsightFolderPath = targetPath;

    runPreviewCommand("files", {
        path: targetPath,
        recursivePdfs: activeFileListView.recursivePdfs
    });
}

function goBackOneInsightFolder() {
    console.log("goBackOneInsightFolder() history:", insightFolderHistory, "root:", insightRootPath, "current:", currentInsightFolderPath);
    if (insightFolderHistory.length > 0) {
        currentInsightFolderPath = insightFolderHistory.pop();

        // When navigating back, avoid immediately auto-entering
        // a single-child directory. The next render will honor
        // `suppressAutoEnterOnce` and not auto-enter.
        suppressAutoEnterOnce = true;

        runPreviewCommand("files", {
            path: currentInsightFolderPath,
            recursivePdfs: activeFileListView.recursivePdfs
        });
        return;
    }

    // If there is no history, and we have a canonical root, show it.
    if (insightRootPath) {
        currentInsightFolderPath = insightRootPath;
        console.log("goBackOneInsightFolder(): no history, returning to root:", insightRootPath);
        runPreviewCommand("files", {
            path: insightRootPath,
            recursivePdfs: activeFileListView.recursivePdfs
        });
    }
}

function toFileUrl(filePath) {
    const normalizedPath = filePath.replace(/\\/g, "/");
    return normalizedPath.startsWith("/")
        ? `file://${normalizedPath}`
        : `file:///${normalizedPath}`;
}

function renderViewFileButtons(files, options = {}, currentPath = null)
{
    const container =
        document.getElementById("viewFilesBtns");

    container.innerHTML = "";

    container.classList.remove("hidden");

    const activeOptions = {
        includePdfs: Boolean(options.includePdfs),
        onlyPdfs: Boolean(options.onlyPdfs),
        recursivePdfs: Boolean(options.recursivePdfs)
    };

    /*if (currentPath) {
        if (!insightRootPath || currentPath === insightRootPath) {
            resetInsightFolderNavigation(currentPath);
        } else if (!currentInsightFolderPath) {
            currentInsightFolderPath = currentPath;
        }
    }*/

    if (currentPath) {
        // Ensure we use the canonical path returned by the backend
        // as the root so absolute/relative mismatches don't occur.
        if (!insightRootPath) {
            console.log("renderViewFileButtons(): setting root to", currentPath);
            insightRootPath = currentPath;
            currentInsightFolderPath = currentPath;
            insightFolderHistory = [];
        } else if (currentPath !== currentInsightFolderPath) {
            // If the renderer is showing a different path than we
            // currently have, set it without pushing into history.
            console.log("renderViewFileButtons(): updating currentInsightFolderPath to", currentPath);
            currentInsightFolderPath = currentPath;
        }
    }

    const filteredFiles = files.filter(file => {
        const name = file.name.toLowerCase();
        const isPdf = name.endsWith('.pdf');
        const isPy = name.endsWith('.py');
        const isTxt = name.endsWith('.txt');
        const isPycache = file.isDirectory && name === '__pycache__';
        const isBusinessRulesFolder = file.isDirectory && name === 'business_rules';

        if (isBusinessRulesFolder || isPycache) {
            return false;
        }

        if (isPy || isTxt) {
            return false;
        }

        if (activeOptions.onlyPdfs) {
            return isPdf;
        }

        if (!activeOptions.includePdfs && isPdf) {
            return false;
        }

        return true;
    });

    // If there's only one directory, automatically navigate into it
    // unless the render was triggered by a Back action. In that
    // case `suppressAutoEnterOnce` will prevent re-entering so the
    // UI actually stays at the parent folder.
    if (filteredFiles.length === 1 && filteredFiles[0].isDirectory) {
        console.log("renderViewFileButtons(): single directory detected ->", filteredFiles[0].path, "suppressAutoEnterOnce:", suppressAutoEnterOnce);

        if (suppressAutoEnterOnce) {
            // consume the suppression and show the parent contents
            suppressAutoEnterOnce = false;
        } else {
            enterInsightFolder(filteredFiles[0].path);
            return;
        }
    }

    filteredFiles.forEach(file => {

        const button =
            document.createElement("button");

        button.className = file.isDirectory ? "btn-folder" : "btn-secondary";

        button.textContent = file.name;

        button.addEventListener("click", () => {

            if(file.isDirectory){

                enterInsightFolder(file.path);

            }
            else{

                runPreviewCommand(
                    "open_file",
                    {
                        path:file.path
                    }
                );

            }

        });

        container.appendChild(button);

    });

    if (insightFolderHistory.length > 0) {
        console.log("renderViewFileButtons(): showing back button, history:", insightFolderHistory);
        const backButton = document.createElement("button");
        backButton.className = "back-btn";
        backButton.textContent = "⬅ Back";
        backButton.addEventListener("click", goBackOneInsightFolder);
        container.appendChild(backButton);
    }

}

function renderPdfPreview(filePath) {
    const output = document.getElementById("viewDisplayOutputBox");

    if (!output) {
        console.error("viewDisplayOutputBox not found");
        return;
    }

    output.innerHTML = "";

    const card = document.createElement("div");
    card.className = "pdf-preview-card";

    const title = document.createElement("h2");
    title.className = "pdf-preview-title";
    title.textContent = filePath.split(/[\\/]/).pop() || "PDF Preview";

    const frame = document.createElement("iframe");
    frame.className = "pdf-preview-frame";
    frame.title = "PDF Preview";
    frame.src = toFileUrl(filePath);
    frame.setAttribute("type", "application/pdf");

    card.appendChild(title);
    card.appendChild(frame);
    output.appendChild(card);
}

function renderViewOutput(entries) {

    const output =
        document.getElementById("viewDisplayOutputBox");

    output.textContent = "";

    entries.forEach(entry => {

        output.textContent +=
            JSON.stringify(entry, null, 2) + "\n\n";
    });
}

function renderSummaryPreview(text) {

    const output = document.getElementById("viewDisplayOutputBox");

    output.innerHTML = "";

    const card = document.createElement("div");
    card.className = "summary-card";


    const sections = text.split("===");


    sections.forEach(section => {

        section = section.trim();

        if (!section)
            return;


        const lines = section.split("\n");


        const title = lines[0].trim();


        // These are your real headers
        const headers = [
            "Summary",
            "Dependencies",
            "Functions",
            "Classes",
            "Business Rules"
        ];


        if (headers.includes(title)) {

            const header = document.createElement("h2");
            header.textContent = title;
            header.className = "summary-header";
            const specificClassName = title.toLowerCase().replace("business ", "")
            header.classList.add(`${specificClassName}-header`);



            const body = document.createElement("pre");
            body.className = "summary-section-body";
            body.textContent = lines.slice(1).join("\n").trim();


            card.appendChild(header);
            card.appendChild(body);

        }

        else {

            // File name + path section
            const body = document.createElement("pre");
            body.className = "summary-section-body";
            body.textContent = section;


            card.appendChild(body);

        }

    });


    output.appendChild(card);

}

function renderSourcePreview(text){

    const output =
        document.getElementById("viewDisplayOutputBox");

    output.innerHTML = "";

    const card = document.createElement("div");
    card.className = "directory-card";


    const sections = text.split("===");


    sections.forEach(section => {

        section = section.trim();

        if (!section)
            return;


        const lines = section.split("\n");

        const title = lines[0].trim();


        const headers = [
            "Directory Name",
            "Directory Path",
            "Purpose",
            "Responsibilities"
        ];


        if (headers.includes(title)) {

            const header = document.createElement("h3");
            header.textContent = title;
            header.className = "directory-header";


            const body = document.createElement("pre");
            body.className = "directory-section-body";

            body.textContent =
                lines.slice(1)
                .join("\n")
                .trim();


            card.appendChild(header);
            card.appendChild(body);

        }
        else {

            const body = document.createElement("pre");

            body.className =
                "directory-section-body directory-title";

            body.textContent =
                section;


            card.appendChild(body);

        }

    });


    output.appendChild(card);

}

function extractBusinessRuleOrigins(entry) {
    if (!entry || typeof entry !== "object") return [];

    const labels = {
        file: "Source file",
        directory_observed: "Observed in directory analysis",
        directory_inferred: "Inferred from directory analysis",
    };
    const originFields = [
        "origin",
        "origins",
        "origin_type",
        "origin_types",
        "provenance",
        "provenance_type",
        "source_type",
        "source_origins",
        "sources",
    ];
    const topFilePaths = Array.isArray(entry.source_file_paths)
        ? entry.source_file_paths.filter(item => typeof item === "string" && item.trim())
        : [];
    const topDirectory = typeof entry.source_directory === "string"
        ? entry.source_directory.trim()
        : "";
    const origins = [];

    const asPaths = value => {
        if (typeof value === "string" && value.trim()) return [value];
        if (!Array.isArray(value)) return [];
        return value.flatMap(item => {
            if (typeof item === "string" && item.trim()) return [item];
            if (!item || typeof item !== "object") return [];
            const path = item.path ?? item.file_path ?? item.source_file_path ?? item.source;
            return typeof path === "string" && path.trim() ? [path] : [];
        });
    };

    const addOrigin = (rawType, detail) => {
        if (typeof rawType !== "string" || !rawType.trim()) return;
        const type = rawType.trim();
        const normalizedType = type.toLowerCase();
        const isFile = normalizedType === "file";
        const isDirectory = normalizedType === "directory_observed" ||
            normalizedType === "directory_inferred";

        let filePaths = [];
        let directory = "";
        if (typeof detail === "string") {
            if (isFile && normalizedType !== detail.trim().toLowerCase()) {
                filePaths = [detail];
            } else if (isDirectory) {
                directory = detail.trim();
            }
        } else if (detail && typeof detail === "object") {
            filePaths = asPaths(
                detail.source_file_paths ?? detail.file_paths ?? detail.source_files ?? detail.files ??
                detail.source_file_path ?? detail.source_file ?? detail.file_path ??
                (isFile ? (detail.path ?? detail.source) : undefined)
            );
            directory = [detail.source_directory, detail.directory, detail.directory_path, detail.folder]
                .find(value => typeof value === "string" && value.trim()) ||
                (isDirectory && typeof detail.path === "string" ? detail.path.trim() : "");
        }

        if (isFile && !filePaths.length) filePaths = topFilePaths;
        if (isDirectory && !filePaths.length) {
            filePaths = topFilePaths;
            if (!filePaths.length) directory = directory || topDirectory;
        }

        origins.push({
            type,
            label: labels[normalizedType] || `Origin: ${type}`,
            icon: isFile ? "📄" : "📁",
            filePaths,
            directory,
        });
    };

    const parseOriginValue = (value, fieldName) => {
        if (Array.isArray(value)) {
            value.forEach(item => parseOriginValue(item, fieldName));
            return;
        }

        if (typeof value === "string") {
            const raw = value.trim();
            if (!raw) return;
            const knownOrigin = /^(file|directory_observed|directory_inferred)$/i.test(raw);
            if (knownOrigin || fieldName !== "sources") addOrigin(raw);
            return;
        }

        if (!value || typeof value !== "object") return;

        const type = value.type ?? value.origin ?? value.source_type ?? value.kind ?? value.category;
        if (typeof type === "string" && type.trim()) {
            addOrigin(type, value);
            return;
        }

        let foundKeyedOrigin = false;
        ["file", "directory_observed", "directory_inferred"].forEach(originType => {
            if (Object.prototype.hasOwnProperty.call(value, originType)) {
                foundKeyedOrigin = true;
                const details = value[originType];
                if (Array.isArray(details) && details.length && details.every(item => typeof item === "string")) {
                    details.forEach(item => addOrigin(originType, item));
                } else {
                    addOrigin(originType, details);
                }
            }
        });
        if (foundKeyedOrigin) return;

        originFields.forEach(field => {
            if (Object.prototype.hasOwnProperty.call(value, field)) {
                parseOriginValue(value[field], field);
            }
        });
    };

    originFields.forEach(field => {
        if (Object.prototype.hasOwnProperty.call(entry, field)) {
            parseOriginValue(entry[field], field);
        }
    });

    return origins;
}

function renderBusinessRulesList(result) {
    const output = document.getElementById("viewDisplayOutputBox");
    output.replaceChildren();

    const card = document.createElement("section");
    card.className = "summary-card business-rules-view";

    const heading = document.createElement("h2");
    heading.className = "business-rules-title";
    heading.textContent = "Business Rules";
    card.appendChild(heading);

    const validated = result.validated || { available: false, rules: [] };
    const discarded = result.discarded || { available: false, rules: [] };
    const ruleRecords = [];
    const recordedFolders = new Set();

    const foldersForRule = entry => {
        const folders = new Set();
        if (!entry || typeof entry !== "object") return folders;

        if (typeof entry.source_directory === "string" && entry.source_directory.trim()) {
            folders.add(entry.source_directory.trim());
        }

        if (Array.isArray(entry.source_file_paths)) {
            entry.source_file_paths.forEach(filePath => {
                if (typeof filePath !== "string" || !filePath.trim()) return;
                const normalizedPath = filePath.trim().replace(/\\/g, "/");
                const parent = normalizedPath.slice(0, normalizedPath.lastIndexOf("/"));
                if (parent) folders.add(parent);
            });
        }

        return folders;
    };

    const collectRules = (dataset, status) => {
        if (!dataset.available) return;
        dataset.rules.forEach(entry => {
            const rawText = typeof entry === "string"
                ? entry
                : entry?.rule ?? entry?.text;
            const folders = foldersForRule(entry);
            folders.forEach(folder => recordedFolders.add(folder));
            ruleRecords.push({
                entry,
                status,
                text: typeof rawText === "string" && rawText.trim()
                    ? rawText
                    : "Rule text unavailable",
                folders,
            });
        });
    };

    collectRules(validated, "Validated");
    collectRules(discarded, "Discarded");

    const countText = (dataset) => dataset.available
        ? dataset.rules.length.toLocaleString()
        : "Unavailable";
    const counts = document.createElement("div");
    counts.className = "business-rules-counts";

    const validatedCount = document.createElement("span");
    validatedCount.className = "business-rules-count business-rules-count-validated";
    validatedCount.textContent = `Validated: ${countText(validated)}`;
    const discardedCount = document.createElement("span");
    discardedCount.className = "business-rules-count business-rules-count-discarded";
    discardedCount.textContent = `Discarded: ${countText(discarded)}`;
    counts.append(validatedCount, discardedCount);
    card.appendChild(counts);

    if (!validated.available && !discarded.available) {
        const empty = document.createElement("p");
        empty.className = "business-rules-empty";
        empty.textContent = "No business-rule validation results are available for this codebase.";
        card.appendChild(empty);
        output.appendChild(card);
        return;
    }

    const toolbar = document.createElement("div");
    toolbar.className = "business-rules-toolbar";

    const search = document.createElement("input");
    search.className = "business-rules-search";
    search.type = "search";
    search.placeholder = "Search rules...";
    search.setAttribute("aria-label", "Search rules");

    const statusFilter = document.createElement("select");
    statusFilter.className = "business-rules-filter";
    statusFilter.setAttribute("aria-label", "Filter by status");
    ["All", "Validated", "Discarded"].forEach(status => {
        const option = document.createElement("option");
        option.value = status;
        option.textContent = status;
        statusFilter.appendChild(option);
    });

    const folderFilter = document.createElement("select");
    folderFilter.className = "business-rules-filter business-rules-folder-filter";
    folderFilter.setAttribute("aria-label", "Filter by source folder");
    const allFolders = document.createElement("option");
    allFolders.value = "";
    allFolders.textContent = "All Folders";
    folderFilter.appendChild(allFolders);
    [...recordedFolders].sort((a, b) => a.localeCompare(b)).forEach(folder => {
        const option = document.createElement("option");
        option.value = folder;
        option.textContent = folder;
        folderFilter.appendChild(option);
    });

    const clearFilters = document.createElement("button");
    clearFilters.className = "business-rules-clear";
    clearFilters.type = "button";
    clearFilters.textContent = "✕ Clear Filters";

    toolbar.append(search, statusFilter, folderFilter, clearFilters);
    card.appendChild(toolbar);

    const resultCount = document.createElement("p");
    resultCount.className = "business-rules-result-count";
    resultCount.setAttribute("aria-live", "polite");
    card.appendChild(resultCount);

    const list = document.createElement("div");
    list.className = "business-rules-list";

    const detailPanel = document.createElement("section");
    detailPanel.className = "business-rule-detail-panel";
    detailPanel.setAttribute("aria-live", "polite");
    let selectedRecord = null;
    let selectionMessage = "Select a rule to view its full details.";

    const renderDetailPanel = () => {
        detailPanel.replaceChildren();

        if (!selectedRecord) {
            const message = document.createElement("p");
            message.className = "business-rule-detail-selection-message";
            message.textContent = selectionMessage;
            detailPanel.appendChild(message);
            return;
        }

        const detailHeader = document.createElement("div");
        detailHeader.className = "business-rule-detail-header";
        const detailTitle = document.createElement("h3");
        detailTitle.textContent = "Rule details";
        const detailStatus = document.createElement("span");
        detailStatus.className = `business-rule-status business-rule-status-${selectedRecord.status.toLowerCase()}`;
        detailStatus.textContent = selectedRecord.status;
        detailHeader.append(detailTitle, detailStatus);
        detailPanel.appendChild(detailHeader);

        const addDetailField = (label, value) => {
            const field = document.createElement("div");
            field.className = "business-rule-detail-field";
            const fieldLabel = document.createElement("h4");
            fieldLabel.textContent = label;
            const fieldValue = document.createElement("div");
            fieldValue.className = "business-rule-detail-value";
            fieldValue.textContent = value;
            field.append(fieldLabel, fieldValue);
            detailPanel.appendChild(field);
        };

        addDetailField("Full Rule", selectedRecord.text);

        const originField = document.createElement("div");
        originField.className = "business-rule-detail-field";
        const originHeading = document.createElement("h4");
        originHeading.textContent = "Origin";
        originField.appendChild(originHeading);
        const origins = extractBusinessRuleOrigins(selectedRecord.entry);
        if (!origins.length) {
            const missingOrigin = document.createElement("p");
            missingOrigin.className = "business-rule-origin-missing";
            missingOrigin.textContent = "Origin not recorded";
            originField.appendChild(missingOrigin);
        } else {
            const originList = document.createElement("div");
            originList.className = "business-rule-origin-list";
            origins.forEach(origin => {
                const originRow = document.createElement("div");
                originRow.className = "business-rule-origin-item";

                const originIcon = document.createElement("span");
                originIcon.className = "business-rule-origin-icon";
                originIcon.setAttribute("aria-hidden", "true");
                originIcon.textContent = origin.icon;

                const originCopy = document.createElement("div");
                originCopy.className = "business-rule-origin-copy";
                const originLabel = document.createElement("strong");
                originLabel.className = "business-rule-origin-label";
                originLabel.textContent = origin.label;
                originCopy.appendChild(originLabel);

                if (origin.filePaths.length) {
                    const sourceList = document.createElement("ul");
                    sourceList.className = "business-rule-origin-paths";
                    origin.filePaths.forEach(filePath => {
                        const source = document.createElement("li");
                        source.textContent = filePath;
                        sourceList.appendChild(source);
                    });
                    originCopy.appendChild(sourceList);
                } else {
                    const location = document.createElement("span");
                    location.className = "business-rule-origin-location";
                    location.textContent = origin.directory || "Not recorded";
                    originCopy.appendChild(location);
                }

                originRow.append(originIcon, originCopy);
                originList.appendChild(originRow);
            });
            originField.appendChild(originList);
        }
        detailPanel.appendChild(originField);

        const sourceDirectory = selectedRecord.entry?.source_directory;
        addDetailField(
            "Source Directory",
            typeof sourceDirectory === "string" && sourceDirectory.trim()
                ? sourceDirectory
                : "Not recorded"
        );

        const paths = selectedRecord.entry?.source_file_paths;
        const pathsField = document.createElement("div");
        pathsField.className = "business-rule-detail-field";
        const pathsLabel = document.createElement("h4");
        pathsLabel.textContent = "Source File Paths";
        pathsField.appendChild(pathsLabel);
        if (Array.isArray(paths) && paths.some(filePath => typeof filePath === "string" && filePath.trim())) {
            const pathList = document.createElement("ul");
            pathList.className = "business-rule-detail-paths";
            paths.forEach(filePath => {
                if (typeof filePath !== "string" || !filePath.trim()) return;
                const pathItem = document.createElement("li");
                pathItem.textContent = filePath;
                pathList.appendChild(pathItem);
            });
            pathsField.appendChild(pathList);
        } else {
            const pathValue = document.createElement("div");
            pathValue.className = "business-rule-detail-value";
            pathValue.textContent = "Not recorded";
            pathsField.appendChild(pathValue);
        }
        detailPanel.appendChild(pathsField);

        if (selectedRecord.status === "Discarded") {
            const entry = selectedRecord.entry;
            const explanation = entry?.explanation;
            const reason = typeof entry?.reason === "string" && entry.reason.trim()
                ? entry.reason
                : typeof explanation === "string" && explanation.trim()
                    ? explanation
                    : typeof explanation?.reasoning === "string" && explanation.reasoning.trim()
                        ? explanation.reasoning
                        : "Not recorded";
            addDetailField("Discard Reason or Explanation", reason);
        }
    };

    const appendRule = record => {
        const row = document.createElement("div");
        row.className = `business-rule-item business-rule-item-${record.status.toLowerCase()}`;
        row.setAttribute("role", "button");
        row.setAttribute("tabindex", "0");
        row.setAttribute("aria-pressed", String(selectedRecord === record));
        row.setAttribute("aria-label", `${record.status} rule: ${record.text}`);
        if (selectedRecord === record) row.classList.add("is-selected");

        const badge = document.createElement("span");
        badge.className = `business-rule-status business-rule-status-${record.status.toLowerCase()}`;
        badge.textContent = record.status;

        const text = document.createElement("p");
        text.className = "business-rule-text";
        text.textContent = record.text;

        row.append(badge, text);
        const selectRule = () => {
            selectedRecord = record;
            selectionMessage = "Select a rule to view its full details.";
            list.querySelectorAll(".business-rule-item").forEach(otherRow => {
                const isSelected = otherRow === row;
                otherRow.classList.toggle("is-selected", isSelected);
                otherRow.setAttribute("aria-pressed", String(isSelected));
            });
            renderDetailPanel();
        };
        row.addEventListener("click", selectRule);
        row.addEventListener("keydown", event => {
            if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                selectRule();
            }
        });
        list.appendChild(row);
    };

    const isUnfiltered = () => !search.value.trim() &&
        statusFilter.value === "All" &&
        folderFilter.value === "";

    const renderFilteredRules = () => {
        const term = search.value.trim().toLowerCase();
        const status = statusFilter.value;
        const folder = folderFilter.value;
        const matches = ruleRecords.filter(record => {
            const matchesText = !term || record.text.toLowerCase().includes(term);
            const matchesStatus = status === "All" || record.status === status;
            const matchesFolder = !folder || record.folders.has(folder);
            return matchesText && matchesStatus && matchesFolder;
        });

        resultCount.textContent = `Showing ${matches.length.toLocaleString()} of ${ruleRecords.length.toLocaleString()} rules`;
        list.replaceChildren();

        if (selectedRecord && !matches.includes(selectedRecord)) {
            selectedRecord = null;
            selectionMessage = "The selected rule no longer matches these filters. Adjust or clear filters to select another rule.";
        }

        if (ruleRecords.length > 0 && matches.length === 0) {
            const empty = document.createElement("p");
            empty.className = "business-rules-no-matches";
            empty.textContent = "No matching rules";
            const hint = document.createElement("span");
            hint.textContent = "Try changing your search or filters, or clear them to restore all rules.";
            empty.appendChild(hint);
            list.appendChild(empty);
            renderDetailPanel();
            return;
        }

        if (isUnfiltered()) {
            if (validated.available && validated.rules.length === 0) {
                const empty = document.createElement("p");
                empty.className = "business-rules-group-empty";
                empty.textContent = "No validated rules";
                list.appendChild(empty);
            }
            if (discarded.available && discarded.rules.length === 0) {
                const empty = document.createElement("p");
                empty.className = "business-rules-group-empty";
                empty.textContent = "No discarded rules";
                list.appendChild(empty);
            }
        }

        matches.forEach(appendRule);
        renderDetailPanel();
    };

    ["input", "change"].forEach(eventName => {
        search.addEventListener(eventName, renderFilteredRules);
        statusFilter.addEventListener(eventName, renderFilteredRules);
        folderFilter.addEventListener(eventName, renderFilteredRules);
    });

    clearFilters.addEventListener("click", () => {
        search.value = "";
        statusFilter.value = "All";
        folderFilter.value = "";
        renderFilteredRules();
        search.focus();
    });

    card.appendChild(list);
    card.appendChild(detailPanel);
    output.appendChild(card);
    renderFilteredRules();
}

function clearBusinessRulesResultsIfCodebaseChanged(previousPath, nextPath) {
    if (previousPath === nextPath) return;

    const output = document.getElementById("viewDisplayOutputBox");
    if (output?.querySelector(".business-rules-view")) {
        output.replaceChildren();
    }
}

function renderBusinessRulesPreview(text) {

    const output = document.getElementById("viewDisplayOutputBox");

    output.innerHTML = "";

    const card = document.createElement("div");
    card.className = "business-rule-card";


    const sections = text.split("===");


    sections.forEach(section => {

        section = section.trim();

        if (!section)
            return;


        const lines = section.split("\n");

        const title = lines[0].trim();


        // Rule headers
        if (title.startsWith("Business Rule")) {

            const header = document.createElement("h2");
            header.textContent = title;
            header.className = "business-rule-header";


            const body = document.createElement("pre");
            body.className = "business-rule-section-body";

            body.textContent =
                lines.slice(1)
                .join("\n")
                .trim();


            card.appendChild(header);
            card.appendChild(body);

        }

        else {

            // Main "# Business Rules" title
            const body = document.createElement("pre");

            body.className =
                "business-rule-section-body business-rule-title";

            body.textContent =
                section;


            card.appendChild(body);

        }

    });


    output.appendChild(card);

}

function renderErrorPreview(errors) {


    console.log("renderErrorPreview called");
    console.log(errors);

    const errorList = Array.isArray(errors)
        ? errors
        : errors
            ? [errors]
            : [];

    const output =
        document.getElementById("viewDisplayOutputBox");

    output.innerHTML = "";

    const card = document.createElement("div");
    card.className = "summary-card error-view-card";

    const header = document.createElement("div");
    header.className = "error-view-header";
    const title = usageHeader("Errors");
    const alertIcon = document.createElement("span");
    alertIcon.className = "error-view-alert-icon";
    alertIcon.setAttribute("aria-hidden", "true");
    alertIcon.textContent = "⚠️";
    title.prepend(alertIcon);

    const noteText =
        "Errors recorded during the current app session. " +
        "They are cleared when the app starts.";
    const topNote = document.createElement("p");
    topNote.className = "error-view-top-note";
    topNote.textContent = noteText;
    header.append(title, topNote);
    card.appendChild(header);

    if (errorList.length === 0) {
        const empty = document.createElement("p");
        empty.className = "error-view-empty";
        empty.textContent = "No errors have been recorded.";
        card.appendChild(empty);
    } else {
        const tableWrap = document.createElement("div");
        tableWrap.className = "error-view-table-wrap";

        const table = document.createElement("table");
        table.className = "usage-table error-table error-view-table";
        const head = table.createTHead().insertRow();
        ["Time", "Code", "Step", "Message"].forEach(label => {
            const th = document.createElement("th");
            th.textContent = label;
            head.appendChild(th);
        });
        const body = table.createTBody();

        const appendMessage = (container, message) => {
            const importantText = /Business rules not found|Validated rules not found/ig;
            const pathText = /(?:\/[A-Za-z0-9._~!$&'()+,;=@%-]+|[A-Za-z]:\\[^\s,;]+)/g;
            const tokens = new RegExp(`${importantText.source}|${pathText.source}`, "ig");
            let offset = 0;
            let match;

            while ((match = tokens.exec(message)) !== null) {
                if (match.index > offset) {
                    container.appendChild(document.createTextNode(message.slice(offset, match.index)));
                }

                const span = document.createElement("span");
                if (/Business rules not found|Validated rules not found/i.test(match[0])) {
                    span.className = "error-view-highlight";
                    span.textContent = match[0];
                } else {
                    span.className = "error-view-path";
                    span.title = match[0];
                    span.textContent = match[0];
                }
                container.appendChild(span);
                offset = match.index + match[0].length;
            }

            if (offset < message.length) {
                container.appendChild(document.createTextNode(message.slice(offset)));
            }
        };

        errorList.forEach(error => {
            const entry = typeof error === "string"
                ? { time: null, code: "—", command: "—", message: error }
                : {
                    time: error.time,
                    code: error.code ?? "—",
                    command: error.command || "—",
                    message: error.message || "—",
                };
            const code = String(entry.code);
            const message = String(entry.message);
            const isCancelled = code.toUpperCase() === "CANCELLED" ||
                /\boperation cancelled\b/i.test(message);
            const isEnvironmentIssue = /GOOGLE_API_KEY|environment variable/i.test(`${code} ${message}`);
            const row = body.insertRow();
            row.className = isCancelled ? "error-view-row-cancelled" : "error-view-row-critical";

            const timeCell = row.insertCell();
            timeCell.textContent = formatStamp(entry.time);

            const codeCell = row.insertCell();
            if (isCancelled) {
                const badge = document.createElement("span");
                badge.className = "error-view-cancelled-badge";
                badge.textContent = "CANCELLED";
                codeCell.appendChild(badge);
            } else {
                const codeLabel = document.createElement("span");
                codeLabel.className = "error-view-code";
                codeLabel.textContent = code;
                codeCell.appendChild(codeLabel);
                if (isEnvironmentIssue) {
                    const warning = document.createElement("span");
                    warning.className = "error-view-environment-warning";
                    warning.setAttribute("aria-label", "Environment configuration warning");
                    warning.textContent = "⚠️";
                    codeCell.appendChild(warning);
                }
            }

            const stepCell = row.insertCell();
            stepCell.className = isCancelled ? "error-view-cancelled-text" : "";
            stepCell.textContent = String(entry.command);

            const messageCell = row.insertCell();
            messageCell.className = "error-view-message-cell";
            const messageCopy = document.createElement("span");
            messageCopy.className = isCancelled ? "error-view-cancelled-text" : "error-view-message";
            appendMessage(messageCopy, message);
            messageCell.appendChild(messageCopy);

            if (!isCancelled) {
                const severity = document.createElement("span");
                severity.className = "error-view-severity-badge";
                severity.textContent = "[ERROR]";
                severity.setAttribute("aria-label", "Error severity");
                messageCell.appendChild(severity);
            }
        });

        tableWrap.appendChild(table);
        card.appendChild(tableWrap);
    }

    const note = document.createElement("p");
    note.className = "usage-note error-view-footer-note";
    note.textContent = noteText;
    card.appendChild(note);

    output.appendChild(card);
}

// ============================================
// AI Usage (last_run_log.json)
// ============================================

function formatSeconds(seconds) {

    if (seconds === null || seconds === undefined) return "—";

    if (seconds < 60) return `${seconds.toFixed(1)} s`;

    const minutes = Math.floor(seconds / 60);
    const rest = Math.round(seconds % 60);

    return `${minutes} min ${rest} s`;
}

function formatCount(value) {

    return (value ?? 0).toLocaleString();
}

function formatStamp(stamp) {

    return stamp ? new Date(stamp).toLocaleString() : "—";
}

function usageStatus(status) {

    const badge = document.createElement("span");
    badge.className = `usage-status ${status}`;
    badge.textContent = `● ${status}`;

    return badge;
}

function usageIcon(text) {

    const icon = document.createElement("span");
    icon.className = "usage-icon";
    icon.setAttribute("aria-hidden", "true");
    icon.textContent = text;

    return icon;
}

function usageMetric(label, value, visual, detail) {

    const metric = document.createElement("section");
    metric.className = "usage-metric-card";

    const copy = document.createElement("div");
    copy.className = "usage-metric-copy";

    const title = document.createElement("h3");
    title.textContent = label;

    const amount = document.createElement("p");
    amount.className = "usage-metric-value";
    amount.textContent = value;

    copy.append(title, amount);

    if (detail) {
        const note = document.createElement("p");
        note.className = "usage-metric-detail";
        note.textContent = detail;
        copy.appendChild(note);
    }

    const spark = document.createElement("span");
    spark.className = `usage-sparkline ${visual}`;
    spark.setAttribute("aria-hidden", "true");
    spark.innerHTML = '<svg viewBox="0 0 72 36" focusable="false"><path d="M2 29 13 22 23 25 34 13 45 18 57 7 70 11" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><path d="M2 35h68" fill="none" stroke="currentColor" stroke-width="1" opacity=".25"/></svg>';

    metric.append(copy, spark);
    return metric;
}

function usageDetail(iconText, label, value, secondaryValue) {

    const detail = document.createElement("div");
    detail.className = "usage-detail-card";

    const heading = document.createElement("div");
    heading.className = "usage-detail-label";
    heading.append(usageIcon(iconText), document.createTextNode(label));

    const content = document.createElement("div");
    content.className = "usage-detail-value";
    content.textContent = value;

    detail.append(heading, content);

    if (secondaryValue !== undefined) {
        const secondary = document.createElement("div");
        secondary.className = "usage-detail-secondary";
        secondary.append(usageIcon("📁"), document.createTextNode(`Run ID: ${secondaryValue}`));
        detail.appendChild(secondary);
    }

    return detail;
}

function usageStepName(name) {

    const step = document.createElement("span");
    step.className = "usage-step-name";
    step.append(usageIcon("⚙️"), document.createTextNode(name || "—"));
    return step;
}

// rows: array of arrays of cells; a cell is text or a DOM node.
// headers may be null for a plain label/value table.
function usageTable(headers, rows, footer) {

    const table = document.createElement("table");
    table.className = "usage-table";

    if (headers) {

        const head = table.createTHead().insertRow();

        headers.forEach(text => {
            const th = document.createElement("th");
            th.textContent = text;
            head.appendChild(th);
        });
    }

    const body = table.createTBody();

    const addRow = (cells, target) => {
        const row = target.insertRow();
        cells.forEach(cell => {
            const td = row.insertCell();
            if (cell instanceof Node) td.appendChild(cell);
            else td.textContent = cell;
        });
    };

    rows.forEach(cells => addRow(cells, body));

    if (footer) addRow(footer, table.createTFoot());

    return table;
}

function usageHeader(text) {

    const header = document.createElement("h2");
    header.className = "summary-header";
    header.textContent = text;

    return header;
}

function renderRunUsage(run) {

    const output =
        document.getElementById("viewDisplayOutputBox");

    output.innerHTML = "";

    if (!run) {
        renderTextPreview(
            "No run recorded yet.\n\n" +
            "Run the full pipeline, or any step that uses the AI, " +
            "and its token usage and timings will show here."
        );
        return;
    }

    const card = document.createElement("div");
    card.className = "summary-card usage-card";

    const totals = run.totals || {};

    const titlebar = document.createElement("div");
    titlebar.className = "usage-titlebar";
    const title = usageHeader("AI Usage — Last Run");
    title.prepend(usageIcon("📈"));
    const identity = document.createElement("div");
    identity.className = "usage-run-identity";
    const codebase = document.createElement("span");
    codebase.className = "usage-codebase-name";
    codebase.textContent = run.codebase || "Codebase unavailable";
    identity.append(codebase, usageStatus(run.status || "unknown"));
    titlebar.append(title, identity);
    card.appendChild(titlebar);

    const dashboard = document.createElement("div");
    dashboard.className = "usage-dashboard-grid";

    const details = document.createElement("section");
    details.className = "usage-detail-list";
    details.setAttribute("aria-label", "Run details and timings");
    details.appendChild(usageDetail("⏱️", "Duration", formatSeconds(run.elapsed_seconds)));

    if (totals.waits) {
        details.appendChild(usageDetail(
            "⏳",
            "Waited for Google",
            `${totals.waits} time${totals.waits === 1 ? "" : "s"}, ${formatSeconds(totals.waited_seconds)}`
        ));
    } else {
        details.appendChild(usageDetail("⏳", "Waited for Google", "No recorded waits"));
    }

    details.appendChild(usageDetail(
        "📅",
        "Started",
        formatStamp(run.started_at),
        run.run_id || "—"
    ));

    const metrics = document.createElement("section");
    metrics.className = "usage-metrics-grid";
    metrics.setAttribute("aria-label", "Token and API call metrics");
    const failedNote = totals.failed_calls
        ? `${formatCount(totals.failed_calls)} failed`
        : "Recorded calls";
    metrics.append(
        usageMetric("Input Tokens", formatCount(totals.input_tokens), "usage-sparkline-input"),
        usageMetric("Output Tokens", formatCount(totals.output_tokens), "usage-sparkline-output"),
        usageMetric("Total Tokens", formatCount(totals.total_tokens), "usage-sparkline-total"),
        usageMetric("AI Calls", formatCount(totals.calls), "usage-sparkline-calls", failedNote)
    );

    dashboard.append(details, metrics);
    card.appendChild(dashboard);

    if (run.error) {
        const error = document.createElement("pre");
        error.className = "summary-section-body usage-error";
        error.textContent = run.error_code
            ? `Error ${run.error_code}: ${run.error}`
            : run.error;
        card.appendChild(error);
    }

    const stages = run.stages || [];

    if (stages.length) {

        card.appendChild(usageHeader("By Step"));

        card.appendChild(usageTable(
            ["Step", "Status", "Time", "Calls", "Input", "Output", "Total"],
            stages.map(stage => [
                stage.stage,
                usageStatus(stage.status),
                formatSeconds(stage.elapsed_seconds),
                formatCount(stage.calls),
                formatCount(stage.input_tokens),
                formatCount(stage.output_tokens),
                formatCount(stage.total_tokens),
            ]),
            [
                "Total", "", formatSeconds(run.elapsed_seconds),
                formatCount(totals.calls),
                formatCount(totals.input_tokens),
                formatCount(totals.output_tokens),
                formatCount(totals.total_tokens),
            ]
        ));
    }

    const calls = run.calls || [];

    if (calls.length) {

        card.appendChild(usageHeader("Slowest AI Calls"));

        const slowest = [...calls]
            .sort((a, b) => (b.duration_seconds ?? 0) - (a.duration_seconds ?? 0))
            .slice(0, 5);

        card.appendChild(usageTable(
            ["Step", "Time", "Input", "Output", "Status"],
            slowest.map(call => [
                usageStepName(call.stage),
                (() => {
                    const time = document.createElement("span");
                    time.className = (call.duration_seconds ?? 0) >= 60
                        ? "usage-call-time usage-call-time-slow"
                        : "usage-call-time";
                    time.textContent = formatSeconds(call.duration_seconds);
                    return time;
                })(),
                formatCount(call.input_tokens),
                formatCount(call.output_tokens),
                usageStatus(call.status),
            ])
        )).classList.add("usage-slowest-table");
    }

    const failed = calls.filter(call => call.status === "failed");

    if (failed.length) {

        card.appendChild(usageHeader("Failed AI Calls"));

        card.appendChild(usageTable(
            ["Step", "Code", "Error"],
            failed.map(call => [
                call.stage,
                call.error_code ?? "—",
                call.error || "—",
            ])
        )).classList.add("error-table");
    }

    const waits = run.waits || [];

    if (waits.length) {

        card.appendChild(usageHeader("Waits for Google"));

        const reasonText = reason => reason === "429"
            ? "429 rate limit"
            : `${reason} Google busy`;

        card.appendChild(usageTable(
            ["Step", "Reason", "Waited"],
            waits.map(wait => [
                usageStepName(wait.stage),
                (() => {
                    const reason = document.createElement("span");
                    reason.className = "usage-wait-reason";
                    reason.textContent = reasonText(wait.reason);
                    return reason;
                })(),
                formatSeconds(wait.seconds),
            ])
        )).classList.add("usage-waits-table");
    }

    const note = document.createElement("p");
    note.className = "usage-note";
    note.textContent =
        "Shows the most recent run that used the AI. " +
        "Output tokens include the model's thinking tokens.";
    card.appendChild(note);

    output.appendChild(card);
}

function renderTextPreview(text) {

    const output =
        document.getElementById("viewDisplayOutputBox");

    if (!output) {
        console.error("viewDisplayOutputBox not found");
        return;
    }

    output.innerHTML = "";

    const pre = document.createElement("pre");

    pre.className = "file-preview-text";

    pre.textContent = text;

    output.appendChild(pre);
}

function showPage(pageId) {

    if (pageId !== "mainViewPage") {
        invalidateTestResultsDashboard();
    }

    document.querySelectorAll('.page').forEach(page => {
        page.classList.add('hidden');
    });

    document.getElementById(pageId).classList.remove('hidden');
}

function showButtons(buttonId) {

    invalidateTestResultsDashboard();

    document.querySelectorAll('.viewBtnContainer').forEach(buttonContainer => {
        if (buttonContainer.id !== "mainViewBtns") {

            buttonContainer.classList.add('hidden');
        }
    });

    document.getElementById(buttonId).classList.remove('hidden');
}

function setFAQSupportActive(activeButton) {

    const supportBtn = document.getElementById("supportBtn");
    const faqBtn = document.getElementById("faqBtn");


    supportBtn.classList.remove("btn-inverted");
    faqBtn.classList.remove("btn-inverted");


    if (activeButton === "support") {

        supportBtn.classList.add("btn-inverted");

    } else {

        
        faqBtn.classList.add("btn-inverted");

    }
}


function escapeHtml(value) {
    return String(value)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/\"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

async function loadValidatedRulesSelection() {
    const statusEl = document.getElementById('validatedRuleSelectionStatus');
    const listEl = document.getElementById('validatedRuleList');
    const detailsEl = document.getElementById('selectedValidatedRuleDetails');

    if (!statusEl || !listEl || !detailsEl) return;

    statusEl.textContent = 'Loading validated rules...';
    listEl.innerHTML = '';
    detailsEl.classList.add('hidden');
    detailsEl.innerHTML = '';

    if (!selectedCodebasePath) {
        statusEl.textContent = 'Select a codebase from the pipeline page first.';
        return;
    }

    try {
        const response = await window.electronAPI.getValidatedRules(selectedCodebasePath);

        if (!response?.success) {
            statusEl.textContent = response?.error || 'No validated rules were found.';
            return;
        }

        const rules = response.rules || [];

        if (!rules.length) {
            statusEl.textContent = 'No validated rules were found for this codebase.';
            return;
        }

        statusEl.textContent = `Showing ${rules.length} validated rule${rules.length === 1 ? '' : 's'}.`;

        const fragment = document.createDocumentFragment();

        rules.forEach((rule, index) => {
            const id = parseInt(rule.id)
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'selection-item-btn';
            button.innerHTML = `
                <span class="selection-item-title">${escapeHtml(index + 1)}: ${escapeHtml(rule.text)}</span>
            `;
            if (selectedRules.includes(id)) {
                button.classList.add('selected')
            }
            button.addEventListener('click', () => {
                if (button.classList.contains('selected')) {
                    selectedRules = selectedRules.filter(item => item !== id);
                    button.classList.remove('selected');
                } else {
                    selectedRules.push(id);
                    button.classList.add('selected')
                }
            });

            fragment.appendChild(button);
        });

        listEl.appendChild(fragment);

    } catch (error) {
        console.error(error);
        statusEl.textContent = 'Unable to load validated rules.';
    }
}


//loading complete button
document.getElementById("loadingCancelBtn").addEventListener("click", async () => {
    if (!activeCommand) return;

    const cancelButton = document.getElementById("loadingCancelBtn");
    const cancelledCommand = activeCommand;
    cancelButton.disabled = true;

    const response = await window.electronAPI.cancelCommand();

    if (!response?.success) {
        cancelButton.disabled = false;
        showErrorPopup(response?.error || "Could not cancel the operation.");
        return;
    }

    const cancellationMessage = "Operation cancelled by user.";
    errorsMade = true;

    await window.electronAPI.recordErrorLog({
        command: cancelledCommand,
        message: cancellationMessage,
        code: "CANCELLED"
    });

    activeCommand = null;
    hideLoading();
    returnToOperationPage();
});

document.getElementById("loadingOkBtn").addEventListener("click", () => {

    hideLoading();
    document.getElementById("loadingOkBtn").classList.add("hidden");
    returnToOperationPage();

});

// MENU BUTTONS
document.getElementById('analysisBtn')
    .addEventListener('click', () => {

        


        if (!pathSubmitted) {
            showPage('codebasePipelinePage');
        } else {
            showPage('analysisPage');
        }

    
    });

document.getElementById("connectCodebaseBtn")
    .addEventListener("click", () => {
        codebasePickerPreviousSelection = selectedCodebasePath;
        document.getElementById("codebasePath").value = selectedCodebasePath;
        showPage("codebasePipelinePage");
    });

document.getElementById('apiBtn')
    .addEventListener('click', () => {

        

        showPage('apiPage');

        if (hasAPI) {
            
            overwriteApiUi();

        } else {

            newApiUi();

        }


    
    });


document.getElementById('faqAndSupportBtn')
    .addEventListener('click', () => {

        showPage('faqAndSupportPage')
    });


// ---------------------------------------------
// ViewPageButtons
// ---------------------------------------------
document.getElementById('mainViewBtn')
    .addEventListener('click', () => {

        
        showPage('mainViewPage');
        refreshErrorLog();

    });

document.getElementById("errorPopupCloseBtn")
    .addEventListener("click", hideErrorPopup);

document.getElementById("viewDisplaySourcesBtn")
.addEventListener("click", () => {

    showButtons("viewSourcesBtns");

    const codebaseName = getSelectedCodebaseName();
    if (!codebaseName) {
        renderTextPreview("Select a codebase first from the pipeline page.");
        return;
    }
    try {
        const rootPath = `agent/directory_agent_output/${codebaseName}`;
        resetInsightFolderNavigation(rootPath);

        runPreviewCommand(
            "files",
            {
                path: rootPath
            }
        );
    } catch (error) {
        const rootPath = `backend/agent/directory_agent_output/${codebaseName}`;
        resetInsightFolderNavigation(rootPath);

        runPreviewCommand(
            "files",
            {
                path: rootPath
            }
        );
    }

    runPreviewCommand(
        "files",
        {
            path: rootPath
        }
    );

});

document.getElementById("viewDisplayErrorsBtn")
.addEventListener("click", async () => {

    showButtons("viewErrorsBtns");

    await refreshErrorLog();

});

document.getElementById("viewUsageBtn")
.addEventListener("click", async () => {

    showButtons("viewUsageBtns");

    await runBackendCommand("get_run_usage");

});

document.getElementById("viewDisplaySummariesBtn")
    .addEventListener("click", () => {

        showButtons("viewFilesBtns");
        setActiveFileListView({ includePdfs: false, onlyPdfs: false, recursivePdfs: false });

        const codebaseName = getSelectedCodebaseName();
        if (!codebaseName) {
            renderTextPreview("Select a codebase first from the pipeline page.");
            return;
        }
        try{
            const rootPath = `agent/file_summary_agent_output/${codebaseName}`;

            resetInsightFolderNavigation(rootPath);

            runPreviewCommand("files", {
                path: rootPath
            });
        }catch(error){
            const rootPath = `backend/agent/file_summary_agent_output/${codebaseName}`;

            resetInsightFolderNavigation(rootPath);

            runPreviewCommand("files", {
                path: rootPath
            });
        }
    });

document.getElementById("viewDisplayFilesBtn").addEventListener("click", () => {

    showButtons("viewFilesBtns");
    setActiveFileListView({ includePdfs: true, onlyPdfs: false, recursivePdfs: false });

    try{
        resetInsightFolderNavigation("agent");

        runPreviewCommand(
            "files",
            {
                path: "agent"
            }
        );
    } catch (error) {
        resetInsightFolderNavigation("backend/agent");

        runPreviewCommand(
            "files",
            {
                path: "backend/agent"
            }
        );
    }

});

document.getElementById("viewDisplayBusinessRulesBtn").addEventListener("click", () => {
    showButtons("mainViewBtns");

    const codebasePath = selectedCodebasePath;
    if (!codebasePath) {
        renderTextPreview("Select a codebase first from the pipeline page.");
        return;
    }

    const output = document.getElementById("viewDisplayOutputBox");
    output.replaceChildren();
    const loading = document.createElement("p");
    loading.className = "business-rules-loading";
    loading.textContent = "Loading business rules…";
    output.appendChild(loading);

    window.electronAPI.getBusinessRuleFiles(codebasePath)
        .then(result => {
            if (selectedCodebasePath !== codebasePath) return;
            if (!result?.success) {
                renderTextPreview(result?.error || "Could not load business rules.");
                return;
            }
            renderBusinessRulesList(result);
        })
        .catch(error => {
            if (selectedCodebasePath !== codebasePath) return;
            renderTextPreview(error?.message || "Could not load business rules.");
        });
});

document.getElementById("viewUnitTestsBtn")
    .addEventListener("click", () => {

        showButtons("viewFilesBtns");
        setActiveFileListView({ includePdfs: false, onlyPdfs: false, recursivePdfs: false });

        const codebaseName = getSelectedCodebaseName();
        if (!codebaseName) {
            renderTextPreview("Select a codebase first from the pipeline page.");
            return;
        }
        const codebasePath = selectedCodebasePath;

        try{
            const rootPath = `agent/UT_agent_output/${codebaseName}`;

            resetInsightFolderNavigation(rootPath);

            runPreviewCommand("files", {
                path: rootPath
            });
        } catch (error) {
            const rootPath = `backend/agent/UT_agent_output/${codebaseName}`;

            resetInsightFolderNavigation(rootPath);

            runPreviewCommand("files", {
                path: rootPath
            });
        }

        loadTestResultsDashboard(codebasePath, codebaseName);

    });

document.getElementById('mainViewBackBtn')
    .addEventListener('click', () => {

        

        showPage('homePage');
    }); 

// UML view should surface the generated PDFs for the selected codebase.
document.getElementById('viewUMLBtn')
    .addEventListener('click', () => {

        showButtons('viewFilesBtns');
        setActiveFileListView({ includePdfs: true, onlyPdfs: true, recursivePdfs: true });

        const codebaseName = getSelectedCodebaseName();
        if (!codebaseName) {
            renderTextPreview("Select a codebase first from the pipeline page.");
            return;
        }

        try{
            const rootPath = `agent/file_summary_agent_output/${codebaseName}`;

            resetInsightFolderNavigation(rootPath);

            runPreviewCommand('files', {
                path: rootPath,
                recursivePdfs: true
            });
        } catch (error) {
            const rootPath = `backend/agent/file_summary_agent_output/${codebaseName}`;

            resetInsightFolderNavigation(rootPath);

            runPreviewCommand('files', {
                path: rootPath,
                recursivePdfs: true
            });
        }
    });




document.getElementById('exitBtn').addEventListener('click', () => {



    setTimeout(() => {
        window.electronAPI.exitApp();
    }, 500);

});
//======================================================
//options for the analysis page
//======================================================
document.getElementById('codebaseAnalysisPipelineBtn')
    .addEventListener('click', () => {
        showLoading(
            "Running Full Pipeline",
            "Preparing..."
        );


        setTimeout(() => {

            runBackendCommand(
                "full_pipeline",
                {
                    codebase:selectedCodebasePath
                }
            );

        }, 100);

});

document.getElementById('estimateTokensBtn')
    .addEventListener('click', () => {

        showLoading(
            "Token Usage Estimate",
            "Scanning codebase..."
        );

        runBackendCommand(
            "estimate_tokens",
        {
            codebase:selectedCodebasePath
        });
    });

document.getElementById('tokenCalibrationBtn')
    .addEventListener('click', () => {

        showLoading(
            "Token Calibration",
            "Reading recorded usage..."
        );

        runBackendCommand(
            "token_calibration",
        {
            codebase:selectedCodebasePath
        });
    });

document.getElementById('createCodeDatabaseOnlyBtn')
    .addEventListener('click', () => {

        showLoading(
            "Source Code Database Creation",
            "Preparing..."
        );

        runBackendCommand(
            "build_database",
        {
            codebase:selectedCodebasePath
        });
    });

document.getElementById('createJSONSummariesOnlyBtn')
    .addEventListener('click', () => {

        showLoading(
            "Summary Database Creation",
            "Preparing..."
        );

        runBackendCommand(
            "file_summary",
        {
            codebase:selectedCodebasePath
        });
    });

document.getElementById('createSummaryDatabasefromJSONOnlyBtn')
    .addEventListener('click', () => {

        showLoading(
            "Summary Database Creation from JSON Only",
            "Preparing..."
        );

        runBackendCommand(
        "build_summary_database",
        {
            codebase:selectedCodebasePath
        });
    });

document.getElementById('createDirectorySummariesOnlyBtn')
    .addEventListener('click', () => {

        showLoading(
            "Directory Summaries Creation",
            "Preparing..."
        );

        runBackendCommand(
            "directory_summary",
            {
                codebase:selectedCodebasePath
            }
        );
    });

document.getElementById('runBusinessRuleValidationOnlyBtn')
    .addEventListener('click', () => {
        
        

        showPage('businessRulePage');

    });


document.getElementById('allBusinessRuleBtn')
    .addEventListener('click', () => {
        
        showLoading(
            "Business Rule Validation",
            "Preparing..."
        );

        runBackendCommand(
            "validate_business_rules",
            {
                codebase:selectedCodebasePath
            }
        );
    });



document.getElementById('individualBusinessRuleBtn')
    .addEventListener('click', () => {
        
        

        showPage('chooseBusinessRulePage');
        loadBusinessRulesSelection();
    });

document.getElementById('businessRuleBackBtn')
    .addEventListener('click', () => {

        

        showPage('analysisPage');

    });

document.getElementById('chooseBusinessRuleBackBtn')
    .addEventListener('click', () => {

        

        showPage('businessRulePage');

    });

document.getElementById('runUnitTestGenerationOnlyBtn')
    .addEventListener('click', () => {
        
        

        showPage('unitTestPage');
    });

document.getElementById('allValidatedRulesBtn')
    .addEventListener('click', (event) => {
        event.preventDefault();
        event.stopPropagation();
        showLoading(
            "Unit Test Generation",
            "Preparing..."
        );

        runBackendCommand(
            "generate_unit_tests",
            {
                codebase:selectedCodebasePath,
                selected_rules: []
            }
        );
    })

document.getElementById('individualValidatedRulesBtn')
    .addEventListener('click', () => {
        
        

        showPage('chooseUnitTestPage');
        loadValidatedRulesSelection();
    });

document.getElementById('unitTestPageBackBtn')
    .addEventListener('click', () => {

        

        showPage('analysisPage');

    });

document.getElementById('chooseUnitTestPageSelectBtn')
    .addEventListener('click', (event) => {
        event.preventDefault();
        event.stopPropagation();
        showLoading(
            "Unit Test Generation",
            "Preparing..."
        );

        runBackendCommand(
            "generate_unit_tests",
            {
                codebase:selectedCodebasePath,
                selected_rules: selectedRules
            }
        );
    });

document.getElementById('chooseUnitTestPageBackBtn')
    .addEventListener('click', () => {

        

        showPage('unitTestPage');

    });

document.getElementById('runUnitTestValidationOnlyBtn')
    .addEventListener('click', (event) => {
        event.preventDefault();
        event.stopPropagation();
        showLoading(
            "Unit Test Validation",
            "Preparing..."
        );

        runBackendCommand(
            "validate_unit_tests",
            {
                codebase:selectedCodebasePath,
            }
        );
    });


document.getElementById('runIntegrationTestGenerationOnlyBtn')
    .addEventListener('click', (event) => {
        event.preventDefault();
        event.stopPropagation();
        showLoading(
            "Integration Test Generation",
            "Preparing..."
        );

        runBackendCommand(
            "generate_integration_tests",
            {
                codebase:selectedCodebasePath,
                selected_rules: []
            }
        );
    });

// FILLER FOR WHEN VALIDATION GETS ADDED ONTO THIS BRANCH, FOR NOW IT WILL NOT DO ANYTHING WHEN CLICKED

// document.getElementById('allUnitTestsBtn')
//     .addEventListener('click', () => {
        
//         showLoading(
//             "Unit Test Generation",
//             "Preparing..."
//         );

//        runBackendCommand(
//             "generate_unit_tests",
//             {
//                 codebase:selectedCodebasePath
//             }
//         );
//     });



document.getElementById('runUMLGenerationOnly')
    .addEventListener('click', () => {

        showLoading(
            "UML Generation",
            "Preparing..."
        );

        runBackendCommand(
            "generate_all_uml",
            {
                codebase:selectedCodebasePath
            }
        );
    });

document.getElementById('analysisBackBtn')
    .addEventListener('click', () => {

        

        showPage('homePage');
    });
//======================================================
//API PAGE BUTTONS
//======================================================

document.getElementById('apiBackBtn')
    .addEventListener('click', () => {

        

        showPage('homePage');
    });

document.getElementById('submitApiKeyBtn')
    .addEventListener('click', async () => {

        const apiKeyInputEl = document.getElementById('apiKeyInput');
        const apiKey = apiKeyInputEl.value.trim();

        // Nothing typed: saving would write a bare "GOOGLE_API_KEY=" and
        // still report success, so the app would behave as though a key
        // were set until the next launch read it back as missing. trim()
        // so a box holding only spaces counts as empty too.
        if (!apiKey) {
            showApiKeyError("Please enter an API key.");
            return;
        }

        hideApiKeyError();

        // executeCommand resolves as soon as the command has been written
        // to the backend's stdin -- its {success:true} means "sent", not
        // "worked". The real verdict arrives later on the response stream,
        // so everything that depends on it lives in onBackendResponse.
        setApiKeyPending(true);

        await runBackendCommand("set_api_key", { api_key: apiKey });

    });

document.getElementById('replaceApiKeyBtn')
    .addEventListener('click', () => {

        console.log("Replace API Key button clicked");

        // newApiUi() reveals the Back button, and it is left revealed on
        // purpose. Hiding it here stranded anyone who opened this page to
        // change a key and then thought better of it -- the only way out
        // was to enter a key that Google would accept.
        newApiUi();

    });
    
document.getElementById('keepApiKeyBtn')
    .addEventListener('click', () => {

        

        showPage('homePage');

    });

    

//======================================================
//back buttons
//======================================================
document.getElementById('codebaseBackBtn')
    .addEventListener('click', () => {

        invalidateTestResultsDashboard();
        if (codebasePickerPreviousSelection !== null) {
            selectedCodebasePath = codebasePickerPreviousSelection;
            document.getElementById("codebasePath").value = codebasePickerPreviousSelection;
            codebasePickerPreviousSelection = null;
        } else {
            selectedCodebasePath = "";
            document.getElementById("codebasePath").value = "";
        }
        updateCodebaseSummary();

        showPage('homePage');
    });

document.getElementById('faqAndSupportBackBtn')
    .addEventListener('click', () => {

        

        showPage('homePage');
    });

//======================================================
// FAQ Page Buttons
//======================================================
document.getElementById("supportBtn")
.addEventListener("click", () => {

    setFAQSupportActive("support");

    document.getElementById('faqContainer').classList.add('hidden');
    document.getElementById('supportContainer').classList.remove('hidden');
    

});


document.getElementById("faqBtn")
.addEventListener("click", () => {

    setFAQSupportActive("faq");

    // show FAQ content here
    document.getElementById('supportContainer').classList.add('hidden');
    document.getElementById('faqContainer').classList.remove('hidden');
    
    

});

//======================================================
// Analysis Page Buttons
//======================================================

document.getElementById('submitPathBtn')
.addEventListener('click', () => {

    invalidateTestResultsDashboard();
    const previousPath = selectedCodebasePath;
    selectedCodebasePath =
        document.getElementById(
            'codebasePath'
        ).value;
    clearBusinessRulesResultsIfCodebaseChanged(previousPath, selectedCodebasePath);
    codebasePickerPreviousSelection = null;

    updateCodebaseSummary();
    showPage('analysisPage');

});

document.getElementById("browseCodebaseBtn")
.addEventListener("click", async () => {

    const path =
        await window.electronAPI.selectCodebase();

    if (!path)
        return;

    invalidateTestResultsDashboard();
    document.getElementById("codebasePath").value = path;

    const previousPath = selectedCodebasePath;
    selectedCodebasePath = path;
    clearBusinessRulesResultsIfCodebaseChanged(previousPath, selectedCodebasePath);
    updateCodebaseSummary();
});






// ============================================
// Backend Response Handler
// ============================================

window.electronAPI.onBackendResponse((response) => {

    console.log("ACTIVE:", activeCommand);
    console.log("RESPONSE:", response);


    if (!response) {
        return;
    }

    const previewName = String(response.path || "").replace(/\\/g, "/").split("/").pop().toLowerCase();
    if (response.type === "file-preview" && ["last_run_log.json", "test_report.json"].includes(previewName)) {
        return;
    }
    if (response.success === false && pendingTestResultsPaths.size > 0) {
        return;
    }


    // ----------------------------------------
    // API key save / verification result
    // ----------------------------------------
    // Handled here rather than at the Submit button because that only ever
    // sees the "command sent" acknowledgement. Taken before the generic
    // error path below so a rejected key stays on the API page with an
    // explanation instead of being dumped into the error box.
    if (response.command === "set_api_key") {

        setApiKeyPending(false);
        activeCommand = null;

        if (!response.success) {
            showApiKeyError(
                response.error || "Could not save the API key. Please try again."
            );
            return;
        }

        hasAPI = true;

        // Saved now, so there is no reason to keep it on screen.
        document.getElementById('apiKeyInput').value = "";

        showApiKeyPresent();
        document.getElementById('analysisBtn').classList.remove('unusable-btn');

        // Show what the check found -- which model answered and what it
        // cost -- before leaving the page. Navigating straight home threw
        // that away, so a verified key looked no different from no check
        // at all.
        const verdict = response.result?.message || "API key saved.";

        if (response.result?.verified === false) {
            showApiKeyError(verdict);
        } else {
            showApiKeySuccess(verdict);
        }

        setTimeout(() => {
            hideApiKeyError();
            showPage('homePage');
        }, 2500);

        return;
    }


    // The command acknowledgement does not contain the error list.
    if (activeCommand === "get_errors" && response.errors !== undefined) {

        renderErrorPreview(response.errors);
        activeCommand = null;

        return;
    }

    // Handled here, ahead of the generic error path, so a missing or
    // unreadable log shows in the Insights panel instead of leaving the page.
    if (activeCommand === "get_run_usage" && !response.type) {

        if (response.success) {
            renderRunUsage(response.run_usage);
        } else {
            renderTextPreview(response.error || "Could not load AI usage.");
        }

        activeCommand = null;

        return;
    }

    

    // ----------------------------------------
    // Formatted file previews
    // Summaries, sources, normal text files
    // ----------------------------------------
    if (response.type === "file-preview") {


        if (response.preview.type === "summary") {

            renderSummaryPreview(
                response.preview.content
            );

        } else if (response.preview.type === "source") {

            renderSourcePreview(
                response.preview.content
            );

        } else if (response.preview.type === "business_rules") {

            renderBusinessRulesPreview(
                response.preview.content
            );

        } else if (response.preview.type === "pdf") {

            renderPdfPreview(
                response.preview.content
            );

        } else if (response.preview.type === "integration_tests") {

            renderIntegrationTestsPreview(
                response.preview.content
            );

        } else {

            renderJsonPreview(
                response.preview.content
            );

        }


        return;
    }



    // ----------------------------------------
    // Progress updates
    // ----------------------------------------
    if (response.type === "progress") {

        console.log(response);

        updateStepLoading(response.stage);

        if (response.progress !== undefined) {
            console.log("Progress:", response.progress);
            updateStepProgressBar(response.progress);
        }

        return;
    }

    // ----------------------------------------
    // Live token usage
    // ----------------------------------------
    if (response.type === "token_usage") {
        updateTokenMeter(response);
        return;
    }

    if (response.type === "token_usage_stage") {
        addTokenMeterStage(response);
        return;
    }


    if (response.type === "pipeline_progress") {

        console.log(response);

        updatePipelineLoading(response.stage);

        if (response.progress !== undefined) {
            console.log("Progress:", response.progress);
            updatePipleineProgressBar(response.progress);
        }

        return;
    }



    // ----------------------------------------
    // Errors
    // ----------------------------------------
    if (!response.success) {

        errorsMade = true;

        showErrorPopup(response.error || "The operation could not be completed.");

        const errorBox =
            document.getElementById("errorOutput");


        if (errorBox && response.error) {

            errorBox.textContent +=
                response.error + "\n";

        }

        console.log("ERROR RESPONSE:", response);

        if (activeCommand) {

            hideLoading();
            returnToOperationPage();
            activeCommand = null;

        }


        return;
    }



    // ----------------------------------------
    // Token estimate report
    // ----------------------------------------
    // Handled ahead of the generic path because the report is multi-line and
    // loadingStepMessage collapses whitespace.
    const reportCommands = ["estimate_tokens", "token_calibration"];

    if (
        reportCommands.includes(activeCommand) &&
        reportCommands.includes(response.command) &&
        response.result
    ) {

        const isCalibration = response.command === "token_calibration";
        if (isCalibration) {
            renderTokenCalibrationResult(response.result);
        } else {
            renderTokenEstimateResult(response.result);
        }

        const low  = response.result.total_input_low;
        const high = response.result.total_input_high;

        let summary =
            response.command === "token_calibration"
                ? "Calibration complete"
                : "Estimate complete";

        if (typeof low === "number" && typeof high === "number") {

            summary =
                low === high
                    ? `~${low.toLocaleString()} input tokens`
                    : `~${low.toLocaleString()} - ${high.toLocaleString()} input tokens`;

        }

        finishLoading(summary);

        const overlay = document.getElementById("loadingOverlay");
        const title = document.getElementById("loadingTitle");
        overlay?.classList.add("token-report-mode");
        if (title) {
            title.textContent = isCalibration
                ? "✓ Calibration Complete"
                : "✓ Complete";
        }

        activeCommand = null;

        return;
    }


    // ----------------------------------------
    // Analysis output messages
    // ----------------------------------------
    if (response.message) {

        const outputBox =
            document.getElementById("analysisOutputBox");


        if (outputBox) {

            outputBox.textContent +=
                response.message + "\n";

        }

    }




    // ----------------------------------------
    // Command completion
    // ----------------------------------------
    if (response.success) {

        if (
            response.individualStep === true ||
            response.result ||
            response.message
        ) {

            finishLoading(
                response.result?.message ||
                response.message ||
                `${activeCommand} completed`
            );

            showLoadingWarning(response.result?.warning);

            activeCommand = null;
        }
    }

});
// ============================================
// Collection List Rendering
// ============================================


function renderCollections(
    collections,
    dbType
){


    console.log(
        "Collections:",
        collections
    );


    const container =
        dbType === "summary"
            ? document.getElementById(
                "summaryOutput"
            )
            :
            document.getElementById(
                "sourceOutput"
            );


    if(!container){
        return;
    }


    container.innerHTML = "";



    collections.forEach(
        (collection)=>{


            const button =
                document.createElement(
                    "button"
                );

            button.className =
                "output-file-btn btn-primary";

            button.textContent =
                collection;

            button.addEventListener(
                "click",
                ()=>{
                    if(dbType==="summary"){

                        viewingSummaryPreview=true;
                    }
                    else{

                        viewingSourcePreview=true;
                    }
                    runPreviewCommand(
                        "preview",
                        {
                            db_type:dbType,
                            collection:collection
                        }
                    );
                }
            );
            container.appendChild(
                button
            );
        }
    );
}

// ============================================
// Collection Preview Rendering
// ============================================


function renderCollectionPreview(
    preview,
    dbType
){
    let container;
    if(dbType==="summary"){

        container =
            document.getElementById(
                "summaryOutput"
            );
    }
    else{
        container =
            document.getElementById(
                "sourceOutput"
            );
    }
    if(!container){
        return;
    }

    container.innerHTML="";

    const pre =
        document.createElement(
            "pre"
        );

    pre.className =
        dbType==="summary"
            ?
            "summary-preview-text"
            :
            "source-preview-text";

    pre.textContent =
        preview;
    container.appendChild(
        pre
    );


}


// ===========================================
// Display Functions
// ==========================================


function toggleTheme() {
  const newTheme =
    document.body.dataset.theme === "dark"
      ? "light"
      : "dark";

  document.body.dataset.theme = newTheme;

  localStorage.setItem("theme", newTheme);
}

function setApiKeyPending(pending) {

    const btn = document.getElementById('submitApiKeyBtn');

    if (!btn) return;

    btn.disabled = pending;
    btn.textContent = pending ? "Verifying..." : "Submit";

}


function showApiKeySuccess(text) {

    const el = document.getElementById('apiKeyErrorMsg');

    if (!el) return;

    el.textContent = text;
    el.classList.remove("api-key-error");
    el.classList.add("api-key-ok");
    el.classList.remove("hidden");

}


function showApiKeyError(text) {

    const el = document.getElementById('apiKeyErrorMsg');

    if (!el) return;

    el.textContent = text;
    el.classList.remove("api-key-ok");
    el.classList.add("api-key-error");
    el.classList.remove("hidden");

}


function hideApiKeyError() {

    const el = document.getElementById('apiKeyErrorMsg');

    if (el) el.classList.add("hidden");

}


function showApiKeyPresent() {

    const apiBtnEl = document.getElementById("apiBtn");

    if (!apiBtnEl) return;

    // Deliberately NOT .unusable-btn. That class is cosmetic -- it only
    // fades the button and shows a not-allowed cursor -- but this button
    // is the only route to the Replace / Keep page, so looking disabled
    // made a stale key appear unchangeable. Relabel instead of greying.
    apiBtnEl.classList.remove("unusable-btn");
    apiBtnEl.textContent = "Change API Key";

}


function overwriteApiUi() {
    hideApiKeyError();

    document.getElementById('apiKeyMsg').classList.remove("hidden");
    document.getElementById('apiKeyInput').classList.add("hidden");
    document.getElementById('submitApiKeyBtn').classList.add("hidden");
    document.getElementById('replaceApiKeyBtn').classList.remove("hidden");            
    document.getElementById('keepApiKeyBtn').classList.remove("hidden");
    document.getElementById('apiBackBtn').classList.add("hidden");
}

function newApiUi() {
    document.getElementById('apiKeyMsg').classList.add("hidden");

    // Start empty every time. Pages here are shown/hidden rather than
    // reloaded, so whatever the last person typed would otherwise still
    // be sitting in the box when someone opens it to change the key.
    document.getElementById('apiKeyInput').value = "";

    hideApiKeyError();

    document.getElementById('apiKeyInput').classList.remove("hidden");
    document.getElementById('submitApiKeyBtn').classList.remove("hidden");
    document.getElementById('replaceApiKeyBtn').classList.add("hidden");
    document.getElementById('keepApiKeyBtn').classList.add("hidden");
    document.getElementById('apiBackBtn').classList.remove("hidden");
}

function renderJsonPreview(content) {

    const output = document.getElementById("viewDisplayOutputBox");

    if (!output) return;

    output.innerHTML = "";

    const card = document.createElement("div");
    card.className = "json-card";

    const title = document.createElement("h2");
    title.className = "json-title";
    title.textContent = "JSON Preview";

    const pre = document.createElement("pre");
    pre.className = "json-body";

    let pretty = "";

    try {
        if (typeof content === 'string') {
            pretty = JSON.stringify(JSON.parse(content), null, 2);
        } else {
            pretty = JSON.stringify(content, null, 2);
        }
    } catch (e) {
        // not JSON, show raw text
        pretty = String(content || "");
    }

    pre.textContent = pretty;

    card.appendChild(title);
    card.appendChild(pre);

    output.appendChild(card);

}

function renderIntegrationTestsPreview(workflows) {

    const output = document.getElementById("viewDisplayOutputBox");

    if (!output) return;

    output.innerHTML = "";

    const rootCard = document.createElement("div");
    rootCard.className = "integration-tests-card";

    const title = document.createElement("h2");
    title.className = "integration-tests-title";
    title.textContent = "Integration Test Workflows";
    rootCard.appendChild(title);

    const items = Array.isArray(workflows) ? workflows : [];

    if (items.length === 0) {
        const empty = document.createElement("pre");
        empty.className = "integration-test-description";
        empty.textContent = "No integration tests were found.";
        rootCard.appendChild(empty);
        output.appendChild(rootCard);
        return;
    }

    items.forEach((workflow, idx) => {
        const section = document.createElement("section");
        section.className = "integration-test-workflow";

        const heading = document.createElement("h3");
        heading.className = "integration-test-workflow-title";
        heading.textContent = `${idx + 1}. ${workflow.workflow_name || "Workflow"}`;
        section.appendChild(heading);

        if (workflow.workflow_description) {
            const descHeader = document.createElement("h4");
            descHeader.className = "integration-test-section-header";
            descHeader.textContent = "Description";
            section.appendChild(descHeader);

            const desc = document.createElement("pre");
            desc.className = "integration-test-description";
            desc.textContent = workflow.workflow_description;
            section.appendChild(desc);
        }

        const ruleIds = Array.isArray(workflow.rule_ids)
            ? workflow.rule_ids
            : [];

        if (ruleIds.length > 0) {
            const ruleHeader = document.createElement("h4");
            ruleHeader.className = "integration-test-section-header";
            ruleHeader.textContent = "Rule IDs";
            section.appendChild(ruleHeader);

            const ruleBody = document.createElement("pre");
            ruleBody.className = "integration-test-description";
            ruleBody.textContent = ruleIds.join(", ");
            section.appendChild(ruleBody);
        }

        const imports = Array.isArray(workflow.imports)
            ? workflow.imports
            : [];

        if (imports.length > 0) {
            const importsHeader = document.createElement("h4");
            importsHeader.className = "integration-test-section-header";
            importsHeader.textContent = "Imports";
            section.appendChild(importsHeader);

            const importsBody = document.createElement("pre");
            importsBody.className = "integration-test-description";
            importsBody.textContent = imports.map(item => `• ${item}`).join("\n");
            section.appendChild(importsBody);
        }

        const codeHeader = document.createElement("h4");
        codeHeader.className = "integration-test-section-header";
        codeHeader.textContent = "Integration Test";
        section.appendChild(codeHeader);

        const code = document.createElement("pre");
        code.className = "integration-test-code";
        code.textContent = workflow.integration_test || "";
        section.appendChild(code);

        rootCard.appendChild(section);
    });

    output.appendChild(rootCard);
}
