# Ten-secret verification (US-034, T-079)

A sample codebase with ten planted fake secrets, and a scanner that looks for
them in what Checkpoint sends, prints and saves. The scanner shares nothing
with the app's own detector (`utils/secret_redaction.py`), so it can judge it.

| File | What it holds |
|---|---|
| `secret_fixture.py` | The LedgerLite codebase: the ten secrets, where each is written, and the nonsecret controls around them. Also detector-only samples in file types the pipeline does not read. |
| `leak_scanner.py` | The independent scanner. Standard library only; imports nothing from the app. |
| `run_verification.py` | The offline run: builds a workspace, starts the real backend on the fixture, sends it a scripted session, scans what came out and writes the evidence. |
| `offline_backend.py` | Starts the app's own `main.py` as Electron does, with the offline model, a callback recorder and a network guard installed first. |
| `offline_model.py` | The model that stands in for the Google provider package. The app's `build_chat_model()` still wraps it with its real retries and redaction; it records exactly what it is sent. |
| `offline_answers.py` | Its scripted answers. They are valid for every schema the pipeline uses, and repeat planted secrets on purpose. They also script a 503 that is retried and 400 errors that quote secrets. |

## The offline run

```powershell
.venv\Scripts\python.exe test\secret_verification\run_verification.py
```

It takes about a minute. It needs the `.venv` packages, the cached
`all-MiniLM-L6-v2` model, dotnet (with xUnit in the local NuGet cache) and
Java. It needs no API key and no network, and installs nothing.

**The workspace** is a new temporary folder:

- the fixture;
- a byte-for-byte copy of the app's Python files and `plantuml.jar`, with
  its own `.env` selecting the offline model;
- `TEMP` for the generated test project;
- the captures: what the provider received, what callbacks were handed,
  stdout, stderr and network attempts.

It is deleted afterwards, unless `--keep` is given. Kept, it holds raw
values: do not share it.

**The session** goes through the real `main.py` over stdin and stdout, read
by `main.js`'s rules:

1. the full pipeline (every stage, real dotnet build and test, real
   PlantUML);
2. run usage, the review result, and a review decision whose note quotes a
   secret;
3. mutation testing (Stryker is not installed, so it reports `not_run`);
4. a Consistency Review check of `docs/architecture.md`, in a run of its own;
5. a directory step and a review check whose model calls are scripted to
   fail with errors quoting secrets;
6. Electron filing an error of its own (`record_error`), and an unknown
   command whose name quotes a secret;
7. the error log;
8. after the backend has exited, the Markdown export (`python -m
   utils.json_to_markdown`). It is a terminal tool, so it runs outside any
   backend run.

**What is checked.** Every sink, read by type with the independent scanner:

- What left the app: the provider requests and the model callbacks.
- Electron's view: stdout (each line as JSON), stderr and `error_log.json`.
- Run records: `last_run_log.json`, `run_logs/` and `token_usage_log.json`.
- Saved outputs:
  - file and directory summaries, and their business rules;
  - validated and discarded rules;
  - unit and integration tests, their results and HTML reports;
  - the generated `UT_*.cs` and `IT_*.cs`;
  - Consistency Review results and review notes.
- Exports and what PlantUML drew:
  - the UML PDFs;
  - what PlantUML was given and drew (its PNGs and SVGs carry their
    source);
  - the run report and its diagram;
  - the mutation report;
  - the Markdown export.

Each sink lists the artifacts it must hold. One that is missing or empty
makes the run incomplete.

**The checks also prove that something was at stake:**

- The model's answers repeated every planted value.
- Each sink they feed holds placeholders where the values were.
- Saved summaries keep the structure of the model's answer and its
  nonsecret text.
- The validated evidence keeps its code.
- The generated tests built and ran as scripted: one repaired by the AI, and
  one failing on its placeholder.
- The review finding keeps its source line.
- The run log's counts add up.

**Not assessed here:**

- The vector stores and dotnet's build of the analysed code: storage
  boundaries, kept by design.
- Stryker's reports: Stryker.NET is not installed.

These are listed as such in the evidence, never as clean.

**Evidence:** `report.json` and `summary.md` go to `--out`. The default is
`runs/<time>/`, which git ignores. They hold case IDs, counts and locations.
They are checked for planted values before they are written.

**Exit status:**

| Status | Meaning |
|---|---|
| 0 | Clean and complete. |
| 1 | A planted value leaked. |
| 2 | Something required is missing: a stage, a request kind, a check. That is incomplete, never clean. |

**The tests:**

