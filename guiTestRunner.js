const { spawn } = require("child_process");
const crypto = require("crypto");
const fs = require("fs");
const net = require("net");
const os = require("os");
const path = require("path");
const { domainToASCII } = require("url");

const IMAGE_VERSION = require("./package.json").version;
const MAX_FILE_BYTES = 2 * 1024 * 1024;
const MAX_TOTAL_BYTES = 20 * 1024 * 1024;
const MAX_FILES = 20;
const ALLOWED_EXTENSIONS = new Set([".js", ".ts"]);

function dockerContextFingerprint(directory) {
    const hash = crypto.createHash("sha256");
    const addDirectory = currentDirectory => {
        const entries = fs.readdirSync(currentDirectory, { withFileTypes: true })
            .sort((left, right) => left.name.localeCompare(right.name));
        for (const entry of entries) {
            const entryPath = path.join(currentDirectory, entry.name);
            if (entry.isDirectory()) {
                addDirectory(entryPath);
            } else if (entry.isFile()) {
                const relativePath = path.relative(directory, entryPath).split(path.sep).join("/");
                hash.update(relativePath);
                hash.update("\0");
                hash.update(fs.readFileSync(entryPath));
            }
        }
    };
    addDirectory(directory);
    return hash.digest("hex").slice(0, 12);
}

function runProcess(command, args, options = {}) {
    return new Promise((resolve, reject) => {
        const { timeoutMs, onStdout, onStderr, ...spawnOptions } = options;
        const child = spawn(command, args, {
            windowsHide: true,
            ...spawnOptions
        });
        let stdout = "";
        let stderr = "";
        let settled = false;

        const timeout = timeoutMs
            ? setTimeout(() => {
                child.kill();
                if (!settled) {
                    settled = true;
                    reject(new Error(`${command} timed out.`));
                }
            }, timeoutMs)
            : null;

        child.stdout?.on("data", data => {
            stdout = (stdout + data.toString()).slice(-1024 * 1024);
            onStdout?.(data.toString());
        });
        child.stderr?.on("data", data => {
            stderr = (stderr + data.toString()).slice(-1024 * 1024);
            onStderr?.(data.toString());
        });
        child.once("error", error => {
            if (settled) return;
            settled = true;
            if (timeout) clearTimeout(timeout);
            reject(error);
        });
        child.once("close", code => {
            if (settled) return;
            settled = true;
            if (timeout) clearTimeout(timeout);
            if (code === 0) {
                resolve({ stdout: stdout.trim(), stderr: stderr.trim() });
            } else {
                reject(new Error(stderr.trim() || stdout.trim() || `${command} exited with code ${code}.`));
            }
        });
    });
}

function validateGuiTestFiles(files, existingRelativePaths = new Set()) {
    if (!Array.isArray(files) || files.length === 0) {
        throw new Error("Choose at least one JavaScript or TypeScript test file.");
    }
    if (files.length > MAX_FILES) {
        throw new Error(`Select no more than ${MAX_FILES} files at a time.`);
    }

    let totalBytes = 0;
    const relativePaths = new Set(existingRelativePaths);
    const validated = [];

    for (const file of files) {
        const extension = path.extname(file.path).toLowerCase();
        if (!ALLOWED_EXTENSIONS.has(extension)) {
            throw new Error(`Unsupported file "${path.basename(file.path)}". Choose .js or .ts files.`);
        }
        if (!file.stat.isFile() || file.stat.isSymbolicLink()) {
            throw new Error(`"${path.basename(file.path)}" is not a regular file.`);
        }
        if (file.stat.size === 0) {
            throw new Error(`"${path.basename(file.path)}" is empty.`);
        }
        if (file.stat.size > MAX_FILE_BYTES) {
            throw new Error(`"${path.basename(file.path)}" exceeds the 2 MB per-file limit.`);
        }

        totalBytes += file.stat.size;
        if (totalBytes > MAX_TOTAL_BYTES) {
            throw new Error("The selected files exceed the 20 MB upload limit.");
        }

        const relativePath = file.relativePath.split(path.sep).join("/");
        if (
            !relativePath ||
            relativePath === ".." ||
            relativePath.startsWith("../") ||
            path.isAbsolute(relativePath) ||
            relativePaths.has(relativePath.toLowerCase())
        ) {
            throw new Error(`The selected files contain a duplicate or invalid path: "${relativePath}".`);
        }

        relativePaths.add(relativePath.toLowerCase());
        validated.push({ ...file, relativePath });
    }

    return validated;
}

