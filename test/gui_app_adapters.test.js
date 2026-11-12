const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const { discoverApplications, detectNode, launchPlan, configuredEndpoint } = require('../guiAppAdapters');
const { LocalGuiApp, parseListeningEndpoint, probeHttp, redactLog, createAppEnvironment } = require('../guiLocalApp');
const { diagnoseResponse } = require('../gui-runner/connectivity.cjs');
function directory(t) {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'checkpoint-adapter-'));
    t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
    return dir;
}
function write(dir, file, content) { fs.mkdirSync(path.dirname(path.join(dir, file)), { recursive: true }); fs.writeFileSync(path.join(dir, file), content); }

test('selected command beats an unrelated Vite dependency', () => {
    for (const [command, framework] of [['next dev','next'], ['ng serve','angular'], ['nuxi dev','nuxt'], ['astro dev','astro'], ['react-router dev','react-router'], ['remix vite:dev','react-router'], ['node server.js','node']]) {
        assert.equal(detectNode({ scripts: { dev: command }, devDependencies: { vite: '*' } }), framework);
    }
    for (const dep of ['react', 'vue', '@sveltejs/kit', 'solid-js']) assert.equal(detectNode({ scripts: { dev: 'vite' }, dependencies: { [dep]: '*' } }), 'vite');
    assert.equal(detectNode({ scripts: { start: 'node server.js' }, dependencies: { express: '*' } }), 'express');
    assert.equal(detectNode({ scripts: { start: 'node server.js' }, dependencies: { fastify: '*' } }), 'fastify');
});

test('discovers Python, static and monorepo choices without importing application code', t => {
    const dir = directory(t);
    write(dir, 'package.json', JSON.stringify({ workspaces: ['apps/*'] }));
    write(dir, 'apps/flask/app.py', 'from flask import Flask\napp = Flask(__name__)');
    write(dir, 'apps/django/manage.py', 'from django.core.management import execute_from_command_line');
    write(dir, 'apps/fastapi/main.py', 'from fastapi import FastAPI\napp = FastAPI()');
    write(dir, 'apps/static/index.html', '<h1>Static</h1>');
    write(dir, 'apps/vite/package.json', JSON.stringify({ scripts: { dev: 'vite' }, dependencies: { vue: '*' } }));
    const projects = discoverApplications(dir);
    assert.deepEqual(projects.map(p => p.framework).sort(), ['django','fastapi','flask','static','vite']);
    assert.match(projects.find(p => p.framework === 'vite').label, /Vue/);
});

test('launch plans preserve explicit ports, avoid wrapper flags and never run lifecycle setup', () => {
    const project = { directory: process.cwd(), runtime: 'node', framework: 'vite', pkg: { scripts: { dev: 'vite --port 4325' } } };
    assert.equal(configuredEndpoint(project).port, 4325);
    assert.ok(!launchPlan(project, 4325).args.includes('--port'));
    assert.ok(launchPlan(project, 4325).args.includes('--ignore-scripts'));
    project.pkg.scripts.dev = 'concurrently "vite" "node server.js"';
    assert.ok(!launchPlan(project, 4325).args.includes('--host'));
    for (const framework of ['flask','django','fastapi','streamlit','gradio']) {
        const plan = launchPlan({ directory: process.cwd(), runtime: 'python', framework, entry: 'app.py', object: 'app' }, 8765);
        assert.ok(plan.args.length);
        assert.ok(!plan.args.includes('install'));
    }
    assert.equal(createAppEnvironment(4325, 'vite').__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS, 'host.docker.internal');
    assert.ok(!Object.values(createAppEnvironment(4325, 'node')).includes('DANGEROUSLY_DISABLE_HOST_CHECK'));
});

test('connectivity classification recognizes restrictions and permits login/non-200 content', () => {
    for (const [input, category] of [
        [{ status: 403, body: 'Blocked request. This host ("host.docker.internal") is not allowed.', framework: 'vite' }, 'host_blocked'],
        [{ status: 400, body: 'DisallowedHost Invalid HTTP_HOST header', framework: 'django' }, 'host_blocked'],
        [{ status: 403, body: 'ERR_ACCESS_DENIED squid' }, 'proxy_denied'],
        [{ error: 'net::ERR_NAME_NOT_RESOLVED' }, 'dns_failure'],
        [{ error: 'net::ERR_CONNECTION_REFUSED' }, 'connection_refused'],
        [{ error: 'Timeout 12000ms exceeded' }, 'connection_timeout'],
        [{ error: 'net::ERR_CERT_AUTHORITY_INVALID' }, 'tls_certificate'],
        [{ error: 'net::ERR_TUNNEL_CONNECTION_FAILED' }, 'proxy_failure'],
        [{ status: 500, body: 'Internal Server Error' }, 'application_startup_error'],
        [{ status: 200, body: '<vite-error-overlay>' }, 'application_startup_error'],
        [{ status: 401, body: 'Please sign in' }, 'accessible'],
        [{ status: 403, body: 'Login required' }, 'accessible'],
        [{ status: 302, body: '' }, 'accessible'],
        [{ status: 404, body: 'Route requires a custom path' }, 'accessible']
    ]) assert.equal(diagnoseResponse(input).category, category);
});

