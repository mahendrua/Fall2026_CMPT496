# T-079 ten-secret verification: PASS

Generated 2026-10-28T22:57:14, seed 79, session 48.9 s, attempt 1.
Leaks: 0 (0 from known gaps, 0 unexpected). Failed checks: 0. Complete: True. Sinks not assessed here: 1 (listed below; not counted as clean).
Storage boundaries (local, kept on purpose, not outputs) hold 30 planted value(s): see below. This verdict is about outputs, not about them.
Workspace (raw fixture and captures): removed.

## Cases

| Case | Category | Planted | Leaks | Leaked in | Known gap |
|---|---|---|---|---|---|
| S01 | PASSWORD | 4 | 0 | - | - |
| S02 | API_KEY | 1 | 0 | - | - |
| S03 | TOKEN | 2 | 0 | - | - |
| S04 | API_KEY | 1 | 0 | - | - |
| S05 | API_KEY | 1 | 0 | - | - |
| S06 | TOKEN | 1 | 0 | - | - |
| S07 | AUTHORIZATION | 2 | 0 | - | - |
| S08 | PASSWORD | 1 | 0 | - | - |
| S09 | PASSWORD | 1 | 0 | - | - |
| S10 | PRIVATE_KEY | 1 | 0 | - | - |
| K00 | credential configuration | 0 | 0 | - | - |
| K01 | credential configuration | 0 | 0 | - | - |

## Sinks

| Sink | Inspected | Leaks | Cases | Problems |
|---|---|---|---|---|
| provider_requests | 58 | 0 | - | 0 |
| model_callbacks | 57 | 0 | - | 0 |
| stderr | 1 | 0 | - | 0 |
| error_log | 1 | 0 | - | 0 |
| run_logs | 5 | 0 | - | 0 |
| token_usage_log | 1 | 0 | - | 0 |
| file_summaries | 8 | 0 | - | 0 |
| uml_pdf | 7 | 0 | - | 0 |
| plantuml_io | 16 | 0 | - | 0 |
| directory_summaries | 8 | 0 | - | 0 |
| business_rules | 2 | 0 | - | 0 |
| unit_tests | 6 | 0 | - | 0 |
| integration_tests | 6 | 0 | - | 0 |
| generated_test_sources | 14 | 0 | - | 0 |
| consistency_review | 3 | 0 | - | 0 |
| run_report | 4 | 0 | - | 0 |
| mutation_report | 1 | 0 | - | 0 |
| markdown_export | 8 | 0 | - | 0 |
| ipc_stdout | 284 | 0 | - | 0 |
| vector_store_summary | 28 | 0 | - | 0 |

## Checks

