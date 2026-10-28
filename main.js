const { app, BrowserWindow, ipcMain, shell, dialog, Menu } = require('electron');
const { spawn } = require('child_process');
const { randomUUID } = require('node:crypto');
const path = require('path');
const fs = require("fs");
const dotenv = require("dotenv");
const { GuiTestRunner } = require("./guiTestRunner");
const { LocalGuiApp } = require("./guiLocalApp");
const {
    applyGuiTestEvent,
    buildGuiReportHtml,
    createGuiTestReport,
    finalizeGuiTestReport,
    getGuiTestReport,
    isGuiTestReportEvent,
    listGuiTestReports,
    saveGuiTestReport
} = require("./guiTestReports");
const { CommandRequestTracker } = require("./command_requests");


let pythonProcess = null;
let mainWindow = null;
let restartBackendAfterCancel = false;
const backendRequests = new CommandRequestTracker();
// Directory the backend actually writes its output to (userData when packaged).
let backendOutputDir = __dirname;
let guiTestRunner = null;
let localGuiApp = null;
let guiShutdownStarted = false;
let activeGuiTestReport = null;
// ----------------------------------------------------
// API KEYS (.env)
// ----------------------------------------------------
// Each saved key is one line  APIKEY__<PROVIDER>__<label>=<key>
// with its model in           APIMODEL__<PROVIDER>__<label>=<model>
// The selected one is mirrored into ACTIVE_LLM_* for the Python backend.

const PROVIDERS = ["google", "openai", "anthropic", "mistral", "groq", "deepseek", "xai", "openrouter"];

function getEnvPath() {
    const backendDir = app.isPackaged
        ? path.join(process.resourcesPath, "backend")
        : path.join(__dirname, "releases", "main");

    const exeName = process.platform === "win32" ? "main.exe" : "main";
    const exeExists = fs.existsSync(path.join(backendDir, exeName));

    // Same rule the Python side uses: next to the exe, else next to main.py.
    return path.join(exeExists ? backendDir : __dirname, ".env");
}

function ensureEnvFile() {
    const p = getEnvPath();
    try {
        fs.mkdirSync(path.dirname(p), { recursive: true });
        if (!fs.existsSync(p)) fs.writeFileSync(p, "", "utf8");
    } catch (error) {
        console.error("Could not create .env:", error);
    }
}

function readEnv() {
    const p = getEnvPath();
    return fs.existsSync(p) ? dotenv.parse(fs.readFileSync(p)) : {};
}

function writeEnv(env) {
    const p = getEnvPath();
    fs.mkdirSync(path.dirname(p), { recursive: true });

    const text = Object.entries(env)
        .filter(([, v]) => v !== undefined && v !== null && v !== "")
        .map(([k, v]) => `${k}=${v}`)
        .join("\n") + "\n";

    const tmp = `${p}.tmp`;
    fs.writeFileSync(tmp, text, "utf8");
    fs.renameSync(tmp, p);
}

function applyActive(env, id) {
    const key = id && env[`APIKEY__${id}`];

    if (!key) {
        delete env.ACTIVE_LLM_KEY;
        delete env.ACTIVE_LLM_PROVIDER;
        delete env.ACTIVE_LLM_MODEL;
        delete env.ACTIVE_LLM_API_KEY;
        return;
    }

    env.ACTIVE_LLM_KEY = id;
    env.ACTIVE_LLM_PROVIDER = id.split("__")[0].toLowerCase();
    env.ACTIVE_LLM_MODEL = env[`APIMODEL__${id}`] || "";
    env.ACTIVE_LLM_API_KEY = key;
}

// Moves an old single GOOGLE_API_KEY into the new format. Returns true if it changed anything.
function migrateLegacyKey(env) {
    const legacy = (env.GOOGLE_API_KEY || "").trim();
    if (!legacy) return false;

    if (!env["APIKEY__GOOGLE__default"]) {
        env["APIKEY__GOOGLE__default"] = legacy;
        env["APIMODEL__GOOGLE__default"] = "gemini-3-flash-preview";
    }

    delete env.GOOGLE_API_KEY;

    if (!env.ACTIVE_LLM_KEY) applyActive(env, "GOOGLE__default");
    return true;
}

function listKeys(env) {
    return Object.keys(env)
        .filter(k => k.startsWith("APIKEY__"))
        .map(k => {
            const id = k.slice("APIKEY__".length);
            const [provider, label] = id.split("__");
            return {
                id,
                provider: provider.toLowerCase(),
                label,
                model: env[`APIMODEL__${id}`] || "",
                masked: "…" + String(env[k]).slice(-4),
                active: env.ACTIVE_LLM_KEY === id
            };
        });
}

function hasAPIKey() {
    const env = readEnv();
    if (migrateLegacyKey(env)) writeEnv(env);
    return Boolean((env.ACTIVE_LLM_API_KEY || "").trim());
}


function detectFileType(json) {

    // Business rules are an array of rule objects
    if (
        Array.isArray(json) &&
        (
            json.length === 0 || // Empty [] is still a business rules file
            json[0].rule ||
            json[0].source_directory ||
            json[0].source_file_paths ||
            json[0].explanation
        )
    ) {
        return "business_rules";
    }

    // Summary files
    if (
        json.summary ||
        json.functions ||
        json.dependencies ||
        json.types
    ) {
        return "summary";
    }

    // Source files
    if (
        json.directory_name ||
        json.directory_path ||
        json.purpose ||
        json.responsibilities
    ) {
        return "source";
    }

    // Integration test workflow files
    if (
        Array.isArray(json) &&
        json.length > 0 &&
        json.every(item => (
            item &&
            typeof item === "object" &&
            (item.workflow_name || item.integration_test)
        ))
    ) {
        return "integration_tests";
    }

    return "unknown";
}

function formatIntegrationTestCode(code) {
    if (!code) return "";

    let formatted = String(code)
        .replace(/\\n/g, "\n")
        .trim();

    // Some entries arrive as a single line; add line breaks for readability.
    if (!formatted.includes("\n")) {
        formatted = formatted
            .replace(/\s*\{\s*/g, " {\n")
            .replace(/;\s+/g, ";\n")
            .replace(/\s*\}\s*/g, "\n}\n")
            .trim();
    }

    return formatted;
}

