// Internal static adapter; never serve hidden files, directory listings, or paths outside the chosen root.
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const root = fs.realpathSync(process.argv[2]);
const types = { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.mjs': 'text/javascript', '.json': 'application/json', '.svg': 'image/svg+xml', '.png': 'image/png', '.jpg': 'image/jpeg', '.ico': 'image/x-icon', '.woff2': 'font/woff2', '.txt': 'text/plain' };
http.createServer((req, res) => {
    try {
        const route = decodeURIComponent(new URL(req.url, 'http://localhost').pathname);
        if (route.split(/[\\/]/).some(part => part.startsWith('.') || ['node_modules', 'credentials', 'secrets'].includes(part))) throw new Error('Forbidden');
        let file = path.resolve(root, '.' + route);
        if (fs.statSync(file).isDirectory()) file = path.join(file, 'index.html');
        file = fs.realpathSync(file);
        const relative = path.relative(root, file);
        if (relative.startsWith('..') || path.isAbsolute(relative) || !types[path.extname(file)] || !fs.statSync(file).isFile()) throw new Error('Forbidden');
        res.setHeader('Content-Type', types[path.extname(file)]);
        fs.createReadStream(file).on('error', () => res.destroy()).pipe(res);
    } catch { res.writeHead(404); res.end('Not found'); }
}).listen(Number(process.argv[3]), '0.0.0.0', () => console.log(`Checkpoint static app: http://localhost:${process.argv[3]}`));
