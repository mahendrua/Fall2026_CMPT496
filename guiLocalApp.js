const { spawn, execFile } = require("child_process");
const fs = require("fs");
const net = require("net");
const path = require("path");
const { promisify } = require('node:util');
const adapters = require('./guiAppAdapters');
const { diagnoseResponse } = require('./gui-runner/connectivity.cjs');
const { ownsListeningPort } = require('./guiAppProcess');

const STARTUP_TIMEOUT_MS = 120000;
const MAX_LOG_BYTES = 32 * 1024;
const HOSTNAME_ALLOWED_FOR_VITE = "host.docker.internal";

function detectFramework(packageJson) {
    return adapters.detectNode(packageJson);
}

function getPreferredPort(framework) {
    return adapters.ADAPTERS[framework]?.port || {
        vite: 5173,
        next: 3000,
        angular: 4200,
        "vue-cli": 8080,
        "create-react-app": 3000,
        node: 3000
    }[framework];
}

function getLaunchOptions(packageJson, framework, port) {
    const scripts = packageJson.scripts || {};
    const script = scripts.dev
        ? "dev"
        : scripts.start
            ? "start"
            : scripts.server
                ? "server"
                : null;
    if (!script) {
        throw new Error('The selected package.json must define a "dev", "start", or "server" script.');
    }

    let args = [];
    if (framework === "vite") {
        args = ["--", "--host", "0.0.0.0", "--port", String(port), "--strictPort"];
    } else if (framework === "next") {
        args = ["--", "--hostname", "0.0.0.0", "--port", String(port)];
    } else if (framework === "vue-cli" || framework === "angular") {
        args = ["--", "--host", "0.0.0.0", "--port", String(port)];
    } else if (framework === "create-react-app") {
        args = ["--", "--host", "0.0.0.0", "--port", String(port)];
    }

    return { script, args };
}

function createAppEnvironment(port, framework) {
    const allowedKeys = new Set([
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "TEMP",
        "TMP",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "HOME",
        "LANG"
    ]);
    const environment = Object.fromEntries(
        Object.entries(process.env).filter(([key]) => allowedKeys.has(key.toUpperCase()))
    );
    return {
        ...environment,
        HOST: "0.0.0.0",
        PORT: String(port),
        BROWSER: "none",
        CI: "true",
        GRADIO_SERVER_NAME: "0.0.0.0",
        GRADIO_SERVER_PORT: String(port),
        ...(framework === "vite" || framework === "react-router" || framework === "astro" || framework === "nuxt"
            ? { __VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS: HOSTNAME_ALLOWED_FOR_VITE }
            : {})
    };
}

function canListen(port, host) {
    return new Promise((resolve, reject) => {
        const server = net.createServer();
        server.once("error", error => {
            if (error.code === "EADDRINUSE" || error.code === "EACCES") resolve(false);
            else reject(new Error(`Could not check local app port ${port}: ${error.message}`));
        });
        server.listen(port, host, () => server.close(error => {
            if (error) reject(new Error(`Could not release local app port ${port}: ${error.message}`));
            else resolve(true);
        }));
    });
}

// Windows lets 0.0.0.0 bind beside a loopback-only listener, which would then answer the 127.0.0.1 readiness probe.
async function isPortAvailable(port) {
    return await canListen(port, "0.0.0.0") && await canListen(port, "127.0.0.1");
}

async function selectPort(framework) {
    const preferredPort = getPreferredPort(framework);
    const lastCandidate = Math.min(preferredPort + 100, 65535);
    for (let port = preferredPort; port <= lastCandidate; port += 1) {
        if (await isPortAvailable(port)) return port;
    }
    throw new Error(`Could not find an available port between ${preferredPort} and ${lastCandidate}.`);
}

function waitForPort(run, port, timeoutMs) {
    const deadline = Date.now() + timeoutMs;
    return new Promise((resolve, reject) => {
        let settled = false;
        let timer;
        const finish = (error) => {
            if (settled) return;
            settled = true;
            clearTimeout(timer);
            error ? reject(error) : resolve();
        };
        const attempt = () => {
            if (run.exitError) {
                finish(run.exitError);
                return;
            }
            const socket = net.createConnection({ host: "127.0.0.1", port });
            socket.once("connect", () => {
                socket.destroy();
                finish();
            });
            socket.once("error", () => {
                socket.destroy();
                if (Date.now() >= deadline) {
                    finish(new Error(
                        `The local app did not listen on port ${port} within ${Math.round(timeoutMs / 1000)} seconds.` +
                        (run.output ? `\nApp output:\n${run.output}` : "")
                    ));
                } else {
                    timer = setTimeout(attempt, 300);
                }
            });
        };
        attempt();
    });
}