function formatIntegrationTests(json) {
    if (!Array.isArray(json) || json.length === 0) {
        return [];
    }

    return json.map((workflow, index) => ({
        index: index + 1,
        workflow_name: workflow.workflow_name || `Workflow ${index + 1}`,
        workflow_description: workflow.workflow_description || "",
        rule_ids: Array.isArray(workflow.rule_ids) ? workflow.rule_ids : [],
        imports: Array.isArray(workflow.imports) ? workflow.imports : [],
        integration_test: formatIntegrationTestCode(workflow.integration_test || "")
    }));
}
// ----------------------------------------------------
// SUMMARY FORMATTING
// ----------------------------------------------------

function formatSummary(json) {

    const lines = [];

    // File
    lines.push(`# ${path.basename(json.path)}`);
    lines.push("");
    lines.push(`File: ${json.path}`);
    lines.push("");

    // Summary
    if (json.summary) {
        lines.push("=== Summary ===");
        lines.push(json.summary);
        lines.push("");
    }

    // Dependencies
    if (json.dependencies?.length) {
        lines.push("=== Dependencies ===");

        json.dependencies.forEach(dep => {
            lines.push(`• ${dep}`);
        });

        lines.push("");
    }

    // Standalone functions
    if (json.functions?.length) {
        lines.push("=== Functions ===");

        json.functions.forEach(func => {

            lines.push(`${func.name}()`);

            if (func.description)
                lines.push(`   ${func.description}`);

            if (func.return_type)
                lines.push(`   Returns: ${func.return_type}`);

            lines.push("");

        });
    }

    // Classes / Types
    if (json.types?.length) {

        lines.push("=== Classes ===");

        json.types.forEach(type => {

            lines.push("");
            lines.push(`${type.kind.toUpperCase()}: ${type.name}`);

            if (type.description)
                lines.push(type.description);

            // Properties
            if (type.properties?.length) {

                lines.push("");
                lines.push("Properties:");

                type.properties.forEach(prop => {

                    lines.push(
                        `  • ${prop.name} : ${prop.type}`
                    );

                });
            }

            // Methods
            if (type.methods?.length) {

                lines.push("");
                lines.push("Methods:");

                type.methods.forEach(method => {

                    lines.push(
                        `  • ${method.name}()`
                    );

                    if (method.description)
                        lines.push(
                            `      ${method.description}`
                        );

                });
            }

            lines.push("");

        });
    }

    // Business Rules
    if (json.business_rules?.length) {

        lines.push("=== Business Rules ===");

        json.business_rules.forEach((rule, i) => {

            lines.push(`${i + 1}. ${rule.rule}`);

        });

        lines.push("");

    }

    return lines.join("\n");

}

function formatSource(json) {

    const lines = [];


    lines.push(`# ${json.directory_name || "Unknown Directory"}`);
    lines.push("");


    if (json.directory_name) {

        lines.push("=== Directory Name ===");
        lines.push(json.directory_name);
        lines.push("");

    }


    if (json.directory_path) {

        lines.push("=== Directory Path ===");
        lines.push(json.directory_path);
        lines.push("");

    }


    if (json.purpose) {

        lines.push("=== Purpose ===");
        lines.push(json.purpose);
        lines.push("");

    }


    if (json.responsibilities?.length) {

        lines.push("=== Responsibilities ===");

        json.responsibilities.forEach(item => {
            lines.push(`• ${item}`);
        });

        lines.push("");

    }


    return lines.join("\n");

}

function canonicalizeRelativeOutputPath(rawPath) {
    if (!rawPath || path.isAbsolute(rawPath)) {
        return rawPath;
    }

    const normalized = String(rawPath).replace(/\\/g, "/");
    const outputRoots = [
        "agent/file_summary_agent_output/",
        "agent/directory_agent_output/",
        "agent/UT_agent_output/",
        "agent/BR_agent_output/"
    ];

    for (const root of outputRoots) {
        if (!normalized.toLowerCase().startsWith(root.toLowerCase())) {
            continue;
        }

        const remainder = normalized.slice(root.length);

        // Handle malformed paths like:
        // agent/file_summary_agent_output/C:/.../Codebase
        // agent/BR_agent_output/C:/.../Codebase/validated_rules.json
        if (/^[a-zA-Z]:\//.test(remainder)) {
            const segments = remainder.split("/").filter(Boolean);

            if (segments.length === 0) {
                return normalized;
            }

            const lastSegment = segments[segments.length - 1];
            const hasExtension = /\.[^./\\]+$/.test(lastSegment);

            if (hasExtension && segments.length >= 2) {
                const codebaseName = segments[segments.length - 2];
                return `${root}${codebaseName}/${lastSegment}`;
            }

            return `${root}${lastSegment}`;
        }
    }

    return normalized;
}

function formatBusinessRules(json) {

    // Handle empty JSON or []
    if (!Array.isArray(json) || json.length === 0) {
        return "No business rules were found.";
    }

    const lines = [];

    json.forEach((ruleObj, index) => {

        lines.push(`=== Business Rule ${index + 1} ===`);
        lines.push(ruleObj.rule || "No rule provided.");
        lines.push("");

        if (ruleObj.source_directory) {
            lines.push("Source Directory:");
            lines.push(ruleObj.source_directory);
            lines.push("");
        }

        if (ruleObj.source_file_paths?.length) {
            lines.push("Source File(s):");

            ruleObj.source_file_paths.forEach(file => {
                lines.push(`• ${file}`);
            });

            lines.push("");
        }

        if (ruleObj.explanation?.reasoning) {
            lines.push("Reasoning:");
            lines.push(ruleObj.explanation.reasoning);
            lines.push("");
        }

        if (ruleObj.explanation?.evidence) {

            lines.push("Evidence:");

            Object.entries(ruleObj.explanation.evidence).forEach(([file, snippets]) => {

                lines.push(`• ${file}`);

                snippets.forEach(snippet => {
                    lines.push(`    - ${snippet}`);
                });

                lines.push("");
            });
        }

        lines.push("");
    });

    return lines.join("\n");

}

// ----------------------------------------------------
// ICP HANDLERS
// ----------------------------------------------------

