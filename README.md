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

## Firebase Authentication (Checkpoint)

Checkpoint includes the Firebase Web app configuration for the `checkpoint-cmpt497` project. Before signing in, make sure Email/Password is enabled:

1. In Firebase Console, open the `checkpoint-cmpt497` project.
2. Open **Authentication → Sign-in method** and enable **Email/Password**.
3. If you use another Firebase project, create `firebase-config.json` in Checkpoint's Electron user-data directory. Checkpoint displays the exact full path on the sign-in screen; on Windows it is under `%APPDATA%`. Use this shape, replacing every value with that project's Web app settings:

   ```json
   {
     "apiKey": "YOUR_FIREBASE_WEB_API_KEY",
     "authDomain": "YOUR_FIREBASE_AUTH_DOMAIN",
     "projectId": "YOUR_FIREBASE_PROJECT_ID",
     "appId": "YOUR_FIREBASE_APP_ID"
   }
   ```

4. Restart Checkpoint after adding or changing the override. New users can then register, and existing users can sign in or request a Firebase password-reset email.

The Firebase Web app configuration in `firebase-config.json` is public client configuration, not an Admin SDK credential. A per-user override takes precedence over the bundled settings. Never put a service-account key in this application. Firebase Authentication sessions are stored using Electron's operating-system-backed `safeStorage`; persistent sign-in requires the OS keychain/credential store to be available. The **Remember me** checkbox controls whether the Firebase session is kept securely between launches. User role records are stored locally under the app's `profiles` user-data folder, keyed by Firebase UID; they are not synchronized to Firebase. Passwords are handled only by Firebase Authentication and are never included in those profile files.

Firebase sign-in identifies the Checkpoint account; it does not make an untrusted local project safe to execute. The existing Docker and GUI-testing execution boundaries remain in effect.

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

## Manual GUI testing

The **GUI Test Cases** screen runs uploaded JavaScript and TypeScript Playwright tests in a separate Docker environment. Docker Desktop must be installed, running Linux containers, and have at least 2 GB of available memory. The Playwright and proxy images are built automatically on the first run and rebuilt when their bundled Docker contexts change; building requires an internet connection.

Upload up to 20 `.js` or `.ts` files (2 MB each, 20 MB total). Select test files to run; unselected uploads remain available as relative helper modules. Tests should use `@playwright/test` or `playwright/test`; additional npm packages are not installed.

Before running, enter each allowed website hostname on its own line. Hostnames allow HTTP and HTTPS on ports 80 and 443; adding a port also allows that port, for example `app.example.com:5173`. Host entries match exactly; prefix with `*.` to allow the hostname and its subdomains. Private network destinations are blocked. To test a web app running on the host machine, use `host.docker.internal:<port>` instead of `localhost` and explicitly allow it.

The application mounts only copied test files into the runner, read-only, and runs the browser in a resource-limited container. Browser traffic is routed through a separate allowlisting proxy; the live browser preview and status stream are relayed through loopback-only host ports, while the runner remains isolated from the host network. Uploaded tests are executable code: only run files you trust.

### AI-generated GUI tests

Choose **Generate Test Cases Using AI** and select a Node.js application source folder. Checkpoint detects common frameworks from the package dependencies and scripts, chooses an available port based on the framework's default (Vite `5173`, Next.js/React `3000`, Angular `4200`, Vue CLI `8080`), and tries the next ports automatically if that port is occupied. It starts the project's `npm run dev`, `npm run start`, or `npm run server` script (in that priority order) while the selected AI provider analyzes a bounded set of source files. The project must have a `package.json`, installed dependencies, and one of those scripts. Supported frameworks receive host/port arguments; Vite also gets a temporary process environment setting to allow Docker's `host.docker.internal` hostname without editing the project's config. Other servers should honor `HOST=0.0.0.0` and `PORT`. The app must listen on `0.0.0.0` so Docker Desktop can reach it. Checkpoint does not install packages or modify project files.

