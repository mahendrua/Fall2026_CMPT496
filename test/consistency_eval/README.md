# Consistency Review evaluation

Small cases for checking how the Consistency Review checker
(`agent/consistency_checker.py`) behaves, and a runner that scores it.

| File | What it holds |
|---|---|
| `eval_cases.py` | The cases: source, generated artifacts, and the expected verdict for every artifact, written before any model run |
| `eval_fixtures.py` | Writes a case to a temporary folder with the pipeline's own schemas |
| `eval_scoring.py` | Scores a check result against the expectations, and totals runs |
| `eval_fakes.py` | The offline oracle, an always-agreeing fake, and a recorder for real models |
| `run_eval.py` | Runs cases and prints and saves a report |

Run outputs go to `runs/` (ignored by git). Nothing is read from or written to
the real `agent/` outputs or `targetCodebases/`.

## Commands

From the repository root:

```powershell
# List cases and expectations
.venv\Scripts\python.exe test\consistency_eval\run_eval.py --list

# Offline (default): the oracle answers, no model is called
.venv\Scripts\python.exe test\consistency_eval\run_eval.py

# Real model (paid): needs --real and a call budget
.venv\Scripts\python.exe test\consistency_eval\run_eval.py --real --cases inclusive_boundary,summary_injection --repeat 2 --max-calls 4

# Offline tests of the cases, scorer and runner
.venv\Scripts\python.exe -m pytest test/other_tests/consistency_eval_test.py
```

`--real` uses the key and model selected in Manage API Keys. Each target is one
call per repeat, plus at most one re-ask if an answer cannot be read. The
runner refuses to start if the batch needs more calls than `--max-calls`
allows, and never runs more than 60 in one go.

## Reading the scores

| Expected | Outcomes |
|---|---|
| inconsistent | `detected` (right category and a supporting line cited), `detected_weak`, `missed`, `abstained`, `rejected` |
| consistent | `correct`, `false_alarm`, `abstained`, `rejected` |
| insufficient_evidence | `correct`, `overconfident`, `unsupported_finding`, `abstained` |
| ambiguous | the verdict is recorded and never scored |
| not_assessed | `correct`, `unexpected_assessment` |

- `not_assessed` in a run that should have assessed the artifact means the
  check was incomplete (see `runs_with_errors`). It is never a clean negative.
- Titles, wording and exact line ranges are not scored. Evidence counts if the
  finding's source lines include any line listed for that expectation.
- Tuning cases (seen while the checker was built) are totalled apart from the
  held-out cases.
- The oracle only proves that the fixtures, validation and scoring agree. A
  handful of real runs shows how one model behaved on these cases, not general
  accuracy.

## What the checker cannot do (and the cases do not claim)

- It sends one file at a time. Behaviour that lives in another file is
  `insufficient_evidence` at best (`cross_file_dependency`).
- Folder-level rules name no file and are reported as skipped, never assessed.
- Intended behaviour is always unknown: comments are not requirements, and the
  app has no confirmed-requirements store.