ipcMain.handle("list-api-keys", () => {
    const env = readEnv();
    if (migrateLegacyKey(env)) writeEnv(env);
    return { success: true, keys: listKeys(env) };
});

ipcMain.handle("save-api-key", (event, { provider, label, apiKey, model, makeActive = true }) => {
    provider = String(provider || "").toLowerCase();
    if (!PROVIDERS.includes(provider)) return { success: false, error: "Unknown provider." };

    apiKey = String(apiKey || "").trim();
    if (!apiKey) return { success: false, error: "API key is empty." };

    label = String(label || "default").replace(/[^A-Za-z0-9-]/g, "-").slice(0, 32) || "default";
    const id = `${provider.toUpperCase()}__${label}`;

    const env = readEnv();
    migrateLegacyKey(env);

    env[`APIKEY__${id}`] = apiKey;
    env[`APIMODEL__${id}`] = String(model || "").trim();

    if (makeActive || !env.ACTIVE_LLM_KEY || env.ACTIVE_LLM_KEY === id) applyActive(env, id);

    try {
        writeEnv(env);
    } catch (error) {
        return { success: false, error: `Could not write .env: ${error.message}` };
    }

    return { success: true, id };
});

ipcMain.handle("select-api-key", (event, { id, model }) => {
    const env = readEnv();
    if (!env[`APIKEY__${id}`]) return { success: false, error: "Key not found." };

    if (typeof model === "string") env[`APIMODEL__${id}`] = model.trim();
    applyActive(env, id);

    try {
        writeEnv(env);
    } catch (error) {
        return { success: false, error: `Could not write .env: ${error.message}` };
    }

    return { success: true };
});

ipcMain.handle("delete-api-key", (event, { id }) => {
    const env = readEnv();
    delete env[`APIKEY__${id}`];
    delete env[`APIMODEL__${id}`];

    if (env.ACTIVE_LLM_KEY === id) {
        const next = listKeys(env)[0];
        applyActive(env, next ? next.id : null);
    }

    try {
        writeEnv(env);
    } catch (error) {
        return { success: false, error: `Could not write .env: ${error.message}` };
    }

    return { success: true };
});

ipcMain.handle(
    "has-api-key",
    () => {
        return hasAPIKey();
    }
);

ipcMain.handle(
    "get-error-log",
    () => {
        const errorLogPath = path.join(backendOutputDir, "error_log.json");

        try {
            const errors = JSON.parse(fs.readFileSync(errorLogPath, "utf8"));
            return {
                success: true,
                errors: Array.isArray(errors) ? errors : []
            };
        } catch (error) {
            return {
                success: false,
                error: `Could not read error log: ${error.message}`
            };
        }
    }
);

ipcMain.handle(
    "record-error-log",
    (event, error) => {
        const errorLogPath = path.join(backendOutputDir, "error_log.json");
        let errors = [];

        try {
            const existing = JSON.parse(fs.readFileSync(errorLogPath, "utf8"));
            errors = Array.isArray(existing) ? existing : [];
        } catch (readError) {
            // Start a new log when no readable log exists yet.
        }

        errors.push({
            time: new Date().toISOString().slice(0, 19),
            ...(error?.request_id ? { request_id: error.request_id } : {}),
            command: error?.command || "unknown",
            code: error?.code || null,
            message: error?.message || "Unknown error"
        });

        try {
            const temporaryPath = `${errorLogPath}.tmp`;
            fs.writeFileSync(temporaryPath, JSON.stringify(errors, null, 2), "utf8");
            fs.renameSync(temporaryPath, errorLogPath);
            return { success: true };
        } catch (writeError) {
            return { success: false, error: writeError.message };
        }
    }
);

ipcMain.handle(
    "exit-app",
    () => {
        if (pythonProcess) {
            pythonProcess.kill();
        }

        if (mainWindow && !mainWindow.isDestroyed()) {
            mainWindow.close();
        }

        app.quit();
        return true;
    }
);


// ----------------------------------------------------
// SEND COMMAND TO PYTHON BACKEND
// ----------------------------------------------------

function publishBackendResponse(response) {
    if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send("backend-response", response);
    }
}

function failPendingBackendRequests(error, extra = {}) {
    for (const failure of backendRequests.failAll(error, extra)) {
        publishBackendResponse(failure.response);
    }
}

function failOldestBackendRequest(error) {
    const request = backendRequests.firstPending();
    if (!request) return;

    const failure = backendRequests.fail(request.requestId, error);
    if (failure) publishBackendResponse(failure.response);
}

function handleBackendLine(line) {
    let response;
    try {
        response = JSON.parse(line);
    } catch (error) {
        console.error("Backend response parse error:", error.message);
        failOldestBackendRequest("Malformed response from Python backend.");
        return;
    }

    if (!response || typeof response !== "object" || Array.isArray(response)) {
        failOldestBackendRequest("Malformed response from Python backend.");
        return;
    }

    if (typeof response.request_id !== "string" || !response.request_id) {
        failOldestBackendRequest("Backend response did not include a request ID.");
        return;
    }

    const result = backendRequests.handle(response);
    if (!result.matched) return;
    publishBackendResponse(result.response);
}

ipcMain.handle(
    "execute-command",
    async (event, request) => {

        const requestId = typeof request?.request_id === "string" && request.request_id
            ? request.request_id
            : randomUUID();

        if (!pythonProcess || !pythonProcess.stdin?.writable) {

            return {
                request_id: requestId,
                success: false,
                error: "Python backend is not running"
            };

        }

        try {
            backendRequests.register(requestId, request.command);
        } catch (error) {
            return { request_id: requestId, success: false, error: error.message };
        }

        const payload =
            JSON.stringify({

                type:"command",
                request_id: requestId,

                command:
                    request.command,

                args:
                    request.args || {}

            });

        try {
            pythonProcess.stdin.write(payload + "\n", (error) => {
                if (!error) return;
                const failure = backendRequests.fail(requestId, error.message);
                if (failure) publishBackendResponse(failure.response);
            });
        } catch (error) {
            const failure = backendRequests.fail(requestId, error.message);
            if (failure) publishBackendResponse(failure.response);
            return { request_id: requestId, accepted: false, success: false, error: error.message };
        }


        return {
            request_id: requestId,
            accepted: true
        };

    }
);