- `test/other_tests/secret_verification_pipeline_test.py` checks the
  harness's own parts.
- `$env:T079_FULL = "1"` runs the full session too (about a minute).

## The ten cases

Values are generated at run time from a seed (default 79), so the repository
never holds a complete vendor-format token or a private key. The same seed
always gives the same values. Nothing here prints a value.

| Case | Category | What it is | Where (codebase `LedgerLite/`) |
|---|---|---|---|
| S01 | PASSWORD | C# constant password | `Billing/InvoiceService.cs` (named, and repeated in a comment with no context); `docs/SETUP.md` (prose); `docs/architecture.md` (PlantUML private field, known gap A) |
| S02 | API_KEY | Python named API key | `tools/report_export.py` |
| S03 | TOKEN | JavaScript refresh token | `web/session.js` (object property, and a `refresh_token=` query parameter) |
| S04 | API_KEY | Stripe-format secret key, under a name that says nothing | `Integrations/PaymentClient.cs` |
| S05 | API_KEY | AWS-format access key ID | `scripts/deploy.sh` |
| S06 | TOKEN | GitHub-format token | `scripts/deploy.sh` |
| S07 | AUTHORIZATION | Bearer credential | `Integrations/PaymentClient.cs` (`AuthenticationHeaderValue`); `scripts/deploy.sh` (curl header) |
| S08 | PASSWORD | SQL Server connection-string password | `Billing/InvoiceService.cs` |
| S09 | PASSWORD | Password in a PostgreSQL URL | `tools/report_export.py` |
| S10 | PRIVATE_KEY | PEM private key body | `docs/SETUP.md` |

There are 15 occurrences in seven files: C#, Python, JavaScript, shell and
Markdown, which the file summary agent reads. `LedgerLite.csproj` lets the
generated tests build against the codebase. The positions of every
occurrence and every control come from the templates, not from the
detector.

**Controls** (C01-C26) are nonsecret text that must come through unchanged:

- business thresholds;
- `primaryKey` and `cacheKey` identifiers;
- names that start with a secret word (`PasswordMinLength`, `tokenType`);
- environment lookups and references;
- ordinary URLs, paths and query parameters;
- an existing `[REDACTED_TOKEN_1]` placeholder and a `YOUR_API_KEY` stand-in;
- the PEM armour lines;
- PlantUML fields and visibility syntax.

**Detector-only samples** are kept out of the codebase.

- **Formats the pipeline never reads:** `appsettings.json`, `.env`,
  `docker-compose.yml`, `web.config`, `payments.ini` and `signing.pem` hold
  the same secrets. These exercise the detector only.
- **Documented misses** (`known_miss`), kept for the record: a password passed
  positionally, a bare `"Key"` name, and a value repeated without context in
  a separate request.

## What the scanner checks

- Each value is looked for as written, and also in these forms:
  - JSON (including escaped twice), Python and HTML escaping;
  - URL and base64 encoding;
  - UTF-16;
  - broken across lines.
- The private key is also looked for one line at a time.
- Each file is read according to its type:
  - JSON is parsed, and every key and string value is checked.
  - HTML, XML and SVG are checked as written, then with entities and tags
    removed. A PlantUML source embedded in an SVG is decoded.
  - PDF: the text drawn on the pages is taken from the decoded content
    streams.
  - PNG: the text chunks are read (PlantUML keeps the diagram source there).
  - SQLite: the tables are queried.
- A finding names the case, the sink and the location, never the value.
  Locations are a file and line, a JSON pointer (whose line number counts
  lines within that string), or a table row.
- **Problems.** These all count as problems, never as clean:
  - a required artifact that is missing or empty;
  - a file that cannot be read or parsed;
  - a PDF with no extractable text;
  - a raw fixture input passed in as an output.

  A report with any problem is incomplete.
- **Not read:** pixels in images, zip or gzip archives, and encodings not
  listed above.

## Tests

```powershell
.venv\Scripts\python.exe -m pytest test/other_tests/secret_verification_fixture_test.py
```

The tests check four things:

- The fixture's recorded positions describe the files it writes.
- No complete vendor token or private key is committed.
- The scanner finds a deliberately planted value in every form and file type
  above, and reports what it could not check as a problem.
- The production detector, measured file by file on the fixture:
  - **Pipeline files:** every planted value is replaced, with the expected
    counts, and every control stays, except known gap A, which is a strict
    `xfail` until it is fixed.
  - **Detector-only formats:** the expected values are replaced and the
    controls stay. The three documented misses are not asserted.
