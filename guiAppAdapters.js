const fs = require('node:fs');
const path = require('node:path');
const { createHash } = require('node:crypto');

const ADAPTERS = {
    vite: { port: 5173, match: /(?:^|\s)vite(?:\s|$)/, args: p => ['--host', '0.0.0.0', '--port', String(p), '--strictPort'] },
    next: { port: 3000, match: /(?:^|\s)next\s+(?:dev|start)\b/, args: p => ['--hostname', '0.0.0.0', '--port', String(p)] },
    angular: { port: 4200, match: /(?:^|\s)ng\s+serve\b/, args: p => ['--host', '0.0.0.0', '--port', String(p), '--allowed-hosts', 'host.docker.internal'] },
    nuxt: { port: 3000, match: /(?:^|\s)(?:nuxt|nuxi)\s+dev\b/, args: p => ['--host', '0.0.0.0', '--port', String(p)] },
    astro: { port: 4321, match: /(?:^|\s)astro\s+dev\b/, args: p => ['--host', '0.0.0.0', '--port', String(p)] },
    'vue-cli': { port: 8080, match: /(?:^|\s)vue-cli-service\s+serve\b/, args: p => ['--host', '0.0.0.0', '--port', String(p)] },
    'create-react-app': { port: 3000, match: /(?:^|\s)react-scripts\s+start\b/, args: () => [] },
    'react-router': { port: 5173, match: /(?:^|\s)(?:react-router\s+dev|remix\s+vite:dev)\b/, args: p => ['--host', '0.0.0.0', '--port', String(p)] },
    remix: { port: 3000, match: /(?:^|\s)remix\s+dev\b/, args: () => [] },
    node: { port: 3000 }, express: { port: 3000 }, fastify: { port: 3000 }, nest: { port: 3000, match: /(?:^|\s)nest\s+start\b/, args: () => [] },
    flask: { port: 5000 }, django: { port: 8000 }, fastapi: { port: 8000 }, streamlit: { port: 8501 }, gradio: { port: 7860 }, static: { port: 8080 }
};
const SKIP = new Set(['node_modules', '.git', '.venv', 'venv', 'dist', 'build', '.next', '.nuxt', '__pycache__', 'coverage']);
function read(file) { try { return fs.readFileSync(file, 'utf8').slice(0, 100000); } catch { return ''; } }
function selectedScript(pkg) {
    return ['dev', 'start', 'serve', 'server', 'preview'].find(key => typeof pkg.scripts?.[key] === 'string') || null;
}
function detectNode(pkg) {
    const script = selectedScript(pkg);
    const command = script ? pkg.scripts[script] : '';
    // The command selected for execution wins over dependencies used by unrelated tools.
    for (const [name, adapter] of Object.entries(ADAPTERS)) if (adapter.match?.test(command)) return name;
    const deps = { ...pkg.dependencies, ...pkg.devDependencies };
    if (script) {
        if (/\bnode\b|\btsx\b|\bts-node\b|\bnodemon\b/.test(command)) {
            if (deps['@nestjs/core']) return 'nest';
            if (deps.fastify) return 'fastify';
            if (deps.express) return 'express';
        }
        return 'node';
    }
    // Metadata-only detection is descriptive; resolveProject still requires a launch command.
    for (const [dep, framework] of [['next','next'], ['@angular/cli','angular'], ['@vue/cli-service','vue-cli'], ['react-scripts','create-react-app'], ['vite','vite']]) if (deps[dep]) return framework;
    return 'node';
}
function labelFor(framework, pkg = {}) {
    const deps = { ...pkg.dependencies, ...pkg.devDependencies };
    if (framework === 'vite') {
        if (deps['@sveltejs/kit']) return 'SvelteKit (Vite)';
        if (deps.svelte) return 'Svelte (Vite)';
        if (deps.react) return 'React + Vite';
        if (deps.vue) return 'Vue + Vite';
        if (deps['solid-js']) return 'SolidJS (Vite)';
    }
    return framework;
}
function pythonEntries(directory) {
    const dependencyText = read(path.join(directory, 'requirements.txt')) + '\n' + read(path.join(directory, 'pyproject.toml'));
    if (fs.existsSync(path.join(directory, 'manage.py')) && /\bdjango\b/i.test(dependencyText + read(path.join(directory, 'manage.py')))) return [{ framework: 'django', entry: 'manage.py' }];
    const files = [];
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
        if (entry.isFile() && /^[A-Za-z_]\w*\.py$/.test(entry.name)) files.push(entry.name);
        if (entry.isDirectory() && ['app', 'src', 'server'].includes(entry.name)) {
            for (const child of fs.readdirSync(path.join(directory, entry.name), { withFileTypes: true })) {
                if (child.isFile() && /^[A-Za-z_]\w*\.py$/.test(child.name)) files.push(`${entry.name}/${child.name}`);
            }
        }
    }
    const entries = [];
    for (const file of files.slice(0, 80)) {
        const source = read(path.join(directory, file));
        const flask = source.match(/^([A-Za-z_]\w*)\s*=\s*Flask\s*\(/m);
        const fastapi = source.match(/^([A-Za-z_]\w*)\s*=\s*FastAPI\s*\(/m);
        if (flask && /(?:from flask import|import flask)/.test(source)) entries.push({ framework: 'flask', entry: file, object: flask[1] });
        else if (fastapi && /(?:from fastapi import|import fastapi)/.test(source)) entries.push({ framework: 'fastapi', entry: file, object: fastapi[1] });
        else if (/^import streamlit\b|^from streamlit\b/m.test(source)) entries.push({ framework: 'streamlit', entry: file });
        else if (/^import gradio\b|^from gradio\b/m.test(source) && /\.launch\s*\(/.test(source)) entries.push({ framework: 'gradio', entry: file });
    }
    return entries;
}
function inspectDirectory(directory) {
    const packageFile = path.join(directory, 'package.json');
    let requiresBuildServer = false;
    if (fs.existsSync(packageFile)) {
        let pkg;
        try { pkg = JSON.parse(read(packageFile)); } catch { throw new Error(`Invalid package.json in ${directory}`); }
        if (!pkg || typeof pkg !== 'object' || Array.isArray(pkg)) throw new Error('package.json must contain an object.');
        if (selectedScript(pkg)) {
            const framework = detectNode(pkg);
            return [{ directory, runtime: 'node', framework, label: labelFor(framework, pkg), pkg }];
        }
        const deps = { ...pkg.dependencies, ...pkg.devDependencies };
        requiresBuildServer = ['vite', 'next', '@angular/cli', 'nuxt', 'astro', '@sveltejs/kit'].some(name => deps[name]);
    }
    const entries = pythonEntries(directory);
    if (entries.length) return entries.map(entry => ({ directory, runtime: 'python', label: entry.framework, ...entry }));
    if (!requiresBuildServer && fs.existsSync(path.join(directory, 'index.html'))) return [{ directory, runtime: 'static', framework: 'static', label: 'Static HTML' }];
    return [];
}
function discoverApplications(root) {
    root = fs.realpathSync(path.resolve(root));
    const results = [];
    let visited = 0;
    function visit(directory, depth) {
        if (++visited > 250 || depth > 3) return;
        const applications = inspectDirectory(directory);
        results.push(...applications);
        // A root workspace script can be an orchestrator: also expose its child applications.
        const pkg = read(path.join(directory, 'package.json'));
        const workspace = /"workspaces"\s*:/.test(pkg) || fs.existsSync(path.join(directory, 'pnpm-workspace.yaml'));
        if (applications.length && !workspace) return;
        for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
            if (entry.isDirectory() && !entry.name.startsWith('.') && !SKIP.has(entry.name)) visit(path.join(directory, entry.name), depth + 1);
        }
    }
    visit(root, 0);
    return results.map(project => ({ ...project,
        id: createHash('sha256').update(project.directory + '#' + (project.entry || project.runtime)).digest('hex').slice(0, 16),
        relativePath: path.relative(root, project.directory) || '.', label: `${project.label}${project.entry ? ` (${project.entry})` : ''}` }));
}
function configuredEndpoint(project) {
    const command = project.pkg?.scripts?.[selectedScript(project.pkg)] || '';
    let port = command.match(/(?:--port(?:=|\s+)|(?:^|\s)-p\s+|\bPORT=)(\d{2,5})\b/)?.[1];
    const entry = command.match(/\b(?:node|nodemon|tsx|ts-node)\s+([\w./-]+\.(?:[cm]?js|ts))\b/)?.[1]
        || (project.framework === 'nest' ? 'src/main.ts' : null)
        || (project.framework === 'gradio' ? project.entry : null);
    let entrySource = '';
    if (entry) {
        const candidate = path.resolve(project.directory, entry);
        const relative = path.relative(project.directory, candidate);
        if (!relative.startsWith('..') && !path.isAbsolute(relative)) entrySource = read(candidate);
        port ||= entrySource.match(/\.listen\s*\(\s*(\d{2,5})\s*[,)]/)?.[1]
            || (project.framework === 'gradio' ? entrySource.match(/\bserver_port\s*=\s*(\d{2,5})/)?.[1] : null);
    }
    const config = ['vite.config.js', 'vite.config.ts', 'next.config.js', 'next.config.mjs', 'astro.config.mjs', 'nuxt.config.ts'].map(file => read(path.join(project.directory, file))).join('\n');
    const basePath = config.match(/\b(?:basePath|baseURL|base)\s*:\s*['"]([^'"]+)['"]/)?.[1] || '/';
    return { port: port ? Number(port) : null, protocol: /--https\b|HTTPS=true/.test(command) || /https\.createServer\s*\(/.test(entrySource) ? 'https:' : 'http:', basePath: basePath.startsWith('/') && !basePath.startsWith('//') ? basePath : '/' };
}
function launchPlan(project, port) {
    if (project.runtime === 'static') return { command: process.execPath, args: [path.join(__dirname, 'guiStaticServer.cjs'), project.directory, String(port)], script: 'static server' };
    if (project.runtime === 'python') {
        const venv = path.join(project.directory, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
        const command = fs.existsSync(venv) ? venv : 'python';
        const module = project.entry.replace(/\.py$/, '').replace(/\//g, '.');
        const options = {
            django: ['manage.py', 'runserver', `0.0.0.0:${port}`, '--noreload'],
            flask: ['-m', 'flask', '--app', `${module}:${project.object}`, 'run', '--host', '0.0.0.0', '--port', String(port), '--no-reload'],
            fastapi: ['-m', 'uvicorn', `${module}:${project.object}`, '--host', '0.0.0.0', '--port', String(port)],
            streamlit: ['-m', 'streamlit', 'run', project.entry, '--server.address', '0.0.0.0', '--server.port', String(port), '--server.headless', 'true'],
            gradio: [project.entry]
        };
        return { command, args: options[project.framework], script: `${project.framework} ${project.entry}`, pythonModule: { fastapi: 'uvicorn', django: 'django', flask: 'flask', streamlit: 'streamlit', gradio: 'gradio' }[project.framework] };
    }
    const script = selectedScript(project.pkg);
    if (!script || !/^[\w:-]+$/.test(script)) throw new Error('No safe, explicit development script was found. Define dev, start, serve, or server in package.json.');
    const text = project.pkg.scripts[script];
    if (/\b(?:npm|pnpm|yarn)\s+(?:install|ci|add)\b|\bpip3?\s+install\b|\b(?:prisma|alembic|manage\.py)\s+(?:migrate|upgrade)\b|\b(?:npm|pnpm|yarn)\s+run\s+(?:setup|prepare|migrate)\b/i.test(text)) {
        throw new Error('The startup script includes dependency installation, setup, or migrations. Run those explicitly yourself, then provide a development-only startup script. Checkpoint did not execute it.');
    }
    const adapter = ADAPTERS[project.framework];
    // Only append arguments to a direct framework command, not a compound/wrapper script.
    const direct = adapter.match && adapter.match.test(text) && !/[;&|]/.test(text) && /^(?:vite|next|ng|nuxt|nuxi|astro|vue-cli-service|react-scripts|react-router|remix)\b/.test(text.trim());
    let extra = direct && adapter.args ? adapter.args(port) : [];
    if (configuredEndpoint(project).port) extra = extra.filter((arg, index, all) => arg !== '--port' && all[index - 1] !== '--port');
    const manager = /^([a-z]+)@/.exec(project.pkg.packageManager || '')?.[1]
        || (fs.existsSync(path.join(project.directory, 'pnpm-lock.yaml')) ? 'pnpm' : fs.existsSync(path.join(project.directory, 'yarn.lock')) ? 'yarn' : 'npm');
    if (!['npm', 'pnpm', 'yarn'].includes(manager)) throw new Error(`Unsupported package manager ${manager}. Use an explicit supported development script.`);
    // Explicit run is permitted; lifecycle setup hooks are not run automatically.
    if (manager === 'yarn') throw new Error('Yarn projects are detected, but automatic startup is not enabled: lifecycle-hook behavior varies by Yarn version. Start the app manually or use an npm/pnpm development script.');
    return { command: manager, args: ['--ignore-scripts', 'run', script, ...(extra.length ? ['--', ...extra] : [])], script };
}
module.exports = { ADAPTERS, detectNode, discoverApplications, inspectDirectory, selectedScript, configuredEndpoint, launchPlan };
