# Local application startup and Docker connectivity

Verified on Windows with Docker Desktop, October 30, 2026. Compatibility is limited to the capabilities below; this is not a claim that every application/framework configuration works.

## What changed

`guiLocalApp.js` now uses shared framework adapters rather than requiring every selected folder to contain an npm project. It discovers applications without importing their code, resolves a development command, checks installed dependencies, selects/checks a port, starts the selected application, waits for HTTP readiness and verifies that the listener belongs to that process tree. Multiple applications produce a native selection dialog with stable application IDs. It never silently selects one of several applications.

Readiness is no longer a successful TCP connection. An HTTP response, the appropriate host header, a still-running owned process and listener ownership are checked. Port/protocol announcements update the endpoint. Literal backend `.listen(port)` values and explicit command ports are honored; occupied fixed ports are rejected without touching their existing service. Literal framework base paths and advertised local URL paths are preserved.

The actual Docker browser now runs a preflight before application assertions. It uses the same context proxy configuration as tests, navigates to the runtime BASE_URL, waits for page load, and checks recognizable host-blocking/startup-error pages, required development scripts and Next.js HMR. DNS, refusal, timeouts, proxy rejection, certificate failures and development-channel errors have distinct diagnostics. Ordinary redirects, login pages and non-200 responses alone do not fail preflight.

Preflight failures are reported as runner/infrastructure errors before any test assertions run. The GUI no longer mistakes a failed preflight with zero executed tests for an upload containing no tests.

The GUI adds **Start Local App for Uploaded Tests**, **Stop Local App**, concise application/preflight status and expandable technical diagnostics. Generated and uploaded tests share the managed local application endpoint. Existing authentication/authorization, AI generation, preview recovery, screenshots, reports and Docker cleanup are preserved.

## Framework capabilities

| Framework/project | Detection and startup | Verification |
| --- | --- | --- |
| React/Vue/Svelte/SvelteKit/Solid/vanilla with Vite | Actual selected `vite` command; dependency-based descriptive label; explicit host/port and one additional allowed hostname | Vite 8.3.5 exercised end to end; React/Vue/SvelteKit/Solid labels have metadata tests, not separate framework app runs |
| Next.js | Existing `next dev`/`next start`; explicit host/port; localhost-origin forwarding through the existing proxy for host apps | Next 16.4.3 / React 19.3.1 exercised end to end, with unchanged fixture source/config |
| Angular | Existing `ng serve`; host/port and explicit `host.docker.internal` allowed-host CLI option | Detection/command tests; not installed or exercised end to end. Older CLI versions may reject the option |
| Nuxt, Astro | Existing direct `nuxt dev`/`nuxi dev` or `astro dev`; supported host/port flags; scoped Vite hostname environment | Detection/planning only; no real framework run |
| Remix / React Router | Distinguishes modern Vite development commands from classic `remix dev`; scoped environment and supported direct-command arguments | Detection/planning only; classic/wrapped commands rely on their own endpoint configuration |
| Create React App / Vue CLI | Existing scripts; host/port arguments where supported; environment supplied without disabling host checks | Existing detection regressions; restrictions unsupported by the installed server version receive manual scoped configuration guidance |
| Express / Fastify / generic Node | Existing development script; PORT/HOST environment, literal listener-port or advertised URL discovery | Express 5.3.0 exercised end to end; Fastify/generic variants depend on their actual script honoring configuration or announcing its endpoint |
| NestJS | `nest start` or matching Node entry with Nest dependencies; literal `src/main.ts` port when present | Detection/planning only; no blind unsupported Nest host flags |
| Flask | Source-backed Flask app object; selected `.venv` interpreter when available; Flask CLI with explicit host/port | Flask 3.1.3 exercised end to end |
| Django | `manage.py` evidence; explicit runserver host/port without automatic migrations | Detection/planning only; ALLOWED_HOSTS restrictions receive exact scoped guidance rather than settings-file edits |
| FastAPI | Source-backed FastAPI object; selected Python environment and uvicorn with explicit host/port | Detection/planning only; both FastAPI and uvicorn must already be installed |
| Streamlit / Gradio | Verified source imports and entry point; Streamlit CLI options or documented Gradio host/port environment | Planning only; not installed or run end to end |
| Static HTML/CSS/JS | Existing `index.html`; internal static server with no dependency installation | Exercised end to end; hidden paths, directory listing and paths/symlinks outside the root are blocked |

Startup scripts take precedence over unrelated installed dependencies. Detection does not imply that arbitrary wrappers or custom launch procedures are supported.

## Root causes reproduced

### Vite host rejection

The local hostname used inside Docker differs from Windows localhost. Vite's hostname allowlist can therefore reject a running, otherwise reachable server. A real controlled Vite run without the additional allowlist returned **403 / host_blocked**; the same fixture started by the adapter returned **200** for `Host: host.docker.internal`, without a config/source edit. Evidence: `test/fixtures/app-frameworks/verification/evidence/vite-host-check.json`.

