(function (root, factory) {
    const api = factory();

    if (typeof module !== "undefined" && module.exports) {
        module.exports = api;
    } else {
        root.ApiKeyVerify = api;
    }
})(globalThis, function () {
    function isAcceptedVerification(response) {
        return Boolean(
            response
            && response.success === true
            && response.result
            && response.result.status === "accepted"
        );
    }

    function shouldSaveVerifiedKey(response, pendingRequestId, pendingKey) {
        if (!pendingKey) return false;
        if (typeof pendingRequestId !== "string" || !pendingRequestId) return false;
        if (!response || response.request_id !== pendingRequestId) return false;
        return isAcceptedVerification(response);
    }

    function consumeVerificationResponse(state, response) {
        const pendingRequestId = state && state.pendingRequestId;
        const pendingKey = state && state.pendingKey;
        const matches = Boolean(
            pendingRequestId
            && response
            && response.request_id === pendingRequestId
        );
        const save = shouldSaveVerifiedKey(response, pendingRequestId, pendingKey);
        const next = {
            pendingRequestId: matches ? null : pendingRequestId,
            pendingKey: matches ? null : pendingKey,
            buttonPending: matches ? false : Boolean(state && state.buttonPending)
        };
        return { save, matches, resetButton: matches, next };
    }

    return {
        isAcceptedVerification,
        shouldSaveVerifiedKey,
        consumeVerificationResponse
    };
});