test('port/protocol discovery is restricted to local listening announcements and logs redact credentials', () => {
    assert.deepEqual(parseListeningEndpoint('Local: https://localhost:8443/custom/'), { protocol: 'https:', port: 8443, basePath: '/custom/' });
    assert.equal(parseListeningEndpoint('Read docs: https://example.com:9000/'), null);
    const text = redactLog('password=private api_key=secret authorization: Bearer abc https://user:pwd@example.com');
    for (const secret of ['private', 'secret', 'abc', 'pwd']) assert.ok(!text.includes(secret));
});

test('HTTP readiness accepts authentication and rejects host-blocking responses', async t => {
    const server = http.createServer((_req,res) => { res.writeHead(401); res.end('Sign in'); });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    t.after(() => new Promise(resolve => server.close(resolve)));
    const response = await probeHttp(`http://127.0.0.1:${server.address().port}`);
    assert.equal(response.status, 401);
});

test('starts a static application, serves its content, blocks hidden files and cleans up', async t => {
    const dir = directory(t);
    write(dir, 'index.html', '<h1>Adapter Static</h1>');
    write(dir, '.env', 'SECRET=private');
    const app = new LocalGuiApp();
    t.after(() => app.stop());
    const result = await app.start(dir, { timeoutMs: 5000 });
    assert.equal(result.framework, 'static');
    assert.match((await probeHttp(`http://127.0.0.1:${result.port}`)).body, /Adapter Static/);
    assert.equal((await probeHttp(`http://127.0.0.1:${result.port}/.env`)).status, 404);
    await app.stop();
    assert.equal(app.active, null);
});

test('detects actual advertised port instead of assuming PORT is honored', async t => {
    const dir = directory(t);
    write(dir, 'package.json', JSON.stringify({ scripts: { dev: 'node server.cjs' } }));
    write(dir, 'server.cjs', `const s=require('http').createServer((q,r)=>r.end('ready'));s.listen(0,'0.0.0.0',()=>console.log('http://localhost:'+s.address().port+'/'));`);
    const app = new LocalGuiApp(); t.after(() => app.stop());
    const result = await app.start(dir, { timeoutMs: 5000 });
    try { assert.match((await probeHttp(`http://127.0.0.1:${result.port}`)).body, /ready/); }
    finally { await app.stop(); }
});

test('startup crash, timeout and cancellation do not leave owned processes running', async t => {
    const dir = directory(t);
    write(dir, 'package.json', JSON.stringify({ scripts: { dev: 'node server.cjs' } }));
    write(dir, 'server.cjs', 'process.exit(9)');
    const app = new LocalGuiApp(); t.after(() => app.stop());
    await assert.rejects(app.start(dir, { timeoutMs: 3000 }), /exited/);
    assert.equal(app.active, null);
    write(dir, 'server.cjs', 'setInterval(()=>{},1000)');
    await assert.rejects(app.start(dir, { timeoutMs: 500 }), /timed out/);
    assert.equal(app.active, null);
    const pending = app.start(dir, { timeoutMs: 10000 });
    await app.stop();
    await assert.rejects(pending, /cancelled/);
    assert.equal(app.active, null);
});

test('unsupported projects, ambiguous apps, absent dependencies and setup scripts fail without launching', async t => {
    const dir = directory(t);
    const app = new LocalGuiApp();
    await assert.rejects(app.start(dir), /No supported/);
    write(dir, 'package.json', JSON.stringify({ scripts: { dev: 'npm install && vite' } }));
    await assert.rejects(app.start(dir), /installation, setup, or migrations/);
    write(dir, 'package.json', JSON.stringify({ scripts: { dev: 'node server.js' }, dependencies: { 'checkpoint-missing-test-module': '*' } }));
    await assert.rejects(app.start(dir), /dependencies are not installed/);
    fs.unlinkSync(path.join(dir, 'package.json'));
    write(dir, 'apps/first/index.html', '<h1>One</h1>');
    write(dir, 'apps/second/index.html', '<h1>Two</h1>');
    await assert.rejects(app.start(dir), /Multiple applications/);
    assert.equal(app.active, null);
});

test('an explicitly occupied port is not reused or stopped', async t => {
    const dir = directory(t);
    const server = http.createServer((_req,res)=>res.end('external service'));
    await new Promise(resolve=>server.listen(0,'0.0.0.0',resolve));
    try {
        write(dir, 'package.json', JSON.stringify({ scripts: { dev: `vite --port ${server.address().port}` } }));
        const app = new LocalGuiApp();
        await assert.rejects(app.start(dir), /already occupied/);
        assert.equal((await probeHttp(`http://127.0.0.1:${server.address().port}`)).body, 'external service');
        assert.equal(app.active, null);
    } finally { await new Promise(resolve=>server.close(resolve)); }
});

test('literal backend listener ports and HTTPS are read without executing source', t => {
    const dir = directory(t);
    write(dir, 'server.cjs', "https.createServer(options, handler).listen(5443, '127.0.0.1');");
    const endpoint = configuredEndpoint({ directory: dir, framework: 'express', pkg: { scripts: { dev: 'node server.cjs' } } });
    assert.equal(endpoint.port, 5443);
    assert.equal(endpoint.protocol, 'https:');
});

test('an endlessly streaming HTTP response cannot extend the readiness deadline', async () => {
    const server = http.createServer((req,res) => {
        const timer = setInterval(()=>res.write('pending'), 15);
        req.on('close',()=>clearInterval(timer));
    });
    await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
    try {
        await assert.rejects(probeHttp(`http://127.0.0.1:${server.address().port}`, { timeoutMs: 80 }), /timed out/);
    } finally { server.closeAllConnections(); await new Promise(resolve=>server.close(resolve)); }
});