ipcMain.handle(
    "cancel-command",
    async (event, requestId) => {
        if (!pythonProcess) {
            return { request_id: requestId, success: false, error: "No backend command is running" };
        }

        if (requestId && !backendRequests.get(requestId)) {
            return { request_id: requestId, success: false, error: "The command is no longer running" };
        }

        const processToCancel = pythonProcess;
        restartBackendAfterCancel = true;

        return new Promise((resolve) => {
            processToCancel.once("close", () => resolve({ success: true, request_id: requestId }));
            processToCancel.kill();
        });
    }
);

// ----------------------------------------------------
// SEND PREVIEW COMMANDS
// ----------------------------------------------------

ipcMain.handle(
    "preview-command",
    async (event, request) => {

        const action = request?.action;
        const args = request?.args || {};

        if (action === "files") {
            const rawPath = canonicalizeRelativeOutputPath(args.path || "agent");
            const targetPath = path.isAbsolute(rawPath)
                ? rawPath
                : path.join(backendOutputDir, rawPath);

            if (!fs.existsSync(targetPath)) {
                return {
                    success: false,
                    error: `Path not found: ${targetPath}`
                };
            }

            const stats = fs.statSync(targetPath);
            if (!stats.isDirectory()) {
                return {
                    success: false,
                    error: `Path is not a directory: ${targetPath}`
                };
            }

            const collectEntries = (currentPath, recursivePdfs = false, collected = []) => {
                const dirents = fs.readdirSync(currentPath, {
                    withFileTypes: true
                });

                dirents.forEach((dirent) => {
                    const entryPath = path.join(currentPath, dirent.name);

                    if (dirent.isDirectory()) {
                        if (recursivePdfs) {
                            collectEntries(entryPath, true, collected);
                        } else {
                            collected.push({
                                name: dirent.name,
                                path: entryPath,
                                isDirectory: true
                            });
                        }
                        return;
                    }

                    if (recursivePdfs && path.extname(dirent.name).toLowerCase() === ".pdf") {
                        collected.push({
                            name: dirent.name,
                            path: entryPath,
                            isDirectory: false
                        });
                    } else if (!recursivePdfs) {
                        collected.push({
                            name: dirent.name,
                            path: entryPath,
                            isDirectory: false
                        });
                    }
                });

                return collected;
            };

            const recursivePdfs = Boolean(args.recursivePdfs);
            const entries = collectEntries(targetPath, recursivePdfs)
                .sort((a, b) => {
                    if (a.isDirectory !== b.isDirectory) {
                        return a.isDirectory ? -1 : 1;
                    }
                    return a.name.localeCompare(b.name, undefined, {
                        sensitivity: 'base'
                    });
                });

            const result = {
                success: true,
                type: "files",
                path: targetPath,
                files: entries
            };

            if (mainWindow && !mainWindow.isDestroyed()) {
                mainWindow.webContents.send("backend-response", result);
            }

            return result;
        }


        if (action === "open_file") {
            const rawPath = canonicalizeRelativeOutputPath(args.path);
            if (!rawPath) {
                const result = {
                    success: false,
                    error: "No file path provided"
                };

                if (mainWindow && !mainWindow.isDestroyed()) {
                    mainWindow.webContents.send("backend-response", result);
                }

                return result;
            }

            const targetPath = path.isAbsolute(rawPath)
                ? rawPath
                : path.join(backendOutputDir, rawPath);

            if (!fs.existsSync(targetPath)) {
                const result = {
                    success: false,
                    error: `File not found: ${targetPath}`
                };

                if (mainWindow && !mainWindow.isDestroyed()) {
                    mainWindow.webContents.send("backend-response", result);
                }

                return result;
            }

            const stats = fs.statSync(targetPath);
            if (!stats.isFile()) {
                const result = {
                    success: false,
                    error: `Path is not a file: ${targetPath}`
                };

                if (mainWindow && !mainWindow.isDestroyed()) {
                    mainWindow.webContents.send("backend-response", result);
                }

                return result;
            }

            const extension = path.extname(targetPath).toLowerCase();

            if (extension === ".pdf") {
                const result = {
                    success: true,
                    type: "file-preview",
                    path: targetPath,
                    preview: {
                        type: "pdf",
                        content: targetPath
                    }
                };

                if (mainWindow && !mainWindow.isDestroyed()) {
                    mainWindow.webContents.send("backend-response", result);
                }

                return result;
            }

            const content = fs.readFileSync(targetPath, "utf8");

            let preview;

            try {

                const json = JSON.parse(content);
                const type = detectFileType(json);

                if (type === "summary") {

                    preview = {
                        type: "summary",
                        content: formatSummary(json)
                    };

                } else if (type === "source") {

                    preview = {
                        type: "source",
                        content: formatSource(json)
                    };

                } else if (type === "business_rules") {

                    preview = {
                        type: "business_rules",
                        content: formatBusinessRules(json)
                    };

                } else if (type === "integration_tests") {

                    preview = {
                        type: "integration_tests",
                        content: formatIntegrationTests(json)
                    };

                }else {

                    preview = {
                        type: "unknown",
                        content: content
                    };

                }

            } catch {

                preview = {
                    type: "text",
                    content
                };

            }


            const result = {
                success:true,
                type:"file-preview",
                path:targetPath,
                preview
            };

            if (mainWindow && !mainWindow.isDestroyed()) {
                mainWindow.webContents.send("backend-response", result);
            }

            return result;
        }

        if (!pythonProcess) {
            return {
                success:false,
                error:"Python backend is not running"
            };
        }


        return {
            success:true
        };

    }
);