function normalizeAllowedHosts(hostEntries) {
    if (!Array.isArray(hostEntries) || hostEntries.length === 0) {
        throw new Error("Enter at least one allowed website hostname.");
    }
    if (hostEntries.length > 30) {
        throw new Error("Enter no more than 30 allowed website hosts.");
    }

    const destinations = new Map();
    for (const entry of hostEntries) {
        const value = String(entry || "").trim();
        const match = value.match(/^(\*\.)?([^:/\s]+)(?::(\d{1,5}))?$/u);
        if (!match) {
            throw new Error(`Invalid allowed host "${value}". Enter a hostname, optionally followed by :port.`);
        }

        const hostname = domainToASCII(match[2]).toLowerCase();
        const labels = hostname.split(".");
        const validLabels = labels.every(label =>
            label.length > 0 &&
            label.length <= 63 &&
            !label.startsWith("-") &&
            !label.endsWith("-") &&
            /^[a-z0-9-]+$/.test(label)
        );
        if (
            !hostname ||
            net.isIP(hostname) ||
            !validLabels ||
            (labels.length < 2 && hostname !== "host.docker.internal") ||
            hostname === "localhost" ||
            hostname.endsWith(".localhost") ||
            hostname.endsWith(".local") ||
            (match[1] && hostname === "host.docker.internal")
        ) {
            throw new Error(`Host "${value}" is invalid or blocked. Use a public hostname or host.docker.internal.`);
        }

        const port = match[3] ? Number(match[3]) : null;
        if (port !== null && (port < 1 || port > 65535)) {
            throw new Error(`Port in "${value}" must be between 1 and 65535.`);
        }

        if (!destinations.has(hostname)) {
            destinations.set(hostname, { ports: new Set(), includeSubdomains: false });
        }
        const destination = destinations.get(hostname);
        if (match[1]) destination.includeSubdomains = true;
        const ports = destination.ports;
        if (port === null) {
            ports.add(80);
            ports.add(443);
        } else {
            if (hostname !== "host.docker.internal") {
                ports.add(80);
                ports.add(443);
            }
            ports.add(port);
        }
    }

    return Array.from(destinations, ([hostname, destination]) => ({
        hostname,
        ports: Array.from(destination.ports).sort((a, b) => a - b),
        includeSubdomains: destination.includeSubdomains
    }));
}

function createProxyConfig(hostEntries) {
    const destinations = normalizeAllowedHosts(hostEntries);
    const lines = [
        "visible_hostname checkpoint-gui-proxy",
        "http_port 3128",
        "cache deny all",
        "cache_log /dev/stderr",
        "access_log stdio:/dev/stdout",
        "pid_filename none",
        "coredump_dir /tmp",
        "acl private_destination dst 0.0.0.0/8 10.0.0.0/8 100.64.0.0/10 127.0.0.0/8 169.254.0.0/16 172.16.0.0/12 192.0.0.0/24 192.0.2.0/24 192.168.0.0/16 198.18.0.0/15 198.51.100.0/24 203.0.113.0/24 224.0.0.0/4 240.0.0.0/4 ::/128 ::1/128 fc00::/7 fe80::/10 ff00::/8",
        "acl docker_host dstdomain host.docker.internal",
        "http_access deny private_destination !docker_host"
    ];

    destinations.forEach(({ hostname, ports, includeSubdomains }, index) => {
        const domainAcl = `allowed_domain_${index}`;
        const portAcl = `allowed_port_${index}`;
        const domains = includeSubdomains ? [hostname, `.${hostname}`] : [hostname];
        lines.push(`acl ${domainAcl} dstdomain ${domains.join(" ")}`);
        lines.push(`acl ${portAcl} port ${ports.join(" ")}`);
        lines.push(`http_access allow ${domainAcl} ${portAcl}`);
    });
    lines.push("http_access deny all");

    return `${lines.join("\n")}\n`;
}

function commonDirectory(paths) {
    const folders = paths.map(filePath => path.dirname(path.resolve(filePath)));
    let root = folders[0];

    for (const folder of folders.slice(1)) {
        while (!isWithinDirectory(root, folder)) {
            const parent = path.dirname(root);
            if (parent === root) break;
            root = parent;
        }
    }

    return root;
}

