// A loopback-only TCP forward for servers that require a localhost development origin.
// Host/Origin/Cookie headers are never rewritten. The existing proxy still authorizes the
// one application destination/port; this does not add a network route or an outbound host.
const net = require('node:net');
async function startAppRelay({ target, proxyUrl }) {
    const destination = new URL(target);
    const proxy = new URL(proxyUrl);
    if (!['http:', 'https:'].includes(destination.protocol) || destination.hostname !== 'host.docker.internal') throw new Error('The local app relay requires the selected Docker host destination.');
    if (proxy.protocol !== 'http:' || proxy.username || proxy.password) throw new Error('Unsupported local app proxy configuration.');
    const sockets = new Set();
    const server = net.createServer(client => {
        const upstream = net.createConnection({ host: proxy.hostname, port: Number(proxy.port) });
        sockets.add(client); sockets.add(upstream);
        const close = () => { client.destroy(); upstream.destroy(); sockets.delete(client); sockets.delete(upstream); };
        client.on('error', close); upstream.on('error', close); client.on('close', close); upstream.on('close', close);
        upstream.setTimeout(10000, close);
        upstream.once('connect', () => {
            const authority = `${destination.hostname}:${destination.port || (destination.protocol === 'https:' ? 443 : 80)}`;
            upstream.write(`CONNECT ${authority} HTTP/1.1\r\nHost: ${authority}\r\n\r\n`);
        });
        let header = Buffer.alloc(0);
        const handshake = chunk => {
            header = Buffer.concat([header, chunk]);
            if (header.length > 16384) return close();
            const boundary = header.indexOf('\r\n\r\n');
            if (boundary < 0) return;
            upstream.removeListener('data', handshake);
            if (!/^HTTP\/1\.[01] 200\b/.test(header.toString('ascii', 0, boundary))) return close();
            upstream.setTimeout(0);
            const pending = header.subarray(boundary + 4);
            if (pending.length) client.write(pending);
            client.pipe(upstream); upstream.pipe(client);
        };
        upstream.on('data', handshake);
    });
    await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
    const url = new URL(destination.pathname + destination.search, `${destination.protocol}//localhost:${server.address().port}`);
    return { url: url.toString(), close() { for (const socket of sockets) socket.destroy(); server.close(); } };
}
module.exports = { startAppRelay };
