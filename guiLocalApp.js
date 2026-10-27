const { spawn } = require("child_process");
const fs = require("fs");
const net = require("net");
const path = require("path");

const STARTUP_TIMEOUT_MS = 120000;
const MAX_LOG_BYTES = 32 * 1024;
const HOSTNAME_ALLOWED_FOR_VITE = "host.docker.internal";

function detectFramework(packageJson) {
    const scripts = packageJson.scripts || {};
    const dependencies = {
        ...packageJson.dependencies,
        ...packageJson.devDependencies
    };
    const scriptText = Object.values(scripts).join(" ").toLowerCase();
    if (dependencies.vite || /\bvite(?:\s|$)/.test(scriptText)) return "vite";
    if (dependencies.next || /\bnext\s+(?:dev|start)\b/.test(scriptText)) return "next";
    if (dependencies["@angular/cli"] || /\bng\s+serve\b/.test(scriptText)) return "angular";
    if (dependencies["@vue/cli-service"] || /\bvue-cli-service\s+serve\b/.test(scriptText)) return "vue-cli";
    if (dependencies["react-scripts"] || /\breact-scripts\s+start\b/.test(scriptText)) return "create-react-app";
    return "node";
}

function getPreferredPort(framework) {
    return {
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
        ...(framework === "vite"
            ? { __VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS: HOSTNAME_ALLOWED_FOR_VITE }
            : {})
    };
}

function isPortAvailable(port) {
    return new Promise((resolve, reject) => {
        const server = net.createServer();
        server.once("error", error => {
            if (error.code === "EADDRINUSE") resolve(false);
            else reject(new Error(`Could not check local app port ${port}: ${error.message}`));
        });
        server.listen(port, "0.0.0.0", () => server.close(error => {
            if (error) reject(new Error(`Could not release local app port ${port}: ${error.message}`));
            else resolve(true);
        }));
    });
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

class LocalGuiApp {
    constructor(onEvent = () => {}) {
        this.onEvent = onEvent;
        this.active = null;
    }

    async start(directory) {
        if (this.active) {
            throw new Error("A local GUI app is already running.");
        }

        const appDirectory = path.resolve(String(directory || ""));

        let packageJson;
        try {
            packageJson = JSON.parse(fs.readFileSync(path.join(appDirectory, "package.json"), "utf8"));
        } catch (error) {
            throw new Error(`A valid package.json is required in the selected app folder: ${error.message}`);
        }
        if (!packageJson || typeof packageJson !== "object" || Array.isArray(packageJson)) {
            throw new Error("The selected folder's package.json must contain a JSON object.");
        }

        const framework = detectFramework(packageJson);
        const port = await selectPort(framework);
        const { script, args } = getLaunchOptions(packageJson, framework, port);

        const commandArgs = ["run", script, ...args];
        const command = process.platform === "win32"
            ? (process.env.ComSpec || "cmd.exe")
            : "npm";
        const argsForCommand = process.platform === "win32"
            ? ["/d", "/s", "/c", `npm ${commandArgs.join(" ")}`]
            : commandArgs;
        const child = spawn(command, argsForCommand, {
            cwd: appDirectory,
            env: createAppEnvironment(port, framework),
            windowsHide: true,
            stdio: ["ignore", "pipe", "pipe"]
        });
        const run = {
            child,
            appDirectory,
            port,
            framework,
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
            const text = chunk.toString();
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
            run.exitError = new Error(`Could not start npm: ${error.message}`);
            run.resolveExit({ error: run.exitError });
        });
        child.once("close", (code, signal) => {
            if (!run.stopping) {
                run.exitError = new Error(
                    `The local app process exited before tests could start (code ${code ?? "unknown"}${signal ? `, ${signal}` : ""}).` +
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
            await waitForPort(run, port, STARTUP_TIMEOUT_MS);
            this.onEvent({
                type: "local_app_started",
                framework,
                port,
                message: `Detected ${framework}. Local app is listening on port ${port} (npm run ${script}).`
            });
            return { port, script, framework, appDirectory };
        } catch (error) {
            await this.stop();
            throw error;
        }
    }

    stop() {
        const run = this.active;
        if (!run) return Promise.resolve(false);
        if (run.stopPromise) return run.stopPromise;
        run.stopPromise = this.stopRun(run);
        return run.stopPromise;
    }

    async stopRun(run) {
        run.stopping = true;
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
            run.child.kill("SIGTERM");
        }

        await Promise.race([
            run.exitPromise,
            new Promise((_, reject) => setTimeout(
                () => reject(new Error("Timed out while stopping the local app process.")),
                10000
            ))
        ]);
        if (this.active === run) this.active = null;
        return true;
    }
}

module.exports = {
    LocalGuiApp,
    createAppEnvironment,
    detectFramework,
    getPreferredPort,
    getLaunchOptions
};