function redactLog(text) {
    return text.replace(/(authorization\s*[:=]\s*)(?:Bearer\s+)?[^\s]+/gi, '$1[redacted]')
        .replace(/((?:password|secret|api[_-]?key|(?:access[_-]?)?token)\s*[:=]\s*)[^\s,;]+/gi, '$1[redacted]')
        .replace(/(https?:\/\/)[^\s/@]+:[^\s/@]+@/gi, '$1[redacted]@');
}
function parseListeningEndpoint(text) {
    const clean = text.replace(/\u001b\[[0-?]*[ -/]*[@-~]/g, '');
    const urls = [...clean.matchAll(/(https?):\/\/(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]):(\d{1,5})(\/[^\s<>"']*)?/g)];
    const match = urls.at(-1);
    if (match) {
        const port = Number(match[2]);
        if (port > 0 && port <= 65535) return { protocol: `${match[1]}:`, port, basePath: match[3] || null };
    }
    const port = Number(clean.match(/(?:listening|running|started)[^\r\n]{0,40}\bport\s*[:=]?\s*(\d{2,5})/i)?.[1]);
    return port > 0 && port <= 65535 ? { port, basePath: null } : null;
}
function probeHttp(url, { signal, hostHeader, timeoutMs = 3000 } = {}) {
    return new Promise((resolve, reject) => {
        let totalTimer;
        const transport = url.startsWith('https:') ? require('node:https') : require('node:http');
        const request = transport.get(url, { signal, headers: hostHeader ? { Host: hostHeader } : {}, timeout: timeoutMs }, response => {
            let body = '';
            response.on('data', chunk => { body += chunk.toString(); if (body.length >= 32768) done(); });
            response.on('end', done);
            response.on('error', error => { clearTimeout(totalTimer); reject(error); });
            function done() { clearTimeout(totalTimer); resolve({ status: response.statusCode, body: body.slice(0, 32768) }); response.destroy(); }
        });
        totalTimer = setTimeout(() => request.destroy(new Error('HTTP readiness request timed out')), timeoutMs);
        request.once('timeout', () => request.destroy(new Error('HTTP readiness connection timed out')));
        request.once('error', error => { clearTimeout(totalTimer); reject(error); });
    });
}
async function waitForHttp(run, timeoutMs) {
    const deadline = Date.now() + timeoutMs;
    let lastDiagnostic = null;
    while (Date.now() < deadline) {
        if (run.stopping || run.abortController.signal.aborted) throw new Error('Local application startup cancelled.');
        if (run.exitError) throw run.exitError;
        try {
            const response = await probeHttp(`${run.protocol}//127.0.0.1:${run.port}${run.basePath}`, {
                signal: run.abortController.signal, hostHeader: `${run.framework === 'next' ? 'localhost' : 'host.docker.internal'}:${run.port}`, timeoutMs: Math.max(1, Math.min(3000, deadline - Date.now()))
            });
            lastDiagnostic = diagnoseResponse({ ...response, framework: run.framework });
            if (lastDiagnostic.category === 'accessible') {
                if (run.exitError || run.child.exitCode !== null) throw run.exitError || new Error('Application process exited during HTTP readiness.');
                let owned;
                try { owned = await ownsListeningPort(run.child.pid, run.port); }
                catch { throw Object.assign(new Error('Could not verify that the responding port belongs to the application process. Check system process-inspection permissions (PowerShell on Windows; lsof/ps on Unix). Checkpoint will not attach to an unrelated service.'), { permanent: true }); }
                if (run.stopping || run.abortController.signal.aborted) throw Object.assign(new Error('Local application startup cancelled.'), { permanent: true });
                if (run.exitError) throw run.exitError;
                if (owned) return response;
                throw Object.assign(new Error(`Port ${run.port} responds, but its listener does not belong to the selected application. Check the actual startup port; no external service was stopped or reused.`), { permanent: true });
            }
            if (['host_blocked', 'tls_certificate', 'proxy_denied'].includes(lastDiagnostic.category)) throw Object.assign(new Error(lastDiagnostic.message), { permanent: true });
        } catch (error) {
            if (error.permanent) throw error;
            lastDiagnostic = diagnoseResponse({ error: error.code || error.message, framework: run.framework });
            if (lastDiagnostic.category === 'tls_certificate') throw new Error(lastDiagnostic.message);
        }
        await new Promise(resolve => {
            const abort = () => { clearTimeout(timer); resolve(); };
            const timer = setTimeout(() => { run.abortController.signal.removeEventListener('abort', abort); resolve(); }, 250);
            run.abortController.signal.addEventListener('abort', abort, { once: true });
        });
    }
    throw new Error(`Application HTTP readiness timed out on port ${run.port}. ${lastDiagnostic?.message || 'No HTTP response was received.'}`);
}

class LocalGuiApp {
    constructor(onEvent = () => {}) {
        this.onEvent = onEvent;
        this.active = null;
    }

    async start(directory, { applicationId = null, timeoutMs = STARTUP_TIMEOUT_MS } = {}) {
        if (this.active || this.starting) {
            throw new Error("A local GUI app is already running.");
        }
        this.starting = true;
        const version = this.startVersion = (this.startVersion || 0) + 1;
        try {

        const projects = adapters.discoverApplications(path.resolve(String(directory || '')));
        const project = applicationId === null ? (projects.length === 1 ? projects[0] : null) : projects.find(p => p.id === String(applicationId));
        if (!project) throw new Error(projects.length ? 'Multiple applications found. Select an application before starting it.' : 'No supported web application entry point or explicit development script was found.');
        const appDirectory = project.directory;
        const framework = project.framework;
        const endpoint = adapters.configuredEndpoint(project);
        const port = endpoint.port || await selectPort(framework);
        if (endpoint.port && !await isPortAvailable(port)) throw new Error(`Configured port ${port} is already occupied. Checkpoint will not stop or reuse an externally managed server.`);
        const plan = adapters.launchPlan(project, port);
        const { script } = plan;
        if (project.runtime === 'node' && Object.keys({ ...project.pkg.dependencies, ...project.pkg.devDependencies }).length) {
            const required = Object.keys({ ...project.pkg.dependencies, ...project.pkg.devDependencies });
            const missing = required.filter(name => {
                if (!/^(?:@[\w.-]+\/)?[\w.-]+$/.test(name)) return true;
                let folder = appDirectory;
                while (true) {
                    if (fs.existsSync(path.join(folder, 'node_modules', name, 'package.json'))) return false;
                    const parent = path.dirname(folder); if (parent === folder) return true; folder = parent;
                }
            });
            if (missing.length) throw new Error(`Node dependencies are not installed (${missing.slice(0, 5).join(', ')}). Install the project dependencies explicitly, then retry. Checkpoint will not install them automatically.`);
        }
        if (plan.pythonModule) {
            try {
                const modules = project.framework === 'fastapi' ? ['uvicorn', 'fastapi'] : [plan.pythonModule];
                await promisify(execFile)(plan.command, ['-c', `import importlib.util,sys;sys.exit(0 if all(importlib.util.find_spec(m) for m in ${JSON.stringify(modules)}) else 1)`], { cwd: appDirectory, env: createAppEnvironment(port, framework), windowsHide: true, timeout: 5000 });
            } catch { throw new Error(`Python dependencies for ${framework} are unavailable in ${plan.command}. Activate/install the intended environment explicitly; no dependencies were installed.`); }
        }
        const shellManager = process.platform === 'win32' && ['npm', 'pnpm'].includes(plan.command);
        if (this.startVersion !== version) throw new Error('Local application startup cancelled.');
        const command = shellManager ? (process.env.ComSpec || 'cmd.exe') : plan.command;
        const argsForCommand = shellManager ? ['/d', '/s', '/c', `${plan.command} ${plan.args.join(' ')}`] : plan.args;
        const child = spawn(command, argsForCommand, {
            cwd: appDirectory,
            env: { ...createAppEnvironment(port, framework), ...(project.runtime === 'static' ? { ELECTRON_RUN_AS_NODE: '1' } : {}) },
            windowsHide: true,
            detached: process.platform !== 'win32',
            stdio: ["ignore", "pipe", "pipe"]
        });
        const run = {
            child,
            appDirectory,
            port,
            framework,
            label: project.label,
            protocol: endpoint.protocol,
            basePath: endpoint.basePath,
            abortController: new AbortController(),
            ready: false,
            script,
            output: "",
            exitError: null,
            stopping: false,
            repairAttempted: false,
            exitPromise: null,
            resolveExit: null,
            stopPromise: null
        };
        run.exitPromise = new Promise(resolve => {
            run.resolveExit = resolve;
        });
        this.active = run;

        const appendOutput = (stream, chunk) => {
            const text = redactLog(chunk.toString());
            if (!run.ready) {
                const detected = parseListeningEndpoint(run.output + text);
                if (detected) { run.port = detected.port; run.protocol = detected.protocol || run.protocol; run.basePath = detected.basePath || run.basePath; }
            }
            run.output = (run.output + text).slice(-MAX_LOG_BYTES);
            this.onEvent({
                type: "local_app_log",
                stream,
                message: text.slice(-4000)
            });
        };
        child.stdout?.on("data", chunk => appendOutput("stdout", chunk));
        child.stderr?.on("data", chunk => appendOutput("stderr", chunk));
        child.once("error", error => {
            run.exitError = new Error(`Could not start ${plan.command}: ${error.message}`);
            run.resolveExit({ error: run.exitError });
        });
        child.once("close", (code, signal) => {
            if (!run.stopping) {
                run.exitError = new Error(
                    `The local app process exited ${run.ready ? 'while managed by Checkpoint' : 'before startup completed'} (code ${code ?? "unknown"}${signal ? `, ${signal}` : ""}).` +
                    (run.output ? `\nApp output:\n${run.output}` : "")
                );
            }
            run.resolveExit({ code, signal, error: run.exitError });
            if (this.active === run && !run.stopping) this.active = null;
            this.onEvent({
                type: "local_app_exit",
                message: run.stopping ? "Local app stopped." : run.exitError.message
            });
        });

        try {
            this.onEvent({ type: 'local_app_status', message: `Framework: ${project.label}. Starting application...` });
            await waitForHttp(run, timeoutMs);
            if (this.startVersion !== version || run.stopping) throw new Error('Local application startup cancelled.');
            run.ready = true;
            this.onEvent({
                type: "local_app_started",
                framework,
                port: run.port,
                message: `Detected ${project.label}. Application ready on port ${run.port}.`
            });
            return { port: run.port, script, framework, label: project.label, protocol: run.protocol, basePath: run.basePath,
                command: `${plan.command} ${plan.args.join(' ')}`, appDirectory,
                dockerUrl: `${run.protocol}//host.docker.internal:${run.port}${run.basePath}` };
        } catch (error) {
            await this.stop();
            throw error;
        }
        } finally { this.starting = false; }
    }

    stop() {
        this.startVersion = (this.startVersion || 0) + 1;
        const run = this.active;
        if (!run) return Promise.resolve(false);
        if (run.stopPromise) return run.stopPromise;
        run.stopPromise = this.stopRun(run);
        return run.stopPromise;
    }

    async stopRun(run) {
        run.stopping = true;
        run.abortController?.abort();
        if (run.child.exitCode !== null || run.child.signalCode !== null) {
            await run.exitPromise;
            if (this.active === run) this.active = null;
            return true;
        }

        if (process.platform === "win32" && run.child.pid) {
            await new Promise((resolve, reject) => {
                const killer = spawn("taskkill.exe", ["/PID", String(run.child.pid), "/T", "/F"], {
                    windowsHide: true,
                    stdio: "ignore"
                });
                killer.once("error", reject);
                killer.once("close", code => {
                    if (code === 0 || run.child.exitCode !== null || run.child.signalCode !== null) resolve();
                    else reject(new Error(`Could not stop the local app process tree (taskkill exit code ${code}).`));
                });
            });
        } else {
            try { process.kill(-run.child.pid, 'SIGTERM'); } catch { run.child.kill('SIGTERM'); }
        }

        let stopTimer;
        try { await Promise.race([
            run.exitPromise,
            new Promise((_, reject) => { stopTimer = setTimeout(
                () => reject(new Error("Timed out while stopping the local app process.")),
                10000
            ); })
        ]); } finally { clearTimeout(stopTimer); }
        if (this.active === run) this.active = null;
        return true;
    }
}

module.exports = {
    LocalGuiApp,
    createAppEnvironment,
    detectFramework,
    getPreferredPort,
    getLaunchOptions,
    isPortAvailable,
    parseListeningEndpoint, probeHttp, redactLog, waitForHttp,
    discoverApplications: adapters.discoverApplications
};
