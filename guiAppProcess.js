const { execFile } = require('node:child_process');
const { promisify } = require('node:util');
const exec = promisify(execFile);

async function ownsListeningPort(rootPid, port) {
    if (!Number.isInteger(rootPid) || !Number.isInteger(port)) return false;
    if (process.platform === 'win32') {
        // Numeric-only parameters; never execute project-supplied shell text here.
        const script = `$ErrorActionPreference='Stop'; $owners=@(Get-NetTCPConnection -State Listen -LocalPort ${port} -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess); if($owners -contains ${rootPid}){Write-Output 'owned';exit}; $parents=@{};Get-CimInstance Win32_Process | ForEach-Object {$parents[[int]$_.ProcessId]=[int]$_.ParentProcessId};foreach($ownerId in $owners){$cursor=[int]$ownerId;for($depth=0;$depth -lt 32;$depth++){if($cursor -eq ${rootPid}){Write-Output 'owned';exit};if(!$parents.ContainsKey($cursor)){break};$cursor=$parents[$cursor]}};Write-Output 'foreign'`;
        const { stdout } = await exec('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', script], { timeout: 8000, windowsHide: true });
        return stdout.trim() === 'owned';
    }
    // lsof/ps report only listener PIDs and process ancestry, never command lines or environments.
    const [{ stdout: listeners }, { stdout: processes }] = await Promise.all([
        exec('lsof', ['-nP', `-iTCP:${port}`, '-sTCP:LISTEN', '-t'], { timeout: 3000 }),
        exec('ps', ['-eo', 'pid=,ppid='], { timeout: 3000 })
    ]);
    const parents = new Map(processes.trim().split('\n').map(line => line.trim().split(/\s+/).map(Number)));
    return listeners.trim().split(/\s+/).map(Number).some(pid => {
        for (let i = 0; i < 32; i++) { if (pid === rootPid) return true; pid = parents.get(pid); if (!pid) return false; }
        return false;
    });
}
module.exports = { ownsListeningPort };