- [ok] step answered: full pipeline
- [ok] step outcome as scripted: full pipeline (success=True, expected True)
- [ok] step answered: run usage
- [ok] step outcome as scripted: run usage (success=True, expected True)
- [ok] step answered: review result
- [ok] step outcome as scripted: review result (success=True, expected True)
- [ok] step answered: review note
- [ok] step outcome as scripted: review note (success=True, expected True)
- [ok] step answered: mutation testing
- [ok] step outcome as scripted: mutation testing (success=True, expected True)
- [ok] step answered: review check of the PlantUML notes
- [ok] step outcome as scripted: review check of the PlantUML notes (success=True, expected True)
- [ok] step answered: failing directory step
- [ok] step outcome as scripted: failing directory step (success=False, expected False)
- [ok] step answered: failing review check
- [ok] step outcome as scripted: failing review check (success=True, expected True)
- [ok] step answered: frontend error record
- [ok] step outcome as scripted: frontend error record (success=True, expected True)
- [ok] step answered: unknown command quoting a secret
- [ok] step outcome as scripted: unknown command quoting a secret (success=False, expected False)
- [ok] step answered: error log
- [ok] step outcome as scripted: error log (success=True, expected True)
- [ok] backend exited cleanly (exit code 0)
- [ok] every stdout line is a message main.js accepts (0 rejected line(s))
- [ok] app modules were loaded from the app copy only (55 module(s) from the copy)
- [ok] the app copy is byte-for-byte the repository's code (59 file(s))
- [ok] every pipeline stage ran, in order (build_database, generate_file_summaries, build_summary_database, generate_directory_summaries, validate_business_rules, generate_unit_tests, generate_integration_tests, generate_all_uml, consistency_review)
- [ok] every pipeline stage succeeded
- [ok] stage made AI calls: generate_file_summaries (7 call(s))
- [ok] stage made AI calls: generate_directory_summaries (25 call(s))
- [ok] stage made AI calls: validate_business_rules (7 call(s))
- [ok] stage made AI calls: generate_unit_tests (4 call(s))
- [ok] stage made AI calls: generate_integration_tests (2 call(s))
- [ok] stage made AI calls: consistency_review (7 call(s))
- [ok] the provider received requests (58 request(s))
- [ok] request kind sent: FileSummaryOutput (8)
- [ok] request kind sent: ContextAnalysisOutput (8)
- [ok] request kind sent: DirectoryOutput (9)
- [ok] request kind sent: JudgementOutput (9)
- [ok] request kind sent: BusinessRulesOutput (2)
- [ok] request kind sent: CondenserOutput (1)
- [ok] request kind sent: ValidatorOutput (6)
- [ok] request kind sent: UnitTest (3)
- [ok] request kind sent: TestRepair (1)
- [ok] request kind sent: WorkflowGroups (1)
- [ok] request kind sent: IntegrationTest (1)
- [ok] request kind sent: ConsistencyAnalysisOutput (9)
- [ok] no request the scenario could not answer (0)
- [ok] every summarised file was sent
- [ok] the 503 was retried with the same cleaned request (2 request(s))
- [ok] every control reaches the provider unchanged
- [ok] model callbacks were recorded (57 call start(s))
- [ok] one counted call for Billing/InvoiceService.cs (1 call(s))
- [ok] redaction count matches the fixture: Billing/InvoiceService.cs (reported {'PASSWORD': 3}, expected {'PASSWORD': 3})
- [ok] one counted call for Integrations/PaymentClient.cs (1 call(s))
- [ok] redaction count matches the fixture: Integrations/PaymentClient.cs (reported {'API_KEY': 1, 'AUTHORIZATION': 1}, expected {'API_KEY': 1, 'AUTHORIZATION': 1})
- [ok] one counted call for tools/report_export.py (1 call(s))
- [ok] redaction count matches the fixture: tools/report_export.py (reported {'API_KEY': 1, 'PASSWORD': 1}, expected {'API_KEY': 1, 'PASSWORD': 1})
- [ok] one counted call for web/session.js (1 call(s))
- [ok] redaction count matches the fixture: web/session.js (reported {'TOKEN': 2}, expected {'TOKEN': 2})
- [ok] one counted call for scripts/deploy.sh (1 call(s))
- [ok] redaction count matches the fixture: scripts/deploy.sh (reported {'API_KEY': 1, 'AUTHORIZATION': 1, 'TOKEN': 1}, expected {'API_KEY': 1, 'AUTHORIZATION': 1, 'TOKEN': 1})
- [ok] one counted call for docs/SETUP.md (1 call(s))
- [ok] redaction count matches the fixture: docs/SETUP.md (reported {'PASSWORD': 1, 'PRIVATE_KEY': 1}, expected {'PASSWORD': 1, 'PRIVATE_KEY': 1})
- [ok] one counted call for docs/architecture.md (1 call(s))
- [ok] redaction count matches the fixture: docs/architecture.md (reported {'PASSWORD': 1}, expected {'PASSWORD': 1} (known gap A))
- [ok] run log has one call per summarised file (retries not counted again) (7 call(s))
- [ok] the 400 is recorded as one failed call (1)
- [ok] no network attempt from the backend (0 attempt(s) to )
- [ok] fixture source files unchanged
- [ok] nothing written into the analysed codebase
- [ok] provider credential configuration unchanged
- [ok] summary keeps the schema and structure of the model's answer: Billing/InvoiceService.cs
- [ok] summary keeps its nonsecret content: Billing/InvoiceService.cs
- [ok] summary is more than placeholders: Billing/InvoiceService.cs (133 characters besides placeholders)
- [ok] summary keeps the schema and structure of the model's answer: Integrations/PaymentClient.cs
- [ok] summary keeps its nonsecret content: Integrations/PaymentClient.cs
- [ok] summary is more than placeholders: Integrations/PaymentClient.cs (135 characters besides placeholders)
- [ok] summary keeps the schema and structure of the model's answer: tools/report_export.py
- [ok] summary keeps its nonsecret content: tools/report_export.py
- [ok] summary is more than placeholders: tools/report_export.py (193 characters besides placeholders)
- [ok] summary keeps the schema and structure of the model's answer: web/session.js
- [ok] summary keeps its nonsecret content: web/session.js
- [ok] summary is more than placeholders: web/session.js (139 characters besides placeholders)
- [ok] summary keeps the schema and structure of the model's answer: scripts/deploy.sh
- [ok] summary keeps its nonsecret content: scripts/deploy.sh
- [ok] the failed file's error is saved, cleaned, as its summary (3 placeholder(s))
- [ok] summary keeps the schema and structure of the model's answer: docs/SETUP.md
- [ok] summary keeps its nonsecret content: docs/SETUP.md
- [ok] summary is more than placeholders: docs/SETUP.md (209 characters besides placeholders)
- [ok] summary keeps the schema and structure of the model's answer: docs/architecture.md
- [ok] summary keeps its nonsecret content: docs/architecture.md
- [ok] summary is more than placeholders: docs/architecture.md (78 characters besides placeholders)
- [ok] file-level business rules saved (6 rule(s))
- [ok] directory summary keeps its content
- [ok] three rules validated, the rest discarded with reasons (3 validated, 3 discarded)
- [ok] validated evidence keeps its code and replaces the secret
- [ok] unit tests built and ran as scripted ({'kept': 3, 'passed': 2, 'failed': 1}, expected {'kept': 3, 'passed': 2, 'failed': 1})
- [ok] integration tests built and ran as scripted ({'kept': 1, 'passed': 1, 'failed': 0}, expected {'kept': 1, 'passed': 1, 'failed': 0})
- [ok] the broken unit test was repaired by the AI, then compiled
- [ok] generated test files written: 3 unit, 1 integration (IT_1.cs, UT_1.cs, UT_2.cs, UT_3.cs)
- [ok] the test that hard-coded a secret holds a placeholder instead
- [ok] the review finding keeps its source location (expected line 19)
- [ok] a finding quoting a line that held a secret is accepted, at that line (gap B) (1 finding(s), expected line 10)
- [ok] a finding with made-up evidence is still rejected
- [ok] the review note is saved, its secret replaced
- [ok] the run report keeps its content
- [ok] the Markdown export ran and keeps its content (7 file(s))
- [ok] PlantUML drew the UML diagrams and the project diagram (8 drawing(s))
- [ok] the 503 retry was announced on stdout
- [ok] the caught summarizer error was announced and logged
- [ok] error_log.json holds the scripted failures (3 entries)
- [ok] every error_log.json message was cleaned, not dropped
- [ok] run log totals add up: each stage, and the run
- [ok] run log counts are category names and numbers only
- [ok] the model's answers repeated every planted value (10 of 10: missing none)
- [ok] placeholders where secrets were: ipc_stdout (48)
- [ok] placeholders where secrets were: stderr (1)
- [ok] placeholders where secrets were: error_log (5)
- [ok] placeholders where secrets were: run_logs (10)
- [ok] placeholders where secrets were: file_summaries (24)
- [ok] placeholders where secrets were: uml_pdf (18)
- [ok] placeholders where secrets were: plantuml_io (2)
- [ok] placeholders where secrets were: directory_summaries (5)
- [ok] placeholders where secrets were: business_rules (4)
- [ok] placeholders where secrets were: unit_tests (7)
- [ok] placeholders where secrets were: integration_tests (3)
- [ok] placeholders where secrets were: generated_test_sources (2)
- [ok] placeholders where secrets were: consistency_review (8)
- [ok] placeholders where secrets were: run_report (71)
- [ok] placeholders where secrets were: markdown_export (15)
- [ok] the vector store holds both indexes (12 code and 28 summary document(s))
- [ok] dotnet's build output was produced and read
- [ok] storage boundaries were read without problems (0 problem(s))

