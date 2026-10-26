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

## Secret redaction in AI requests (US-034, T-077)

Before a request reaches an AI provider, Checkpoint replaces passwords, API
keys, tokens, authorization values, connection-string credentials and private
keys in it with typed placeholders:

```text
private const string AdminPassword = "Fake-Pass-111!";   ->   ... = "[REDACTED_PASSWORD_1]";
"Server=db;User Id=sa;Password=Fake-Pass-111!;"          ->   "...;Password=[REDACTED_PASSWORD_1];"
Authorization: Bearer Fake-Token-123456789               ->   Authorization: Bearer [REDACTED_AUTHORIZATION_1]
```

Detection is local pattern matching; nothing is sent anywhere to decide what
is a secret. Only the outgoing copy changes: source files and saved outputs
are not touched, and the provider's own API key still authenticates the call.

### Where it happens

| Module | Role |
|---|---|
| `utils/secret_redaction.py` | Finds secrets and assigns placeholders. Plain text, no LangChain. Its docstring lists every supported pattern and limitation. |
| `agent/request_redaction.py` | Applies it to chat messages: string content, content blocks and tool-call arguments. Roles, ids, `tool_call_id`, metadata and base64 payloads are copied unchanged. |
| `agent/llm.py` | `build_chat_model()` gives every provider's model `redacting()`. |

LangChain hands the messages to callbacks (token usage, any tracer) inside
`generate`, `agenerate`, `stream` and `astream`, before `_generate` and its
retries run. Redaction therefore happens on the way into those four methods,
and the chain `with_structured_output()` returns redacts its input before it
starts. Callbacks, retries and the provider all receive only the redacted copy.

### What is covered

Every AI request the app makes is built by `build_chat_model()`:

- file summaries;
- directory summaries and business rules;
- business-rule condensing and validation;
- unit and integration test generation;
- test repair, including compiler errors and codebase context;
- Consistency Review;
- the key check in **Manage API Keys**.

A test fails if production code imports a provider package anywhere else.
The vector-store embeddings are computed locally and send nothing.

### Placeholders

- Typed and numbered within a request: `PASSWORD`, `API_KEY`, `TOKEN`,
  `AUTHORIZATION`, `PRIVATE_KEY`. The type says what the secret is, so a
  connection-string password is a `PASSWORD`.
- The same secret gets the same placeholder everywhere in one request: system
  and user messages, and tool arguments. A secret found with context is also
  replaced where it appears on its own, if it is 8 or more characters mixing
  letters with digits or symbols.
- Redacting an already redacted request changes nothing. Existing
  placeholders are kept, and new ones are numbered after them.
- The secret-to-placeholder mapping exists only for the one request. It is
  not saved, logged or shared, so concurrent requests cannot see each
  other's values. The model never sees real values, so they cannot come back
  in its answers.

### Supported patterns (summary)

- **Assignments** to names ending in password/pwd/pass, api key (and access,
  secret, private, signing, subscription, … key), secret or token. Covered
  formats: C#, Python, JavaScript/TypeScript, JSON, YAML, `.env`, INI and XML
  attributes, including code inside JSON strings (`\"password\": \"...\"`).
- **Vendor key and token formats** wherever they appear: AWS, Google,
  Stripe, OpenAI/Anthropic, SendGrid, GitHub, GitLab, Slack, npm, Hugging
  Face, and JWTs.
- **Authorization:** `Authorization` headers, `AuthenticationHeaderValue`,
  `NetworkCredential`, `HTTPBasicAuth`, `curl -u`, and `X-Api-Key`-style
  headers.
- **Connection strings:** `Password`/`Pwd`/`AccountKey`/`SharedAccessKey`,
  passwords in URLs (`postgres://user:...@host`), and secrets in query
  strings.
- **Private keys:** the body of a PEM private key block.
- **Prose:** "the admin password is ...", "the token was changed to ...".
  Only values that look generated count.

