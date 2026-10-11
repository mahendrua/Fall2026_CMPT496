# Checkpoint

## Purpose
This repository contains a tool which can be used to perform analysis and test generation for large codebases. The tool creates as output:
- Summaries of each file
- Code structure information for each file, including PlantUML
- Summaries of the contents of each directory
- A summary of the entire repository
- A list of business rules extracted from the codebase
- UML reports for each given file
- Unit tests for the codebase
- A test report for all generated unit tests

This is all completed using LangGraph, with an LLM acting as the generator of outputs.

## Installation
- If you are a user then navigate to the releases tab of this repository and follow the instructions under the latest release
- If you are a dev then continue reading to setup the dev environment on your device

## Notes
- This tool is currently able to parse codebases written in C# or Javascript for the vector store component. For the unit test component only C# is supported. Other languages are not supported
- The system defaults to using Gemini 3 Flash, given a valid API key in a .env file. This file is generated automatically
- The system is mostly set up to support the use of other models. Additional steps required: Either the agents should be instantiated and run manually, by passing in the desired model, or main.py should be modified slightly to do so
- When running the tool, a filepath to the target codebase is needed. Use absolute paths
- It is recommended to store the target codebase in a directory in the root of the project called "targetCodebases", as this directory is already included in the .gitignore. The target codebase can be located anywhere though

## Requirements

**Python 3.13+** is required for this project.
**Java 11+** is required for this project.
**DotNet 10+** is required for this project.

