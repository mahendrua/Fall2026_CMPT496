const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const net = require('node:net');
const { startAppRelay } = require('../gui-runner/appRelay.cjs');

test('loopback forwarding retains browser Host/Origin and tunnels only the selected proxy destination', async () => {
    const sockets = new Set();
    let authority;
    const target = http.createServer((req,res) => res.end(JSON.stringify({ host: req.headers.host, origin: req.headers.origin, url: req.url })));
    const proxy = http.createServer();
    await new Promise(resolve => target.listen(0, '127.0.0.1', resolve));
    proxy.on('connect', (req, client, head) => {
        authority = req.url;
        const upstream = net.connect(target.address().port, '127.0.0.1', () => {
            client.write('HTTP/1.1 200 Connection established\r\n\r\n');
            if (head.length) upstream.write(head);
            client.pipe(upstream); upstream.pipe(client);
        });
        sockets.add(client); sockets.add(upstream);
        client.on('error', () => upstream.destroy()); upstream.on('error', () => client.destroy());
    });
    await new Promise(resolve => proxy.listen(0, '127.0.0.1', resolve));
    let relay;
    try {
        relay = await startAppRelay({ target: `http://host.docker.internal:${target.address().port}/custom/`, proxyUrl: `http://127.0.0.1:${proxy.address().port}` });
        const url = new URL(relay.url);
        const body = await new Promise((resolve,reject) => {
            http.get(url, { headers: { Origin: url.origin } }, res => {
                let data='';res.on('data',c=>{data+=c;});res.on('end',()=>resolve(JSON.parse(data)));
            }).on('error', reject);
        });
        assert.equal(body.host, url.host);
        assert.equal(body.origin, url.origin);
        assert.equal(body.url, '/custom/');
        assert.equal(authority, `host.docker.internal:${target.address().port}`);
        await assert.rejects(startAppRelay({ target: 'http://example.com/', proxyUrl: 'http://127.0.0.1:3128' }), /selected Docker host/);
    } finally {
        relay?.close(); for (const socket of sockets) socket.destroy();
        await Promise.all([new Promise(r=>proxy.close(r)),new Promise(r=>target.close(r))]);
    }
});
