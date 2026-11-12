(function (root, factory) {
    const api = factory();

    if (typeof module !== "undefined" && module.exports) {
        module.exports = api;
    } else {
        root.CommandRequestTracker = api.CommandRequestTracker;
    }
})(globalThis, function () {
    const STREAM_TYPES = new Set([
        "progress",
        "pipeline_progress",
        "token_usage",
        "token_usage_stage"
    ]);

    class CommandRequestTracker {
        constructor(idFactory = () => globalThis.crypto.randomUUID()) {
            this.idFactory = idFactory;
            this.pending = new Map();
        }

        begin(command) {
            return this.register(this.idFactory(), command);
        }

        register(requestId, command) {
            if (typeof requestId !== "string" || !requestId.trim()) {
                throw new TypeError("A non-empty request ID is required.");
            }
            if (this.pending.has(requestId)) {
                throw new Error(`Request ID is already pending: ${requestId}`);
            }

            const request = {
                requestId,
                command,
                status: "pending",
                accepted: false
            };
            this.pending.set(requestId, request);
            return request;
        }

        get(requestId) {
            return this.pending.get(requestId) || null;
        }

        firstPending() {
            return this.pending.values().next().value || null;
        }

        accept(requestId) {
            const request = this.get(requestId);
            if (!request) return false;
            request.accepted = true;
            return true;
        }

        handle(response) {
            if (!response || typeof response !== "object") {
                return { matched: false, malformed: true };
            }

            const request = this.get(response.request_id);
            if (!request) return { matched: false, duplicateOrUnknown: true };

            if (response.accepted === true) {
                request.accepted = true;
                return { matched: true, terminal: false, request, response };
            }

            if (STREAM_TYPES.has(response.type)) {
                return { matched: true, terminal: false, request, response };
            }

            this.pending.delete(request.requestId);
            request.status = response.success === true ? "succeeded" : "failed";

            const normalizedResponse = typeof response.success === "boolean"
                ? response
                : {
                    request_id: request.requestId,
                    command: request.command,
                    success: false,
                    error: "Malformed backend response: missing success status."
                };

            return {
                matched: true,
                terminal: true,
                request,
                response: normalizedResponse
            };
        }

        fail(requestId, error, extra = {}) {
            const request = this.get(requestId);
            if (!request) return null;

            this.pending.delete(request.requestId);
            request.status = extra.cancelled ? "cancelled" : "failed";

            return {
                matched: true,
                terminal: true,
                request,
                response: {
                    request_id: request.requestId,
                    command: request.command,
                    success: false,
                    error,
                    ...extra
                }
            };
        }

        failAll(error, extra = {}) {
            const failures = [];
            for (const requestId of this.pending.keys()) {
                failures.push(this.fail(requestId, error, extra));
            }
            return failures.filter(Boolean);
        }
    }

    return { CommandRequestTracker };
});