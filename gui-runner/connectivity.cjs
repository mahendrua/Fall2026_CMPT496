function diagnoseResponse({ status = 0, body = '', error = '', framework = '' }) {
    if (error) {
        if (/CERT_|certificate|SSL_/i.test(error)) return { category: 'tls_certificate', message: 'The development HTTPS certificate is not trusted. Configure a trusted development certificate/CA for the host and Docker browser. TLS verification remains enabled.' };
        if (/NAME_NOT_RESOLVED|ENOTFOUND|EAI_AGAIN/i.test(error)) return { category: 'dns_failure', message: 'Docker or its allowlist proxy could not resolve the application hostname. Check Docker Desktop host networking and the requested hostname.' };
        if (/CONNECTION_REFUSED|ECONNREFUSED/i.test(error)) return { category: 'connection_refused', message: 'The application refused the connection. Check its process, actual port and listening interface.' };
        if (/Timeout|TIMEDOUT|ETIMEDOUT|timed\s+out/i.test(error)) return { category: 'connection_timeout', message: 'Application access timed out. It may still be starting, unresponsive, or blocked by host networking/firewall rules.' };
        if (/TUNNEL|PROXY|proxy/i.test(error)) return { category: 'proxy_failure', message: 'The allowlist proxy could not establish application access. Check its hostname/port allowlist and Docker host reachability.' };
        return { category: 'navigation_failure', message: 'Browser navigation failed. Check the application URL, startup logs and Docker networking.' };
    }
    if (/Blocked request[\s\S]{0,250}(?:host|allowed)|Invalid Host header|DisallowedHost|Invalid HTTP_HOST header|This host[\s\S]{0,150}is not allowed/i.test(body)) {
        const hints = {
            vite: 'Use a Vite version supporting __VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS=host.docker.internal, or add only "host.docker.internal" to server.allowedHosts.',
            django: 'Add only "host.docker.internal" to ALLOWED_HOSTS in the selected Django settings.',
            angular: 'Use ng serve --allowed-hosts host.docker.internal with a CLI version that supports it.',
            'vue-cli': 'Set devServer.allowedHosts to include only "host.docker.internal" using the installed dev-server version\'s supported configuration.',
            'create-react-app': 'Use the installed react-scripts version\'s supported explicit development-host configuration; do not enable DANGEROUSLY_DISABLE_HOST_CHECK.'
        };
        return { category: 'host_blocked', message: `The development server rejected host.docker.internal. ${hints[framework] || 'Add only that hostname to the framework\'s explicit development-host allowlist.'} Checkpoint will not disable host validation or edit project files.` };
    }
    if (/ERR_DNS_FAIL/i.test(body)) return { category: 'dns_failure', message: 'The Docker proxy could not resolve the application hostname. Check Docker Desktop host DNS.' };
    if (/ERR_CONNECT_FAIL/i.test(body)) return { category: 'connection_refused', message: 'The Docker proxy could not connect to the application. Check its process, actual port, listening interface and host firewall.' };
    if (/ERR_ACCESS_DENIED/i.test(body)) return { category: 'proxy_denied', message: 'The Docker allowlist proxy denied access. Verify the exact application hostname and port; no additional destinations were allowed automatically.' };
    if (/The requested URL could not be retrieved[\s\S]*squid/i.test(body)) return { category: 'proxy_failure', message: 'The Docker proxy returned a network error. Check Docker DNS, host reachability and the application startup log.' };
    if (status >= 500 && /Internal Server Error|Traceback \(most recent call last\)|Failed to compile|Module not found|Vite Error|Error loading/i.test(body)
        || /<vite-error-overlay|id=["']__next_error__|data-nextjs-dialog-header/i.test(body)) {
        return { category: 'application_startup_error', message: 'The server returned a recognizable framework error page. Inspect its startup/compile log before running application assertions.' };
    }
    return { category: 'accessible', message: status === 401 || status === 403 ? `Application responds with HTTP ${status}; authentication or authorization may be required. This alone is not a connectivity failure.` : `Application responded with HTTP ${status}.` };
}
module.exports = { diagnoseResponse };
