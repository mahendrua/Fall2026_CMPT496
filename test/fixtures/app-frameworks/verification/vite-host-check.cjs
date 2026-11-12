const fs = require('node:fs');
const path = require('node:path');
const net = require('node:net');
const { spawn, execFile } = require('node:child_process');
const { promisify } = require('node:util');
const { LocalGuiApp, createAppEnvironment, probeHttp } = require('../../../../guiLocalApp');
const { diagnoseResponse } = require('../../../../gui-runner/connectivity.cjs');
(async () => {
    const fixture = path.resolve(__dirname, '../vite');
    const port = await new Promise(resolve => { const s=net.createServer();s.listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>resolve(p));}); });
    const env = createAppEnvironment(port, 'vite');
    delete env.__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS;
    const cli = path.resolve(__dirname, '../node_modules/vite/bin/vite.js');
    const child = spawn(process.execPath, [cli, '--host', '0.0.0.0', '--port', String(port), '--strictPort'], { cwd: fixture, env, windowsHide: true, stdio: ['ignore','pipe','pipe'] });
    let output='';child.stdout.on('data',data=>{output+=data;});child.stderr.on('data',data=>{output+=data;});
    const exited = new Promise(resolve => child.once('exit', resolve));
    let blocked;
    try {
        const deadline=Date.now()+15000;
        while(Date.now()<deadline) {
            if(child.exitCode!==null) throw new Error('Controlled Vite startup failed: '+output.slice(-1000));
            try { blocked=await probeHttp(`http://127.0.0.1:${port}/`,{hostHeader:`host.docker.internal:${port}`});break; }
            catch { await new Promise(resolve=>setTimeout(resolve,100)); }
        }
        if(!blocked || diagnoseResponse({...blocked,framework:'vite'}).category!=='host_blocked') throw new Error('The unconfigured Vite host restriction was not reproduced.');
    } finally {
        if(process.platform==='win32') await promisify(execFile)('taskkill.exe',['/PID',String(child.pid),'/T','/F'],{windowsHide:true}).catch(()=>{});
        else child.kill('SIGTERM');
        await exited;
    }
    const app = new LocalGuiApp();
    try {
        const startup=await app.start(fixture);
        const accepted=await probeHttp(`http://127.0.0.1:${startup.port}/`,{hostHeader:`host.docker.internal:${startup.port}`});
        if(accepted.status!==200) throw new Error('Scoped Vite host access failed.');
        const result={withoutScopedAllowlist:{status:blocked.status,category:'host_blocked'},withScopedAllowlist:{status:accepted.status,host:'host.docker.internal'},sourceFilesModified:false};
        fs.writeFileSync(path.join(__dirname,'evidence/vite-host-check.json'),JSON.stringify(result,null,2));
        console.log(JSON.stringify(result));
    } finally { await app.stop(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
