// Explicit opt-in integration harness: run with node_modules/electron/dist/electron.exe.
// Uses the production runner, proxy, renderer, iframe/CSP and secure BrowserWindow settings.
const { app, BrowserWindow, ipcMain } = require('electron');
app.disableHardwareAcceleration();
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const os = require('node:os');
const { GuiTestRunner } = require('../../../guiTestRunner');
const root = path.resolve(__dirname, '../../..');
const evidenceDir = path.join(__dirname, 'evidence');
fs.mkdirSync(evidenceDir, { recursive: true });
app.setPath('userData', fs.mkdtempSync(path.join(os.tmpdir(), 'checkpoint-preview-profile-')));
fs.appendFileSync(path.join(evidenceDir, 'progress.log'), `Harness loaded: ${process.versions.electron}\n`);
const evidence = { frames: [], diagnostics: [], results: [], securityViolations: [] };
let runner, window, server, poll, polling = false;
const deadline = setTimeout(() => finish(new Error('Integration verification exceeded three minutes')), 180000);
let finishing = false;
async function finish(error) {
    if (finishing) return;
    finishing = true;
    clearTimeout(deadline);
    clearInterval(poll);
    if (runner) await runner.dispose();
    if (server) server.close();
    if (window && !window.isDestroyed()) window.destroy();
    evidence.error = error?.stack || null;
    fs.writeFileSync(path.join(evidenceDir, 'results.json'), JSON.stringify(evidence, null, 2));
    console.log(JSON.stringify(evidence));
    app.exit(error ? 1 : 0);
}
ipcMain.handle('auth:get-config', () => ({ configured: false }));
ipcMain.handle('stop-local-gui-app', () => ({ success: true }));
ipcMain.on('gui-test-report-event', (_event, event) => {
    if (event.type === 'test_finished') evidence.results.push({ title: event.title, status: event.status, screenshot: !!event.screenshot, duration: event.duration });
    if (event.type === 'run_complete') {
        evidence.completion = event;
        // Wait for normal production monitor cleanup instead of stopping the active container.
        runner.waitForIdle().then(() => {
            evidence.cleanedUp = runner.activeRun === null;
            if (!evidence.legacyPolicyBlocked) throw new Error('Did not reproduce the original frame-policy failure');
            if (!evidence.displayVisible || evidence.securityViolations.length) throw new Error('Missing visible Chromium window or unexpected preview policy violation');
            if (evidence.frames.length < 2) throw new Error('Did not observe both application colors in the Electron iframe');
            if (evidence.results.length !== 2 || !evidence.results.some(r => r.status === 'failed' && r.screenshot)) throw new Error('Missing test result or failure screenshot');
            return finish();
        }).catch(finish);
    }
});
app.whenReady().then(async () => {
    fs.appendFileSync(path.join(evidenceDir, 'progress.log'), 'Electron ready\n');
    server = http.createServer((req, res) => {
        res.setHeader('Content-Type', 'text/html');
        res.end(`<!doctype html><html><body style="background:rgb(21,196,119);font:28px sans-serif">
        <h1>${req.url === '/second' ? 'Navigation Evidence' : 'Live Browser Evidence'}</h1>
        <label>Message<input></label><button onclick="const n=++window.count;document.querySelector('output').textContent=n;document.body.style.background=n%2?'rgb(219,44,153)':'rgb(21,196,119)'">Change color</button><output>0</output><script>window.count=0</script></body></html>`);
    });
    await new Promise(resolve => server.listen(0, '0.0.0.0', resolve));
    window = new BrowserWindow({ show: false, width: 1500, height: 1100,
        webPreferences: { preload: path.join(root, 'preload.js'), contextIsolation: true, sandbox: true, nodeIntegration: false, offscreen: true, backgroundThrottling: false } });
    window.webContents.on('did-fail-load', (_event, code, description) => fs.appendFileSync(path.join(evidenceDir, 'progress.log'), `Load failure: ${code} ${description}\n`));
    window.webContents.on('render-process-gone', (_event, details) => fs.appendFileSync(path.join(evidenceDir, 'progress.log'), `Renderer exited: ${JSON.stringify(details)}\n`));
    window.webContents.on('console-message', (_event, ...details) => {
        const message = details.map(String).join(' ');
        if (/frame-src|Refused to frame/i.test(message)) evidence.securityViolations.push(message);
    });
    await window.loadURL('data:text/html,' + encodeURIComponent(`<meta http-equiv="Content-Security-Policy" content="default-src 'self'; frame-src 'self' data:">`));
    evidence.legacyPolicyBlocked = await window.webContents.executeJavaScript(`new Promise(resolve => {
        document.addEventListener('securitypolicyviolation', event => resolve(event.effectiveDirective === 'frame-src'), {once:true});
        const frame=document.createElement('iframe');frame.src='http://127.0.0.1:${server.address().port}';document.body.appendChild(frame);
        setTimeout(()=>resolve(false),2000);
    })`);
    evidence.securityViolations = [];
    await window.loadFile(path.join(root, 'index.html'));
    fs.appendFileSync(path.join(evidenceDir, 'progress.log'), 'Actual index loaded\n');
    await window.webContents.executeJavaScript(`document.getElementById('authScreen').classList.add('hidden'); document.querySelectorAll('.page').forEach(p=>p.classList.add('hidden')); document.getElementById('guiTestPage').classList.remove('hidden'); document.getElementById('guiDemoMode').checked=true;`);
    runner = new GuiTestRunner({ appRoot: root, isPackaged: false, onEvent: event => {
        if (event.type === 'runner_log') console.log(event.message);
        if (!window.isDestroyed()) window.webContents.send('gui-test-event', event);
    } });
    const files = runner.addFiles([path.join(__dirname, 'visible.spec.js')]);
    const response = await runner.start(files.map(file => file.id), [], { mode: 'host', port: server.address().port }, { demoMode: true });
    evidence.runId = response.runId;
    console.log('Runner started:', response.runId);
    await window.webContents.executeJavaScript(`connectGuiRun(${JSON.stringify(response)}); document.getElementById('guiPreview').scrollIntoView();`);
    poll = setInterval(async () => {
        if (polling || finishing) return;
        polling = true;
        try {
            const frame = window.webContents.mainFrame.frames.find(f => f.url.startsWith('http://127.0.0.1:'));
            if (!frame) return;
            const pixels = await frame.executeJavaScript(`(() => {
                const c=document.querySelector('canvas'); if(!c || !c.width) return null;
                const data=c.getContext('2d').getImageData(0,0,c.width,c.height).data;
                let green=0,pink=0;
                for(let i=0;i<data.length;i+=32) {
                    if(Math.abs(data[i]-21)<4 && Math.abs(data[i+1]-196)<4 && Math.abs(data[i+2]-119)<4) green++;
                    if(Math.abs(data[i]-219)<4 && Math.abs(data[i+1]-44)<4 && Math.abs(data[i+2]-153)<4) pink++;
                }
                return {green,pink,width:c.width,height:c.height};
            })()`);
            if (!pixels) return;
            const color = pixels.green > 1000 ? 'green' : pixels.pink > 1000 ? 'pink' : null;
            if (color && !evidence.frames.some(f => f.color === color)) {
                const layout = await window.webContents.executeJavaScript(`(() => {const r=document.getElementById('guiPreview').getBoundingClientRect();return {width:r.width,height:r.height};})()`);
                if (!layout.width || !layout.height) throw new Error('Preview iframe has zero dimensions');
                const screenshot = await window.webContents.capturePage();
                fs.writeFileSync(path.join(evidenceDir, `electron-${color}.png`), screenshot.toPNG());
                evidence.frames.push({ color, ...pixels, layout });
                evidence.displayVisible = await window.webContents.executeJavaScript(`document.getElementById('guiExecutionLogs').textContent.includes('Chromium window is visible on Xvfb :99')`);
            }
        } catch (error) { console.log('Frame probe:', error.message); }
        finally { polling = false; }
    }, 300);
}).catch(finish);