function isWithinDirectory(root, target) {
    const relative = path.relative(root, target);
    return !path.isAbsolute(relative) &&
        relative !== ".." &&
        !relative.startsWith(`..${path.sep}`);
}

function waitForPort(port, timeoutMs = 15000, signal) {
    const deadline = Date.now() + timeoutMs;

    return new Promise((resolve, reject) => {
        let retryTimer = null;
        let currentSocket = null;
        const abort = () => {
            if (retryTimer) clearTimeout(retryTimer);
            currentSocket?.destroy();
            reject(new Error("GUI test run was stopped during startup."));
        };
        if (signal?.aborted) {
            abort();
            return;
        }
        signal?.addEventListener("abort", abort, { once: true });

        const attempt = () => {
            if (signal?.aborted) return;
            const socket = net.createConnection({ host: "127.0.0.1", port });
            currentSocket = socket;
            socket.once("connect", () => {
                signal?.removeEventListener("abort", abort);
                socket.destroy();
                resolve();
            });
            socket.once("error", () => {
                socket.destroy();
                if (Date.now() >= deadline) {
                    signal?.removeEventListener("abort", abort);
                    reject(new Error(`GUI test service did not open port ${port}.`));
                } else {
                    retryTimer = setTimeout(attempt, 200);
                }
            });
        };
        attempt();
    });
}

class GuiTestRunner {
    constructor({ appRoot, resourcesPath, isPackaged, onEvent }) {
        this.appRoot = appRoot;
        this.resourcesPath = resourcesPath;
        this.isPackaged = isPackaged;
        this.onEvent = onEvent;
        this.uploadedFiles = new Map();
        this.imageReady = false;
        this.imageNames = null;
        this.activeRun = null;
    }

    get imageContext() {
        return this.isPackaged
            ? path.join(this.resourcesPath, "gui-runner")
            : path.join(this.appRoot, "gui-runner");
    }

    get proxyContext() {
        return this.isPackaged
            ? path.join(this.resourcesPath, "gui-proxy")
            : path.join(this.appRoot, "gui-proxy");
    }

    listFiles() {
        return Array.from(this.uploadedFiles.values()).map(({ id, relativePath, size }) => ({
            id,
            name: relativePath,
            size
        }));
    }

    addFiles(filePaths) {
        if (!Array.isArray(filePaths) || filePaths.length === 0) {
            throw new Error("Choose at least one JavaScript or TypeScript test file.");
        }
        if (this.uploadedFiles.size + filePaths.length > MAX_FILES) {
            throw new Error(`Upload no more than ${MAX_FILES} files total.`);
        }
        const previousFiles = Array.from(this.uploadedFiles.values());
        const root = commonDirectory([
            ...previousFiles.map(file => file.sourcePath),
            ...filePaths
        ]);
        const previousRelativePaths = new Map(
            previousFiles.map(file => [
                file.id,
                path.relative(root, file.sourcePath).split(path.sep).join("/")
            ])
        );
        const validPaths = filePaths.map(filePath => {
            let stat;
            try {
                stat = fs.lstatSync(filePath);
            } catch (error) {
                throw new Error(`Cannot read "${path.basename(filePath)}": ${error.message}`);
            }
            return { path: filePath, stat };
        });
        const prepared = validPaths.map(file => ({
            ...file,
            relativePath: path.relative(root, file.path)
        }));
        const existingPaths = new Set(
            Array.from(previousRelativePaths.values(), relativePath => relativePath.toLowerCase())
        );
        const validated = validateGuiTestFiles(prepared, existingPaths);
        const existingBytes = Array.from(this.uploadedFiles.values())
            .reduce((total, file) => total + file.size, 0);
        const selectedBytes = validated.reduce((total, file) => total + file.stat.size, 0);
        if (existingBytes + selectedBytes > MAX_TOTAL_BYTES) {
            throw new Error("All uploaded files together must stay under 20 MB.");
        }

        const uploads = validated.map(file => {
            const content = fs.readFileSync(file.path);
            if (content.length !== file.stat.size || content.length > MAX_FILE_BYTES) {
                throw new Error(`"${path.basename(file.path)}" changed while it was being uploaded. Please select it again.`);
            }
            return { file, content };
        });

        for (const file of previousFiles) {
            file.relativePath = previousRelativePaths.get(file.id);
        }
        for (const { file, content } of uploads) {
            const id = crypto.randomUUID();
            this.uploadedFiles.set(id, {
                id,
                content,
                sourcePath: file.path,
                relativePath: file.relativePath,
                size: content.length
            });
        }

        return this.listFiles();
    }

