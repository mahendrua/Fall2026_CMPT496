const test = require("node:test");
const assert = require("node:assert/strict");

const {
    isAcceptedVerification,
    shouldSaveVerifiedKey,
    consumeVerificationResponse
} = require("../../api_key_verify");
const { CommandRequestTracker } = require("../../command_requests");

const pendingKey = {
    provider: "google",
    label: "lab",
    model: "gemini-3-flash-preview",
    apiKey: "test-placeholder-key"
};

test("accepted IPC is not treated as verification success", () => {
    const requests = new CommandRequestTracker(() => "request-1");
    const request = requests.begin("verify_api_key");
    assert.equal(requests.accept(request.requestId), true);
    assert.equal(request.status, "pending");

    const ack = {
        request_id: request.requestId,
        command: "verify_api_key",
        accepted: true
    };
    const handled = requests.handle(ack);
    assert.equal(handled.terminal, false);
    assert.equal(isAcceptedVerification(ack), false);
    assert.equal(shouldSaveVerifiedKey(ack, request.requestId, pendingKey), false);
});

test("rejected IPC submission restores the button and does not save", () => {
    const state = {
        pendingRequestId: "request-1",
        pendingKey,
        buttonPending: true
    };
    const outcome = consumeVerificationResponse(state, {
        request_id: "request-1",
        command: "verify_api_key",
        success: false,
        error: "Python backend is not running"
    });
    assert.equal(outcome.save, false);
    assert.equal(outcome.resetButton, true);
    assert.equal(outcome.next.buttonPending, false);
    assert.equal(outcome.next.pendingKey, null);
});

test("IPC exceptions restore the button and do not save", () => {
    const outcome = consumeVerificationResponse(
        { pendingRequestId: "request-2", pendingKey, buttonPending: true },
        {
            request_id: "request-2",
            command: "verify_api_key",
            success: false,
            error: "Could not send the command to the backend."
        }
    );
    assert.equal(outcome.save, false);
    assert.equal(outcome.resetButton, true);
});

test("terminal backend success saves only an accepted verification", () => {
    const accepted = consumeVerificationResponse(
        { pendingRequestId: "request-3", pendingKey, buttonPending: true },
        {
            request_id: "request-3",
            command: "verify_api_key",
            success: true,
            result: { status: "accepted", message: "Key verified against gemini-fake." }
        }
    );
    assert.equal(accepted.save, true);
    assert.equal(accepted.resetButton, true);

    const rejected = consumeVerificationResponse(
        { pendingRequestId: "request-3b", pendingKey, buttonPending: true },
        {
            request_id: "request-3b",
            command: "verify_api_key",
            success: true,
            result: { status: "rejected", message: "Google rejected this key or model." }
        }
    );
    assert.equal(rejected.save, false);
    assert.equal(rejected.resetButton, true);

    const unverified = consumeVerificationResponse(
        { pendingRequestId: "request-3c", pendingKey, buttonPending: true },
        {
            request_id: "request-3c",
            command: "verify_api_key",
            success: true,
            result: { status: "unverified", message: "Could not verify this key. The key was not saved." }
        }
    );
    assert.equal(unverified.save, false);
    assert.equal(unverified.resetButton, true);
});

test("backend exit restores pending verification UI", () => {
    const requests = new CommandRequestTracker(() => "request-4");
    requests.begin("verify_api_key");
    const failures = requests.failAll("Backend exited unexpectedly (code null).");
    assert.equal(failures.length, 1);
    const outcome = consumeVerificationResponse(
        { pendingRequestId: "request-4", pendingKey, buttonPending: true },
        failures[0].response
    );
    assert.equal(outcome.save, false);
    assert.equal(outcome.resetButton, true);
    assert.equal(outcome.next.buttonPending, false);
});

test("late or unmatched responses cannot save a key", () => {
    const current = {
        pendingRequestId: "request-current",
        pendingKey,
        buttonPending: true
    };
    const late = consumeVerificationResponse(current, {
        request_id: "request-old",
        command: "verify_api_key",
        success: true,
        result: { status: "accepted", message: "Key verified against gemini-fake." }
    });
    assert.equal(late.save, false);
    assert.equal(late.matches, false);
    assert.equal(late.resetButton, false);
    assert.equal(late.next.pendingRequestId, "request-current");
    assert.equal(late.next.buttonPending, true);
});

test("non-verification commands are not treated as key saves", () => {
    assert.equal(
        shouldSaveVerifiedKey(
            {
                request_id: "request-db",
                command: "build_database",
                success: true
            },
            "request-db",
            pendingKey
        ),
        false
    );

    const requests = new CommandRequestTracker(() => "request-db");
    const request = requests.begin("build_database");
    const completion = requests.handle({
        request_id: request.requestId,
        success: true,
        command: request.command
    });
    assert.equal(completion.terminal, true);
    assert.equal(completion.request.status, "succeeded");
});
