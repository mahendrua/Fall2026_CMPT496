
// ============================================
// Backend State
// ============================================


let pathSubmitted = false;

let ranFullPipeline = false;

let summariesMade = false;
let sourcesMade = false;
let errorsMade = false;


// Currently selected codebase
let selectedCodebasePath = "";


// Selections
let selectedRules = [];

// Backend response buffer
let backendOutputBuffer = "";

//loadingscreen
const commandRequests = new window.CommandRequestTracker();
let activeRequestId = null;
let operationReturnPage = null;

// The saved check a finished pipeline or Consistency Review operation can
// open: the codebase it ran on and the check ID it produced.
let reviewTarget = null;

// True between a pipeline click and the command being sent (a short delay).
let pipelineStarting = false;

// A new check replaces the saved result that unsaved review notes belong to,
// so a run that would do so does not start; the user saves or discards first.
function blockedByUnsavedReviews(codebasePath) {
    const unsaved = window.ConsistencyReviewScreen.unsavedReviews(codebasePath);
    if (!unsaved) return false;
    showErrorPopup(
        `You have ${unsaved} unsaved review ${unsaved === 1 ? "note" : "notes"} for this codebase in `
        + "View Insights → Consistency Review. Save or discard them there first: a new check replaces "
        + "the result they belong to."
    );
    return true;
}

// Shows "Open Consistency Review" on the Complete screen when a check was saved.
function offerReview(codebasePath, checkId) {
    reviewTarget = checkId ? { codebasePath, checkId } : null;
    document.getElementById("loadingOpenReviewBtn").classList.toggle("hidden", !checkId);
}

// Backend calls whose response goes to the caller's promise rather than the
// shared handler below (the Consistency Review screen). They never become the
// active request, so they cannot take over the loading screen or error popup.
const directRequests = new Map();

// options.onEvent receives the request's streamed messages (progress, provider
// waits, running token totals) before the final response resolves the promise.
function backendCall(command, args = {}, options = {}) {
    const request = commandRequests.begin(command);

    return new Promise(resolve => {
        directRequests.set(request.requestId, { resolve, onEvent: options.onEvent });

        const refuse = error => {
            const failure = commandRequests.fail(request.requestId, error);
            directRequests.delete(request.requestId);
            resolve(failure ? failure.response : { success: false, error });
        };

        window.electronAPI.executeCommand(command, args, request.requestId)
            .then(ack => {
                if (ack?.accepted === true && ack.request_id === request.requestId) {
                    commandRequests.accept(request.requestId);
                } else {
                    refuse(ack?.error || "The backend did not accept the command.");
                }
            })
            .catch(error => refuse(error.message || "Could not send the command to the backend."));
    });
}

// Resolves a direct call's promise with its final response. True if the
// response belonged to a direct call (handled, or still in progress).
function settleDirectRequest(correlated) {
    const requestId = correlated?.request?.requestId;
    if (!requestId || !directRequests.has(requestId)) return false;

    const entry = directRequests.get(requestId);
    if (correlated.terminal) {
        directRequests.delete(requestId);
        entry.resolve(correlated.response);
    } else if (entry.onEvent && correlated.response && correlated.response.accepted !== true) {
        try { entry.onEvent(correlated.response); } catch (error) { console.error("Progress handler failed:", error); }
    }
    return true;
}
let pipelineMode = false;

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
let insightsPanelRequest = 0;
const unitTestPreviewPaths = new Set();
let unitTestPreviewLoading = false;
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

// ============================================
// API key manager
// ============================================

const PROVIDER_LABELS = {
    google: "Google (Gemini)",
    openai: "OpenAI",
    anthropic: "Anthropic (Claude)",
    mistral: "Mistral",
    groq: "Groq",
    deepseek: "DeepSeek",
    xai: "xAI (Grok)",
    openrouter: "OpenRouter"
};

// Suggestions for the model box. Any model name can still be typed in.
// Fill the empty ones from each provider's current docs; names change often.
const PROVIDER_MODELS = {
    google: ["gemini-3-flash-preview"],
    openai: [],
    anthropic: ["claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-4-5-20251001"],
    mistral: [],
    groq: [],
    deepseek: [],
    xai: [],
    openrouter: []
};

let cachedKeys = [];
let pendingKey = null;

function fillModelSuggestions(provider) {
    const list = document.getElementById("modelSuggestions");
    list.innerHTML = "";
    (PROVIDER_MODELS[provider] || []).forEach(m => list.appendChild(new Option(m, m)));
}

function initProviderSelect() {
    const sel = document.getElementById("providerSelect");
    if (sel.options.length) return;

    Object.entries(PROVIDER_LABELS).forEach(([value, text]) => {
        sel.add(new Option(text, value));
    });

    sel.addEventListener("change", () => fillModelSuggestions(sel.value));
    fillModelSuggestions(sel.value);
}

async function refreshApiKeyList() {
    initProviderSelect();

    const res = await window.electronAPI.listApiKeys();
    cachedKeys = res?.keys || [];

    const sel = document.getElementById("apiKeySelect");
    const modelInput = document.getElementById("activeModelInput");
    const deleteBtn = document.getElementById("deleteApiKeyBtn");

    sel.innerHTML = "";

    if (!cachedKeys.length) {
        sel.add(new Option("No keys saved yet", ""));
        sel.disabled = true;
        modelInput.value = "";
        modelInput.disabled = true;
        deleteBtn.disabled = true;
        hasAPI = false;
        document.getElementById("analysisBtn").classList.add("unusable-btn");
        return;
    }

    sel.disabled = false;
    modelInput.disabled = false;
    deleteBtn.disabled = false;

    cachedKeys.forEach(k => {
        const opt = new Option(
            `${PROVIDER_LABELS[k.provider] || k.provider} - ${k.label} (${k.masked})`,
            k.id
        );
        opt.selected = k.active;
        sel.add(opt);
    });

    const active = cachedKeys.find(k => k.active) || cachedKeys[0];
    modelInput.value = active.model;
    hasAPI = true;
    showApiKeyPresent();
    document.getElementById("analysisBtn").classList.remove("unusable-btn");
}

