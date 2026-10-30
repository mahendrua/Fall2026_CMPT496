const test = require('node:test');
const assert = require('node:assert/strict');
const createConnection = require('../gui-runner/previewConnection');
const { diagnoseFailure } = require('../gui-runner/reporter.cjs');

function setup() {
    const clients = [], messages = [], timers = new Map();
    let id = 0;
    const connection = createConnection({
        createClient() {
            const handlers = {};
            const client = { addEventListener: (name, fn) => { handlers[name] = fn; },
                emit: name => handlers[name]?.(), disconnect() { this.closed = true; this.emit('disconnect'); } };
            clients.push(client);
            return client;
        },
        onStatus: message => messages.push(message),
        setTimer: fn => { timers.set(++id, fn); return id; },
        clearTimer: key => timers.delete(key), retryDelays: [1, 2]
    });
    return { clients, messages, connection, timers, tick() {
        const [key, fn] = timers.entries().next().value;
        timers.delete(key); fn();
    } };
}

test('preview starts, reconnects the existing session and ignores stale disconnects', () => {
    const s = setup();
    s.clients[0].emit('connect');
    s.clients[0].emit('disconnect');
    assert.match(s.messages.at(-1), /Reconnecting/);
    s.tick();
    assert.equal(s.clients.length, 2);
    s.clients[0].emit('disconnect');
    s.clients[1].emit('connect');
    assert.match(s.messages.at(-1), /successfully reconnected/);
    s.connection.finish();
    assert.equal(s.timers.size, 0);
    assert.match(s.messages.at(-1), /ended normally/);
});

test('delayed startup and refused sockets have a bounded retry budget', () => {
    const s = setup();
    for (let i = 0; i < 5; i++) s.tick();
    assert.equal(s.clients.length, 3);
    assert.equal(s.timers.size, 0);
    assert.match(s.messages.at(-1), /recovery failed.*Test execution continues/);
});

test('container exit and failure cleanup cancel pending recovery', () => {
    const s = setup();
    s.clients[0].emit('disconnect');
    s.connection.finish('Docker container exited');
    assert.equal(s.timers.size, 0);
    assert.equal(s.messages.at(-1), 'Docker container exited');
});

test('authentication failure does not create another browser session', () => {
    const s = setup();
    s.clients[0].emit('securityfailure');
    assert.equal(s.timers.size, 0);
    assert.equal(s.clients.length, 1);
    assert.match(s.messages.at(-1), /authenticate/);
});

test('matched elements with incorrect values are assertions, not selector failures', () => {
    const result = diagnoseFailure([{ message: "expect(locator).toHaveText(expected) failed\nwaiting for locator('#count')\nlocator resolved to <output>2</output>\nExpected: 1\nReceived: 2" }], []);
    assert.equal(result.category, 'assertion_mismatch');
});

test('missing selectors, browser crashes and refused navigation stay distinct', () => {
    for (const [message, expected] of [
        ['waiting for getByRole(button); locator resolved to 0 elements', 'locator_timeout'],
        ['page crashed', 'browser_infrastructure_failure'],
        ['Target page, context or browser has been closed', 'browser_infrastructure_failure'],
        ['strict mode violation: locator resolved to 2 elements', 'selector_ambiguity'],
        ['page.goto: net::ERR_CONNECTION_REFUSED', 'connection_failure'],
        ['page.goto: Timeout 30000ms exceeded', 'navigation_failure'],
        ['page.goto: Cannot navigate to invalid URL', 'navigation_failure']
    ]) assert.equal(diagnoseFailure([{ message }], []).category, expected);
});