The Playwright runner reaches the app at `http://host.docker.internal:<port>` through the allowlist proxy. Generated JavaScript Playwright specs are saved under `agent/GUI_test_agent_output/<app-name>/run-<timestamp>/` and run in the isolated browser container. The local app process is stopped after generation failure, test completion, cancellation, or app shutdown. The generator supports common web UI source types (HTML, CSS, JavaScript/TypeScript, Vue, Svelte, Razor, C#, PHP, and Python), skips dependency/build folders and common secret files, and limits each request to 40 files and 90,000 characters of source.

Starting a project runs its npm script directly on your PC with your Windows user permissions. Checkpoint asks for confirmation first; only select folders whose code you trust. The app process receives a reduced environment (no inherited API keys or other arbitrary environment variables), but host execution is not a security sandbox. To make it reachable from Docker, the app listens on all network interfaces; your firewall configuration determines whether other devices can reach that port. The selected source files are also sent to the configured AI provider, so only choose source you are permitted to share with that provider.

Generated specs are also saved so you can inspect or reuse them. The selected source files are sent to the configured AI provider, so only choose source you are permitted to share with that provider.

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

## Consistency Review

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
a model. A check runs in one of two ways:

- **Automatically at the end of the Full Codebase Analysis Pipeline** (below):
  the pipeline generates its outputs, reviews them, and offers **Open
  Consistency Review** on its Complete screen.
- **On its own, from Operations → Consistency Review**, against outputs
  already on disk, without regenerating anything.

Both save a whole-project check. Neither changes the selected-files result or
saved reviews.

### After the full pipeline

Run Operations → select the codebase → **Full Codebase Analysis Pipeline**.
The note under the button says it ends with a Consistency Review that makes
one more AI request per source file. **Estimate Token Usage** lists the review as
its own stage with a lower bound ("at least N"), because the size of the
review's answers is not known in advance.

After every generation step has succeeded, the last stage, **Checking
consistency...**, compares the whole codebase with the outputs this run wrote.
Output files left over from an earlier run, which this run did not write
again, are not compared; the result's notes list them. The stage shows the
file being checked but no step percentage.

The Complete screen reports the generation outcome and the review outcome
separately, e.g. "Full pipeline completed. Consistency Review: Consistency
check completed: 2 mismatches found." **Open Consistency Review** opens that
exact check, labelled as from the most recent run.

- Mismatches are findings, not a pipeline failure.
- An incomplete or failed review (e.g. the key's daily limit is reached during
  the review) is reported as a warning; everything generated is kept and the
  pipeline still completes.
- If a generation step fails, the pipeline stops before the review as before.
  No review is saved for that run, and the previous review is labelled "From
  an earlier run".
- The pipeline will not start while you have unsaved review notes for the
  codebase (the new review replaces the result they belong to).

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

### Running an independent check

Run Operations → select the codebase → **Consistency Review** (with the other
operations). It checks the whole codebase: every source file that has a
generated summary, business rule or unit test is compared with those outputs.
Nothing is regenerated and no pipeline step runs; it uses the outputs already
on disk, whichever run wrote them.

What it needs on disk for the codebase (matched by folder name):

| Output | Written by | Without it |
|---|---|---|
| `agent/file_summary_agent_output/<codebase>/` | Create JSON Summaries | No summary comparisons |
| `agent/BR_agent_output/<codebase>/validated_rules.json` | Business Rule Validation | No rule comparisons; the check is incomplete |
| `agent/UT_agent_output/<codebase>/validated_tests.json` (or `unit_tests.json`) | Unit Test Generation | No test comparisons; the check is incomplete |

With none of them there is nothing to compare: the check makes no request and
is reported as failed ("Nothing to check"). Results are saved under
`agent/consistency_output/<codebase>/` (git-ignored).

While it runs, the loading screen shows the backend's own progress (the file
being checked, and any rate-limit wait from the AI provider) and the token
meter. There is no percentage, because the check does not report one.
**Cancel** stops the check at once: nothing from it is saved, the previous
result stays, and requests already made still count toward your quota.

When it finishes, the title says **Complete**, **Check incomplete** or
**Check failed**, with the reason underneath, and points you to View Insights
→ Consistency Review, which then shows that check's result.

It will not start while another operation is running, or while you have
unsaved review notes for the codebase on the Consistency Review screen (a new
check replaces the result they belong to): save them, or use **Discard unsaved
reviews** on that screen.

### What a result means

| Shown as | Meaning |
|---|---|
| **N mismatches found** | The check completed. Each finding is a disagreement between the code and a generated artifact; which one is right is not known. |
| **No mismatches found in assessed artifacts** | The check completed and found no disagreement in what it compared. It does not mean the code is correct or the tests pass. |
| **Incomplete check** | Some artifacts were not compared: outputs missing (e.g. no business rules yet), links ambiguous or stale, comparisons undetermined, or errors. Findings shown are real, but others may be missing. The lists under the banner say what was not compared. |
| **The check failed** | Nothing usable was produced, e.g. the API key hit its daily limit or no key is selected. The errors list says why. No conclusion can be drawn. |

