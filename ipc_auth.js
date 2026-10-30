function assertMainRenderer(event, mainWindow) {
    if (
        !mainWindow ||
        mainWindow.isDestroyed() ||
        event.sender !== mainWindow.webContents ||
        event.senderFrame !== mainWindow.webContents.mainFrame
    ) {
        throw new Error("This operation is available only to the Checkpoint window.");
    }
}

function assertAuthenticatedRenderer(event, mainWindow, authSession) {
    assertMainRenderer(event, mainWindow);
    if (
        !authSession ||
        authSession.webContentsId !== event.sender.id ||
        authSession.expiresAt <= Date.now()
    ) {
        throw new Error("Sign in to Checkpoint to continue.");
    }
}

module.exports = { assertAuthenticatedRenderer, assertMainRenderer };