    async ensureImage(signal) {
        if (this.imageReady) return;

        const images = [
            { key: "runner", prefix: "checkpoint-gui-runner", context: this.imageContext },
            { key: "proxy", prefix: "checkpoint-gui-proxy", context: this.proxyContext }
        ].map(image => ({
            ...image,
            name: `${image.prefix}:${IMAGE_VERSION}-${dockerContextFingerprint(image.context)}`
        }));
        this.imageNames = Object.fromEntries(images.map(image => [image.key, image.name]));

        try {
            await runProcess("docker", ["info", "--format", "{{.ServerVersion}}"], {
                timeoutMs: 15000,
                signal
            });
        } catch (error) {
            throw new Error(`Docker is unavailable or not running: ${error.message}`);
        }

        for (const image of images) {
            try {
                await runProcess("docker", ["image", "inspect", image.name], { timeoutMs: 15000, signal });
            } catch {
                this.onEvent({ type: "runner_log", message: "Preparing the isolated Docker images. The first run may take several minutes." });
                await runProcess(
                    "docker",
                    ["build", "--tag", image.name, "--file", path.join(image.context, "Dockerfile"), image.context],
                    { timeoutMs: 15 * 60 * 1000, signal }
                );
            }
        }

        this.imageReady = true;
    }

    async start(selectedIds, allowedHosts) {
        if (this.activeRun) {
            throw new Error("A GUI test run is already active.");
        }
        if (!Array.isArray(selectedIds) || selectedIds.length === 0) {
            throw new Error("Select at least one test file to run.");
        }
        if (new Set(selectedIds).size !== selectedIds.length) {
            throw new Error("The selected test files contain a duplicate entry.");
        }

        const selectedFiles = selectedIds.map(id => this.uploadedFiles.get(id));
        if (selectedFiles.some(file => !file)) {
            throw new Error("The selection includes a file that is no longer available. Upload it again.");
        }
        const proxyConfig = createProxyConfig(allowedHosts);

        const stagingDirectory = fs.mkdtempSync(path.join(os.tmpdir(), "checkpoint-gui-tests-"));
        const proxyDirectory = fs.mkdtempSync(path.join(os.tmpdir(), "checkpoint-gui-proxy-"));
        const name = `checkpoint-gui-${crypto.randomUUID().replace(/-/g, "").slice(0, 20)}`;
        const run = {
            name,
            proxyName: `${name}-egress`,
            networkName: `${name}-net`,
            stagingDirectory,
            proxyDirectory,
            configPath: path.join(proxyDirectory, "squid.conf"),
            eventToken: crypto.randomBytes(24).toString("hex"),
            vncPassword: crypto.randomBytes(6).toString("base64url"),
            abortController: new AbortController(),
            cancelled: false,
            containerId: null,
            proxyContainerId: null
        };
        this.activeRun = run;
        const allFiles = Array.from(this.uploadedFiles.values());
        const selectedPaths = [];

        try {
            fs.writeFileSync(run.configPath, proxyConfig, "utf8");
            for (const file of allFiles) {
                const destination = path.join(stagingDirectory, ...file.relativePath.split("/"));
                const relative = path.relative(stagingDirectory, destination);
                if (
                    relative === ".." ||
                    relative.startsWith(`..${path.sep}`) ||
                    path.isAbsolute(relative)
                ) {
                    throw new Error("A test file has an unsafe relative path.");
                }
                fs.mkdirSync(path.dirname(destination), { recursive: true });
                fs.writeFileSync(destination, file.content, { flag: "wx" });
                if (selectedIds.includes(file.id)) selectedPaths.push(file.relativePath);
            }

            this.onEvent({ type: "runner_status", status: "starting", message: "Starting isolated Playwright browser..." });
            await this.ensureImage(run.abortController.signal);
            this.assertNotCancelled(run);

            await runProcess("docker", ["network", "create", "--driver", "bridge", "--internal", run.networkName], {
                timeoutMs: 15000,
                signal: run.abortController.signal
            });
            this.assertNotCancelled(run);

            const configMount = `type=bind,source=${run.configPath},target=/etc/squid/squid.conf,readonly`;
            const proxy = await runProcess("docker", [
                "run", "--detach", "--init",
                "--name", run.proxyName,
                "--network", "bridge",
                "--publish", "127.0.0.1::16080",
                "--publish", "127.0.0.1::13001",
                "--mount", configMount,
                "--read-only",
                "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m",
                "--memory", "256m",
                "--cpus", "0.5",
                "--pids-limit", "128",
                "--security-opt", "no-new-privileges",
                "--cap-drop", "ALL",
                "--user", "13:13",
                this.imageNames.proxy
            ], { timeoutMs: 30000, signal: run.abortController.signal });
            run.proxyContainerId = proxy.stdout.split(/\s+/)[0];
            this.assertNotCancelled(run);

            try {
                await runProcess("docker", [
                    "network", "connect", "--alias", "gui-egress", run.networkName, run.proxyContainerId
                ], { timeoutMs: 15000, signal: run.abortController.signal });
            } catch (error) {
                const logs = await this.getContainerLogs(run.proxyContainerId);
                throw new Error(`Could not attach the egress proxy to the test network: ${error.message}${logs}`);
            }
            await this.assertContainerRunning(run.proxyContainerId, "egress proxy");
            this.assertNotCancelled(run);

            const mount = `type=bind,source=${stagingDirectory},target=/opt/gui-runner/tests,readonly`;
            const result = await runProcess("docker", [
                "run", "--detach", "--init",
                "--name", run.name,
                "--network", run.networkName,
                "--network-alias", "gui-runner",
                "--mount", mount,
                "--read-only",
                "--tmpfs", "/tmp:rw,exec,nosuid,nodev,size=1g",
                "--shm-size", "512m",
                "--memory", "2g",
                "--memory-swap", "2g",
                "--cpus", "2",
                "--pids-limit", "256",
                "--security-opt", "no-new-privileges",
                "--cap-drop", "ALL",
                "--user", "10001:10001",
                "--env", `GUI_SELECTED_FILES=${JSON.stringify(selectedPaths)}`,
                "--env", "HOME=/tmp",
                "--env", "HTTP_PROXY=http://gui-egress:3128",
                "--env", "HTTPS_PROXY=http://gui-egress:3128",
                "--env", "NO_PROXY=localhost,127.0.0.1,::1",
                "--env", `GUI_EVENT_TOKEN=${run.eventToken}`,
                "--env", `VNC_PASSWORD=${run.vncPassword}`,
                this.imageNames.runner
            ], { timeoutMs: 30000, signal: run.abortController.signal });

            run.containerId = result.stdout.split(/\s+/)[0];
            this.assertNotCancelled(run);
            const vncPort = await this.getPublishedPort(run.proxyContainerId, "16080/tcp");
            const eventsPort = await this.getPublishedPort(run.proxyContainerId, "13001/tcp");
            await Promise.all([
                waitForPort(vncPort, 15000, run.abortController.signal),
                waitForPort(eventsPort, 15000, run.abortController.signal)
            ]);
            await this.waitForRunnerPorts(run);

            this.assertNotCancelled(run);
            this.monitorRun(run);
            const previewHash = new URLSearchParams({ password: run.vncPassword }).toString();
            return {
                runId: run.name,
                previewUrl: `http://127.0.0.1:${vncPort}/gui-preview.html#${previewHash}`,
                eventsUrl: `ws://127.0.0.1:${eventsPort}/${run.eventToken}`
            };
        } catch (error) {
            await this.cleanupRun(run);
            if (this.activeRun === run) this.activeRun = null;
            if (run.cancelled) throw new Error("GUI test run was stopped during startup.");
            throw error;
        }
    }

