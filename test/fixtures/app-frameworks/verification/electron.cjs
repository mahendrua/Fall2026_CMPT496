const { app, BrowserWindow, ipcMain } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { LocalGuiApp } = require('../../../../guiLocalApp');
const { GuiTestRunner } = require('../../../../guiTestRunner');
const root = path.resolve(__dirname, '../../../..');
const evidenceDir = path.join(__dirname, 'evidence');
fs.mkdirSync(evidenceDir, { recursive: true });
app.disableHardwareAcceleration();
app.setPath('userData', fs.mkdtempSync(path.join(os.tmpdir(), 'checkpoint-frameworks-electron-')));
const results = [];
let window, local, runner, current, poll, busy = false, complete;
ipcMain.handle('auth:get-config', () => ({ configured: false }));
ipcMain.handle('stop-local-gui-app', async () => { await local?.stop(); return { success: true }; });
ipcMain.on('gui-test-report-event', (_event, event) => {
    if (!current) return;
    if (event.type === 'test_started') current.running = true;
    if (event.type === 'test_finished') current.tests.push({ title: event.title, status: event.status, duration: event.duration, screenshot: !!event.screenshot, errors: event.errors, output: event.output });
    if (event.type === 'runner_error') current.errors.push(event.message);
    if (event.type === 'run_complete') { current.running = false; current.completion = event; complete?.(); }
});
let finished = false;
async function finish(error) {
    if (finished) return; finished = true;
    clearInterval(poll); clearTimeout(deadline);
    await runner?.dispose(); await local?.stop();
    if (window && !window.isDestroyed()) window.destroy();
    fs.writeFileSync(path.join(evidenceDir, 'results.json'), JSON.stringify({ results, error: error?.stack || null }, null, 2));
    console.log(JSON.stringify({ results, error: error?.message || null }));
    app.exit(error ? 1 : 0);
}
const deadline = setTimeout(() => finish(new Error('Framework verification exceeded eight minutes')), 480000);
app.whenReady().then(async () => {
    window = new BrowserWindow({ show: false, width: 1500, height: 1100, webPreferences: {
        preload: path.join(root, 'preload.js'), sandbox: true, contextIsolation: true,
        nodeIntegration: false, offscreen: true, backgroundThrottling: false
    } });
    await window.loadFile(path.join(root, 'index.html'));
    await window.webContents.executeJavaScript(`document.getElementById('authScreen').classList.add('hidden');document.querySelectorAll('.page').forEach(p=>p.classList.add('hidden'));document.getElementById('guiTestPage').classList.remove('hidden');document.getElementById('guiDemoMode').checked=true;`);
    poll = setInterval(async () => {
        if (busy || !current?.running || finished) return;
        busy = true;
        try {
            const frame = window.webContents.mainFrame.frames.find(frame => frame.url.startsWith('http://127.0.0.1:'));
            if (!frame) return;
            const colors = await frame.executeJavaScript(`(() => {const c=document.querySelector('canvas');if(!c?.width)return null;const d=c.getContext('2d').getImageData(0,0,c.width,c.height).data;let green=0,pink=0;for(let i=0;i<d.length;i+=32){if(Math.abs(d[i]-21)<4&&Math.abs(d[i+1]-196)<4&&Math.abs(d[i+2]-119)<4)green++;if(Math.abs(d[i]-219)<4&&Math.abs(d[i+1]-44)<4&&Math.abs(d[i+2]-153)<4)pink++;}return {green,pink,width:c.width,height:c.height};})()`);
            if (!colors) return;
            const color = colors.green > 1000 ? 'green' : colors.pink > 1000 ? 'pink' : null;
            if (color && !current.frames.some(f => f.color === color)) {
                current.frames.push({ color, ...colors });
                fs.writeFileSync(path.join(evidenceDir, `${current.name}-${color}.png`), (await window.webContents.capturePage()).toPNG());
            }
        } catch (error) { console.log('Frame probe:', error.message); }
        finally { busy = false; }
    }, 250);
    for (const name of (process.env.CHECKPOINT_VERIFY_FRAMEWORKS || 'vite,next,express,python,static').split(',')) {
        current = { name, tests: [], frames: [], errors: [] }; results.push(current);
        local = new LocalGuiApp(event => { if (event.type !== 'local_app_log') console.log(event.message); });
        try {
            const startup = await local.start(path.join(__dirname, '..', name));
            current.startup = startup;
            console.log('APP_READY:', name, startup.port, startup.framework);
            runner = new GuiTestRunner({ appRoot: root, isPackaged: false, onEvent: event => {
                if (event.type === 'runner_log') console.log(event.message);
                window.webContents.send('gui-test-event', event);
            } });
            const files = runner.addFiles([path.join(__dirname, 'workflow.spec.js')]);
            const response = await runner.start(files.map(f => f.id), [], { mode: 'host', port: startup.port, protocol: startup.protocol, basePath: startup.basePath, framework: startup.framework }, { demoMode: true });
            const completion = new Promise(resolve => { complete = resolve; });
            await window.webContents.executeJavaScript(`resetGuiDashboard();connectGuiRun(${JSON.stringify(response)});document.getElementById('guiPreview').scrollIntoView();`);
            await completion;
            current.preflightVerified = await window.webContents.executeJavaScript(`document.getElementById('guiExecutionLogs').textContent.includes('Application accessible from Playwright')`);
            await runner.waitForIdle();
            current.cleanedUp = runner.activeRun === null;
            current.ok = current.preflightVerified && current.frames.length === 2 && current.tests.length === 2
                && current.tests[0].status === 'passed' && current.tests[1].status === 'failed' && current.tests[1].screenshot;
            if (!current.ok) throw new Error('Missing preflight, visible color transitions, result or failure screenshot');
        } catch (error) { current.error = error.message; console.error('APP_FAILED:', name, error.message); }
        finally { await runner?.dispose(); runner = null; await local.stop(); }
        console.log('APP_RESULT:', name, JSON.stringify(current));
        fs.writeFileSync(path.join(evidenceDir, 'results.json'), JSON.stringify({ results }, null, 2));
    }
    await finish(results.every(result => result.ok) ? null : new Error('One or more framework checks did not pass'));
}).catch(finish);