### Reading a finding and recording a decision

- **Scope:** whole project (the pipeline and the Operations check) or selected
  files; each keeps its own latest result.
- **Evidence:** the code and the artifact's claim as stored with the check,
  with file and line numbers, and whether either has changed since.
- **Intended behaviour: unknown**, always: a finding records a disagreement,
  not which side is right.
- **Review decision:** unresolved, reviewed or dismissed, with an optional
  note. It is a record for people only: it changes no code, artifact or
  verdict, and stays on the same finding across later checks and reruns.

### API usage and provider limits

Each file checked is one model request on the key selected in Manage API Keys
(plus at most one re-ask if an answer cannot be read). A codebase with no
outputs, or files with nothing to compare, cost no request. The token meter on
the loading screen shows calls and tokens as they happen, and **View Insights
→ AI Usage** shows the full record: an independent check is logged as
`check_consistency`; in a pipeline run the review is its own
`consistency_review` stage, counted once. A check that made no model call is
not logged, as for other steps.

Provider limits use the app's existing handling: on a rate limit or a busy
provider the call waits and retries (the wait is shown on the loading
screen), for up to 10 minutes per call. If the key reaches its daily limit, or
the provider keeps refusing for that long, the check makes no further
requests: findings already made are kept, the remaining files are listed as
not checked, and the result is incomplete (or failed, if nothing was
assessed). Cancel stops the backend; requests already sent still count.

### Coverage and limits

- The code in scope for one comparison is one file (or one method). Behaviour
  that depends on another file is only partly visible; such comparisons may
  be reported as undetermined.
- Folder-level business rules name no source file, so they are skipped and
  listed as skipped.
- A file whose source and artifacts exceed one request's size limit is left
  unassessed (listed, never truncated); select single methods instead.
- One request per file: time and cost grow with codebase size, and the
  feature has not been measured on large codebases.
- Verdicts are a model's reading of the code; nothing is compiled or run, and
  no accuracy figure is claimed. The offline evaluation in
  [test/consistency_eval](test/consistency_eval/README.md) checks the
  workflow on small tracked cases, not a real model's accuracy.

### Trying it with a real codebase (uses your API quota)

Use a small C# codebase (a handful of files) under `targetCodebases/`, and a
key saved and selected under **Manage API Keys**. Steps 3 and 7 send code to
the model and use tokens; nothing else does.

**A. Through the pipeline**

1. `npm.cmd start` → **Run Operations** → browse to the codebase folder →
   **Submit**.
2. Press **Estimate Token Usage** and read the `consistency_review` line, then
   press OK.
3. Press **Full Codebase Analysis Pipeline** and wait for the last stage,
   "Checking consistency...".
4. On the Complete screen read both outcomes (pipeline and Consistency
   Review) and any warning, then press **Open Consistency Review**.
5. Check the header: Result "Whole project", and a Run line saying it is from
   the most recent logged run of this codebase. Open a finding, compare its
   claims and evidence with the files, and save a review with a note.
6. **View Insights → AI Usage**: the latest `full_pipeline` run has a
   `consistency_review` stage with the review's calls and tokens.

**B. On its own**

7. Back to **Run Operations** (same codebase) → **Consistency Review**. It
   uses the outputs from step 3 and regenerates nothing.
8. On completion read the title and message, press OK, then View Insights →
   **Consistency Review**. The review saved in step 5 is still on its finding
   if the same disagreement is found again.
9. **AI Usage** lists the run as `check_consistency`.

For an incomplete result, run step 7 on a codebase that has summaries but no
business rules or unit tests yet.

To look at the screen without a model or real outputs, use the isolated demo
results in [test/consistency_review_ui](test/consistency_review_ui/README.md).

### Tests

```powershell
.venv\Scripts\python.exe -m pytest test/other_tests -k consistency
node --test test/other_tests/consistency_review_model.test.js
```

The real-app walk-through (screenshots of every state) is described in
[test/consistency_review_ui/README.md](test/consistency_review_ui/README.md).

### Packaged app

A packaged backend (`releases/main/main.exe`) built before this feature lacks
its backend commands and the pipeline's review stage. Rebuild it with the
PyInstaller command below before `npm run dist`. `releases/main.spec` needs no
change: the new modules are imported directly by `backend/commands.py`, so
PyInstaller finds them.
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
