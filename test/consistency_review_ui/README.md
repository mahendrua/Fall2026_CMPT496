# Consistency Review screen: demo results and verification

Tools for trying and checking the Consistency Review screen (View Insights →
Consistency Review) without a model and without touching real outputs.

| File | What it does |
|---|---|
| `make_demo.py` | Builds codebases from the tracked evaluation fixtures (`test/consistency_eval`), checks them with the real checker and offline stand-in models, and saves one result per screen state in an isolated folder |
| `backend_shim.py` | Starts the real backend (`main.py`) with `APP_DIR` pointed at that folder and model creation disabled (a request leaves a `MODEL_REQUESTED` file) |
| `verify_screen.js` | Starts the real app (`main.js`, preload, renderer, backend through the shim), drives the screen through every state, and saves screenshots and `report.json` |

## Demo states

| State | Codebase | What it shows |
|---|---|---|
| findings | ignored_parameter | Whole-project and selected checks with two mismatches, from an earlier run than the latest logged one |
| clean | misleading_name_accurate | Completed, no mismatches in the assessed artifacts |
| skipped | inclusive_boundary | A mismatch plus a skipped folder-level rule; the source was edited after the check |
| partial_clean | reference_problems | Incomplete (ambiguous summary, stale test), no mismatches |
| partial_findings | validation_and_state | Incomplete (a comparison not assessed), with mismatches |
| failed | summary_injection | Every model call failed |
| unreadable | side_effects | The saved file is damaged |
| older | same_file_helper | Saved without verdicts, skipped and undetermined lists |
| injection | exclusive_boundary_comments | HTML and script tags in titles, claims, reasons and a review note |
| missing_source | arithmetic_shared_mistake | The source file was deleted after the check |
| undetermined | cross_file_dependency | One comparison could not be decided |
| no_check | collection_order_js | Never checked |

## Commands (PowerShell, from the repository root)

```powershell
# 1. Build the demo results somewhere temporary
.venv\Scripts\python.exe test\consistency_review_ui\make_demo.py $env:TEMP\cr_demo

# 2a. Open the real app on the demo results and use it yourself
$env:ELECTRON_RUN_AS_NODE = $null
node_modules\.bin\electron test\consistency_review_ui\verify_screen.js $env:TEMP\cr_demo\manifest.json --interactive

# 2b. Or run the automated walk-through (screenshots and report.json in the output folder)
node_modules\.bin\electron test\consistency_review_ui\verify_screen.js $env:TEMP\cr_demo\manifest.json $env:TEMP\cr_shots
```

- `--interactive` opens on the "findings" state; name another one to start
  there, e.g. `--interactive injection`. The console lists every demo codebase
  path; to switch, paste one into the codebase path on the Run Operations page,
  press Submit, then go back to View Insights → Consistency Review.
- `ELECTRON_RUN_AS_NODE` must be unset: some editors (VS Code) set it, and
  Electron then runs as plain Node.
- Rebuild the demo (step 1) before each automated run: the walk-through saves
  reviews and edits one saved result to test a refused save.
- `main.js` clears the repository's `error_log.json` on start; the script puts
  your copy back when it exits normally.

The offline tests `test/other_tests/consistency_review_demo_test.py` check the
demo states without Electron.