    assertNotCancelled(run) {
        if (run.cancelled) throw new Error("GUI test run was stopped during startup.");
    }

    async assertContainerRunning(containerId, label) {
        const result = await runProcess("docker", [
            "inspect", "--format", "{{.State.Running}}", containerId
        ], { timeoutMs: 10000 });
        if (result.stdout !== "true") {
            const logs = await this.getContainerLogs(containerId);
            throw new Error(`The ${label} container exited during startup.${logs}`);
        }
    }

    async getContainerLogs(containerId) {
        if (!containerId) return "";
        try {
            const result = await runProcess("docker", ["logs", "--tail", "40", containerId], { timeoutMs: 10000 });
            const output = [result.stdout, result.stderr].filter(Boolean).join("\n").trim();
            return output ? `\nContainer output:\n${output}` : "";
        } catch (error) {
            return `\nCould not retrieve container output: ${error.message}`;
        }
    }

    async getPublishedPort(containerId, containerPort) {
        let result;
        try {
            result = await runProcess("docker", ["port", containerId, containerPort], { timeoutMs: 10000 });
        } catch (error) {
            const logs = await this.getContainerLogs(containerId);
            throw new Error(`Docker did not publish ${containerPort}: ${error.message}${logs}`);
        }
        const match = result.stdout.match(/:(\d+)\s*$/);
        if (!match) {
            const logs = await this.getContainerLogs(containerId);
            throw new Error(`Docker returned no host port for ${containerPort}.${logs}`);
        }
        return Number(match[1]);
    }

