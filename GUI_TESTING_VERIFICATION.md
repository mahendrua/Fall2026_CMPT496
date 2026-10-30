# GUI testing reliability verification

Verified on October 30, 2026. No Button Lab files were found in this repository.

Follow-up: `LIVE_PREVIEW_VERIFICATION.md` records the subsequently reproduced Electron frame-policy block, its fix, and actual browser-frame captures through the complete production preview path. The Electron and proxy verification limits below describe this initial investigation and are superseded by that follow-up where stated.

## Findings and changes

- The preview constructed a single noVNC RFB client and only changed a status label on disconnect. It had no timeout or reconnection. Added an independently bounded connection controller with stale-client protection, authentication diagnostics, normal completion, and cleanup. Reconnection never starts tests or containers.
- Xvfb, x11vnc and fluxbox started concurrently. A failed initial display attachment could leave the preview unavailable. Added an X display readiness probe before starting dependent services. This race was found by source inspection; the user's original intermittent freeze was not reproduced with its original session logs.
- The runner required a `viewer_ready` message and converted its absence into a failed run after 30 seconds. Tests now start when the application and proxy are ready, independently of preview connectivity.
- Real controlled failures showed that `diagnoseFailure` classified wrong values on matched elements as locator timeouts because Playwright also printed "waiting for locator". Corrected this ordering without changing assertions. This also prevents those assertion failures from entering the existing automatic selector-repair path.
- Added separate browser closure, ambiguous selector and navigation diagnostics. Classifications retain uncertainty about application versus test defects.
- Added BASE_URL to Playwright configuration, failure screenshots (bounded to 256 KiB each), screenshot display/report export, assumption display, and more explicit generation instructions for stable selectors, web-first synchronization and isolated state. Existing CommonJS, URL normalization, syntax repair and conservative selector-repair instructions remain.
- Suppressed stale/cancelled container-monitor exit events so cleanup of a previous run cannot terminate a new run's event connection. Exit code 1 is no longer described as an unexpected container crash by itself.
- The default npm test glob omitted the existing `*_test.js` GUI tests. Included those files in the default suite.

## Verification results

| Check | Result |
| --- | --- |
| JavaScript suite including GUI, auth, GitHub and report regressions | 91 passed |
| Python GUI generator regression suite | 19 passed |
| JavaScript syntax checks | Passed for renderer, runner, preview controller and controlled specs |
| Actual Docker runner, buggy controlled app | 4 failed, 1 passed; all four intentional defects detected |
| Same reviewed specs, fixed controlled app | 5 passed, 0 failed |
| Real noVNC preview, both modes | Framebuffer dimensions verified; forced WebSocket closure recovered successfully |
| Failure evidence | Screenshots present for all four intentional failures; all classified as assertion mismatches |
| Termination | Both containers stopped naturally; buggy exit 1, fixed exit 0; neither OOM-killed |
| Cleanup | Five task-created verification containers removed |

No false positives or missed defects occurred within this five-test controlled comparison. This does not establish a false-positive rate for arbitrary AI-generated tests.

## Files changed

- Preview and Docker startup: `gui-runner/previewConnection.js`, `gui-runner/gui-preview.html`, `gui-runner/entrypoint.sh`, `gui-runner/Dockerfile`.
- Execution and reporting: `gui-runner/runner.js`, `gui-runner/playwright.config.cjs`, `gui-runner/reporter.cjs`, `guiTestRunner.js`, `guiTestReports.js`, `renderer.js`.
- Generation: `agent/gui_test_agent.py`.
- Regressions and evidence: `package.json`, `test/gui_preview_connection.test.js`, `test/gui_test_reports_test.js`, `test/fixtures/gui-reliability/bootstrap.cjs`, `test/fixtures/gui-reliability/behavior.spec.js`, `test/fixtures/gui-reliability/observe-preview.cjs`, `test/fixtures/gui-reliability/verification-results.json`, and this report.

The controlled app and identical reviewed specs are in `test/fixtures/gui-reliability/`.
Compact captured Docker evidence is in `verification-results.json` there. The bootstrap serves a local app and readiness-only proxy socket; localhost traffic bypasses that socket. The production runner, entrypoint, Playwright configuration, reporter, Xvfb, x11vnc and noVNC are used directly.

## Reproduce the controlled run

Run from the repository root in PowerShell, with Docker Desktop running:

```powershell
docker build -t checkpoint-preview-verification ./gui-runner
$fixturePath = Join-Path (Get-Location) 'test/fixtures/gui-reliability'
docker run --name checkpoint-controlled-check --shm-size 512m --env BUGGY=1 --env VNC_PASSWORD=verify --env GUI_EVENT_TOKEN=verify --env HTTPS_PROXY=http://127.0.0.1:3128 --env BASE_URL=http://127.0.0.1:8080 --mount "type=bind,source=$fixturePath,target=/opt/gui-runner/tests,readonly" --entrypoint bash checkpoint-preview-verification -c 'node /opt/gui-runner/tests/bootstrap.cjs & exec bash /opt/gui-runner/entrypoint.sh'
docker rm checkpoint-controlled-check
```

Use `BUGGY=0` for the fixed mode. The buggy command intentionally exits 1. Logs include `VERIFICATION_EVENT` and `PREVIEW_VERIFIED`. These credentials are only for this unexposed local fixture; production still generates random per-run credentials.

## Limits and outstanding verification

- The user's original Button Lab source, generated specs and failure artifacts were unavailable. Individual original failures cannot be attributed to application bugs, generated expectations, selectors or environment without them.
- The Docker comparison used reviewed controlled specs, not a new live LLM generation request. Python regressions exercise the generation/validation pipeline with controlled model responses. Actual model quality remains unverified.
- Full interactive Electron workflows and the production Squid/host-port path were not exercised end to end. Existing automated auth, GitHub and GUI tests passed; that is not equivalent to manual verification of every protected feature.
- Browser crash and navigation classifications have automated error-evidence coverage. A real forced Chromium crash, Docker kill during an active run, and sustained frame updates across all navigations were not explicitly fault-injected in Docker.
- The preview recovers existing reachable VNC sessions. It cannot restore a destroyed container or browser process; exhausted recovery reports the failure while preserving test execution.
- The first sandboxed local-app and Python runs encountered process/filesystem restrictions. Their approved reruns passed. Only the GUI Python suite was run, not every Python test in the repository.
