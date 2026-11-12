const test = require("node:test");
const assert = require("node:assert/strict");

const { CommandRequestTracker } = require("../../command_requests");

function tracker() {
    let nextId = 0;
    return new CommandRequestTracker(() => `request-${++nextId}`);
}

test("a sent or accepted command remains pending until completion", () => {
    const requests = tracker();
    const request = requests.begin("build_database");

    assert.equal(request.status, "pending");
    assert.equal(requests.accept(request.requestId), true);
    assert.equal(requests.get(request.requestId).status, "pending");

    const completion = requests.handle({
        request_id: request.requestId,
        success: true,
        command: request.command
    });

    assert.equal(completion.terminal, true);
    assert.equal(completion.request.status, "succeeded");
    assert.equal(requests.get(request.requestId), null);
});

test("matching error fails only its request and duplicate completion is ignored", () => {
    const requests = tracker();
    const request = requests.begin("generate_unit_tests");
    const response = {
        request_id: request.requestId,
        success: false,
        error: "generation failed"
    };

    const failure = requests.handle(response);

    assert.equal(failure.request.status, "failed");
    assert.equal(failure.response.error, "generation failed");
    assert.equal(requests.handle(response).matched, false);
});

test("malformed completion fails its matching request instead of reporting success", () => {
    const requests = tracker();
    const request = requests.begin("build_database");

    const result = requests.handle({ request_id: request.requestId, result: "incomplete" });

    assert.equal(result.request.status, "failed");
    assert.equal(result.response.success, false);
    assert.match(result.response.error, /missing success status/);
});

test("a late response for another ID leaves pending requests untouched", () => {
    const requests = tracker();
    const request = requests.begin("current");

    const result = requests.handle({ request_id: "old-request", success: true });

    assert.equal(result.matched, false);
    assert.equal(requests.get(request.requestId).status, "pending");
});

test("commands settle independently when responses arrive out of order", () => {
    const requests = tracker();
    const first = requests.begin("first");
    const second = requests.begin("second");

    const secondResult = requests.handle({ request_id: second.requestId, success: true });
    const firstResult = requests.handle({ request_id: first.requestId, success: false, error: "first failed" });

    assert.equal(secondResult.request.command, "second");
    assert.equal(secondResult.request.status, "succeeded");
    assert.equal(firstResult.request.command, "first");
    assert.equal(firstResult.request.status, "failed");
});

test("disconnect fails each pending request with its own ID", () => {
    const requests = tracker();
    const first = requests.begin("first");
    const second = requests.begin("second");

    const failures = requests.failAll("backend disconnected");

    assert.deepEqual(failures.map(item => item.response.request_id), [first.requestId, second.requestId]);
    assert.ok(failures.every(item => item.response.success === false));
    assert.equal(requests.pending.size, 0);
});

test("progress is correlated without settling the request", () => {
    const requests = tracker();
    const request = requests.begin("full_pipeline");

    const progress = requests.handle({
        request_id: request.requestId,
        type: "pipeline_progress",
        stage: "Building database"
    });

    assert.equal(progress.matched, true);
    assert.equal(progress.terminal, false);
    assert.equal(requests.get(request.requestId).status, "pending");
});