ipcMain.handle(
    "get-validated-rules",
    async (event, request) => {

        try {
            const codebasePath = request?.codebasePath;

            if (!codebasePath) {
                return {
                    success:false,
                    error:"No codebase path provided"
                };
            }

            const codebaseName = path.basename(codebasePath);
            // Where the backend saved them: not __dirname in the installed app.
            const rulesPath = path.join(backendOutputDir, "agent", "BR_agent_output", codebaseName, "validated_rules.json");

            let rulesFile = null;
            if (fs.existsSync(rulesPath)) {
                rulesFile = rulesPath;
            }

            if (!rulesFile) {
                return {
                    success:false,
                    error:"No validated business rules file found for this codebase"
                };
            }

            const rawContent = fs.readFileSync(rulesFile, "utf8");
            const rawData = JSON.parse(rawContent);
            const rules = [];

            if (Array.isArray(rawData)) {
                rawData.forEach((rule, index) => {
                    const text = rule?.rule || "";
                    if (text) {
                        rules.push({
                            id: String(rule?.id),
                            text,
                            source: rule?.source_directory || "Generated rule"
                        });
                    }
                });
            } else if (rawData && typeof rawData === "object") {
                Object.entries(rawData).forEach(([sourcePath, entries]) => {
                    if (!Array.isArray(entries)) return;
                    entries.forEach((rule, index) => {
                        const text = rule?.rule || "";
                        if (text) {
                            rules.push({
                                id: String(rule?.id),
                                text,
                                source: sourcePath
                            });
                        }
                    });
                });
            }

            return {
                success:true,
                rules
            };

        } catch (error) {
            return {
                success:false,
                error:error.message
            };
        }
    }
);

ipcMain.handle("get-business-rules", async (_event, request) => {
    const codebaseName = path.basename(String(request?.codebasePath || ""));
    if (!codebaseName || codebaseName === "." || codebaseName === ".." || codebaseName === path.sep) {
        return { success: false, error: "No codebase selected" };
    }

    const folder = path.join(backendOutputDir, "agent", "BR_agent_output", codebaseName);
    const readRules = (filename) => {
        const file = path.join(folder, filename);
        if (!fs.existsSync(file)) {
            return { exists: false, loaded: false, count: null, rules: [] };
        }
        try {
            const entries = JSON.parse(fs.readFileSync(file, "utf8"));
            if (!Array.isArray(entries)) {
                return { exists: true, loaded: false, count: null, rules: [], error: "Expected a JSON array." };
            }
            return {
                exists: true,
                loaded: true,
                count: entries.length,
                rules: entries.map((entry) => ({
                    text: typeof entry === "string"
                        ? entry
                        : (typeof entry?.rule === "string" && entry.rule) || "No rule text recorded",
                    source: typeof entry?.source_directory === "string"
                        ? entry.source_directory.trim()
                        : ""
                }))
            };
        } catch (error) {
            return { exists: true, loaded: false, count: null, rules: [], error: `Could not read ${filename}: ${error.message}` };
        }
    };

    return {
        success: true,
        codebase: codebaseName,
        validated: readRules("validated_rules.json"),
        discarded: readRules("discarded_rules.json")
    };
});




// ----------------------------------------------------
// CREATE WINDOW
// ----------------------------------------------------

function createWindow() {


    mainWindow = new BrowserWindow({

        width:1200,
        height:800,
        icon: path.join(__dirname, "assets", "checkpoint.ico"),
        autoHideMenuBar: true,

        webPreferences: {

            preload:
                path.join(
                    __dirname,
                    "preload.js"
                ),

            nodeIntegration:false,

            contextIsolation:true

        }

    });

    // This just gets rid of the top bar
    Menu.setApplicationMenu(null);

    // This is for using f12 to see the dev tools since you can't toggle menu with alt
    mainWindow.webContents.on("before-input-event", (event, input) => {
        if (input.key === "F12") {
            mainWindow.webContents.toggleDevTools();
        }
    });

    ensureEnvFile();
    startPythonBackend();

    // Clear the previous session before the renderer can request the error log.
    mainWindow.loadFile(
        "index.html"
    );

}

// ----------------------------------------------------
// Open file directory
// ----------------------------------------------------

ipcMain.handle("select-codebase", async () => {

    const result = await dialog.showOpenDialog(mainWindow, {
        properties: ["openDirectory"]
    });

    if (result.canceled) {
        return null;
    }

    return result.filePaths[0];
});

function guiTestReportDirectory() {
    return path.join(app.getPath("userData"), "gui-testing-reports");
}

function persistGuiTestReport(report = activeGuiTestReport) {
    if (!report) return null;
    try {
        return saveGuiTestReport(guiTestReportDirectory(), report);
    } catch (error) {
        console.error("Could not save GUI testing report:", error);
        if (mainWindow && !mainWindow.isDestroyed()) {
            mainWindow.webContents.send("gui-test-event", {
                type: "report_storage_error",
                message: `Could not save GUI testing report: ${error.message}`
            });
        }
        return null;
    }
}

function beginGuiTestReport({ appName, appDirectory, framework, testFiles }) {
    activeGuiTestReport = createGuiTestReport({
        appName: appName || (appDirectory ? path.basename(appDirectory) : "Application not specified"),
        appDirectory: appDirectory || null,
        framework: framework || "Not specified",
        testFiles: testFiles || []
    });
    if (!persistGuiTestReport()) {
        activeGuiTestReport = null;
        throw new Error("Could not create a persistent GUI testing report.");
    }
    return activeGuiTestReport;
}

function finishActiveGuiTestReport(status, message = null) {
    if (!activeGuiTestReport || activeGuiTestReport.status !== "running") return;
    finalizeGuiTestReport(activeGuiTestReport, status, message);
    persistGuiTestReport();
}

function recordGuiTestReportEvent(event) {
    if (!isGuiTestReportEvent(event)) return;
    if (!activeGuiTestReport || activeGuiTestReport.status !== "running") return;
    applyGuiTestEvent(activeGuiTestReport, event);
    if (event.type === "run_complete") {
        const status = event.status === "passed"
            ? "passed"
            : ["interrupted", "timedout"].includes(event.status)
                ? "interrupted"
                : "failed";
        finishActiveGuiTestReport(status);
    } else if (event.type === "runner_container_exit") {
        finishActiveGuiTestReport(
            "interrupted",
            event.message || "The Playwright container exited without a completed test summary."
        );
    } else if (event.type === "runner_error") {
        finishActiveGuiTestReport("interrupted", event.message);
    } else {
        persistGuiTestReport();
    }
}

ipcMain.on("gui-test-report-event", (_event, payload) => {
    if (isGuiTestReportEvent(payload)) recordGuiTestReportEvent(payload);
});