Download Python: [https://www.python.org/downloads/](https://www.python.org/downloads/)
Downlaod Java: 
Download Dotnet: 

## Setup Instructions

1. **Clone the repository**

2. **Navigate to the directory containing the project folder:**
   ```powershell
   cd path/to/project
   ```

3. **Create a virtual environment:**
   ```powershell
   python -m venv .venv
   ```

4. **Activate the virtual environment:**
   ```powershell
   .\.venv\Scripts\Activate.ps1
   ```

5. **Install Python dependencies:**
   ```powershell
   pip install -r requirements.txt
   ```

6. **Install Node.js dependencies:**
   ```powershell
   npm install
   ```

7. **Run the program:**
   ```powershell
   npm.cmd start
   ```

## Deactivate Virtual Environment
```powershell
deactivate
```

## Convert JSON summaries to Markdown

```powershell
# default: reads `agent/file_summary_agent_output` and writes to `<input-dir>/markdown`
python -m utils.json_to_markdown

# print to stdout for quick verification
python -m utils.json_to_markdown --stdout

# specify input/output and avoid overwriting
python -m utils.json_to_markdown --input-dir agent/file_summary_agent_output --output-dir ./markdown --no-overwrite
```

Flags:
- `--input-dir`: directory containing JSON summary files (default: `agent/file_summary_agent_output`)
- `--output-dir`: directory to write markdown files (default: `<input-dir>/markdown`)
- `--stdout`: print results instead of writing files
- `--overwrite` / `--no-overwrite`: controls replacing existing `.md` files (default: overwrite enabled)
- `--ext`: file extension to search for (default: `.json`)

Notes:
- Run the command from the project root so the default input path resolves correctly.

## Verifying combined business rule validation (US-028)

Business rule validation reads both file-level and folder-level rules:

| Input | Written by | Path |
|---|---|---|
| File-level rules | Create JSON Summaries | `agent/file_summary_agent_output/<codebase>/business_rules/business_rules.json` |
| Folder-level rules (`observed_rules`, `inferred_rules`) | Create Directory Summaries & Business Rules | `agent/directory_agent_output/<codebase>/business_rules/business_rules.json` |

`validate_business_rules` loads both, merges exact duplicates (same folder, same text apart from extra spaces), and sends the result to the BR agent. Results are written to `agent/BR_agent_output/<codebase>/validated_rules.json` and `discarded_rules.json`. Each rule lists its `source_directory` (`.` is the codebase root), `source_file_paths`, and `provenance`: one entry per origin (`file`, `directory_observed` or `directory_inferred`) with that origin's source files. Folder rules have no source files.

### Offline tests (no API key, AI calls or vector database)

With the virtual environment active (see Setup Instructions), install pytest if needed and run from the project root:

```powershell
pip install pytest
python -m pytest test/other_tests/BR_input_loader_test.py test/other_tests/BR_rule_dedup_test.py test/other_tests/BR_validation_integration_test.py
```

These use temporary files and a fake LLM and vector store. They check loading, path handling, duplicate merging, folder grouping, provenance through condensation and validation, error handling, the full pipeline call path, and the unit/integration test commands reading the results. They do not show how a real model condenses or validates rules.

### Manual check in the app (uses live AI calls)

The first three steps send code to the configured AI model and use API tokens.

1. Run `npm.cmd start`, choose **Run Operations** (it needs a key saved under **Manage API Keys**), pick the codebase with 📂 and click **Submit**.
2. Click **Create Code Database**, then **Create JSON Summaries**, then **Create Summary Database from JSON**, then **Create Directory Summaries & Business Rules**. Both `business_rules.json` files above should now exist.
3. Click **Business Rule Validation**, then **Run All Business Rules**.
4. Choose **View Insights**, then **Business Rules**, then **Validated Business Rules** and **Discarded Business Rules**.
5. Confirm folder provenance: rules from the folder output have `provenance` entries with origin `directory_observed` or `directory_inferred`, `source_file_paths: []`, and the folder's `source_directory` (`.` for the codebase root). A folder rule may be discarded; it must not be missing from both files.
6. Confirm duplicate handling: find a rule text that appears in both the file-level output and the folder-level output for the same folder. It appears once in the results, with both a `file` and a `directory_observed`/`directory_inferred` provenance entry. The same text in two different folders stays as two rules.

**Full Codebase Analysis Pipeline** runs the same validation step after the summaries, and its Complete screen shows any missing-input warning.

### Expected behaviour for missing, empty and malformed inputs

| Situation | Result |
|---|---|
| One default input file is missing | The other is validated; the step finishes with a warning naming the missing file |
| Both default input files are missing | The step fails: "No business rule inputs found…" with both expected paths |
| A custom `rules_path` or `directory_rules_path` does not exist | The step fails, naming that path. A custom file-level path does not add the default folder rules |
| A file contains `{}` or only empty rule lists | `validated_rules.json` and `discarded_rules.json` are written as `[]`, with no AI calls |
| Malformed JSON or a wrong field | The step fails, naming the file and the entry (and line/column for malformed JSON) |

To try these without touching real outputs, copy the codebase's two `business_rules.json` files somewhere safe first and restore them afterwards, or run the offline tests above, which cover every row.

### Short demo

1. Run the offline tests and show them passing.
2. In the app, run **Business Rule Validation** → **Run All Business Rules** on a codebase that already has both rule files.
3. Open **Discarded Business Rules** and **Validated Business Rules** and point out a `directory_inferred` rule, a root (`.`) folder rule, and a rule with both `file` and folder provenance.
4. Rename the folder-level `business_rules.json`, run validation again, and show the missing-input warning; then rename it back.

## Consistency Review screen

Consistency Review compares source code with its generated summaries, business
rules and unit tests, and flags disagreements for a person to review. A
finding says that an artifact and the code disagree; it never says which one
is right. Intended behaviour is always shown as unknown, because there are no
confirmed requirements in the app.

### Opening it

1. Select a codebase (Run Operations → enter or browse to the path → Submit).
2. View Insights → **Consistency Review**.

The screen shows the latest saved check for that codebase. Whole-project and
selected-files checks are kept apart; use the buttons at the top to switch.
Opening the screen only reads saved results: it does not run a check or call
a model.

### What works now

- The check's identity (check ID, run, time, selection), with a label when it
  comes from an earlier run than the most recent logged one.
- Its status, told apart: completed with mismatches, **no mismatches found in
  assessed artifacts**, incomplete, failed, no saved check, or unreadable.
  Zero findings never reads as "the code is correct".
- Counts of findings, unresolved reviews, compared artifacts, verdicts,
  skipped items, undetermined comparisons and errors; errors, missing
  artifacts and limitations listed.
- Findings with search and file, category and review-status filters.
- For each finding: the conflicting claims side by side, the evidence stored
  with the check (with its origin and line numbers), uncertainty, rule
  provenance, and whether each artifact has changed since the check. Changed
  content is shown separately as current content, never in place of the
  stored evidence.
- Every verdict with its reason, skipped artifacts and undetermined
  comparisons, each in its own tab.
- Saving a review: unresolved, reviewed or dismissed, with an optional note.
  Unsaved notes are kept while you move around the app. A review is a record
  of your decision only; it changes nothing in the code, artifacts, findings
  or verdicts, and "reviewed" or "dismissed" does not mean anything is
  correct. Reviews belong to the finding (same disagreement about the same
  content), so the same finding shows its review in either scope.

### Getting a result to look at

There is no button to run a check yet (Part 5). Until then, results come from
the checker's Python entry point (a paid model call):

```powershell
.venv\Scripts\python.exe -c "from agent.consistency_checker import run_consistency_check as r; print(r(r'C:\path\to\Codebase', save=True).status)"
```

To look at the screen without a model or real outputs, use the isolated demo
results in [test/consistency_review_ui](test/consistency_review_ui/README.md).

### Tests

```powershell
.venv\Scripts\python.exe -m pytest test/other_tests/consistency_review_backend_test.py test/other_tests/consistency_review_demo_test.py
node --test test/other_tests/consistency_review_model.test.js
```

The real-app walk-through (screenshots of every state) is described in
[test/consistency_review_ui/README.md](test/consistency_review_ui/README.md).

### Not yet connected

- Part 5: a button to run a check (whole project or selected files) from the app.
- Part 6: running the check automatically after the full pipeline.
- A packaged backend (`releases/main/main.exe`) built before this change does not
  have the screen's backend commands; rebuild it (below) before packaging.

## Packaging the backend using Pyinstaller

Package backend using main.spec found in releases. Copy and paste any needed programs in before going to the next step. Stuff like plantuml.jar

Use this command to package the backend assuming the project is located directly in your c drive. Otherwise use the correct path to your project
```powershell
pyinstaller --clean --distpath "C:\CMPT-496-Capstone\releases" --workpath "C:\CMPT-496-Capstone\build" "releases\main.spec"
```

## Packaging the electron application

```powershell
npm run dist
```
