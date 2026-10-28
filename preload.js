const { contextBridge, ipcRenderer } = require("electron");


contextBridge.exposeInMainWorld(
    "electronAPI",
    {

        getFirebaseConfig: () => ipcRenderer.invoke("auth:get-config"),
        readAuthSession: () => ipcRenderer.invoke("auth:read-session"),
        writeAuthSession: session => ipcRenderer.invoke("auth:write-session", session),
        clearAuthSession: () => ipcRenderer.invoke("auth:clear-session"),
        establishAuthSession: idToken => ipcRenderer.invoke("auth:establish", idToken),
        signOutAuthSession: () => ipcRenderer.invoke("auth:sign-out"),
        getUserProfile: () => ipcRenderer.invoke("auth:get-profile"),
        saveUserProfile: role => ipcRenderer.invoke("auth:save-profile", role),

        // ==================================
        // Backend Commands
        // ==================================

        executeCommand: (
            command,
            args = {},
            requestId
        ) => {

            return ipcRenderer.invoke(
                "execute-command",
                {
                    command,
                    args,
                    request_id: requestId
                }
            );

        },

    cancelCommand: (requestId) => ipcRenderer.invoke("cancel-command", requestId),

        getErrorLog: () => ipcRenderer.invoke("get-error-log"),

        recordErrorLog: (error) => ipcRenderer.invoke("record-error-log", error),



        // ==================================
        // Collection Preview Commands
        // ==================================

        previewCommand: (
            action,
            args = {}
        ) => {

            return ipcRenderer.invoke(
                "preview-command",
                {
                    action,
                    args
                }
            );

        },



        // ==================================
        // API Key
        // ==================================

        hasAPIKey: () => {

            return ipcRenderer.invoke(
                "has-api-key"
            );

        },

        // --- multi-key manager ---
        listApiKeys:  ()     => ipcRenderer.invoke("list-api-keys"),
        saveApiKey:   (data) => ipcRenderer.invoke("save-api-key", data),
        selectApiKey: (data) => ipcRenderer.invoke("select-api-key", data),
        deleteApiKey: (data) => ipcRenderer.invoke("delete-api-key", data),

        getValidatedRules: (
            codebasePath
        ) => {
            return ipcRenderer.invoke(
                "get-validated-rules",
                {
                    codebasePath
                }
            );
        },

        getBusinessRules: (codebasePath) => ipcRenderer.invoke(
            "get-business-rules",
            { codebasePath }
        ),

        setAPIKey: (
            key
        ) => {


            return ipcRenderer.invoke(
                "execute-command",
                {
                    command:"set_api_key",

                    args:{
                        api_key:key
                    }
                }
            );

        },

        exitApp: () => {
            return ipcRenderer.invoke("exit-app");
        },

        // Run report (US-049): opens run_reports/<codebase>/run_report.html in its own window.
        openReport: (reportPath) => ipcRenderer.invoke("open-report", reportPath),
        // The selected codebase's report, or the newest one when none is selected.
        openLatestReport: (codebaseName) => ipcRenderer.invoke("open-latest-report", codebaseName),
        // Saved report HTML for the selected codebase, rendered by the UI in a sandboxed iframe.
        getRunReportHtml: (codebaseName) => ipcRenderer.invoke("get-run-report-html", codebaseName),


        // ==================================
        // Backend Response Listener
        // ==================================

        onBackendResponse: (
            callback
        ) => {


            ipcRenderer.on(
                "backend-response",
                (
                    event,
                    response
                )=>{


                    callback(response);


                }
            );

        },


        // Optional cleanup
        removeBackendListener: () => {

            ipcRenderer.removeAllListeners(
                "backend-response"
            );

        },
        
        selectCodebase: () =>
        ipcRenderer.invoke("select-codebase"),

        selectGuiTestFiles: () => ipcRenderer.invoke("select-gui-test-files"),
        startGuiTests: (testIds, allowedHosts) =>
            ipcRenderer.invoke("start-gui-tests", { testIds, allowedHosts }),
        startLocalGuiApp: request =>
            ipcRenderer.invoke("start-local-gui-app", request),
        stopLocalGuiApp: () => ipcRenderer.invoke("stop-local-gui-app"),
        startGeneratedGuiTests: request =>
            ipcRenderer.invoke("start-generated-gui-tests", request),
        repairGeneratedGuiTest: request =>
            ipcRenderer.invoke("repair-generated-gui-test", request),
        stopGuiTests: () => ipcRenderer.invoke("stop-gui-tests"),
        listGuiTestingReports: () => ipcRenderer.invoke("list-gui-testing-reports"),
        getGuiTestingReport: reportId => ipcRenderer.invoke("get-gui-testing-report", reportId),
        exportGuiTestingReportPdf: reportId => ipcRenderer.invoke("export-gui-testing-report-pdf", reportId),
        reportGuiTestEvent: payload => ipcRenderer.send("gui-test-report-event", payload),
        onGuiTestEvent: (callback) => {
            ipcRenderer.on("gui-test-event", (event, payload) => callback(payload));
        },
        removeGuiTestListener: () => {
            ipcRenderer.removeAllListeners("gui-test-event");
        },

        }
);