function getGuiTestRunner() {
    if (!guiTestRunner) {
        guiTestRunner = new GuiTestRunner({
            appRoot: __dirname,
            resourcesPath: process.resourcesPath,
            isPackaged: app.isPackaged,
            onEvent: payload => {
                recordGuiTestReportEvent(payload);
                if (mainWindow && !mainWindow.isDestroyed()) {
                    mainWindow.webContents.send("gui-test-event", payload);
                }
            }
        });
    }
    return guiTestRunner;
}

function getLocalGuiApp() {
    if (!localGuiApp) {
        localGuiApp = new LocalGuiApp(payload => {
            if (mainWindow && !mainWindow.isDestroyed()) {
                mainWindow.webContents.send("gui-test-event", payload);
            }
        });
    }
    return localGuiApp;
}

async function stopLocalGuiApp() {
    if (localGuiApp?.active) await localGuiApp.stop();
}

ipcMain.handle("start-local-gui-app", async (event, request = {}) => {
    const confirmation = await dialog.showMessageBox(mainWindow, {
        type: "warning",
        buttons: ["Run project", "Cancel"],
        defaultId: 1,
        cancelId: 1,
        title: "Run local app for GUI testing?",
        message: "This will execute the selected project's npm dev/start script on your PC.",
        detail: "The script runs with your Windows user permissions and the app listens on all network interfaces so Docker can connect. Continue only if you trust the selected folder."
    });
    if (confirmation.response !== 0) {
        return { success: false, cancelled: true };
    }
    try {
        const result = await getLocalGuiApp().start(request.appDirectory);
        return { success: true, ...result };
    } catch (error) {
        const report = beginGuiTestReport({
            appDirectory: request.appDirectory,
            testFiles: []
        });
        applyGuiTestEvent(report, {
            type: "runner_error",
            message: `Application startup failed: ${error.message}`
        });
        finishActiveGuiTestReport("startup_failed");
        return { success: false, error: error.message };
    }
});

ipcMain.handle("stop-local-gui-app", async () => {
    try {
        await stopLocalGuiApp();
        return { success: true };
    } catch (error) {
        return { success: false, error: error.message };
    }
});

ipcMain.handle("select-gui-test-files", async () => {
    let selection;
    try {
        selection = await dialog.showOpenDialog(mainWindow, {
            title: "Upload Playwright GUI test files",
            properties: ["openFile", "multiSelections"],
            filters: [
                { name: "JavaScript and TypeScript", extensions: ["js", "ts"] },
                { name: "All files", extensions: ["*"] }
            ]
        });
    } catch (error) {
        return { success: false, error: `Could not open the test file picker: ${error.message}` };
    }

    if (selection.canceled || !selection.filePaths.length) {
        return { success: false, cancelled: true };
    }

    try {
        return { success: true, tests: getGuiTestRunner().addFiles(selection.filePaths) };
    } catch (error) {
        return { success: false, error: error.message };
    }
});

ipcMain.handle("start-gui-tests", async (event, request = {}) => {
    const runner = getGuiTestRunner();
    const testFiles = (request.testIds || [])
        .map(id => runner.uploadedFiles.get(id)?.sourcePath)
        .filter(Boolean);
    const report = beginGuiTestReport({
        appName: request.appName,
        appDirectory: request.appDirectory,
        framework: request.framework,
        testFiles
    });
    try {
        const run = await runner.start(request.testIds, request.allowedHosts);
        return { success: true, ...run };
    } catch (error) {
        applyGuiTestEvent(report, { type: "runner_error", message: error.message });
        finishActiveGuiTestReport("startup_failed");
        return { success: false, error: error.message };
    }
});

ipcMain.handle("start-generated-gui-tests", async (event, request = {}) => {
    try {
        const generatedFiles = request.testFiles;
        if (!Array.isArray(generatedFiles) || generatedFiles.length === 0 || generatedFiles.length > 8) {
            throw new Error("The generated test selection is empty or invalid.");
        }

        const generatedRoot = path.resolve(backendOutputDir, "agent", "GUI_test_agent_output");
        const resolvedFiles = generatedFiles.map(filePath => path.resolve(String(filePath)));
        for (const filePath of resolvedFiles) {
            const relative = path.relative(generatedRoot, filePath);
            if (
                !relative ||
                relative === ".." ||
                relative.startsWith(`..${path.sep}`) ||
                path.isAbsolute(relative) ||
                !/\.spec\.js$/i.test(filePath)
            ) {
                throw new Error("Only generated Playwright spec files can be started by this action.");
            }
        }

        const runner = getGuiTestRunner();
        if (request.afterRunComplete === true) await runner.waitForIdle();
        runner.addFiles(resolvedFiles);
        const requestedPaths = new Set(resolvedFiles.map(filePath => path.resolve(filePath).toLowerCase()));
        const selectedIds = Array.from(runner.uploadedFiles.values())
            .filter(file => requestedPaths.has(path.resolve(file.sourcePath).toLowerCase()))
            .map(file => file.id);
        if (selectedIds.length !== resolvedFiles.length) {
            throw new Error("Could not prepare all generated test files for execution.");
        }
        if (!localGuiApp?.active) {
            throw new Error("The local app is no longer running. Start generation again to relaunch it.");
        }
        const appRun = localGuiApp.active;
        beginGuiTestReport({
            appDirectory: appRun.appDirectory,
            framework: appRun.framework,
            testFiles: resolvedFiles
        });
        const appPort = localGuiApp.active.port;

        const run = await runner.start(
            selectedIds,
            Array.isArray(request.allowedHosts) ? request.allowedHosts : [],
            {
                mode: "host",
                port: appPort
            }
        );
        return { success: true, tests: runner.listFiles(), ...run };
    } catch (error) {
        if (activeGuiTestReport?.status === "running") {
            applyGuiTestEvent(activeGuiTestReport, {
                type: "runner_error",
                message: error.message
            });
            finishActiveGuiTestReport("startup_failed");
        }
        await stopLocalGuiApp().catch(stopError =>
            console.error("Failed to stop local GUI app after runner startup error:", stopError)
        );
        return { success: false, error: error.message };
    }
});

