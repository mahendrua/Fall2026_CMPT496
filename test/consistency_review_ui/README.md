# Consistency Review screen: demo results and verification

Tools for trying and checking the Consistency Review screen (View Insights →
Consistency Review) without a model and without touching real outputs.

| File | What it does |
|---|---|
| `make_demo.py` | Builds codebases from the tracked evaluation fixtures (`test/consistency_eval`), checks them with the real checker and offline stand-in models, and saves one result per screen state in an isolated folder |
| `backend_shim.py` | Starts the real backend (`main.py`) with `APP_DIR` pointed at that folder and no real model. Generation agents and database builds are refused and logged. With `CR_SHIM_MODELS=oracle`, checks started from the screen get an offline stand-in that answers from the tracked expectations; every model created and call made goes to `MODEL_LOG.jsonl`, and each call reports 1,000 input and 200 output tokens through the real usage callback. Full-pipeline steps are replaced by stand-ins that keep the real stage machinery but only rewrite existing outputs. `SHIM_CONTROL.json` can slow calls, announce a provider wait, fail a codebase with a daily-limit error, or fail one pipeline step |
| `verify_screen.js` | Starts the real app (`main.js`, preload, renderer, backend through the shim, offline stand-in model), drives the review screen through every state and the Operations check workflow, and saves screenshots and `report.json` |

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
| no_outputs | no_outputs | Source code with no pipeline outputs: a new check cannot start |
| missing_outputs | missing_outputs | A summary but no rule or test outputs: a new check is incomplete |
| many_findings | many_findings | 30 findings (a copy of ignored_parameter with generated findings), for paging |

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
- In both modes **Operations → Consistency Review** works offline: the
  stand-in answers from the tracked fixture expectations, so no API key or
  quota is used. Its answers are scripted, not a real model's judgement.

## What the walk-through covers

The review screen (Part 4): every result state, evidence, verdicts, skipped
and undetermined tabs, current versus stored content, review saving and
persistence, HTML shown as text, and that browsing creates no model.

The Operations check (Part 5): the button among the other operations, the
running screen (backend progress, no percentage, the Cancel note), completed,
incomplete and failed (daily limit) outcomes, provider waits without
resubmission, no call for a codebase without outputs, duplicate clicks and
Enter starting one check, unsaved review notes blocking a run and being
discarded, Cancel keeping the previous result, the exact result shown in View
Insights, a review saved on it surviving project switches, whole-codebase runs
leaving the selected-files result alone, and that no pipeline output changes
and no generation agent starts.

The full pipeline (Part 6): the pipeline button's note and the token estimate
(the review stage as a lower bound), the review as the last stage after every
generation step, its progress without a step percentage, generation and
review outcomes reported separately, the exact saved check opened from the
completion screen, a failed review not failing the pipeline, a failed
generation step stopping before the review and the previous review labelled
as from an earlier run, unsaved review notes blocking a start, and repeated
clicks and Enter starting one pipeline.

Feature-wide: a pipeline run's usage recorded once, on the review stage (run
totals equal that stage, the token meter has no double-counting pipeline row),
an independent check logged as its own run and shown in AI Usage, 30 findings
paged 25 at a time with search across pages, and the review screen in the dark
theme (set on the page only, so the saved theme is not changed) with a text
contrast check.

The offline tests `test/other_tests/consistency_review_demo_test.py` check the
demo states without Electron. The pipeline stage is also tested without
Electron in `test/other_tests/consistency_pipeline_test.py` (stage order, run
identity, usage counted once, leftover outputs not compared, provider errors,
a crashed stage, saved reviews kept across reruns). Partial results keeping
their findings after a provider error, scope and codebase isolation and older
saved results are covered by the checker and store tests in
`test/other_tests/`.

## Limits of this verification

- All model answers are scripted from the tracked fixtures. These checks show
  that the workflow records, shows and keeps what the checker returns; they
  say nothing about how accurate a real model's verdicts are.
- The fixtures are small codebases. Nothing here measures behaviour or cost on
  large codebases.
- What the checker itself does not cover, and the screen reports as such:
  dependencies across files are only partly followed (the code in scope is
  one file or method), folder-level rules are skipped, and inputs too large
  for one request are left unassessed rather than truncated.
- Cancel stops the backend process; a request already sent to the provider
  may still be processed and billed by it. The walk-through checks only that
  Cancel keeps the previous result.