## Storage boundaries (measured; kept on purpose)

| Store | What it is | Inspected | Planted values held | Cases |
|---|---|---|---|---|
| vector_store_code | the code index (ChromaDB): raw source chunks, so retrieval finds the real code | 12 | 3 | S03, S07, S08 |
| vector_store_files | the vector store's files as bytes (SQLite pages, full-text index, HNSW) | 9 | 15 | S03, S07, S08 |
| test_build_output | dotnet's build of the analysed codebase for the generated tests (its compiled constants) | 129 | 12 | S01, S04, S07, S08 |

## Not assessed here

- stryker_reports (app/agent/MT_output/LedgerLite/stryker/): not produced: Stryker.NET is not installed (mutation testing reports not_run); Stryker's own reports contain the mutated source files by design (see the storage boundaries in the README).

## Diagnostics

- The model's answers repeated 40 planted value(s): S01, S02, S03, S04, S05, S06, S07, S08, S09, S10.
- Placeholders by sink: business_rules 4, consistency_review 8, directory_summaries 5, error_log 5, file_summaries 24, generated_test_sources 2, integration_tests 3, ipc_stdout 48, markdown_export 15, mutation_report 0, plantuml_io 2, run_logs 10, run_report 71, stderr 1, token_usage_log 0, uml_pdf 18, unit_tests 7.
- Consistency Review: status partial, 2 finding(s); 1 accepted quoting a line that held a secret (gap B), 1 with made-up evidence rejected.
- LangGraph chain callbacks (excluded): 211 event(s), 361 planted value(s) seen (S01, S02, S03, S04, S05, S06, S07, S08, S09, S10). LangGraph hands each node's raw input (the graph state) to chain callbacks before the node calls its model (T-077 known limit). The app registers no handler that keeps it and turns tracing off in every protected run; only this harness's recorder saw it.