ipcMain.handle("repair-generated-gui-test", async (event, request = {}) => {
    try {
        if (!localGuiApp?.active) {
            throw new Error("The local app is no longer running; selector repair cannot be verified.");
        }
        if (localGuiApp.active.repairAttempted) {
            throw new Error("The single automatic selector-repair attempt has already been used for this run.");
        }
        const generatedRoot = path.resolve(backendOutputDir, "agent", "GUI_test_agent_output");
        const testFile = path.resolve(String(request.testFile || ""));
        const relative = path.relative(generatedRoot, testFile);
        if (
            !relative ||
            relative === ".." ||
            relative.startsWith(`..${path.sep}`) ||
            path.isAbsolute(relative) ||
            !testFile.endsWith(".spec.js")
        ) {
            throw new Error("Only generated Playwright spec files can be repaired.");
        }
        if (!request.failure || request.failure.category !== "locator_timeout") {
            throw new Error("Only a failed locator timeout is eligible for selector repair.");
        }
        if (!fs.existsSync(testFile)) {
            throw new Error("The generated test file no longer exists.");
        }
        localGuiApp.active.repairAttempted = true;
        if (!pythonProcess) throw new Error("Python backend is not running.");
        pythonProcess.stdin.write(JSON.stringify({
            type: "command",
            command: "repair_gui_test_case",
            args: {
                codebase: localGuiApp.active.appDirectory,
                test_file: testFile,
                failure: request.failure
            }
        }) + "\n");
        return { success: true };
    } catch (error) {
        return { success: false, error: error.message };
    }
});

ipcMain.handle("stop-gui-tests", async () => {
    try {
        const stopped = guiTestRunner ? await guiTestRunner.stop() : false;
        if (stopped) finishActiveGuiTestReport("interrupted", "The GUI test run was stopped by the user.");
        await stopLocalGuiApp();
        return stopped
            ? { success: true }
            : { success: true };
    } catch (error) {
        return { success: false, error: error.message };
    }
});

ipcMain.handle("list-gui-testing-reports", async () => {
    try {
        return {
            success: true,
            reports: listGuiTestReports(
                guiTestReportDirectory(),
                activeGuiTestReport?.id || null
            )
        };
    } catch (error) {
        return { success: false, error: error.message };
    }
});

ipcMain.handle("get-gui-testing-report", async (event, reportId) => {
    try {
        return {
            success: true,
            report: getGuiTestReport(
                guiTestReportDirectory(),
                reportId,
                activeGuiTestReport?.id || null
            )
        };
    } catch (error) {
        return { success: false, error: error.message };
    }
});

