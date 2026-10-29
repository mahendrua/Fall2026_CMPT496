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
is a secret. Only the outgoing copy changes: source files are not touched,
and the provider's own API key still authenticates the call. What comes back,
and what the app logs and saves, is cleaned separately (see
[Secret protection in logs and saved outputs](#secret-protection-in-logs-and-saved-outputs-us-034-t-078)).

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
- Since T-078 the run log records them for each call, with totals per stage
  and per run (see [Redaction counts](#redaction-counts-in-the-run-log)).
  They are not shown in the UI.

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
    configures no tracer, and since T-078 backend runs are never traced.
  - Other chain wrappers (`with_retry`, `prompt | model`), which the agents
    do not use.
  - Structured-output schemas from this repository.
  - Base64 image and file payloads.
  - The model's answers, application logs, and summaries saved to disk.
    T-078 cleans these on the way out.
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

- **T-078:** done; see
  [Secret protection in logs and saved outputs](#secret-protection-in-logs-and-saved-outputs-us-034-t-078).
- **T-079:** done; see
  [Secret protection verified](#secret-protection-verified-us-034-t-079),
  including the acceptance verdict.

T-077 alone does not complete US-034.

### Packaged app

The new modules are imported by `agent/llm.py`, so PyInstaller finds them and
`releases/main.spec` needs no change. Rebuild the backend before `npm run dist`.

## Secret protection in logs and saved outputs (US-034, T-078)

T-077 cleans what is sent to an AI provider. T-078 cleans what Checkpoint
shows and keeps: console output, logs, run records, the outputs it saves, and
the reports built from them. It uses the same detector and the same
placeholders:

```text
Error generating summary: 400 Bad Request: could not parse 'password = "Fake-Pass-111!"'
  ->  Error generating summary: 400 Bad Request: could not parse 'password = "[REDACTED_PASSWORD_1]"'
```

Source files, `.env` and the API keys the providers authenticate with are
never changed.

### Where it happens

| Module | Role |
|---|---|
| `utils/output_redaction.py` | `sanitize_text()` and `sanitize_data()`; `safe_text()`, which never raises; `save_json()` and `save_text()`, which clean in memory, write a temporary file and swap it in. Also `secret_scope()`. |
| `utils/secret_redaction.py` | `KnownSecrets`: what one run has found, kept in memory for the secrets it repeats without context. |
| `backend/log_redaction.py` | The log record factory, the stdout and stderr guards, and exception hooks that print a whole traceback in one write. |
| `backend/progress_logging.py` | `send()` and `send_response()`: every message to Electron. |
| `backend/run_protection.py` | `protected_run()`: the run's secret scope, with LangSmith tracing off. |

### What is protected

| Output | Written by | Notes |
|---|---|---|
| Messages to Electron on stdout (progress, token counts, responses) | `progress_logging` | Framing, fields and request IDs are unchanged. A stray `print()` to stdout is cleaned too. |
| stderr (Electron's console) | log factory, stream guard | Records from every logger and library, whichever handler they reach; warnings; tracebacks with their causes; uncaught exceptions in threads. |
| `error_log.json` | `backend/dispatcher.py` | Electron's own entries are backend messages (already clean) or its own fixed text. The command name is cleaned too (T-079): for an unknown command it is whatever the request sent. |
| `last_run_log.json`, `run_logs/` | `RunLog.save()` | Also records the redaction counts. |
| File summaries, file-level `business_rules.json` | file summary agent | Includes `Error generating summary: ...` text. |
| Directory summaries, directory-level `business_rules.json` | directory agent | |
| `validated_rules.json`, `discarded_rules.json` | BR agent | Evidence snippets, reasons. |
| `unit_tests.json/.txt`, `integration_tests.json/.txt` | UT and IT agents | |
| `UT_*.cs`, `IT_*.cs`, `validated_tests.json`, `discarded_tests.json`, `test_report.json`, the HTML test report | `agent/test_harness.py` | The HTML report shows failed assertions' actual values, read from the running code. |
| `consistency_result*.json`, `reviews.json` | `agent/consistency_store.py` | Evidence excerpts copied from source lines, claims, explanations, review notes. |
| `run_reports/<codebase>/*` | `backend/run_report.py` | Built from a cleaned copy of everything it reads, older outputs included. |
| `mutation_report.json`, `stryker_output.txt` | `backend/mutation_testing.py` | |
| UML PDF, Markdown export | `utils/uml_json_to_pdf.py`, `utils/json_to_markdown.py` | The summary is cleaned as it is loaded. |

These are kept exactly as they were, so the links between outputs keep
working:

- dictionary keys, numbers and booleans;
- reference fields: IDs, paths, symbols, line numbers and content hashes
  (`REFERENCE_KEYS`).

As a result:

- Finding IDs, and the reviews filed under them, do not change.
- Line locations and schemas stay valid.
- Every output still loads where the next step reads it.
- Generated tests still compile: a secret inside a string literal becomes a
  placeholder inside the same literal.

### Secrets repeated without context

A secret is found by its context (`password = "..."`). An answer, an error
message or an excerpt cut out of a file can repeat it without that context.

**How it works:** each backend request and each command (`main.py`,
`Commands._run_command`) runs as one protected run, and the stages of the full
pipeline share it. Inside a run, the secrets found in every AI request (T-077)
and in every cleaned output are remembered, in memory only. Every later output
in the same run is also cleaned of those values wherever they appear. When the
run ends, they are dropped. They are never saved, logged or shown.

So is every later AI request in the run (from T-079). The agents send a
model's earlier answers back: a directory summary to its judge and to the
parent directory, a judge's feedback to the refinement, a broken test to its
repair. The T-079 ten-secret run found answers repeating a secret without its
context reaching the provider that way.

**Limits:**

- Only values of 8 or more characters mixing letters with digits or symbols
  are replaced without context. This is the same rule T-077 uses within one
  request, so ordinary words are never blanked out. `Pw-12` or `letmein` is
  replaced only where its context shows.
- It works only within one run. Runs never share what they found, including
  two runs at once, and a value found in an earlier request is not known to
  the next.
- Outside a backend run there is only contextual detection. This applies to
  the terminal tools: `python -m utils.json_to_markdown`, `uml_json_to_pdf.py`,
  and `python -m backend.run_report`.
- A value nothing in the run identified, because T-077's detector missed it,
  is missed here too.
- Placeholder numbers are local to each request and each saved file. A
  `[REDACTED_PASSWORD_1]` in a summary need not be the same secret as in the
  request, or in another file.

### When something cannot be checked

The original text is never written or shown instead, and no error message
quotes it.

| Output | Result |
|---|---|
| Log record | A fixed notice replaces the message and the traceback. |
| Progress message | A fixed notice replaces its text. Its type, numbers and request ID still go, so the bars keep moving. |
| Command response | It becomes a failure saying the response was withheld, still carrying its request ID. |
| Console line | A fixed notice line. |
| Saved output | Nothing is written and the previous file is kept. The step fails with only the file name: `Db-cs__1.json was not saved: The text could not be checked for secrets.` |
| HTML test report | It is not kept, and a progress message says so. The test step carries on. |
| Run log | It is saved with its error texts withheld; the rest is names, times and numbers. |
| Consistency Review result | `ConsistencyStoreError`, naming the file. |

### Tracing

LangChain uploads a trace of every run to LangSmith when `LANGSMITH_TRACING`
(or `LANGCHAIN_TRACING_V2`) is `true`. Such a trace would carry the raw graph
state, which LangGraph reports before a node calls its model: source code,
summaries and rules. T-077 noted this exposure.

- **What happens:** every protected run turns tracing off with langsmith's
  own `tracing_context(enabled=False)`, and sets aside a tracer installed with
  `tracing_v2_enabled()`.
- **Scope:** only this process, and only inside the run. The environment and
  other programs are not changed.
- **Notice:** when the environment asks for tracing, a warning on stderr
  says, once, that Checkpoint's runs are not traced, and why.
- **How it was checked:** `run_record_redaction_test.py` turns tracing on with
  an endpoint nothing listens on, and replaces the tracer's upload methods
  with recorders. Without protection, the raw graph state is traced. Inside
  protected runs and backend commands, nothing is.
- **Not covered:** a tracer passed in explicitly as a callback (the app
  passes none), and LangChain's `set_debug()` console output (never turned
  on).

### Redaction counts in the run log

```json
"calls": [{"stage": "generate_file_summaries", "status": "success", "redacted_secrets": {"API_KEY": 1, "PASSWORD": 2}, ...}],
"totals": {"calls": 4, "redacted_secrets": {"API_KEY": 3, "PASSWORD": 6}, ...}
```

Each stage has the same total for its own calls.

- **What a count is:** an occurrence replaced in the request one AI call
  sent, by category, as `agent/llm.py` reports it to the callbacks. The same
  secret sent in five calls counts five times; it is not a count of distinct
  secrets.
- **Each call is counted once.** Retries resend the same cleaned request
  within that call, and `with_structured_output()` cleaning the request a
  second time finds nothing new.
- **What is not stored:** placeholder numbers, or which value a count was.
- **What is not counted:** replacements made in logs and saved outputs.
- **One exception:** a direct `generate()` with several prompts would report
  their combined count on each of them. The agents never call it that way;
  `batch()` and `abatch()` are separate calls.

### Where the count is shown

After a full pipeline run, the run's total appears as **"Secret occurrences
redacted from AI requests"**, with a breakdown by category:

- **The Complete screen:** under the token count.
- **The run report:**
  - in the Business view, a tile and a short "Secrets kept out of the AI"
    card;
  - in the Developer view, a tile and a line in "The run".

Both say how it is counted: a secret sent in several requests counts each
time. It is not a number of unique secrets. Replacements in logs and saved
outputs are not part of it.

- **Where the number comes from:** the run's own record, so it is never
  another run's:
  - the Complete screen gets it in the pipeline's response, read from the
    run (`token_usage.current_run_redactions()`) once its last AI call has
    finished;
  - the report is built from that run's own `run_logs/` copy, not from the
    newest log on disk.
- **What it shows when there is no count:**
  - an explicit zero shows as **0**;
  - a run log saved before the counts existed, or no run log at all, shows
    **Not recorded**, never 0.
- **Only these runs show it:**
  - The Complete screen shows it for a full pipeline that finished (a
    partial Consistency Review included).
  - Failed and cancelled runs keep their error and cancel messages and have
    no report. Their counts stay in `run_logs/`.
  - Single steps run on their own show no count.
  - Mutation testing rebuilds the report for the newest full run, as before.

Only counts and category names travel; no value, prompt or
placeholder-to-secret mapping.

### Outputs saved before T-078

Files already on disk are **not** cleaned automatically, and none are rewritten
in bulk. They keep what they hold until a run writes them again. To clean them,
delete them and run the pipeline again.

- The exports clean what they copy, older outputs included: the run report,
  the UML PDF and the Markdown export.
- Responses from the backend are cleaned too.
- Electron's file browser and View Insights read some files directly, so
  they show older files as they are stored.

### Limitations

- **Detection is T-077's**, with the misses listed there. A PlantUML private
  field (`-password : string = "..."`), missed until then because the `-`
  read as part of a hyphenated name, is detected from T-079 on.
- **Not covered:**
  - **Vector stores** (`vectorStores/`): the code index holds raw source
    chunks, a copy of the source files themselves. The summary index is built
    from the saved summaries, which are cleaned from T-078 on.
  - **Stryker's own reports:** they contain the mutated source files.
  - **dotnet build output** of the generated test project.
  - **Raw writes:** bytes written to a stream's `.buffer` or straight to a
    file descriptor.
- **Keys and reference fields are never scanned**, so a secret inside a file
  path or a dictionary key stays.
- **Private keys:** the body of a private key becomes one placeholder, so an
  excerpt holding one has fewer lines. Line numbers are not affected.
- **Consistency Review evidence.** The model sees placeholders, so a finding
  that quotes a line that held a secret quotes the placeholder. Such findings
  used to be rejected ("its source excerpt does not appear in the code in
  scope").

  From T-079, `agent/consistency_checker.py` checks every excerpt against
  the request as the model was sent it. It applies the same redaction to a
  copy. The finding keeps its file and lines, and its evidence shows the
  placeholder, never the raw value.

  The check is as strict as before:
  - an excerpt quoting the raw value is rejected;
  - so is one with a placeholder the request did not hold there;
  - so is one that is in no version of the file.

  Placeholder numbers are local to each saved file. A finding's excerpt keeps
  the request's `[REDACTED_PASSWORD_1]`, so the same secret repeated in its
  explanation is saved as `[REDACTED_PASSWORD_2]`.
- **Electron does no detection of its own.** There is one detector, in
  Python.
- **Cost on the development machine:** about 0.5 ms per KB of saved output,
  and 0.1 ms per progress line.

### Tests

```powershell
.venv\Scripts\python.exe -m pytest test/other_tests/output_redaction_test.py test/other_tests/log_redaction_test.py test/other_tests/frontend_output_redaction_test.py test/other_tests/saved_summary_redaction_test.py test/other_tests/generated_artifact_redaction_test.py test/other_tests/run_record_redaction_test.py
```

No API key, network, dotnet, Java or Stryker is needed, and every secret is
fake. The checks for a planted value fail with a count, never the value.

| File | What it checks |
|---|---|
| `output_redaction_test.py` | Text and structure; schemas and reference fields; context-free echoes; isolation between threads; detector failures; atomic saves. |
| `log_redaction_test.py` | Parameterized and pre-formatted log messages; tracebacks and their chains; stack info; handler order and propagation; the stream guards. Also two real processes: the guards alone, and `main.py` answering requests. |
| `frontend_output_redaction_test.py` | Progress, token and response framing; each request as its own scope; command errors; `error_log.json`. |
| `saved_summary_redaction_test.py` | A file summary agent run with a misbehaving fake provider; directory and BR writers; rules loading in the next step; a summary that cannot be saved. |
| `generated_artifact_redaction_test.py` | Generated tests, `.cs` files and test reports; Consistency Review end to end, including reviews and a long title; the run report built from older planted outputs; the mutation report; the UML PDF; the Markdown export. |
| `run_record_redaction_test.py` | Run logs and counts; offline tracing checks; echoes across stages; concurrent runs. |

### Verified end to end

T-078 is tested piece by piece above. T-079 runs the whole pipeline on ten
planted secrets and checks every output in this table; see
[Secret protection verified](#secret-protection-verified-us-034-t-079).

### Packaged app

The new modules are imported by `main.py` and the agents, so PyInstaller
finds them, and `releases/main.spec` was not changed. T-079 built the backend
with it and checked the build; see
[Secret protection verified](#secret-protection-verified-us-034-t-079).

## Secret protection verified (US-034, T-079)

T-079 plants ten fake secrets in a small sample codebase and runs Checkpoint's
real backend on it, offline. It checks for the secrets in everything that
leaves the app or is kept by it. The checker is independent of the app's
detector.

Zero leaks is a measured result for these cases and these sinks. It is not
proof that every secret anywhere is found. The tools are in
[`test/secret_verification/`](test/secret_verification/README.md).

```powershell
# The full run, about a minute: exit 0 clean and complete, 1 leak, 2 incomplete
.venv\Scripts\python.exe test\secret_verification\run_verification.py
# The same run with request redaction removed: it must fail
.venv\Scripts\python.exe test\secret_verification\run_verification.py --negative-control
# The real Electron window, about a minute
.venv\Scripts\python.exe test\secret_verification\run_electron_check.py
```

Nothing needs an API key or the network. The evidence (`report.json`,
`summary.md`) goes to `test/secret_verification/runs/`, which git ignores. It
holds case IDs, counts and locations, never a value.

### How it runs

**Real:**
- `main.py` over stdin and stdout, read by `main.js`'s rules;
- every pipeline stage and agent;
- `agent/llm.py`'s retries and redaction;
- ChromaDB, with the cached embedding model;
- dotnet build and test of the generated tests (offline NuGet);
- PlantUML;
- the run report and the Markdown export.

**Substituted:**
- The AI provider: an offline model in place of the Google package. The
  app's own `build_chat_model()` wraps it.
- The model's answers: scripted, and they repeat all ten secrets on purpose.
- dotnet's build servers: switched off, so nothing holds the workspace open.

**Not verified:**
- Gemini's own structured-output mode and HTTP client;
- what a real model would answer;
- network use by child processes (dotnet, Java);
- Stryker.NET, which is not installed.

### The ten cases

| Case | Category | Where | Planted | Leaks |
|---|---|---|---|---|
| S01 | PASSWORD | C# constant; a comment repeating it; Markdown prose; a PlantUML private field | 4 | 0 |
| S02 | API_KEY | Python named key | 1 | 0 |
| S03 | TOKEN | JavaScript property and query string | 2 | 0 |
| S04 | API_KEY | Stripe format, under a name that says nothing | 1 | 0 |
| S05 | API_KEY | AWS format, shell export | 1 | 0 |
| S06 | TOKEN | GitHub format, shell variable | 1 | 0 |
| S07 | AUTHORIZATION | Bearer value in C# and in a curl header | 2 | 0 |
| S08 | PASSWORD | SQL Server connection string | 1 | 0 |
| S09 | PASSWORD | PostgreSQL URL | 1 | 0 |
| S10 | PRIVATE_KEY | PEM block in Markdown | 1 | 0 |

The app's configured API key (K00) and a LangSmith key in the environment
(K01) are checked too: 0 leaks.

The same secrets in JSON, `.env`, YAML, XML, INI and PEM (file types the
pipeline does not read) are detected by the detector alone. Three
documented misses stay missed, as documented:
- a password passed positionally;
- a bare `"Key"` name;
- a value repeated without context in a separate run.

### Measured

Final run on 2026-10-28, seed 79: **PASS**. There were 0 leaks, 134 checks
passed, and 0 network attempts.

- **What left the app:**
  - 58 provider requests, covering all 12 request kinds;
  - 57 model callbacks.
- **Electron's view:** 284 stdout messages, 0 rejected by `main.js`'s rules;
  stderr; `error_log.json`.
- **Run records:** `last_run_log.json`, `run_logs/` and
  `token_usage_log.json`.
- **Saved outputs:**
  - file and directory summaries, and their business rules;
  - validated and discarded rules;
  - unit and integration tests, their results and HTML reports;
  - the generated `.cs` files;
  - Consistency Review results and review notes.
- **Exports and indexes:**
  - 7 UML PDFs, and what PlantUML was given and drew;
  - the run report and its diagram;
  - the mutation report;
  - the Markdown export;
  - the summary index of the vector store.

The 0 leaks mean something:

- **Every value was in play.** The model's answers repeated all ten values
  (40 times).
- **Outputs were cleaned, not emptied.** Each output those answers feed holds
  placeholders where the values were (run report 71, IPC 48, summaries 24,
  UML PDF text 18 ...).
- **Counts match the fixture:**
  - Every file's request matched the fixture's own count of what had to be
    replaced, with one count per call.
  - The scripted 503 was retried with the same cleaned request and counted
    once.
  - The run log's totals add up: PASSWORD 53, AUTHORIZATION 35, TOKEN 32,
    API_KEY 12 and PRIVATE_KEY 2, over 52 calls.
- **Useful content kept:**
  - All 26 nonsecret controls reached the provider unchanged: thresholds,
    `primaryKey` and `cacheKey`, environment lookups, URLs, paths,
    `YOUR_API_KEY`, an existing placeholder, PlantUML fields.
  - Saved summaries keep the structure of the model's answer and its
    nonsecret text.
- **The pipeline still did its job:**
  - Three rules were validated.
  - The generated tests built and ran: 3 unit tests (one repaired by the AI,
    one failing on its placeholder) and 1 integration test.
  - The Consistency Review findings keep their source lines.
- **Inputs untouched:** the source files, the codebase folder and the `.env`
  were unchanged.

**Storage boundaries.** These are local and kept on purpose. They are measured
and listed with every run, never counted as clean:

| Store | Holds |
|---|---|
| Code index (`vectorStores/`) | Raw source chunks, so retrieval finds the real code: S03, S07 and S08, the values inside methods, properties and JS pairs. As file bytes: 15 occurrences. |
| dotnet build of the analysed code (the generated tests' artifacts folder, under TEMP) | Its compiled string constants: S01, S04, S07, S08 |
| Stryker's own reports | Not produced (Stryker not installed). They contain the mutated source files. |

Also measured, outside the result: LangGraph hands each node's raw input to
chain callbacks, as T-077 noted. The app registers no handler that keeps it,
and tracing is off in every run. Only the harness's recorder saw it (211
events).

**The checks can fail.** In the negative control, request redaction is
removed in the backend process only; the app has no such switch.

- The run fails (exit 1).
- All ten cases are found in the provider requests and callbacks.
- With no run memory left, values repeated without context leak into the
  summaries, the run report, the Markdown export and the summary index.

The scanner also finds a planted value in each of 18 encodings and file
types: JSON (escaped twice), HTML entities, URL and base64 encoding, UTF-16,
split lines, PDF text, PNG text chunks, SVG-embedded PlantUML, and SQLite
rows. A missing or unreadable artifact makes a run incomplete, never clean.

**Electron.** The real window ran the full pipeline from the Run Operations
button. Each of these was checked for the planted values, and none was found:

- every message `main.js` sent the renderer;
- the Complete screen;
- the Consistency Review screen, which shows placeholders and the finding at
  its line;
- Electron's console.

**Packaged backend.** A PyInstaller build from `releases/main.spec`, built
outside `releases/main/`, was checked. Its responses, stderr, `error_log.json`
and a UML PDF from a summary with a PlantUML private field are clean, and
every stdout line is a message `main.js` accepts. AI requests cannot run in a
frozen build, so they were not checked there.

### Fixed in T-079

- **Answers sent back to the model.** The agents send a model's earlier
  answers back: a directory summary to its judge, feedback to the refinement,
  a broken test to its repair. Answers that repeated a secret without its
  context reached the provider. The first run found 12 such occurrences in
  requests, and the same 12 in their callbacks.

  Inside a run, every AI request is now also cleaned of what the run already
  found (`agent/request_redaction.py`). Runs never share it.
- **`error_log.json`.** It saved an unknown command's name as received. It is
  now cleaned like the message (`backend/dispatcher.py`).
- **PlantUML private fields** (`-password : string = "..."`) are detected
  (`utils/secret_redaction.py`). Other `-name` forms and nonsecret fields are
  unchanged.
- **Consistency Review evidence** is checked against what the model was sent,
  so a finding quoting a line that held a secret is accepted at its lines. The
  check is as strict as before:
  - an excerpt quoting the raw value is rejected;
  - so is one with a placeholder the request did not hold there;
  - so is one that is in no version of the file.

  See the T-078 limitations above.

### Outputs saved before T-078

Older outputs are not cleaned in bulk; they stay as they were.

- **What T-079 checked:** today's exports read them and copy no secret out.
  This covers the run report, the UML PDF and the Markdown export, with no
  run open and including a PlantUML private field. The older files
  themselves stay byte for byte as they were.
- **To clean them:** delete them and run the pipeline again.
- **Still showing old values:** Electron's file browser and View Insights,
  which read some files directly.

### Not covered or not run

- **Detection is pattern matching.** T-077's documented misses remain.
- **Manual, NOT RUN.** These View Insights screens read the repository's own
  folders directly: File Summaries, Directory Summaries, Business Rules, Unit
  Tests, UML, Errors and Run Report. A checklist is in
  `test/secret_verification/README.md`.
- **Not built:** the packaged Electron app (`electron-builder`). It may
  download Electron binaries.
- **Unrelated app defect, found here.** ChromaDB sometimes cannot open an
  index the stage before has just written. The directory stage then fails
  with "Error creating hnsw segment reader: Nothing found on disk" (2 of
  about 20 runs). The harness retries that failure once and records it;
  the app does not.

### US-034 acceptance

| Criterion (project tracker) | Result |
|---|---|
| Known secret patterns are replaced with a placeholder in prompts, logs and saved summaries | **Demonstrated** for the ten cases and every sink above, including requests, callbacks, stdout, stderr, logs, run records, summaries, rules, tests, reviews and exports |
| A sample with 10 planted fake secrets shows 0 leaks (T-079: 0 secrets in prompts, logs or outputs) | **Demonstrated:** 0 leaks in requests and in every output. The storage boundaries above hold raw values on purpose, locally. |
| The number of secrets removed is shown after the run | **Demonstrated** for full pipeline runs. The Complete screen and the run report both show "Secret occurrences redacted from AI requests" from that run's own record (see [Where the count is shown](#where-the-count-is-shown)). In the real Electron window the screen showed 134, the count the backend recorded for that run, and so did both views of the run report it opened. A zero shows as 0; a log without counts shows "Not recorded". |

**All three US-034 criteria are demonstrated** for the ten measured cases, the
sinks above and full pipeline runs.

This is a measured result, not a guarantee:

- detection is pattern matching, with T-077's documented misses;
- the storage boundaries keep raw values locally by design;
- the screens listed under "Not covered or not run" were not checked by
  hand.

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