document.getElementById("apiKeySelect").addEventListener("change", async (e) => {
    if (!e.target.value) return;
    await window.electronAPI.selectApiKey({ id: e.target.value });
    await refreshApiKeyList();
});

document.getElementById("activeModelInput").addEventListener("change", async (e) => {
    const id = document.getElementById("apiKeySelect").value;
    if (!id) return;
    await window.electronAPI.selectApiKey({ id, model: e.target.value });
    await refreshApiKeyList();
});

document.getElementById("deleteApiKeyBtn").addEventListener("click", async () => {
    const id = document.getElementById("apiKeySelect").value;
    if (!id || !confirm("Delete this key?")) return;
    await window.electronAPI.deleteApiKey({ id });
    await refreshApiKeyList();
});

document.getElementById('apiBtn').addEventListener('click', async () => {
    showPage('apiPage');
    hideApiKeyError();
    await refreshApiKeyList();
});

document.getElementById('submitApiKeyBtn').addEventListener('click', async () => {

    const apiKey = document.getElementById('apiKeyInput').value.trim();
    const model = document.getElementById('newModelInput').value.trim();

    if (!apiKey) { showApiKeyError("Please enter an API key."); return; }
    if (!model)  { showApiKeyError("Please enter a model name."); return; }

    pendingKey = {
        provider: document.getElementById('providerSelect').value,
        label: document.getElementById('apiKeyLabel').value.trim() || "default",
        model,
        apiKey
    };

    hideApiKeyError();
    setApiKeyPending(true);

    // Resolves when the command is *sent*; the verdict arrives in
    // onBackendResponse (the verify_api_key block you already pasted).
    await runBackendCommand("verify_api_key", {
        provider: pendingKey.provider,
        model: pendingKey.model,
        api_key: pendingKey.apiKey
    });
});

refreshApiKeyList();

// ============================================
// Backend Communication Helpers
// ============================================