ipcMain.handle("export-gui-testing-report-pdf", async (event, reportId) => {
    let printWindow = null;
    try {
        const report = getGuiTestReport(
            guiTestReportDirectory(),
            reportId,
            activeGuiTestReport?.id || null
        );
        const suggestedName = `${String(report.appName || "application").replace(/[^A-Za-z0-9._-]+/g, "-")}-gui-report.pdf`;
        const selection = await dialog.showSaveDialog(mainWindow, {
            title: "Export GUI testing report",
            defaultPath: suggestedName,
            filters: [{ name: "PDF document", extensions: ["pdf"] }]
        });
        if (selection.canceled || !selection.filePath) return { success: false, cancelled: true };

        printWindow = new BrowserWindow({
            show: false,
            webPreferences: {
                sandbox: true,
                contextIsolation: true,
                nodeIntegration: false
            }
        });
        await printWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(buildGuiReportHtml(report))}`);
        const pdf = await printWindow.webContents.printToPDF({
            printBackground: true,
            pageSize: "A4",
            margins: { top: 0.5, bottom: 0.5, left: 0.5, right: 0.5 }
        });
        fs.writeFileSync(selection.filePath, pdf);
        return { success: true, filePath: selection.filePath };
    } catch (error) {
        return { success: false, error: `Could not export the GUI report: ${error.message}` };
    } finally {
        if (printWindow && !printWindow.isDestroyed()) printWindow.close();
    }
});

// ----------------------------------------------------
// RUN REPORT (US-049)
// ----------------------------------------------------
// The backend writes one self-contained HTML report after a full run, to
// run_reports/<codebase>/run_report.html. It opens in its own window with no
// access to Node or the backend. Only a run_report.html inside the backend's
// run_reports folder is opened, so the renderer cannot use this to open any
// other file.

const REPORT_NAME = "run_report.html";
let reportWindow = null;

function reportsDir() {
    return path.resolve(backendOutputDir, "run_reports");
}

function isRunReport(file) {
    const fold = (p) => process.platform === "win32" ? p.toLowerCase() : p;
    return path.basename(file) === REPORT_NAME &&
        fold(file).startsWith(fold(reportsDir() + path.sep)) &&
        fs.existsSync(file);
}

async function showReportWindow(file) {

    if (!reportWindow || reportWindow.isDestroyed()) {

        reportWindow = new BrowserWindow({
            width: 1300,
            height: 900,
            icon: path.join(__dirname, "assets", "checkpoint.ico"),
            autoHideMenuBar: true,
            webPreferences: {
                nodeIntegration: false,
                contextIsolation: true,
                sandbox: true
            }
        });

        // Links in the report only move within the page; keep everything else out.
        reportWindow.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
        reportWindow.webContents.on("will-navigate", (navigation) => navigation.preventDefault());

        reportWindow.webContents.on("before-input-event", (e, input) => {
            if (input.key === "F12") {
                reportWindow.webContents.toggleDevTools();
            }
        });

        reportWindow.on("closed", () => {
            reportWindow = null;
            if (mainWindow && !mainWindow.isDestroyed()) {
                if (mainWindow.isMinimized()) mainWindow.restore();
                mainWindow.show();
                mainWindow.focus();
            }
        });
    }

    await reportWindow.loadFile(file);
    reportWindow.show();
    reportWindow.focus();
}

// From the Complete screen, right after a full run.
ipcMain.handle("open-report", async (event, reportPath) => {

    const file = path.resolve(String(reportPath || ""));

    if (!isRunReport(file)) {
        return {
            success: false,
            error: "The run report was not found. Run the full pipeline to make one."
        };
    }

    await showReportWindow(file);
    return { success: true };
});

// View Insights embeds only the saved report for its explicitly selected codebase.
// The renderer places this HTML in a sandboxed iframe; it never enters the app DOM.
ipcMain.handle("get-run-report-html", async (_event, codebaseName) => {

    const folder = path.basename(String(codebaseName || "").trim());
    if (!folder || folder === "." || folder === "..") {
        return { success: false, error: "Select a codebase to view its run report." };
    }

    const file = path.join(reportsDir(), folder, REPORT_NAME);
    if (!isRunReport(file)) {
        return {
            success: false,
            error: `No run report is available for ${folder}. Run the full pipeline on this codebase to create one.`
        };
    }

    try {
        return {
            success: true,
            codebase: folder,
            html: fs.readFileSync(file, "utf8")
        };
    } catch (error) {
        console.error("Could not read the selected codebase run report:", error);
        return { success: false, error: `Could not read the run report for ${folder}.` };
    }
});

// From View Insights, any time later: the selected codebase's report, or
// when none is selected, the most recently written report.
ipcMain.handle("open-latest-report", async (event, codebaseName) => {

    let file = null;

    if (codebaseName) {
        file = path.join(reportsDir(), path.basename(String(codebaseName)), REPORT_NAME);

        if (!isRunReport(file)) {
            return {
                success: false,
                error: `No run report for ${codebaseName} yet. Run the full pipeline on it to make one.`
            };
        }
    } else {
        let folders = [];
        try {
            folders = fs.readdirSync(reportsDir());
        } catch (error) {
            // No report has been written yet.
        }

        const reports = folders
            .map((folder) => path.join(reportsDir(), folder, REPORT_NAME))
            .filter(isRunReport)
            .sort((a, b) => fs.statSync(b).mtimeMs - fs.statSync(a).mtimeMs);

        if (!reports.length) {
            return {
                success: false,
                error: "No run report yet. Run the full pipeline to make one."
            };
        }

        file = reports[0];
    }

    await showReportWindow(file);
    return { success: true, codebase: path.basename(path.dirname(file)) };
});

// ----------------------------------------------------
// START PYTHON BACKEND
// ----------------------------------------------------

function startPythonBackend(preserveErrors = false) {

    const isWindows = process.platform === "win32";

    const backendDir = app.isPackaged
        ? path.join(process.resourcesPath, "backend")
        : path.join(__dirname, "releases", "main");

    const executableName = isWindows ? "main.exe" : "main";
    const exePath = path.join(backendDir, executableName);
    const exeExists = fs.existsSync(exePath);

    // Must match where the Python side (backend/commands.py) resolves
    // APP_DIR to: the frozen exe's own directory, or the repo root
    // (__dirname) when falling back to running main.py directly.
    backendOutputDir = exeExists ? backendDir : __dirname;

    if (!preserveErrors) {
        try {
            fs.writeFileSync(
                path.join(backendOutputDir, "error_log.json"),
                "[]",
                "utf8"
            );
        } catch (error) {
            console.error("Could not clear the previous error log:", error);
        }
    }

    console.log("Backend directory:", backendDir);
    console.log("Output directory:", backendOutputDir);

    if (exeExists) {

        console.log("Launching packaged executable:", exePath);

        pythonProcess = spawn(
            exePath,
            [],
            {
                cwd: backendDir
            }
        );

    } else {

        console.log("Executable not found, falling back to Python script");

        const pythonPath = isWindows
            ? path.join(__dirname, ".venv", "Scripts", "python.exe")
            : path.join(__dirname, ".venv", "bin", "python");

        const scriptPath = path.join(__dirname, "main.py");

        pythonProcess = spawn(
            pythonPath,
            [
                "-u",
                scriptPath,
                backendOutputDir
            ],
            {
                cwd: __dirname,
                env: {
                    ...process.env,
                    PYTHONUNBUFFERED: "1"
                }
            }
        );

    }

    let pythonBuffer = "";

    pythonProcess.stdout.on("data", (data) => {

        pythonBuffer += data.toString();


        let lines = pythonBuffer.split("\n");

        pythonBuffer = lines.pop();


        for (const line of lines) {

            if (!line.trim())
                continue;


            handleBackendLine(line);

        }

    });

    pythonProcess.stderr.on("data", (data) => {

        console.error("Backend stderr:");
        console.error(data.toString());

    });

    pythonProcess.on("error", (err) => {

        console.error("Failed to start backend:");
        console.error(err);
        failPendingBackendRequests(`Python backend failed: ${err.message}`);

    });

    pythonProcess.on("close", (code) => {

        console.log("Backend exited:", code);

        if (restartBackendAfterCancel) {
            restartBackendAfterCancel = false;
            failPendingBackendRequests("Operation cancelled by user.", { cancelled: true });
            pythonProcess = null;
            startPythonBackend(true);
            return;
        }

        failPendingBackendRequests(`Backend exited unexpectedly (code ${code}).`);

        pythonProcess = null;

    });

}



// ----------------------------------------------------
// APP EVENTS
// ----------------------------------------------------

app.whenReady()
.then(
    createWindow
);



app.on(
    "window-all-closed",
    ()=>{


        if(pythonProcess && !pythonProcess.killed && !pythonProcess.exitCode && !pythonProcess.signalCode){
            try {
                pythonProcess.kill();
            } catch (error) {
                console.warn("Failed to stop Python process:", error);
            }
        }


        if(process.platform !== "darwin"){

            app.quit();

        }

    }
);

app.on(
    "before-quit",
    (event)=>{
        if ((guiTestRunner?.activeRun || localGuiApp?.active) && !guiShutdownStarted) {
            event.preventDefault();
            guiShutdownStarted = true;
            Promise.all([
                guiTestRunner?.dispose(),
                stopLocalGuiApp()
            ])
                .catch(error => console.error("Failed to clean up GUI test container:", error))
                .finally(() => {
                    finishActiveGuiTestReport("interrupted", "Checkpoint closed before the GUI test run finished.");
                    app.quit();
                });
        }
    }
);

app.on(
    "before-quit",
    ()=>{

        if(pythonProcess && !pythonProcess.killed && !pythonProcess.exitCode && !pythonProcess.signalCode){
            try {
                pythonProcess.kill();
            } catch (error) {
                console.warn("Failed to stop Python process:", error);
            }
        }

    }
);