Ordinary values are left alone: `cacheKey`, `CancellationToken`, labels, file
paths, environment-variable lookups, `${TEMPLATES}` and obvious stand-ins
such as `<your-api-key>`. It was run against this repository's code and tests
(apart from the redaction tests' planted fakes), its target codebases, and
about 11 MB of third-party Python (openai, anthropic, langchain, httpx,
requests, google-genai). It changed nothing in the first two. In the
libraries it changed only documentation passwords, test-fixture keys and one
pagination token.

### Safe counts

- `RedactionReport` gives counts per category (occurrences and distinct
  values), never the values themselves: `str(report)` →
  `3 secret value(s) replaced (API_KEY x1, PASSWORD x2)`.
- Each model call passes its counts to callbacks as `redacted_secrets`
  metadata, for example `{"PASSWORD": 2}`.
- The counts are not yet shown in the UI or written to the run log.

### When a request cannot be checked

Anything the sanitizer cannot read raises `SecretRedactionError` before any
callback fires or anything is sent: an unknown message class, bytes, or an
object inside a content block. It is never passed through. The agents send
plain text, so in practice this does not happen. If it did:

| Step | Result |
|---|---|
| File summary | that file is skipped with an error |
| Rule condensing/validation, unit/integration test generation | that rule or test is reported as an error and skipped |
| Test repair | that test is dropped |
| Consistency Review | that file is listed as not checked |

### Limitations

- **Pattern matching, not proof.** It misses:
  - a secret with no telling name or format beside it (`Login("admin", "...")`);
  - a bare `Key` name (the `Jwt:Key` in `appsettings.json`);
  - vendor formats not listed above;
  - unquoted INI values that look like identifiers;
  - values built by concatenation;
  - key or token values containing spaces;
  - prose without quotes or "is/equals/set to" before the value;
  - short or letters-only passwords in prose;
  - numbers in tool arguments.
- **Over-redaction is possible:** a JavaScript object property that holds a
  variable (`password: pw`), or documentation examples written in a real
  vendor format.
- **Generated tests see only placeholders.** A test that asserts on a secret's
  literal value will contain the placeholder and fail until edited.
- **Outside this boundary**, and so not covered by T-077:
  - LangGraph node state reported to chain callbacks. This only matters if
    LangSmith tracing is switched on through environment variables; the app
    configures no tracer.
  - Other chain wrappers (`with_retry`, `prompt | model`), which the agents
    do not use.
  - Structured-output schemas from this repository.
  - Base64 image and file payloads.
  - The model's answers, application logs, and summaries saved to disk.
- **Cost:** about 2 ms per KB of prompt on the development machine (about
  30 ms for a 15 KB file, 120 ms for a 56 KB prompt), run on the agents' event
  loop.

### Checking what would be sent (no AI call)

```powershell
.venv\Scripts\python.exe -m utils.secret_redaction path\to\Db.cs path\to\appsettings.json
```

Prints the files as one request would carry them, with the counts on stderr.
The files are not changed.

### Tests

```powershell
.venv\Scripts\python.exe -m pytest test/other_tests/secret_redaction_test.py test/other_tests/request_redaction_test.py test/other_tests/llm_redaction_test.py test/other_tests/agent_redaction_test.py
```

These tests need no API key, network or model. Every secret is fake.
Vendor-format fakes are assembled at run time, so the repository never
contains a real-format token, which GitHub push protection would block.

| File | What it checks |
|---|---|
| `secret_redaction_test.py` | Patterns and false positives, placeholder consistency, idempotence, safe reports, and speed on adversarial input. |
| `request_redaction_test.py` | Message structures, roles, tool calls, metadata, isolation between concurrent requests, and refusal of unreadable content. |
| `llm_redaction_test.py` | Every sync and async entry point, structured output, retries, token accounting and callbacks. Also a real `ChatOpenAI` request checked at the HTTP layer. |
| `agent_redaction_test.py` | Each agent's real prompt code with secrets planted in source files, retrieved context, rules, generated tests and repair context. |

### Remaining work

- **T-078:** redact application logs (progress messages, error logs,
  `last_run_log.json`) and the summaries, rules and tests saved to disk, and
  consider recording the per-call counts in the run log.
- **T-079:** broader verification with ten planted fake secrets across file
  types, run through the pipeline, reporting what is missed and what is
  over-redacted.

T-077 alone does not complete US-034.

### Packaged app

The new modules are imported by `agent/llm.py`, so PyInstaller finds them and
`releases/main.spec` needs no change. Rebuild the backend before `npm run dist`.

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