// options.noPercent hides the step percentage bar, for steps whose backend
// reports no percentage (the bar would otherwise sit at a meaningless value).
function showLoading(title, message, options = {}) {


    const overlay =
        document.getElementById("loadingOverlay");

    if (!overlay) return;

    // Processing starts from Run Operations, including its task-selection
    // subpages. Acknowledging its result returns to the main operations page.
    operationReturnPage = "analysisPage";

    overlay.classList.remove("hidden");

    document.getElementById("loadingTitle").textContent = title;
    document.getElementById("loadingStepMessage").textContent = message;

    // show spinner & loading bar
    document.getElementById("loadingSpinner").classList.remove("hidden");
    document.getElementById("stepProgressContainer").classList.remove("hidden");
    document.getElementById("loadingStepProgressContainer").classList.toggle("hidden", Boolean(options.noPercent));
    document.getElementById("loadingStepProgressText").classList.toggle("hidden", Boolean(options.noPercent));

    // Hide pipeline progress by default
    document.getElementById("pipelineProgressContainer").classList.add("hidden");


    //hide ok button
    document.getElementById("loadingOkBtn").classList.add("hidden");
    document.getElementById("loadingOpenReviewBtn").classList.add("hidden");
    showReportButton(null);
    document.getElementById("loadingCancelBtn").classList.remove("hidden");
    document.getElementById("loadingCancelBtn").disabled = false;

    // hide any report left over from a previous estimate
    const previousEstimate =
        document.getElementById("estimateOutput");

    if (previousEstimate) {
        previousEstimate.classList.add("hidden");
        previousEstimate.textContent = "";
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


// The run report (US-049) of the full run that just finished. The Complete
// screen's "Open Report" button opens it in its own window.
let runReportPath = null;

function showReportButton(reportPath) {

    runReportPath = reportPath || null;

    const button =
        document.getElementById("loadingReportBtn");

    if (!button) return;

    button.classList.toggle("hidden", !runReportPath);

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


    // hide spinner and progress bars; the message itself (inside the step
    // container) stays visible
    document.getElementById("loadingSpinner").classList.add("hidden");
    document.getElementById("loadingStepProgressContainer").classList.add("hidden");
    document.getElementById("loadingStepProgressText").classList.add("hidden");
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
    hideLoading();
    document.getElementById("loadingOkBtn").classList.add("hidden");
    document.getElementById("loadingOpenReviewBtn").classList.add("hidden");
    showPage(operationReturnPage || "analysisPage");
    operationReturnPage = null;
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

    const request = commandRequests.begin(command);
    activeRequestId = request.requestId;

    try {
        const acknowledgement = await window.electronAPI.executeCommand(
            command,
            args,
            request.requestId
        );

        if (acknowledgement?.accepted === true && acknowledgement.request_id === request.requestId) {
            commandRequests.accept(request.requestId);
        } else {
            handleBackendResponse({
                ...acknowledgement,
                request_id: request.requestId,
                command,
                success: false,
                error: acknowledgement?.error || "The backend did not accept the command."
            });
        }

        return acknowledgement;
    } catch (error) {
        handleBackendResponse({
            request_id: request.requestId,
            command,
            success: false,
            error: error.message || "Could not send the command to the backend."
        });
        return { request_id: request.requestId, success: false, error: error.message };
    }

}

async function refreshErrorLog() {
    const requestId = insightsPanelRequest;
    const response = await window.electronAPI.getErrorLog();

    if (requestId !== insightsPanelRequest) return;

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

    if (errorList.length === 0) {

        output.textContent =
            "No errors have been recorded.";

        return;
    }

    // Entries are {time, command, code, message}; older ones may be strings.
    const rows = errorList.map(error => (
        typeof error === "string"
            ? ["—", "—", "—", error]
            : [
                formatStamp(error.time),
                error.code ?? "—",
                error.command || "—",
                error.message || "—",
            ]
    ));

    const card = document.createElement("div");
    card.className = "summary-card usage-card";

    card.appendChild(usageHeader("Errors"));

    card.appendChild(usageTable(["Time", "Code", "Step", "Message"], rows))
        .classList.add("error-table");

    const note = document.createElement("p");
    note.className = "usage-note";
    note.textContent =
        "Errors recorded during the current app session. " +
        "They are cleared when the app starts.";
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
    badge.textContent = status;

    return badge;
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

function usageMetric(label, value, ratio, kind) {

    const metric = document.createElement("article");
    metric.className = `usage-metric usage-metric-${kind}`;

    const title = document.createElement("span");
    title.className = "usage-metric-label";
    title.textContent = label;

    const number = document.createElement("strong");
    number.className = "usage-metric-value";
    number.textContent = value;

    const svgNS = "http://www.w3.org/2000/svg";
    const visual = document.createElementNS(svgNS, "svg");
    visual.classList.add("usage-metric-visual");
    visual.setAttribute("aria-hidden", "true");
    visual.setAttribute("viewBox", kind === "calls" ? "0 0 76 40" : "0 0 76 48");
    visual.setAttribute("focusable", "false");
    if (kind === "calls") {
        visual.classList.add("usage-wave");
        const wave = document.createElementNS(svgNS, "path");
        wave.setAttribute("d", "M2 21h10l5-12 8 23 8-18 7 12 7-8 6 3h11");
        wave.setAttribute("fill", "none");
        wave.setAttribute("stroke", "currentColor");
        wave.setAttribute("stroke-width", "2.5");
        wave.setAttribute("stroke-linecap", "round");
        wave.setAttribute("stroke-linejoin", "round");
        visual.appendChild(wave);
    } else {
        const heights = [15, 25, 20, 38, 30, 44, 35];
        heights.forEach((height, index) => {
            const bar = document.createElementNS(svgNS, "rect");
            bar.setAttribute("x", String(3 + index * 10));
            bar.setAttribute("y", String(46 - height * (0.35 + Math.max(0, Math.min(1, ratio / 100)) * 0.65)));
            bar.setAttribute("width", "5");
            bar.setAttribute("height", String(height * (0.35 + Math.max(0, Math.min(1, ratio / 100)) * 0.65)));
            bar.setAttribute("rx", "2");
            visual.appendChild(bar);
        });
    }

    metric.append(title, number, visual);
    return metric;
}

function usageDetail(label, value, icon) {

    const row = document.createElement("div");
    row.className = "usage-detail-row";

    const iconNode = document.createElement("span");
    iconNode.className = "usage-detail-icon";
    iconNode.setAttribute("aria-hidden", "true");
    iconNode.textContent = icon;

    const copy = document.createElement("div");
    copy.className = "usage-detail-copy";
    const title = document.createElement("span");
    title.className = "usage-detail-label";
    title.textContent = label;
    const text = document.createElement("strong");
    text.className = "usage-detail-value";
    text.textContent = value;
    copy.append(title, text);

    row.append(iconNode, copy);
    return row;
}

function usageTime(seconds) {

    const time = document.createElement("span");
    time.textContent = formatSeconds(seconds);
    if (Number(seconds) >= 60) time.className = "usage-time-long";
    return time;
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
    card.className = "summary-card usage-card usage-dashboard";

    const totals = run.totals || {};

    const failedNote = totals.failed_calls
        ? ` (${totals.failed_calls} failed)`
        : "";

    const titlebar = document.createElement("div");
    titlebar.className = "usage-titlebar";
    const title = usageHeader("AI Usage — Last Run");
    title.classList.add("usage-title");
    const project = document.createElement("span");
    project.className = "usage-project";
    project.textContent = run.codebase || "—";
    const status = usageStatus(run.status || "unknown");
    status.classList.add("usage-run-status");
    titlebar.append(title, project, status);
    card.appendChild(titlebar);

    const dashboard = document.createElement("div");
    dashboard.className = "usage-dashboard-grid";
    const details = document.createElement("section");
    details.className = "usage-details";
    details.setAttribute("aria-label", "Run details and timings");
    details.append(
        usageDetail("Duration", formatSeconds(run.elapsed_seconds), "⏱️"),
        usageDetail("Waited", `${formatCount(totals.waits)} times, ${formatSeconds(totals.waited_seconds ?? 0)}`, "⏳")
    );
    const identity = document.createElement("div");
    identity.className = "usage-identity-card";
    identity.append(
        usageDetail("Started", formatStamp(run.started_at), "📅"),
        usageDetail("Run ID", run.run_id || "—", "📁")
    );
    details.appendChild(identity);

    const tokenMetrics = document.createElement("section");
    tokenMetrics.className = "usage-metrics";
    tokenMetrics.setAttribute("aria-label", "Token and API call metrics");
    const inputs = Number(totals.input_tokens) || 0;
    const outputs = Number(totals.output_tokens) || 0;
    const total = Number(totals.total_tokens) || 0;
    const maxToken = Math.max(inputs, outputs, total);
    tokenMetrics.append(
        usageMetric("Input Tokens", formatCount(totals.input_tokens), maxToken ? inputs / maxToken * 100 : 0, "input"),
        usageMetric("Output Tokens", formatCount(totals.output_tokens), maxToken ? outputs / maxToken * 100 : 0, "output"),
        usageMetric("Total Tokens", formatCount(totals.total_tokens), maxToken ? 100 : 0, "total"),
        usageMetric("AI Calls" + failedNote, formatCount(totals.calls), 0, "calls")
    );
    dashboard.append(details, tokenMetrics);
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

        const byStep = usageTable(
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
        );
        byStep.classList.add("usage-data-table");
        card.appendChild(byStep);
    }

    const calls = run.calls || [];

    if (calls.length) {

        card.appendChild(usageHeader("Slowest AI Calls"));

        const slowest = [...calls]
            .sort((a, b) => (b.duration_seconds ?? 0) - (a.duration_seconds ?? 0))
            .slice(0, 5);

        const slowTable = usageTable(
            ["Step", "Time", "Input", "Output", "Status"],
            slowest.map(call => [
                `⚙️ ${call.stage || "—"}`,
                usageTime(call.duration_seconds),
                formatCount(call.input_tokens),
                formatCount(call.output_tokens),
                usageStatus(call.status),
            ])
        );
        slowTable.classList.add("usage-data-table", "usage-slowest-table");
        [...slowTable.tBodies[0].rows].forEach(row => {
            row.cells[1].classList.add("usage-time-cell");
            row.cells[2].classList.add("usage-number-cell");
            row.cells[3].classList.add("usage-number-cell");
        });
        const slowTableShell = document.createElement("div");
        slowTableShell.className = "usage-table-shell";
        slowTableShell.appendChild(slowTable);
        card.appendChild(slowTableShell);
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

        const reasonText = reason => {
            const badge = document.createElement("span");
            badge.className = `usage-reason ${reason === "429" ? "rate-limit" : ""}`;
            badge.textContent = reason === "429" ? "429 rate limit" : `${reason} Google busy`;
            return badge;
        };

        const waitsTable = usageTable(
            ["Step", "Reason", "Waited"],
            waits.map(wait => [
                `⚙️ ${wait.stage || "—"}`,
                reasonText(wait.reason),
                formatSeconds(wait.seconds),
            ])
        );
        waitsTable.classList.add("usage-data-table", "usage-waits-table");
        [...waitsTable.tBodies[0].rows].forEach(row => row.cells[2].classList.add("usage-number-cell"));
        card.appendChild(waitsTable);
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

function renderUnitTestsPreview(data) {

    const output = document.getElementById("viewDisplayOutputBox");
    output.innerHTML = "";

    const title = document.createElement("h2");
    title.className = "summary-header";
    title.textContent = "Unit Tests";
    output.appendChild(title);

    const generated = Array.isArray(data.generated) ? data.generated : [];
    const validated = Array.isArray(data.validated) ? data.validated : [];
    const discarded = Array.isArray(data.discarded) ? data.discarded : [];
    const report = data.report && typeof data.report === "object" ? data.report : null;
    const sourceTests = generated.length
        ? generated
        : [...validated, ...discarded];

    if (report) {
        const summary = document.createElement("p");
        summary.className = "unit-test-summary";
        summary.textContent = [
            `Generated: ${report.generated ?? sourceTests.length}`,
            `Passed: ${report.passed ?? "—"}`,
            `Failed: ${report.failed ?? "—"}`,
            `Skipped: ${report.skipped ?? "—"}`
        ].join(" · ");
        output.appendChild(summary);

        if (report.project_error) {
            const error = document.createElement("p");
            error.className = "unit-test-project-error";
            error.textContent = `Tests were not run because the test project could not be built: ${report.project_error}`;
            output.appendChild(error);
        } else if (report.message) {
            const note = document.createElement("p");
            note.className = "unit-test-note";
            note.textContent = report.message;
            output.appendChild(note);
        }
    }

    if (!sourceTests.length) {
        const empty = document.createElement("p");
        empty.className = "unit-test-empty";
        empty.textContent = "No saved unit-test results are available for this codebase.";
        output.appendChild(empty);
        return;
    }

    const byId = entries => new Map(entries
        .filter(entry => entry && entry.id !== undefined)
        .map(entry => [String(entry.id), entry]));
    const validatedById = byId(validated);
    const discardedById = byId(discarded);
    const failedNames = new Set((Array.isArray(report?.failed_tests) ? report.failed_tests : [])
        .flatMap(value => String(value).match(/\bUT_[\w]+\b/g) || []));
    const list = document.createElement("div");
    list.className = "unit-test-list";

    sourceTests.forEach((test, index) => {
        if (!test || typeof test !== "object") return;

        const id = test.id === undefined ? `item-${index + 1}` : String(test.id);
        const name = `UT_${id}`;
        const dropped = discardedById.get(id);
        const kept = validatedById.get(id);
        let statusLabel;
        let statusClass;
        let reason = "";

        if (dropped) {
            statusLabel = "Discarded";
            statusClass = "discarded";
            reason = dropped.reason || "This generated test was discarded during validation.";
        } else if (report?.project_error) {
            statusLabel = "Not run";
            statusClass = "not-run";
            reason = "The test project did not build, so this test was not executed.";
        } else if (!report) {
            statusLabel = "Not run";
            statusClass = "not-run";
            reason = "No saved test report is available for this test.";
        } else if (failedNames.has(name)) {
            statusLabel = "Failed";
            statusClass = "failed";
        } else if ((Number(report?.passed) || 0) + (Number(report?.failed) || 0) > 0) {
            // Match the status calculation already used by the saved Run Report:
            // failed test names identify failures; otherwise a test run with
            // recorded pass/fail results marks the kept test as passed.
            statusLabel = "Passed";
            statusClass = "passed";
        } else if (kept) {
            statusLabel = "Not run";
            statusClass = "not-run";
            reason = "The test was validated, but the saved report records no execution result.";
        } else {
            statusLabel = "Not run";
            statusClass = "not-run";
        }

        const item = document.createElement("article");
        item.className = "unit-test-item";
        const header = document.createElement("div");
        header.className = "unit-test-item-header";
        const nameNode = document.createElement("h3");
        nameNode.textContent = name;
        const badge = document.createElement("span");
        badge.className = `unit-test-status ${statusClass}`;
        badge.textContent = statusLabel;
        header.append(nameNode, badge);
        item.appendChild(header);

        const description = document.createElement("p");
        description.className = "unit-test-details";
        description.textContent = reason || (test.source_file_paths?.length
            ? `Source: ${test.source_file_paths.join(", ")}`
            : "Saved unit-test result.");
        item.appendChild(description);

        const finalTest = kept || dropped || test;
        const code = document.createElement("pre");
        code.className = "unit-test-code";
        const imports = Array.isArray(finalTest.imports) ? finalTest.imports.join("\n") : "";
        const body = finalTest.unit_test || finalTest.test || "";
        code.textContent = [imports, body].filter(Boolean).join("\n\n") || "Test source was not saved.";
        item.appendChild(code);
        list.appendChild(item);
    });

    output.appendChild(list);
}

function showPage(pageId) {

    document.querySelectorAll('.page').forEach(page => {
        page.classList.add('hidden');
    });

    document.getElementById(pageId).classList.remove('hidden');
}

function showButtons(buttonId) {

    insightsPanelRequest += 1;

    // Another view is taking the output box: the Consistency Review screen
    // must not draw into it when a background load or check finishes.
    if (buttonId !== "viewConsistencyBtns" && window.ConsistencyReviewScreen) {
        window.ConsistencyReviewScreen.hide();
    }

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
    const request = commandRequests.get(activeRequestId);
    if (!request) return;

    const cancelButton = document.getElementById("loadingCancelBtn");
    const cancelledRequestId = request.requestId;
    const cancelledCommand = request.command;
    cancelButton.disabled = true;

    const response = await window.electronAPI.cancelCommand(cancelledRequestId);

    if (!response?.success) {
        cancelButton.disabled = false;
        showErrorPopup(response?.error || "Could not cancel the operation.");
        return;
    }

    const cancellationMessage = "Operation cancelled by user.";
    errorsMade = true;

    await window.electronAPI.recordErrorLog({
        request_id: cancelledRequestId,
        command: cancelledCommand,
        message: cancellationMessage,
        code: "CANCELLED"
    });

    commandRequests.fail(cancelledRequestId, cancellationMessage, { cancelled: true });
    if (activeRequestId === cancelledRequestId) activeRequestId = null;
    finishLoading(cancellationMessage);
    document.getElementById("loadingTitle").textContent = "Cancelled";
    activeCommand = null;
});

// Opens the whole-project result of the check that just finished, by its ID:
// the review screen says so if the saved result has since become another one.
document.getElementById("loadingOpenReviewBtn").addEventListener("click", () => {

    const target = reviewTarget;
    if (!target) return;

    hideLoading();
    document.getElementById("loadingOkBtn").classList.add("hidden");
    document.getElementById("loadingOpenReviewBtn").classList.add("hidden");

    showPage('mainViewPage');
    showButtons("viewConsistencyBtns");
    const output = document.getElementById("viewDisplayOutputBox");
    output.innerHTML = "";
    window.ConsistencyReviewScreen.open(output, { call: backendCall }, target.codebasePath,
        { scope: "full", expectCheckId: target.checkId });

});

document.getElementById("loadingOkBtn").addEventListener("click", () => {
    returnToOperationPage();
});

document.getElementById("loadingReportBtn").addEventListener("click", async () => {

    if (!runReportPath) return;

    // Opening the report dismisses the completion overlay but keeps the
    // selected codebase's Run Operations page in place behind its window.
    returnToOperationPage();

    const response = await window.electronAPI.openReport(runReportPath);

    if (!response?.success) {
        showErrorPopup(response?.error || "Could not open the run report.");
    }

});

// MENU BUTTONS
document.getElementById("homeConnectCodebaseBtn")
    .addEventListener("click", () => showPage("codebasePipelinePage"));

document.getElementById('analysisBtn')
    .addEventListener('click', () => {

        


        if (!pathSubmitted) {
            showPage('codebasePipelinePage');
        } else {
            showPage('analysisPage');
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
        document.getElementById("viewDisplayOutputBox").innerHTML = "";

    });

document.getElementById("errorPopupCloseBtn")
    .addEventListener("click", () => {
        hideErrorPopup();
        // Failed operation responses dismiss their loading overlay before
        // showing this popup. Cancellation-request errors leave the operation
        // active, so closing that popup must not dismiss its progress screen.
        if (!activeCommand && operationReturnPage) returnToOperationPage();
    });

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

document.getElementById("viewConsistencyBtn")
.addEventListener("click", () => {

    showButtons("viewConsistencyBtns");

    const output = document.getElementById("viewDisplayOutputBox");
    output.innerHTML = "";

    // Reads saved results only: no check is run and no model is called.
    window.ConsistencyReviewScreen.open(output, { call: backendCall }, selectedCodebasePath);

});

document.getElementById("viewUsageBtn")
.addEventListener("click", async () => {

    showButtons("viewUsageBtns");

    await runBackendCommand("get_run_usage");

});

// The run report (US-049) in its own window: the selected codebase's, or the
// most recent one when no codebase has been selected yet this session.
document.getElementById("viewRunReportBtn")
.addEventListener("click", async () => {

    // This message takes the output box (see showButtons).
    if (window.ConsistencyReviewScreen) {
        window.ConsistencyReviewScreen.hide();
    }

    const codebaseName = getSelectedCodebaseName();
    const response = await window.electronAPI.openLatestReport(codebaseName);

    renderTextPreview(
        response?.success
            ? `Opened the run report for ${response.codebase} in its own window.`
            : (response?.error || "Could not open the run report.")
    );

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

let businessRulesRequest = 0;

document.getElementById("viewDisplayBusinessRulesBtn").addEventListener("click", async () => {
    showButtons("mainViewBtns");

    const output = document.getElementById("viewDisplayOutputBox");
    const requestId = ++businessRulesRequest;
    const codebasePath = selectedCodebasePath;
    output.innerHTML = "";

    const title = document.createElement("h2");
    title.className = "business-rules-title";
    title.textContent = "Business Rules";
    output.appendChild(title);

    if (!codebasePath) {
        const message = document.createElement("p");
        message.className = "business-rules-empty";
        message.textContent = "Select a codebase first from the pipeline page.";
        output.appendChild(message);
        return;
    }

    const loading = document.createElement("p");
    loading.className = "business-rules-loading";
    loading.textContent = "Loading business rules…";
    output.appendChild(loading);

    let response;
    try {
        response = await window.electronAPI.getBusinessRules(codebasePath);
    } catch (error) {
        response = { success: false, error: error.message || "Could not load business rules." };
    }

    if (requestId !== businessRulesRequest || codebasePath !== selectedCodebasePath) return;
    output.innerHTML = "";
    output.appendChild(title);

    if (!response?.success) {
        const message = document.createElement("p");
        message.className = "business-rules-unavailable";
        message.textContent = response?.error || "Could not load business rules.";
        output.appendChild(message);
        return;
    }

    const validated = response.validated || { exists: false, loaded: false, count: null, rules: [] };
    const discarded = response.discarded || { exists: false, loaded: false, count: null, rules: [] };
    const summary = document.createElement("div");
    summary.className = "business-rules-summary";
    for (const [label, file] of [["Validated", validated], ["Discarded", discarded]]) {
        const badge = document.createElement("span");
        badge.className = `business-rules-count ${label.toLowerCase()}`;
        badge.textContent = `${label}: ${file.loaded ? file.count : "Unavailable"}`;
        summary.appendChild(badge);
    }
    output.appendChild(summary);

    const allRules = [
        ...(validated.loaded ? validated.rules.map((rule) => ({
            text: typeof rule === "string" ? rule : rule.text,
            source: typeof rule === "string" ? "" : (rule.source || ""),
            status: "Validated"
        })) : []),
        ...(discarded.loaded ? discarded.rules.map((rule) => ({
            text: typeof rule === "string" ? rule : rule.text,
            source: typeof rule === "string" ? "" : (rule.source || ""),
            status: "Discarded"
        })) : [])
    ];

    const toolbar = document.createElement("div");
    toolbar.className = "business-rules-toolbar";
    const search = document.createElement("input");
    search.type = "search";
    search.className = "business-rules-search";
    search.placeholder = "Search rules...";
    search.setAttribute("aria-label", "Search rules");
    const status = document.createElement("select");
    status.className = "business-rules-filter";
    status.setAttribute("aria-label", "Filter by status");
    [["All", "all"], ["Validated", "validated"], ["Discarded", "discarded"]].forEach(([label, value]) => {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = label;
        status.appendChild(option);
    });
    const folder = document.createElement("select");
    folder.className = "business-rules-filter";
    folder.setAttribute("aria-label", "Filter by source folder");
    const allFolders = document.createElement("option");
    allFolders.value = "all";
    allFolders.textContent = "All Folders";
    folder.appendChild(allFolders);
    [...new Set(allRules.map((rule) => rule.source).filter(Boolean))]
        .sort((a, b) => a.localeCompare(b, undefined, { sensitivity: "base" }))
        .forEach((source) => {
            const option = document.createElement("option");
            option.value = source;
            option.textContent = source;
            folder.appendChild(option);
        });
    const clear = document.createElement("button");
    clear.type = "button";
    clear.className = "business-rules-clear";
    clear.textContent = "✕ Clear Filters";
    toolbar.append(search, status, folder, clear);
    output.appendChild(toolbar);

    const list = document.createElement("div");
    list.className = "business-rules-list";
    output.appendChild(list);

    const hasAnyRuleFile = validated.exists || discarded.exists;
    if (!hasAnyRuleFile) {
        const empty = document.createElement("p");
        empty.className = "business-rules-empty";
        empty.textContent = "No business-rule validation results are available for this codebase.";
        list.appendChild(empty);
    }

    const addStatusMessage = (message, className = "business-rules-empty") => {
        const empty = document.createElement("p");
        empty.className = className;
        empty.textContent = message;
        list.appendChild(empty);
    };

    if (validated.exists && !validated.loaded) {
        addStatusMessage(`Validated rules unavailable${validated.error ? `: ${validated.error}` : "."}`, "business-rules-unavailable");
    } else if (validated.loaded && validated.count === 0) {
        addStatusMessage("No validated rules");
    }
    if (discarded.exists && !discarded.loaded) {
        addStatusMessage(`Discarded rules unavailable${discarded.error ? `: ${discarded.error}` : "."}`, "business-rules-unavailable");
    } else if (discarded.loaded && discarded.count === 0) {
        addStatusMessage("No discarded rules");
    }

    const renderRows = () => {
        list.querySelectorAll(".business-rule-row, .business-rules-no-match").forEach((node) => node.remove());
        if (!hasAnyRuleFile || allRules.length === 0) return;

        const query = search.value.trim().toLocaleLowerCase();
        const selectedStatus = status.value;
        const selectedFolder = folder.value;
        const matches = allRules.filter((rule) =>
            (selectedStatus === "all" || rule.status.toLowerCase() === selectedStatus) &&
            (selectedFolder === "all" || rule.source === selectedFolder) &&
            rule.text.toLocaleLowerCase().includes(query)
        );

        if (matches.length === 0) {
            const noMatch = document.createElement("div");
            noMatch.className = "business-rules-no-match";
            const message = document.createElement("p");
            message.textContent = "No matching rules";
            const clearLink = document.createElement("button");
            clearLink.type = "button";
            clearLink.className = "business-rules-clear";
            clearLink.textContent = "Clear Filters";
            clearLink.addEventListener("click", () => clear.click());
            noMatch.append(message, clearLink);
            list.appendChild(noMatch);
            return;
        }

        matches.forEach((rule) => {
            const row = document.createElement("article");
            row.className = "business-rule-row";
            const badge = document.createElement("span");
            const statusClass = rule.status.toLowerCase();
            badge.className = `business-rule-status ${statusClass}`;
            badge.textContent = rule.status;
            const text = document.createElement("p");
            text.className = "business-rule-text";
            text.textContent = rule.text;
            row.append(badge, text);
            list.appendChild(row);
        });
    };

    const clearFilters = () => {
        search.value = "";
        status.value = "all";
        folder.value = "all";
        renderRows();
    };
    search.addEventListener("input", renderRows);
    status.addEventListener("change", renderRows);
    folder.addEventListener("change", renderRows);
    clear.addEventListener("click", clearFilters);
    renderRows();
});

document.getElementById("viewUnitTestsBtn").addEventListener("click", async () => {
    showButtons("mainViewBtns");
    const requestId = insightsPanelRequest;
    const output = document.getElementById("viewDisplayOutputBox");
    output.innerHTML = "";

    const codebaseName = getSelectedCodebaseName();
    if (!codebaseName) {
        renderTextPreview("Select a codebase first from the pipeline page.");
        return;
    }

    const loading = document.createElement("p");
    loading.className = "unit-test-empty";
    loading.textContent = "Loading saved unit-test results…";
    output.appendChild(loading);

    const rootPath = `agent/UT_agent_output/${codebaseName}`;
    const listing = await window.electronAPI.previewCommand("files", { path: rootPath });
    if (requestId !== insightsPanelRequest || codebaseName !== getSelectedCodebaseName()) return;
    if (!listing?.success) {
        renderUnitTestsPreview({ generated: [], validated: [], discarded: [], report: null });
        return;
    }

    const filesByName = new Map((listing.files || []).map(file => [file.name.toLowerCase(), file]));
    const readSavedJson = async fileName => {
        const file = filesByName.get(fileName.toLowerCase());
        if (!file || file.isDirectory) return undefined;

        const normalized = normalizePath(file.path);
        unitTestPreviewPaths.add(normalized);
        unitTestPreviewLoading = true;
        try {
            const response = await window.electronAPI.previewCommand("open_file", { path: file.path });
            if (!response?.success || !response.preview) return undefined;
            const content = response.preview.content;
            return typeof content === "string" ? JSON.parse(content) : content;
        } catch (error) {
            console.warn(`Could not read saved ${fileName}:`, error);
            return undefined;
        } finally {
            unitTestPreviewPaths.delete(normalized);
            unitTestPreviewLoading = unitTestPreviewPaths.size > 0;
        }
    };

    const [generated, validated, discarded, report] = await Promise.all([
        readSavedJson("unit_tests.json"),
        readSavedJson("validated_tests.json"),
        readSavedJson("discarded_tests.json"),
        readSavedJson("test_report.json")
    ]);

    if (requestId !== insightsPanelRequest || codebaseName !== getSelectedCodebaseName()) return;
    renderUnitTestsPreview({ generated, validated, discarded, report });
});

document.getElementById('mainViewBackBtn')
    .addEventListener('click', () => {

        

        showPage('homePage');
    }); 

// UML view should surface the generated PDFs for the selected codebase.
document.getElementById('viewUMLBtn')
    .addEventListener('click', async () => {

        showButtons('viewFilesBtns');
        setActiveFileListView({ includePdfs: true, onlyPdfs: true, recursivePdfs: true });

        const output = document.getElementById("viewDisplayOutputBox");
        output.innerHTML = "";
        const codebaseName = getSelectedCodebaseName();
        if (!codebaseName) {
            renderTextPreview("Select a codebase first from the pipeline page.");
            return;
        }

        const requestId = insightsPanelRequest;
        const rootPath = `agent/file_summary_agent_output/${codebaseName}`;
        resetInsightFolderNavigation(rootPath);
        const response = await runPreviewCommand('files', {
            path: rootPath,
            recursivePdfs: true
        });

        if (requestId !== insightsPanelRequest) return;
        if (!response?.success || !response.files?.some(file => file.name.toLowerCase().endsWith(".pdf"))) {
            renderTextPreview("No UML diagram is available for this codebase yet. Generate UML from Run Operations to create one.");
            return;
        }

        renderTextPreview("Saved UML diagrams are available. Select a diagram from the file list to preview it.");
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

        // Focus stays on the button under the loading screen: no second run
        // while one is pending (including the moment before it is sent), and
        // no run over unsaved review notes (its Consistency Review stage
        // replaces the whole-project result).
        if (activeRequestId || pipelineStarting) return;
        if (blockedByUnsavedReviews(selectedCodebasePath)) return;
        pipelineStarting = true;
        reviewTarget = { codebasePath: selectedCodebasePath, checkId: null };

        showLoading(
            "Running Full Pipeline",
            "Preparing..."
        );
        showLoadingWarning(
            "This run ends with a Consistency Review stage that compares the code with what was just "
            + "generated: one more AI request per source file, on your selected API key."
        );


        setTimeout(() => {

            pipelineStarting = false;              // activeRequestId takes over from here
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


// Compares the code with the summaries, rules and tests already generated for
// the selected codebase and saves the result (View Insights → Consistency
// Review). Nothing is regenerated.
document.getElementById('runConsistencyReviewOnlyBtn')
    .addEventListener('click', (event) => {
        event.preventDefault();
        event.stopPropagation();

        // Focus stays on this button under the loading screen; a second Enter
        // or click must not start another check while one is pending.
        if (activeRequestId) return;

        if (blockedByUnsavedReviews(selectedCodebasePath)) return;
        reviewTarget = { codebasePath: selectedCodebasePath, checkId: null };

        showLoading(
            "Consistency Review",
            "Preparing...",
            { noPercent: true }
        );
        showLoadingWarning(
            "This compares the code with the summaries, rules and tests already generated; nothing is "
            + "regenerated. Each file checked is a request on your selected API key. Cancel stops the check "
            + "at once: nothing from it is saved (the previous result stays), and requests already made still count."
        );

        runBackendCommand(
            "check_consistency",
            {
                codebase:selectedCodebasePath,
                scope:"full",
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

// Mutation testing: how many small code changes the passing unit tests
// catch. Takes about a minute on ConsoleTables; the Open Report button shows
// when it finishes, since the backend rebuilds the run report.
document.getElementById('runMutationTestingOnlyBtn')
    .addEventListener('click', (event) => {
        event.preventDefault();
        event.stopPropagation();
        showLoading(
            "Test Strength (Mutation Testing)",
            "Preparing..."
        );

        runBackendCommand(
            "run_mutation_tests",
            {
                codebase:selectedCodebasePath
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
    

//======================================================
//back buttons
//======================================================
document.getElementById('codebaseBackBtn')
    .addEventListener('click', () => {

        

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


    


    showPage('analysisPage');


    selectedCodebasePath =
        document.getElementById(
            'codebasePath'
        ).value;

    updateHomeCodebaseSummary(selectedCodebasePath);


});

document.getElementById("browseCodebaseBtn")
.addEventListener("click", async () => {

    const path =
        await window.electronAPI.selectCodebase();

    if (!path)
        return;

    document.getElementById("codebasePath").value = path;

    selectedCodebasePath = path;
    updateHomeCodebaseSummary(path);
});

function updateHomeCodebaseSummary(codebasePath) {
    const cleaned = String(codebasePath || "").replace(/[\\/]+$/, "");
    const codebaseName = cleaned ? cleaned.split(/[\\/]/).pop() : "No codebase selected";
    const name = document.getElementById("homeCodebaseName");
    const operationsName = document.getElementById("operationsProjectName");
    const operationsPath = document.getElementById("operationsCodebasePath");

    if (name) {
        name.textContent = codebaseName;
        name.title = cleaned || "";
    }
    if (operationsName) {
        operationsName.textContent = codebaseName;
        operationsName.title = cleaned || "";
    }
    if (operationsPath) {
        operationsPath.textContent = cleaned || "No path selected";
        operationsPath.title = cleaned || "";
    }
}

updateHomeCodebaseSummary(selectedCodebasePath);






// ============================================
// Backend Response Handler
// ============================================

window.electronAPI.onBackendResponse(async (response) => {

    console.log("ACTIVE REQUEST:", activeRequestId);
    console.log("RESPONSE:", response);


    if (!response) {
        return;
    }

    if ((response.type === "file-preview" && unitTestPreviewPaths.has(normalizePath(response.path))) ||
        (unitTestPreviewLoading && !response.type && !response.request_id)) {
        return;
    }

    let activeCommand = null;
    if (response.type !== "file-preview" && response.type !== "files") {
        let correlated;

        if (typeof response.request_id !== "string" || !response.request_id) {
            const failures = commandRequests.failAll("Malformed backend response: missing request ID.");
            failures.forEach(settleDirectRequest);
            correlated = failures.find(item => item.request.requestId === activeRequestId);
        } else {
            correlated = commandRequests.handle(response);
        }

        if (correlated?.matched && settleDirectRequest(correlated)) return;
        if (!correlated?.matched) return;
        if (correlated.request.requestId !== activeRequestId) return;
        if (correlated.terminal) {
            activeCommand = correlated.request.command;
            activeRequestId = null;
            response = correlated.response;
        } else if (response.accepted === true) {
            return;
        }
    }


    // ----------------------------------------
    // API key verification result
    // ----------------------------------------
    // The Submit button only sees the "command sent" acknowledgement, so the
    // real verdict is handled here, ahead of the generic error path. A
    // rejected key stays on the API page with an explanation instead of
    // being dumped into the error popup.
    if (response.command === "verify_api_key") {

        setApiKeyPending(false);
        activeCommand = null;

        if (!response.success) {
            showApiKeyError(response.error || "Could not verify the API key.");
            pendingKey = null;
            return;
        }

        const check = response.result || {};

        if (check.status === "rejected") {
            showApiKeyError(check.message);
            pendingKey = null;
            return;
        }

        const saved = await window.electronAPI.saveApiKey({ ...pendingKey, makeActive: true });
        pendingKey = null;

        if (!saved?.success) {
            showApiKeyError(saved?.error || "Could not save the key.");
            return;
        }

        document.getElementById('apiKeyInput').value = "";
        document.getElementById('apiKeyLabel').value = "";
        await refreshApiKeyList();

        if (check.status === "unverified") {
            showApiKeyError(check.message);
        } else {
            showApiKeySuccess(check.message);
        }

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

        // A stage without a percentage of its own (Consistency Review) hides
        // the step bar instead of leaving the previous stage's value there.
        const noStepPercent = response.step_percent === false;
        document.getElementById("loadingStepProgressContainer").classList.toggle("hidden", noStepPercent);
        document.getElementById("loadingStepProgressText").classList.toggle("hidden", noStepPercent);

        if (response.progress !== undefined) {
            console.log("Progress:", response.progress);
            updatePipleineProgressBar(response.progress);
        }

        return;
    }



    // ----------------------------------------
    // Errors
    // ----------------------------------------
    if (response.cancelled) {
        finishLoading(response.message || "Operation cancelled.");
        document.getElementById("loadingTitle").textContent = "Cancelled";
        activeCommand = null;
        return;
    }

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

        const report =
            document.getElementById("estimateOutput");

        if (report && response.result.message) {

            report.textContent = response.result.message;
            report.classList.remove("hidden");

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
    // Full pipeline completion
    // ----------------------------------------
    // Generation finished (a failed step never gets here). The Consistency
    // Review stage is reported on its own line: a mismatch, an incomplete or a
    // failed review is not a pipeline failure.
    if (activeCommand === "full_pipeline" && response.success && response.result) {

        const review = window.ConsistencyReviewModel.pipelineReview(response.result.consistency_review);

        finishLoading(`${response.result.message || "Full pipeline completed"}. ${review.text}`);
        showLoadingWarning(response.result.warning);
        offerReview(reviewTarget ? reviewTarget.codebasePath : selectedCodebasePath, review.checkId);
        showReportButton(response.result.run_report);

        activeCommand = null;

        return;
    }


    // ----------------------------------------
    // Consistency check completion
    // ----------------------------------------
    // Completed, incomplete and failed checks all return success (the result
    // was saved); the title tells them apart, and the warning carries why.
    if (activeCommand === "check_consistency" && response.success && response.result) {

        const check = response.result;

        finishLoading(`${check.message} Open View Insights → Consistency Review to see the result.`);
        offerReview(reviewTarget ? reviewTarget.codebasePath : selectedCodebasePath, check.check_id);

        document.getElementById("loadingTitle").textContent = {
            completed: "Complete",
            partial: "Check incomplete",
            failed: "Check failed",
        }[check.status] || "Complete";

        showLoadingWarning(check.warning);

        activeCommand = null;

        return;
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
            showReportButton(response.result?.run_report);

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
    btn.textContent = pending ? "Verifying..." : "Verify & Save";

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

    // Keep the landing action label stable; this route also manages/replaces
    // an existing key, so it remains available when a key is already saved.
    apiBtnEl.classList.remove("unusable-btn");
    apiBtnEl.textContent = "↑ Upload API Key";

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
