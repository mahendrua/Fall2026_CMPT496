// Controlled Button Lab substitute. BUGGY switches four known defects; specs stay identical.
const http = require('node:http');
const buggy = process.env.BUGGY === '1';
const html = `<!doctype html><html><body>
<p>Increase adds one. Theme toggles dark mode. Show/Hide toggles details. Save notes persists after refresh.</p>
<button id="increase">Increase</button><output id="count">0</output>
<button id="theme">Theme</button><button id="toggle">Show/Hide</button>
<p id="details" hidden>Details</p><label>Notes<textarea id="notes"></textarea></label>
<button id="save">Save notes</button><output id="saved"></output>
<script>
const notes = document.getElementById('notes');
notes.value = localStorage.getItem('notes') || '';
document.getElementById('increase').onclick = () => {
const count = document.getElementById('count'); count.textContent = Number(count.textContent) + ${buggy ? 2 : 1}; };
document.getElementById('theme').onclick = () => { ${buggy ? '' : "document.body.classList.toggle('dark');"} };
document.getElementById('toggle').onclick = () => { ${buggy ? '' : "document.getElementById('details').hidden = !document.getElementById('details').hidden;"} };
document.getElementById('save').onclick = () => { ${buggy ? '' : "localStorage.setItem('notes', notes.value);"} document.getElementById('saved').textContent = 'Saved'; };
</script></body></html>`;
http.createServer((req, res) => { res.setHeader('Content-Type', 'text/html'); res.end(html); }).listen(8080);
// Runner readiness probe only; all controlled-app requests bypass the proxy on localhost.
require('node:net').createServer(socket => socket.end()).listen(3128);
const W = require('/opt/gui-runner/node_modules/ws');
function events() {
    const ws = new W('ws://127.0.0.1:3001/verify');
    ws.on('message', data => console.log('VERIFICATION_EVENT:' + data.toString()));
    ws.on('error', () => {});
    ws.on('close', () => setTimeout(events, 250).unref());
}
events();
setTimeout(() => {
    require('node:child_process').spawn(process.execPath, ['/opt/gui-runner/tests/observe-preview.cjs'], { stdio: 'inherit' });
}, 1500);