    async waitForRunnerPorts(run) {
        const deadline = Date.now() + 15000;
        const readinessCheck = [
            "const net = require('node:net');",
            "Promise.all(process.argv.slice(1).map(value => new Promise((resolve, reject) => {",
            "const socket = net.createConnection({ host: '127.0.0.1', port: Number(value) });",
            "socket.once('connect', () => { socket.destroy(); resolve(); });",
            "socket.once('error', reject);",
            "}))).then(() => process.exit(0), () => process.exit(1));"
        ].join("");

        while (Date.now() < deadline) {
            this.assertNotCancelled(run);
            try {
                await runProcess("docker", [
                    "exec", run.containerId, "node", "-e", readinessCheck, "6080", "3001"
                ], { timeoutMs: 3000, signal: run.abortController.signal });
                return;
            } catch (error) {
                this.assertNotCancelled(run);
                await this.assertContainerRunning(run.containerId, "Playwright");
                await new Promise(resolve => setTimeout(resolve, 200));
            }
        }

        const logs = await this.getContainerLogs(run.containerId);
        throw new Error(`The Playwright preview services did not become ready.${logs}`);
    }

    monitorRun(run) {
        run.monitor = runProcess("docker", ["wait", run.containerId], { timeoutMs: 24 * 60 * 60 * 1000 })
            .then(({ stdout }) => {
                const exitCode = Number(stdout);
                this.onEvent({
                    type: "runner_container_exit",
                    exitCode,
                    message: exitCode === 0 ? "Playwright container finished." : `Playwright container exited with code ${exitCode}.`
                });
            })
            .catch(error => {
                if (!run.cancelled) {
                    this.onEvent({ type: "runner_error", message: `Could not monitor the test container: ${error.message}` });
                }
            })
            .finally(async () => {
                await this.cleanupRun(run);
                if (this.activeRun === run) this.activeRun = null;
            });
    }

    async cleanupRun(run) {
        const removeContainer = async name => {
            try {
                await runProcess("docker", ["rm", "--force", name], { timeoutMs: 10000 });
            } catch (error) {
                if (!/no such container|not found/i.test(error.message)) {
                    this.onEvent({ type: "runner_log", message: `Container cleanup warning: ${error.message}` });
                }
            }
        };
        await Promise.all([removeContainer(run.name), removeContainer(run.proxyName)]);
        try {
            await runProcess("docker", ["network", "rm", run.networkName], { timeoutMs: 10000 });
        } catch (error) {
            if (!/no such network|not found/i.test(error.message)) {
                this.onEvent({ type: "runner_log", message: `Network cleanup warning: ${error.message}` });
            }
        }
        fs.rmSync(run.stagingDirectory, { recursive: true, force: true });
        fs.rmSync(run.proxyDirectory, { recursive: true, force: true });
    }

    async stop() {
        const run = this.activeRun;
        if (!run) return false;

        run.cancelled = true;
        run.abortController.abort();
        this.onEvent({ type: "runner_status", status: "stopping", message: "Stopping the GUI test run..." });
        await this.cleanupRun(run);
        if (run.monitor) await run.monitor;
        if (this.activeRun === run) this.activeRun = null;
        this.onEvent({ type: "runner_status", status: "stopped", message: "GUI test run stopped." });
        return true;
    }

    async dispose() {
        await this.stop();
        this.uploadedFiles.clear();
    }
}

module.exports = {
    GuiTestRunner,
    createProxyConfig,
    normalizeAllowedHosts,
    validateGuiTestFiles
};