## Substitutions

- **AI provider**: offline_model.OfflineProvider in place of ChatGoogleGenerativeAI; agent/llm.py builds it with its real _retrying() and redacting() wrappers. Not verified: Gemini's own structured-output mode, HTTP client and error types; what a real model answers.
- **Model answers**: scripted answers (offline_answers.py) that repeat planted secrets on purpose. Not verified: whether a real model repeats secrets in other ways.
- **Retry wait**: the same, unchanged (one 503 is scripted). Not verified: nothing.
- **Network**: this process refuses non-loopback connections and lookups and records them; NuGet sources are cleared; telemetry is switched off for dotnet and ChromaDB; Hugging Face runs offline. Not verified: network use by child processes (dotnet, java), which the guard cannot see.
- **LangSmith tracing**: turned on in the environment, pointed at an unroutable address (198.51.100.7). Not verified: a real LangSmith endpoint.
- **dotnet build servers**: switched off for the backend (UseSharedCompilation=false, MSBUILDDISABLENODEREUSE=1), so nothing holds the workspace open after the run. Not verified: nothing about the generated tests: they are built and run the same way.
- **App location**: a byte-for-byte copy of the app's Python files and plantuml.jar in a temporary folder. Not verified: the packaged (PyInstaller) build.
- **Electron**: this script, applying main.js's rules (one JSON object per line, request_id, stream types). Not verified: the Electron screens (see the manual checklist).
