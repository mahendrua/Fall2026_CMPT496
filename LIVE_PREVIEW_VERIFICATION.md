# Live Chromium preview: root cause and verification

Verified October 30, 2026.

## Exact root cause

`index.html` used `frame-src 'self' data:`. The application is loaded from a local file, while `GuiTestRunner` returns `http://127.0.0.1:<dynamic-port>/gui-preview.html`. Electron therefore blocked the preview document before its noVNC JavaScript could run. The separately allowed loopback WebSocket continued delivering test results, and Playwright screenshots were unaffected.

The integration harness reproduced the old policy's `ERR_BLOCKED_BY_CSP` and recorded `legacyPolicyBlocked: true`. With the corrected policy, the actual production index loaded the preview without a frame-policy violation. The failure was at the Electron iframe boundary, rather than Chromium → Xvfb → VNC.

## Fixes

- Permit `http://127.0.0.1:*` in `frame-src`. No remote HTTP origins were added. Context isolation, renderer sandboxing, default web security, and Node isolation remain enabled.
- Add parent-visible preview status messages with source-window and exact-origin checks. Distinguish frame policy failures, iframe load errors, delayed initialization, VNC reconnect messages and tests ending before preview initialization.
- Inspect Xvfb's actual window list with `xprop` during tests. Report a visible Chromium window, a minimized window, a missing window, a wrong DISPLAY, or evidence of headless Chromium. These diagnostics do not change test outcomes.
- Add optional Demo Mode: 600 ms Playwright `slowMo`, passed through both manual and generated execution paths. Normal mode uses zero slowMo. Test assertions and retries are unchanged. The option is fixed for the active run.
- Keep existing noVNC reconnection, preview-independent execution, failure screenshots, and test reports.

CSS inspection and runtime measurements found no zero-sized or hidden iframe after the GUI page was selected. No unrelated CSS changes were needed.

## Real end-to-end verification

The opt-in Electron harness uses the real `GuiTestRunner`, its internal Docker network, Squid allowlist proxy and socat preview relay, the real Playwright configuration/reporter, Xvfb/x11vnc/noVNC, and the actual `index.html`, `preload.js` and `renderer.js`. It serves a controlled local application on the host and allows its Docker host destination through the production proxy.

The harness creates an isolated Electron BrowserWindow with `sandbox: true`, `contextIsolation: true`, `nodeIntegration: false`, and default web security. It hides the authentication overlay only within the harness; it does not modify authentication or bypass IPC authorization in the product. Production main-process authorization is not replaced or disabled.

Recorded evidence in `test/fixtures/live-preview/evidence/results.json`:

- The original frame policy blocked the loopback document.
- Chromium was visible on Xvfb `:99` according to the production display diagnostic.
- Electron's actual noVNC canvas was **1280 × 800**. The iframe measured approximately **885 × 498** CSS pixels.
- Two distinct application states reached that canvas: over **106,000 sampled pink pixels** and then over **106,000 sampled green pixels**.
- Composed Electron screenshots show the actual Chromium title/address bar, the application heading, text filled by Playwright, and different click counters/colors while the test status is RUNNING:
  - `evidence/electron-pink.png`
  - `evidence/electron-green.png`
- The interaction/navigation test passed. The deliberately incorrect second assertion failed and included a failure screenshot. Completion totals were 2 tests, 1 passed, 1 failed, accurately reported through the renderer.
- The runner's normal monitor completed cleanup; `cleanedUp: true`. A subsequent Docker listing found no remaining `checkpoint-gui-*` containers. Only the user's normal Electron application remained running.

These are captures used as verification artifacts. The product still displays live VNC frames, not a screenshot slideshow.

## Automated tests

`npm.cmd test`: **94 passed, 0 failed**. New tests cover the narrow frame policy, headed Chromium and normal/demo launch settings, and visible/minimized/headless/incorrect/unavailable display diagnostics. Existing preview recovery, GUI reporting, screenshot evidence, local app, Firebase and GitHub regressions remain passing.

JavaScript syntax checks and `git diff --check` passed.

## Files changed for this follow-up

`index.html`, `renderer.js`, `preload.js`, `main.js`, `guiTestRunner.js`, `gui-runner/playwright.config.cjs`, `gui-runner/gui-preview.html`, `gui-runner/runner.js`, `gui-runner/Dockerfile`, new `gui-runner/displayDiagnostics.cjs`, new `test/gui_preview_visibility.test.js`, the `test/fixtures/live-preview/` integration harness and evidence, and this report. Prior task changes were preserved.

## Reproduce

Run from the repository root with Docker Desktop available. In PowerShell, temporarily clear `ELECTRON_RUN_AS_NODE` if your environment sets it, then launch the fixture app and restore the variable afterward:

```powershell
$savedElectronMode = $env:ELECTRON_RUN_AS_NODE
Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
try {
    Start-Process -FilePath '.\node_modules\electron\dist\electron.exe' -ArgumentList "$PWD\test\fixtures\live-preview" -WindowStyle Hidden -Wait
} finally {
    if ($null -eq $savedElectronMode) { Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue }
    else { $env:ELECTRON_RUN_AS_NODE = $savedElectronMode }
}
```

The fixture intentionally includes one failed assertion; the harness succeeds only when live color frames, both test results, a failure screenshot and cleanup are observed. Docker images build automatically if their context fingerprints changed.

## Limits

- The controlled application was used, rather than the user's original unavailable Button Lab files. Generated tests may still contain unsupported expectations; this fix does not alter them.
- A very short normal run can finish before the viewer shows useful interactions. Enable Demo Mode before starting such a run; completed tests are never restarted to produce a preview.
- Initial repeated harness attempts sharing Electron cache state produced cache errors or missed frame captures. The successful final reproduction uses an isolated temporary profile, software rendering and background throttling disabled in the harness only. Those settings were not applied to the production app.
- The complete production Firebase sign-in flow was not repeated manually. The actual preview UI/preload/event flow and Docker path were exercised, with automated auth regressions passing.
