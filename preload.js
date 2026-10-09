const { contextBridge, ipcRenderer } = require("electron");


contextBridge.exposeInMainWorld(
    "electronAPI",
    {


        // ==================================
        // Backend Commands
        // ==================================

        executeCommand: (
            command,
            args = {}
        ) => {

            return ipcRenderer.invoke(
                "execute-command",
                {
                    command,
                    args
                }
            );

        },

    cancelCommand: () => ipcRenderer.invoke("cancel-command"),

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
        stopGuiTests: () => ipcRenderer.invoke("stop-gui-tests"),
        onGuiTestEvent: (callback) => {
            ipcRenderer.on("gui-test-event", (event, payload) => callback(payload));
        },
        removeGuiTestListener: () => {
            ipcRenderer.removeAllListeners("gui-test-event");
        },

        }
);