The adapter supplies only `__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS=host.docker.internal`, following [Vite's server options](https://v8.vite.dev/config/server-options). It never sets `allowedHosts: true`. The user's original Vite app/version was not provided, so why an earlier installed Checkpoint version did not apply its previous Vite environment handling cannot be determined from this controlled reproduction.

### Development WebSockets and Next.js hydration

The initial Next fixture rendered its heading/background but remained noninteractive. Captured browser errors showed failed HMR WebSocket handshakes, and the unchanged increment assertion correctly failed. Squid normally removes Upgrade headers unless configured otherwise; see its [Upgrade directive](https://www.squid-cache.org/Doc/config/http_upgrade_request_protocols/).

The generated proxy configuration now permits WebSocket Upgrade **only for each already-allowed hostname and port**, with other upgrade protocols denied. Normal HTTP destination/private-network rules remain in force.

Next's development origin rules required an additional supported-origin endpoint. A loopback-only TCP forward inside the runner connects through the same proxy using CONNECT to **only the selected `host.docker.internal:port`**. The browser's BASE_URL becomes a real localhost endpoint inside that container. The forward does not rewrite Host, Origin, Cookie or Authorization headers; server authentication and CSRF/origin validation remain active. No next.config or hosts-file edit is made. Nonlocal requests still use the allowlist proxy. This fixed the original Next fixture without changing its assertion or application source. [Next's development-origin policy](https://nextjs.org/docs/app/api-reference/config/next-config-js/allowedDevOrigins) remains enabled.

### Port readiness race

Concurrent local app tests exposed a race between checking a free port and starting a process: another service could answer HTTP before the selected process failed to bind. Startup now checks OS listener ownership and process ancestry. It refuses to attach to a responding but unrelated listener. Windows uses PowerShell process/listener inspection; Unix requires lsof and ps.

## Security and recovery

- The existing native confirmation now describes the chosen application and explicitly requests permission to execute it and expose its development listener on all local interfaces. No app is started before that confirmation in the product.
- No source/configuration files or machine hosts entries are written by the adapters. Framework development servers may create their normal build/cache artifacts.
- No project dependencies are installed by product startup. Missing dependencies or interpreter tools produce actionable errors. Only isolated test dependencies were installed during this task, with the user's explicit permission.
- npm/pnpm startup uses ignore-scripts to suppress lifecycle setup hooks. Recognizable install/setup/migration commands in the selected startup script are refused. The chosen development command still executes application code and can have its own side effects; the trust confirmation remains necessary. See [npm's ignore-scripts behavior](https://docs.npmjs.com/cli/commands/npm-ci/).
- Framework host restrictions are configured through supported arguments/environment where available. The single Next localhost forward retains the proxy destination allowlist and does not add arbitrary outbound access.
- TLS certificate verification, application authentication and CSRF checks are not disabled. An untrusted development certificate requires a properly trusted certificate/CA; no global ignore flag is added.
- HTTP requests, connection attempts, readiness and preflight retries are bounded. Known permanent restrictions fail promptly. No test/container restart loops or assertion rewrites occur.
- Cancellation aborts pending startup checks and stops only managed process trees. Externally managed listeners are neither stopped nor silently reused.
- Application stdout/stderr is bounded and common password/token/API-key/authorization patterns are redacted. Preflight does not log arbitrary page bodies. Redaction is not a guarantee against an application deliberately printing secrets in arbitrary formats.

Documented options were checked against [Next CLI](https://nextjs.org/docs/app/api-reference/cli/next), [Flask CLI](https://flask.palletsprojects.com/en/stable/cli/), [Angular serve](https://angular.dev/cli/serve), [Nuxt CLI](https://github.com/nuxt/cli/blob/main/docs/dev.md), [Streamlit configuration](https://docs.streamlit.io/develop/concepts/configuration/options) and [Gradio environment variables](https://gradio.app/guides/environment-variables).

## Actual verification results

The isolated Electron harness loads the real index, renderer, preload and preview, with sandboxing/context isolation enabled and Node integration disabled. It uses the production LocalGuiApp and GuiTestRunner, Docker network, Squid/socat relay, Playwright, Xvfb, x11vnc and noVNC. It does not manually run Firebase sign-in; the authentication overlay is hidden only in the harness.

| Application | Host startup | Docker preflight | Interaction test | Actual Electron frames | Failure screenshot | Cleanup |
| --- | --- | --- | --- | --- | --- | --- |
| Vite | Passed | Passed | Passed | Pink and green | Present | Passed |
| Next.js, unchanged source/config | Passed | Passed | Passed | Pink and green | Present | Passed |
| Express | Passed | Passed | Passed | Pink and green | Present | Passed |
| Flask | Passed | Passed | Passed | Pink and green | Present | Passed |
| Static HTML | Passed | Passed | Passed | Pink and green | Present | Passed |

Each application ran the same two reviewed tests: a passing 16-click workflow and a deliberately incorrect `99` assertion to verify screenshot/failure reporting. Thus each run correctly reports **1 passed and 1 failed**, rather than falsely marking the controlled assertion as passed. The passing workflows were never weakened. Ordinary favicon 404 console messages did not become test failures.

For every application, the actual Electron iframe received a 1280×800 canvas with more than 100,000 sampled application-color pixels in **two different states while the interaction test was running**. Composed screenshots show Chromium, the correct framework heading and changing counters. This verifies application content and interaction updates, rather than merely a VNC handshake.

Final evidence:

- `test/fixtures/app-frameworks/verification/evidence/results.json`: five successful end-to-end checks and accurate deliberate failures.
- `evidence/{vite,next,express,python,static}-{green,pink}.png`: composed Electron captures.
- `evidence/startup-ownership.json`: final listener-ownership startup/cleanup checks against all five real servers.
- `evidence/vite-host-check.json`: actual blocked-versus-allowlisted Vite responses.
- `evidence/initial-run-results.json`: initial investigation, including the original Next interaction failure before the connectivity fix.

After verification, Docker listed no remaining `checkpoint-gui-*` containers and no isolated framework-verification Electron process remained. The user's existing application was left running.

Automated verification: **112 JavaScript tests passed**, including new detection, monorepo, missing dependency, setup rejection, fixed-port, readiness, streaming-response timeout, crash, cancellation, listener ownership, proxy, preflight and relay tests. **19 existing Python GUI-generation tests passed.** Syntax checks and `git diff --check` passed. Live AI model generation was not repeated.

## Files changed

- Adapters/startup: `guiAppAdapters.js`, `guiAppProcess.js`, `guiLocalApp.js`, `guiStaticServer.cjs`.
- Docker/browser connectivity: `guiTestRunner.js`, `gui-runner/runner.js`, `gui-runner/appPreflight.cjs`, `gui-runner/appRelay.cjs`, `gui-runner/connectivity.cjs`, `gui-runner/playwright.config.cjs`, `gui-runner/Dockerfile`.
- Product integration: `main.js`, `renderer.js`, `index.html`.
- Regressions: `test/gui_app_adapters.test.js`, `test/gui_app_preflight.test.js`, `test/gui_app_relay.test.js`, `test/gui_test_runner_test.js`.
- Controlled fixtures/dependency lock/evidence: `test/fixtures/app-frameworks/` and this report.

## Reproduce

Dependencies for verification live only under `test/fixtures/app-frameworks/node_modules` and its Python fixture `.venv`. These are not runtime product dependencies. The checked-in fixture lockfile records the Node versions; Flask 3.1.3 was used.

From the repository root, with Docker Desktop and the verification dependencies available, temporarily clear ELECTRON_RUN_AS_NODE if present, then launch:

```powershell
$savedElectronMode = $env:ELECTRON_RUN_AS_NODE
Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
try {
    Start-Process -FilePath '.\node_modules\electron\dist\electron.exe' -ArgumentList "$PWD\test\fixtures\app-frameworks\verification" -WindowStyle Hidden -Wait
} finally {
    if ($null -eq $savedElectronMode) { Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue }
    else { $env:ELECTRON_RUN_AS_NODE = $savedElectronMode }
}
```

The harness builds fingerprinted Docker images if needed and writes the evidence above. Run `npm.cmd test` for regressions. Only run fixture dependency installation explicitly; product startup does not perform it.

## Remaining limits

- Real end-to-end verification covers the five listed frameworks on Windows/Docker Desktop. Other adapter commands have detection/planning coverage, not proof of complete framework compatibility. Mac/Linux process inspection and host-network routing were not exercised.
- Custom Python factories/ASGI object layouts, Poetry/uv environment selection, Yarn-version-dependent lifecycle semantics, arbitrary package managers, shell wrappers and daemonizing commands are not automatically resolved. Ambiguous/unsupported entries receive an error or application choice.
- Generic backend startup must honor its supplied port/host environment, expose a statically detectable literal listener port, or advertise a recognizable local URL. Arbitrary computed/custom endpoints cannot always be inferred.
- Complex/dynamic framework base-path, HTTPS and hostname configuration cannot be safely inferred in every project. Literal values and reported URLs are supported; untrusted certificates and unsupported host/origin restrictions require explicit scoped manual configuration.
- The Next localhost endpoint changes the browser origin to the isolated forwarded URL. Applications requiring an externally fixed OAuth callback, cookie domain or absolute hostname need their own matching development configuration. No authentication/origin headers are rewritten to disguise that difference.
- A login page responding normally passes network preflight; tests still need legitimate authentication/data setup. Successful preflight does not prove application correctness or the absence of later runtime/network failures.
- No automatic server restart is attempted after an unresolved crash/restriction. Supported host settings are applied before startup, transient readiness failures are retried, and permanent failures are shown rather than hidden.
- Full manual Firebase/GitHub/Run Operations/View Insights workflows were not repeated. Their existing automated regressions passed; preview reconnection code and existing security settings were preserved.
