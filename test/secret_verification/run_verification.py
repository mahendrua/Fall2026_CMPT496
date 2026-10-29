"""
@file run_verification.py
@brief T-079: plants ten fake secrets in a sample codebase, runs Checkpoint's
real backend on it offline, and checks what left the app for them (US-034).
@details

    .venv\\Scripts\\python.exe test\\secret_verification\\run_verification.py [--out DIR] [--keep]

1. Workspace (a new temporary folder):
       inputs/LedgerLite/   the fixture: raw, never scanned as an output
       app/                 a copy of the app's Python code and plantuml.jar,
                            with its own .env selecting the offline model;
                            everything the backend writes lands here
       tmp/                 TEMP for the backend: the generated test project
       capture/             what the provider received, what callbacks were
                            handed, stdout, stderr, network attempts
2. The real backend (main.py, via offline_backend.py) is started as Electron
   starts it and sent a scripted session over stdin: the full pipeline, then
   follow-up commands, some scripted to fail.
3. The independent scanner (leak_scanner.py) checks each sink for the ten
   values; the fixture says what must have been replaced and what must not.
4. report.json and summary.md go to --out (default runs/<time>/, ignored by
   git). They hold case IDs, counts and locations, never a value, and are
   checked for values before they are written.

Exit status: 0 clean and complete, 1 a planted value leaked, 2 a required
stage, artifact or check is missing (incomplete, never clean).

Real: main.py, the dispatcher, every pipeline stage and agent, agent/llm.py
with its retries and redaction, ChromaDB with the cached all-MiniLM-L6-v2
model (offline), dotnet build and test (offline NuGet), PlantUML (Java).
Substituted: the AI provider (offline_model.py), and see SUBSTITUTIONS.
"""

import argparse
import hashlib
import json
import os
import queue
import random
import re
import shutil
import string
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))

from leak_scanner import LeakScanner, Needle, missing_controls  # noqa: E402
from offline_answers import FAIL_CONSISTENCY, FAIL_DIRECTORY, NORMAL, Answers  # noqa: E402
from offline_model import PHASE_FILE, REQUESTS_FILE  # noqa: E402
from secret_fixture import CASE_IDS, KNOWN_GAPS, build_fixture  # noqa: E402

APP_PARTS = ("agent", "backend", "src", "utils")
APP_FILES = ("main.py", "plantuml.jar")

# What last_run_log.json must show for the full pipeline, in order.
PIPELINE_STAGES = (
    "build_database", "generate_file_summaries", "build_summary_database",
    "generate_directory_summaries", "validate_business_rules", "generate_unit_tests",
    "generate_integration_tests", "generate_all_uml", "consistency_review",
)
AI_STAGES = (
    "generate_file_summaries", "generate_directory_summaries", "validate_business_rules",
    "generate_unit_tests", "generate_integration_tests", "consistency_review",
)

# Every kind of request the pipeline must have sent, by structured-output schema.
REQUEST_KINDS = (
    "FileSummaryOutput", "ContextAnalysisOutput", "DirectoryOutput", "JudgementOutput",
    "BusinessRulesOutput", "CondenserOutput", "ValidatorOutput", "UnitTest", "TestRepair",
    "WorkflowGroups", "IntegrationTest", "ConsistencyAnalysisOutput",
)

# Files the pipeline must summarise (all but the project file).
SUMMARISED = (
    "Billing/InvoiceService.cs", "Integrations/PaymentClient.cs", "tools/report_export.py",
    "web/session.js", "scripts/deploy.sh", "docs/SETUP.md", "docs/architecture.md",
)
RETRIED = "tools/report_export.py"        # its first request gets a 503
FAILED = "scripts/deploy.sh"              # always a 400 quoting secrets

CODEBASE = "LedgerLite"
MARKDOWN_DIR = "markdown_export"
DIRECTORIES = ("Billing", "Integrations", "tools", "web", "scripts", "docs")

# What the file summary agent saves each summarised file as.
SUMMARY_FILES = {rel: Path(rel).name.replace(".", "-") + "__1.json" for rel in SUMMARISED}

# Nonsecret text the scripted answers put in each saved summary, beside the
# secrets they repeat: it must come through cleaning unchanged.
OUTPUT_CONTROLS = {
    "Billing/InvoiceService.cs": [
        "ApprovalThreshold = 5000m", 'var primaryKey = "InvoiceId";', "-approvalThreshold : decimal = 5000",
        '-cacheKey : string = "invoice-cache-v2"', "Server=db.ledger.internal;Database=Ledger;User Id=ledger_app;Password=",
        "AdminPassword is a public setting.",
    ],
    "Integrations/PaymentClient.cs": ["https://docs.example.com/ledger/api", "amount >= 10000m", "Payments above 10000 are large."],
    "tools/report_export.py": ['os.getenv("REPORTING_API_KEY")', "C:\\LedgerLite\\exports", "postgresql://report_user:",
                               "@reports.ledger.internal:5432/ledger"],
    "web/session.js": ["process.env.LEDGER_API_KEY", 'tokenType "Bearer"', "primaryKey is ledgerId"],
    "docs/SETUP.md": ["Invoices above 5000 CAD need manager approval", "REPORTING_API_KEY=YOUR_API_KEY"],
    "docs/architecture.md": ["+ApproveInvoice(invoice : Invoice) : bool", "-approvalThreshold : decimal = 5000"],
}

PLACEHOLDER = re.compile(r"\[REDACTED_[A-Z_]+_\d+\]")

# Kept by the app on purpose or outside what T-078 cleans; assessed in
# Checkpoint 5 (storage boundaries), not counted here as clean or leaking.
NOT_ASSESSED = [
    {"sink": "vector_stores", "where": "app/vectorStores/",
     "why": "the code index holds raw source chunks by design; storage boundary, assessed separately"},
    {"sink": "test_build_output", "where": "tmp/checkpoint-generated-tests/*/LedgerLite.Tests.artifacts/",
     "why": "dotnet's build of the analysed codebase (its own compiled constants); storage boundary"},
    {"sink": "stryker_reports", "where": "app/agent/MT_output/LedgerLite/stryker/",
     "why": "not produced: Stryker.NET is not installed (mutation testing reports not_run)"},
]

SUBSTITUTIONS = [
    {"what": "AI provider", "real": "Google Gemini through langchain_google_genai",
     "used": "offline_model.OfflineProvider in place of ChatGoogleGenerativeAI; agent/llm.py builds it with its "
             "real _retrying() and redacting() wrappers",
     "unverified": "Gemini's own structured-output mode, HTTP client and error types; what a real model answers"},
    {"what": "Model answers", "real": "a model's answers",
     "used": "scripted answers (offline_answers.py) that repeat planted secrets on purpose",
     "unverified": "whether a real model repeats secrets in other ways"},
    {"what": "Retry wait", "real": "agent/llm.py backs off 2 s and more on a 503",
     "used": "the same, unchanged (one 503 is scripted)", "unverified": "nothing"},
    {"what": "Network", "real": "open",
     "used": "this process refuses non-loopback connections and lookups and records them; NuGet sources are "
             "cleared; telemetry is switched off for dotnet and ChromaDB; Hugging Face runs offline",
     "unverified": "network use by child processes (dotnet, java), which the guard cannot see"},
    {"what": "LangSmith tracing", "real": "off unless the environment turns it on",
     "used": "turned on in the environment, pointed at an unroutable address (198.51.100.7)",
     "unverified": "a real LangSmith endpoint"},
    {"what": "dotnet build servers", "real": "the compiler and MSBuild servers stay running after a build",
     "used": "switched off for the backend (UseSharedCompilation=false, MSBUILDDISABLENODEREUSE=1), so nothing "
             "holds the workspace open after the run",
     "unverified": "nothing about the generated tests: they are built and run the same way"},
    {"what": "App location", "real": "the repository or the packaged app folder",
     "used": "a byte-for-byte copy of the app's Python files and plantuml.jar in a temporary folder",
     "unverified": "the packaged (PyInstaller) build"},
    {"what": "Electron", "real": "main.js reading the backend's stdout",
     "used": "this script, applying main.js's rules (one JSON object per line, request_id, stream types)",
     "unverified": "the Electron screens (see the manual checklist)"},
]

STREAM_TYPES = {"progress", "pipeline_progress", "token_usage", "token_usage_stage"}


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _jsonl(path):
    path = Path(path)
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# Workspace
# ---------------------------------------------------------------------------

_NUGET_OFFLINE = """<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <!-- T-079: restore only from the local package cache; nothing is downloaded. -->
  <packageSources>
    <clear />
  </packageSources>
</configuration>
"""


def _random_key(prefix, length, seed, label):
    rng = random.Random(f"T-079/{seed}/{label}")
    return prefix + "".join(rng.choice(string.ascii_letters + string.digits) for _ in range(length))


class Workspace:
    """The folders of one run, and what was true of them before it."""

    def __init__(self, root, seed):
        self.root = Path(root)
        self.seed = seed
        self.inputs = self.root / "inputs"
        self.app = self.root / "app"
        self.tmp = self.root / "tmp"
        self.capture = self.root / "capture"

        for folder in (self.app, self.tmp, self.capture):
            folder.mkdir(parents=True)

        self.fixture = build_fixture(self.inputs, seed=seed)
        self.manifest = self._copy_app()
        (self.root / "NuGet.Config").write_text(_NUGET_OFFLINE, encoding="utf-8")

        # The provider key the app is configured with, and a tracing key in
        # the environment: neither may appear in any output either.
        self.provider_key = _random_key("AIza", 35, seed, "provider")
        self.tracing_key = _random_key("lsv2_pt_", 32, seed, "tracing")
        env = self.app / ".env"
        env.write_text(
            "ACTIVE_LLM_PROVIDER=google\n"
            "ACTIVE_LLM_MODEL=offline-gemini\n"
            f"ACTIVE_LLM_API_KEY={self.provider_key}\n",
            encoding="utf-8",
        )
        self.env_digest = _digest(env)
        (self.capture / PHASE_FILE).write_text(NORMAL, encoding="utf-8")

    def _copy_app(self):
        files = [REPO / name for name in APP_FILES]
        # Only Python files: the agents' output folders hold none, so no
        # earlier run's outputs come along.
        for part in APP_PARTS:
            files += sorted(p for p in (REPO / part).rglob("*.py") if "__pycache__" not in p.parts)

        manifest = {}
        for source in files:
            relative = source.relative_to(REPO).as_posix()
            target = self.app / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            manifest[relative] = _digest(source)

        identical = all(_digest(self.app / rel) == digest for rel, digest in manifest.items())
        return {"files": len(manifest), "identical": identical, "digests": manifest}

    def needles(self):
        return [Needle(*n) for n in self.fixture.needles()] + [
            Needle("K00", self.provider_key),
            Needle("K01", self.tracing_key),
        ]

    def backend_env(self):
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith("PYTHON")}
        env.update({
            "PYTHONUNBUFFERED": "1",
            "TEMP": str(self.tmp), "TMP": str(self.tmp),
            "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_DATASETS_OFFLINE": "1",
            "ANONYMIZED_TELEMETRY": "False",
            "DOTNET_CLI_TELEMETRY_OPTOUT": "1", "DOTNET_NOLOGO": "1",
            "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1", "DOTNET_CLI_WORKLOAD_UPDATE_NOTIFY_DISABLE": "1",
            "DOTNET_GENERATE_ASPNET_CERTIFICATE": "false",
            # No compiler or MSBuild server outlives the run, holding the
            # workspace (and the compiled fixture) open.
            "UseSharedCompilation": "false", "MSBUILDDISABLENODEREUSE": "1",
            "DOTNET_CLI_USE_MSBUILD_SERVER": "0",
            "LANGSMITH_TRACING": "true", "LANGSMITH_ENDPOINT": "http://198.51.100.7:9",
            "LANGSMITH_API_KEY": self.tracing_key,
        })
        return env


# ---------------------------------------------------------------------------
# The backend session
# ---------------------------------------------------------------------------

class Backend:
    """main.py over stdin and stdout, read the way main.js reads it."""

    def __init__(self, ws):
        self.ws = ws
        self.lines = []           # every stdout line, as received
        self.malformed = []       # (line number, why) for lines main.js would reject
        self._queue = queue.Queue()
        self.process = subprocess.Popen(
            [sys.executable, "-u", str(HERE / "offline_backend.py"),
             "--app", str(ws.app), "--capture", str(ws.capture),
             "--codebase", str(ws.fixture.root), "--seed", str(ws.seed)],
            cwd=ws.app, env=ws.backend_env(),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self._stderr = open(ws.capture / "stderr.txt", "wb")
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stdout(self):
        with open(self.ws.capture / "stdout.jsonl", "wb") as raw:
            for line in self.process.stdout:
                raw.write(line)
                raw.flush()
                self._queue.put(line.decode("utf-8", "replace").rstrip("\r\n"))
        self._queue.put(None)

    def _read_stderr(self):
        for chunk in iter(lambda: self.process.stderr.read(4096), b""):
            self._stderr.write(chunk)
            self._stderr.flush()

    def request(self, number, command, args, timeout):
        """Send one command and wait for its terminal response, as main.js does."""
        request_id = f"t079-{number}"
        payload = json.dumps({"type": "command", "request_id": request_id, "command": command, "args": args})
        self.process.stdin.write((payload + "\n").encode("utf-8"))
        self.process.stdin.flush()

        deadline = time.monotonic() + timeout
        while True:
            try:
                line = self._queue.get(timeout=max(0.1, deadline - time.monotonic()))
            except queue.Empty:
                return None
            if line is None:
                return None
            if not line.strip():
                continue

            self.lines.append(line)
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                self.malformed.append((len(self.lines), "not JSON"))
                continue
            if not isinstance(message, dict):
                self.malformed.append((len(self.lines), "not a JSON object"))
                continue
            if not isinstance(message.get("request_id"), str) or not message["request_id"]:
                self.malformed.append((len(self.lines), "no request_id"))
                continue
            if message["request_id"] != request_id or message.get("type") in STREAM_TYPES or message.get("accepted") is True:
                continue
            if not isinstance(message.get("success"), bool):
                self.malformed.append((len(self.lines), "terminal response without a success flag"))
            return message

    def close(self, timeout=120):
        self.process.stdin.close()
        try:
            code = self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.process.kill()
            code = None
        # Drain what is left on stdout.
        while True:
            try:
                line = self._queue.get(timeout=5)
            except queue.Empty:
                break
            if line is None:
                break
            if line.strip():
                self.lines.append(line)
        time.sleep(0.2)
        self._stderr.close()
        return code


def _review_note(ws):
    """A review decision on the first saved finding, its note quoting a secret with its label."""
    saved = json.loads((ws.app / "agent" / "consistency_output" / CODEBASE / "consistency_result.json")
                       .read_text(encoding="utf-8"))
    return {
        "codebase": str(ws.fixture.root), "scope": "full",
        "check_id": saved["check_id"], "finding_id": saved["findings"][0]["id"], "status": "reviewed",
        "note": f'Checked with the DBA; the old password = "{ws.fixture.secret("S08").value}" was rotated.',
    }


def session_steps(ws):
    codebase = str(ws.fixture.root)
    value = {s.case_id: s.value for s in ws.fixture.secrets}
    return [
        # (label, phase, command, args or args(ws), expected success, timeout seconds)
        ("full pipeline", NORMAL, "full_pipeline", {"codebase": codebase}, True, 1800),
        ("run usage", NORMAL, "get_run_usage", {}, True, 60),
        ("review result", NORMAL, "get_consistency_result", {"codebase": codebase}, True, 60),
        ("review note", NORMAL, "save_consistency_review", _review_note, True, 60),
        ("mutation testing", NORMAL, "run_mutation_tests", {"codebase": codebase}, True, 300),
        # A run of its own, so it knows nothing the pipeline found: what the
        # detector misses in this file (known gap A) shows here.
        ("review check of the PlantUML notes", NORMAL, "check_consistency",
         {"codebase": codebase, "scope": "selected", "targets": ["docs/architecture.md"]}, True, 300),
        ("failing directory step", FAIL_DIRECTORY, "generate_directory_summaries", {"codebase": codebase}, False, 600),
        ("failing review check", FAIL_CONSISTENCY, "check_consistency",
         {"codebase": codebase, "scope": "selected", "targets": ["Integrations/PaymentClient.cs"]}, True, 300),
        # Electron files its own errors through the backend.
        ("frontend error record", NORMAL, "record_error",
         {"source_command": "renderer", "message": f'Could not render the summary: password = "{value["S01"]}"'},
         True, 60),
        ("unknown command quoting a secret", NORMAL, f"export --password={value['S09']}", {}, False, 60),
        ("error log", NORMAL, "get_errors", {}, True, 60),
    ]


def export_markdown(ws):
    """The Markdown export, a terminal tool run outside any backend run, on the saved summaries."""
    result = subprocess.run(
        [sys.executable, "-m", "utils.json_to_markdown",
         "--input-dir", str(Path("agent") / "file_summary_agent_output" / CODEBASE),
         "--output-dir", MARKDOWN_DIR],
        cwd=ws.app, env=ws.backend_env(), capture_output=True, timeout=300,
    )
    (ws.capture / "markdown_console.txt").write_bytes(result.stdout + b"\n" + result.stderr)
    return result.returncode


def run_session(ws):
    backend = Backend(ws)
    steps = []
    started = time.monotonic()

    for number, (label, phase, command, args, expect, timeout) in enumerate(session_steps(ws), 1):
        (ws.capture / PHASE_FILE).write_text(phase, encoding="utf-8")
        t0 = time.monotonic()
        try:
            args = args(ws) if callable(args) else args
        except (OSError, ValueError, KeyError, IndexError):
            # What it needs from an earlier step is missing: reported as unanswered.
            steps.append({"label": label, "command": command, "phase": phase, "answered": False,
                          "success": None, "expected_success": expect, "warning": False, "seconds": 0,
                          "response": None})
            continue
        response = backend.request(number, command, args, timeout)
        steps.append({
            "label": label, "command": command, "phase": phase,
            "answered": response is not None,
            "success": None if response is None else response.get("success"),
            "expected_success": expect,
            "warning": bool(isinstance(response, dict) and isinstance(response.get("result"), dict)
                            and response["result"].get("warning")),
            "seconds": round(time.monotonic() - t0, 1),
            "response": response,
        })
        if response is None:
            break

    (ws.capture / PHASE_FILE).write_text(NORMAL, encoding="utf-8")
    code = backend.close()
    markdown = export_markdown(ws)
    return {
        "steps": steps, "exit_code": code, "stdout_lines": len(backend.lines),
        "malformed": backend.malformed, "seconds": round(time.monotonic() - started, 1),
        "lines": backend.lines, "markdown_exit_code": markdown,
    }


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

class Checks:
    """Named pass/fail checks, each with a short, value-free detail."""

    def __init__(self):
        self.items = []

    def add(self, name, ok, detail="", required=True):
        self.items.append({"check": name, "ok": bool(ok), "required": required, "detail": detail})
        return ok

    def failed(self):
        return [c for c in self.items if c["required"] and not c["ok"]]


def _file_of(text, root):
    """The fixture-relative path a file summary or Consistency Review request is about."""
    match = re.search(r"^File path:\s*\n(.+)$", text, re.M)
    if match:
        path = match.group(1).strip().replace("\\", "/")
        base = str(root).replace("\\", "/").rstrip("/") + "/"
        return path[len(base):] if path.lower().startswith(base.lower()) else None

    # Consistency Review: "Codebase: ...", then the file relative to its root.
    match = re.search(r"^Codebase: .*\nFile: (.+)\nCode in scope:", text, re.M)
    return match.group(1).strip() if match else None


def _message_text(messages):
    return "\n".join(m.get("content") if isinstance(m.get("content"), str) else json.dumps(m.get("content"))
                     for m in messages)


def _full_pipeline_log(app):
    logs = []
    for path in sorted((app / "run_logs").glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("command") == "full_pipeline":
            logs.append((path, data))
    return logs[-1] if logs else (None, None)


def evaluate(ws, session):
    fixture = ws.fixture
    checks = Checks()
    scanner = LeakScanner(ws.needles(), raw_roots=[ws.inputs])
    result = {"requests": {}, "stages": {}, "counts": {}, "diagnostics": {}}

    # --- the session ------------------------------------------------------
    for step in session["steps"]:
        checks.add(f"step answered: {step['label']}", step["answered"])
        if step["answered"]:
            checks.add(f"step outcome as scripted: {step['label']}", step["success"] == step["expected_success"],
                       f"success={step['success']}, expected {step['expected_success']}")
    checks.add("backend exited cleanly", session["exit_code"] == 0, f"exit code {session['exit_code']}")
    checks.add("every stdout line is a message main.js accepts", not session["malformed"],
               f"{len(session['malformed'])} rejected line(s)")

    started = _jsonl(ws.capture / "backend_started.jsonl")
    loaded = [e for e in started if "modules" in e]
    checks.add("app modules were loaded from the app copy only",
               bool(loaded) and not loaded[-1]["modules"]["outside_app"],
               f"{loaded[-1]['modules']['from_app'] if loaded else 0} module(s) from the copy")
    checks.add("the app copy is byte-for-byte the repository's code", ws.manifest["identical"],
               f"{ws.manifest['files']} file(s)")

    # --- stages ---------------------------------------------------------
    log_path, log = _full_pipeline_log(ws.app)
    if log is None:
        checks.add("full pipeline run log exists", False)
    else:
        names = [s["stage"] for s in log["stages"]]
        result["stages"] = {
            s["stage"]: {"status": s["status"], "calls": s["calls"], "redacted_secrets": s["redacted_secrets"]}
            for s in log["stages"]
        }
        checks.add("every pipeline stage ran, in order", tuple(names) == PIPELINE_STAGES, ", ".join(names))
        checks.add("every pipeline stage succeeded", all(s["status"] == "success" for s in log["stages"]),
                   ", ".join(f"{s['stage']}={s['status']}" for s in log["stages"] if s["status"] != "success"))
        for stage in AI_STAGES:
            calls = result["stages"].get(stage, {}).get("calls", 0)
            checks.add(f"stage made AI calls: {stage}", calls > 0, f"{calls} call(s)")
        result["counts"]["run_totals"] = log["totals"]["redacted_secrets"]
        result["counts"]["run_calls"] = log["totals"]["calls"]

    # --- outgoing requests ------------------------------------------------
    requests = _jsonl(ws.capture / REQUESTS_FILE)
    kinds = Counter(r["tool"] for r in requests)
    result["requests"] = {"total": len(requests), "by_kind": dict(sorted(kinds.items(), key=str))}
    checks.add("the provider received requests", bool(requests), f"{len(requests)} request(s)")
    for kind in REQUEST_KINDS:
        checks.add(f"request kind sent: {kind}", kinds.get(kind, 0) > 0, f"{kinds.get(kind, 0)}")
    checks.add("no request the scenario could not answer",
               not [r for r in requests if (r.get("outcome") or {}).get("error") == "UnexpectedRequest"],
               f"{sum((r.get('outcome') or {}).get('error') == 'UnexpectedRequest' for r in requests)}")

    # Which fixture file each scanned request or callback was about, to name known gaps.
    about = result["about"] = {}

    for entry in requests:
        where = f"request {entry['number']} ({entry['tool']})"
        about[where] = _file_of(_message_text(entry["messages"]), fixture.root)
        scanner.scan_data("provider_requests", {"messages": entry["messages"], "tools": entry["tools"]}, where)

    # Which file each file summary request was about.
    summary_requests = {}
    for entry in requests:
        if entry["tool"] == "FileSummaryOutput":
            rel = _file_of(_message_text(entry["messages"]), fixture.root)
            summary_requests.setdefault(rel, []).append(entry)
    checks.add("every summarised file was sent", set(SUMMARISED) <= set(summary_requests),
               ", ".join(sorted(set(SUMMARISED) - set(summary_requests))))
    checks.add("the 503 was retried with the same cleaned request",
               len(summary_requests.get(RETRIED, [])) == 2
               and summary_requests[RETRIED][0]["messages"] == summary_requests[RETRIED][1]["messages"],
               f"{len(summary_requests.get(RETRIED, []))} request(s)")

    # Nonsecret controls reach the provider unchanged.
    over = {}
    for rel, entries in summary_requests.items():
        if rel is None:
            continue
        controls = {c.control_id: c.text for c in fixture.controls_in(rel)}
        missing = missing_controls(_message_text(entries[0]["messages"]), controls)
        if missing:
            over[rel] = missing
    result["requests"]["controls_changed"] = over
    checks.add("every control reaches the provider unchanged", not over,
               "; ".join(f"{rel}: {', '.join(ids)}" for rel, ids in over.items()))

    # --- model callbacks --------------------------------------------------
    callbacks = _jsonl(ws.capture / "model_callbacks.jsonl")
    starts = [e for e in callbacks if e["event"] in ("chat_model_start", "llm_start")]
    checks.add("model callbacks were recorded", bool(starts), f"{len(starts)} call start(s)")
    # Numbered, not named by run ID: LangChain's run IDs are time-ordered and share prefixes.
    for number, entry in enumerate(starts, 1):
        where = f"callback {number} ({entry['event']})"
        batches = entry.get("messages") or []
        about[where] = _file_of(_message_text([m for batch in batches for m in batch]), fixture.root)
        scanner.scan_data("model_callbacks", entry, where)

    # Counts: per call, as the callbacks were told, against the fixture.
    per_file = {}
    for entry in starts:
        if entry["event"] != "chat_model_start":
            continue
        text = _message_text([m for batch in entry["messages"] for m in batch])
        rel = _file_of(text, fixture.root)
        if rel and "FileSummaryOutput" in json.dumps(entry.get("invocation_params") or {}):
            per_file.setdefault(rel, []).append((entry.get("metadata") or {}).get("redacted_secrets") or {})

    count_rows = {}
    for rel in SUMMARISED:
        reported = per_file.get(rel, [])
        expected = fixture.expected_counts(rel)
        gaps = sorted({o.known_gap for o in fixture.occurrences_in(rel) if o.known_gap})
        count_rows[rel] = {"calls": len(reported), "reported": reported[0] if reported else None,
                           "expected": expected, "known_gaps": gaps}
        checks.add(f"one counted call for {rel}", len(reported) == 1, f"{len(reported)} call(s)")
        if reported:
            ok = reported[0] == expected
            checks.add(f"redaction count matches the fixture: {rel}", ok,
                       f"reported {reported[0]}, expected {expected}" + (f" (known gap {', '.join(gaps)})" if gaps else ""))
    result["counts"]["file_summary_calls"] = count_rows

    if log is not None:
        summary_calls = [c for c in log["calls"] if c["stage"] == "generate_file_summaries"]
        checks.add("run log has one call per summarised file (retries not counted again)",
                   len(summary_calls) == len(SUMMARISED), f"{len(summary_calls)} call(s)")
        failed_calls = [c for c in summary_calls if c["status"] == "failed"]
        checks.add("the 400 is recorded as one failed call", len(failed_calls) == 1, f"{len(failed_calls)}")

    # --- LangGraph chain callbacks (diagnostic) ----------------------------
    chain = LeakScanner(ws.needles(), raw_roots=[ws.inputs])
    for entry in _jsonl(ws.capture / "chain_callbacks.jsonl"):
        chain.scan_data("chain_callbacks", entry.get("inputs"), f"chain {entry.get('name')}")
    chain_report = chain.report()
    result["diagnostics"]["chain_callbacks"] = {
        "events": len(chain_report.inspections),
        "leaks": chain_report.leaks,
        "cases": sorted({f.case_id for f in chain_report.findings}),
        "why_excluded": (
            "LangGraph hands each node's raw input (the graph state) to chain callbacks before the "
            "node calls its model (T-077 known limit). The app registers no handler that keeps it "
            "and turns tracing off in every protected run; only this harness's recorder saw it."
        ),
    }

    # --- network --------------------------------------------------------
    network = _jsonl(ws.capture / "network.jsonl")
    result["network"] = {"attempts": len(network), "hosts": sorted({e["host"] for e in network})}
    checks.add("no network attempt from the backend", not network,
               f"{len(network)} attempt(s) to {', '.join(result['network']['hosts'])}")

    # --- inputs unchanged ------------------------------------------------
    changed = fixture.changed_files()
    present = {p.relative_to(fixture.root).as_posix() for p in fixture.root.rglob("*") if p.is_file()}
    added = sorted(present - set(fixture.files))
    checks.add("fixture source files unchanged", not changed, ", ".join(changed))
    checks.add("nothing written into the analysed codebase", not added,
               f"{len(added)} file(s): {', '.join(sorted({a.split('/')[0] for a in added}))}" if added else "")
    checks.add("provider credential configuration unchanged", _digest(ws.app / ".env") == ws.env_digest)

    evaluate_outputs(ws, session, scanner, checks, result, log)
    return scanner, checks, result


# ---------------------------------------------------------------------------
# Logs, records and saved outputs
# ---------------------------------------------------------------------------

def output_sinks(ws):
    """(sink, what it is, required files, folders scanned whole, file suffixes kept or None)."""
    agent = ws.app / "agent"
    fs = agent / "file_summary_agent_output" / CODEBASE
    ds = agent / "directory_agent_output" / CODEBASE
    br = agent / "BR_agent_output" / CODEBASE
    ut = agent / "UT_agent_output" / CODEBASE
    it = agent / "IT_agent_output" / CODEBASE
    cr = agent / "consistency_output" / CODEBASE
    rr = ws.app / "run_reports" / CODEBASE
    mt = agent / "MT_output" / CODEBASE
    projects = sorted((ws.tmp / "checkpoint-generated-tests").glob(f"*/{CODEBASE}.Tests"))
    tests = projects[0] if projects else ws.tmp / "checkpoint-generated-tests" / f"{CODEBASE}.Tests"
    results = ("validated_tests.json", "discarded_tests.json", "test_report.json")

    return [
        ("stderr", "backend stderr (Electron's console)", [ws.capture / "stderr.txt"], [], None),
        ("error_log", "error_log.json", [ws.app / "error_log.json"], [], None),
        ("run_logs", "last_run_log.json and run_logs/", [ws.app / "last_run_log.json"], [ws.app / "run_logs"], None),
        ("token_usage_log", "token_usage_log.json", [ws.app / "token_usage_log.json"], [], None),
        ("file_summaries", "file summaries and file-level business rules",
         [fs / name for name in SUMMARY_FILES.values()] + [fs / "business_rules" / "business_rules.json"], [fs], {".json"}),
        ("uml_pdf", "UML PDFs", [fs / name.replace(".json", ".pdf") for name in SUMMARY_FILES.values()], [fs], {".pdf"}),
        ("plantuml_io", "what PlantUML was given and drew (UML step and run report)",
         [], [ws.capture / "plantuml"], None),
        ("directory_summaries", "directory summaries and directory-level business rules",
         [ds / f"{d}.json" for d in DIRECTORIES]
         + [ds / "root_output" / f"{CODEBASE}.json", ds / "business_rules" / "business_rules.json"], [ds], None),
        ("business_rules", "validated and discarded business rules",
         [br / "validated_rules.json", br / "discarded_rules.json"], [br], None),
        ("unit_tests", "unit tests, results and the HTML test report",
         [ut / n for n in ("unit_tests.json", "unit_tests.txt", *results, "unit_test_report.html")], [ut], None),
        ("integration_tests", "integration tests, results and the HTML test report",
         [it / n for n in ("integration_tests.json", "integration_tests.txt", *results, "integration_test_report.html")],
         [it], None),
        ("generated_test_sources", "the generated test project (UT_*.cs, IT_*.cs)", [], [tests], None),
        ("consistency_review", "Consistency Review results and review notes",
         [cr / n for n in ("consistency_result.json", "consistency_result_selected.json", "reviews.json")], [cr], None),
        ("run_report", "run report, its data and project diagram",
         [rr / n for n in ("run_report.html", "run_report_data.json", "project_diagram.puml", "project_diagram.svg")],
         [rr], None),
        ("mutation_report", "mutation_report.json", [mt / "mutation_report.json"], [mt], None),
        ("markdown_export", "Markdown export (terminal tool, outside a run)",
         [ws.capture / "markdown_console.txt"], [ws.app / MARKDOWN_DIR], None),
    ]


def scan_outputs(ws, scanner):
    """Scan every output sink; a required file that is missing or empty is a problem."""
    for sink, _, required, folders, suffixes in output_sinks(ws):
        files = set(required)
        for folder in folders:
            if not folder.is_dir():
                scanner.missing(sink, folder.relative_to(ws.root).as_posix(), "the folder was not produced")
                continue
            files |= {
                p for p in folder.rglob("*")
                if p.is_file() and (suffixes is None or p.suffix.lower() in suffixes)
                and p.parent.name != "stryker" and "stryker" not in p.relative_to(folder).parts
            }
        if not files:
            scanner.missing(sink, sink, "nothing was produced")
        for path in sorted(files):
            if path in required:
                scanner.require_file(sink, path, base=ws.root)
            else:
                scanner.scan_file(sink, path, base=ws.root)

    # stdout: one JSON object per line, each read structurally.
    for number, line in enumerate(_lines(ws.capture / "stdout.jsonl"), 1):
        try:
            scanner.scan_data("ipc_stdout", json.loads(line), f"stdout line {number}")
        except json.JSONDecodeError:
            scanner.scan_text("ipc_stdout", line, f"stdout line {number}")


def _lines(path):
    return [l for l in Path(path).read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()] \
        if Path(path).is_file() else []


def _strings(value):
    """Every string in JSON-shaped data, joined: the text a control or placeholder is looked for in."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "\n".join(_strings(v) for v in value.values())
    if isinstance(value, list):
        return "\n".join(_strings(v) for v in value)
    return ""


def _load(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _prose(text):
    """Text left once placeholders are taken out: what the output still says."""
    return " ".join(PLACEHOLDER.sub(" ", text).split())


def _shape(value):
    """JSON data with its text taken out: keys, list lengths, numbers, flags and nulls."""
    if isinstance(value, dict):
        return {k: _shape(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_shape(v) for v in value]
    return "text" if isinstance(value, str) else value


def _without_nulls(value):
    if isinstance(value, dict):
        return {k: _without_nulls(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_without_nulls(v) for v in value]
    return value


def _expected_summary(ws, rel, schema):
    """The saved form of the scripted answer, before any cleaning: what the agent would write."""
    answer = Answers(ws.fixture.root, ws.seed).summary(rel)
    if answer is None:   # the failed file: the agent saves the error as its summary
        answer = {"summary": "error"}
    saved = schema.model_validate(dict(answer, path="path")).model_dump(mode="json")
    for rule in saved.get("business_rules", []):
        rule["source_file"] = "path"     # set by the agent after the call
    return saved


def evaluate_outputs(ws, session, scanner, checks, result, log):
    # The app's own schema, only to check saved summaries still match it.
    if str(REPO) not in sys.path:
        sys.path.append(str(REPO))
    from agent.structured_output.file_summary_output import FileSummaryOutput

    scan_outputs(ws, scanner)
    agent = ws.app / "agent"
    fs = agent / "file_summary_agent_output" / CODEBASE
    quality = result["outputs"] = {}

    # --- file summaries: schema, content, controls ------------------------
    for rel, name in SUMMARY_FILES.items():
        data = _load(fs / name)
        if data is None:
            checks.add(f"summary saved and readable: {rel}", False)
            continue
        # Cleaning may change text, never structure. (The app saves unset
        # visibilities as null, which its own schema rejects on reading
        # back, cleaned or not, so nulls are set aside to validate.)
        try:
            FileSummaryOutput.model_validate(_without_nulls(data))
            valid = _shape(data) == _shape(_expected_summary(ws, rel, FileSummaryOutput))
        except Exception:
            valid = False
        text = _strings(data)
        missing = [c for c in OUTPUT_CONTROLS.get(rel, []) if c not in text]
        quality[rel] = {"schema_valid": valid, "placeholders": len(PLACEHOLDER.findall(text)),
                        "prose_chars": len(_prose(text)), "controls_missing": missing}
        checks.add(f"summary keeps the schema and structure of the model's answer: {rel}", valid)
        checks.add(f"summary keeps its nonsecret content: {rel}", not missing, ", ".join(missing))
        if rel == FAILED:
            checks.add("the failed file's error is saved, cleaned, as its summary",
                       data.get("summary", "").startswith("Error generating summary:") and PLACEHOLDER.search(text),
                       f"{len(PLACEHOLDER.findall(text))} placeholder(s)")
        else:
            checks.add(f"summary is more than placeholders: {rel}",
                       len(_prose(data.get("summary", ""))) >= 40 and PLACEHOLDER.search(text),
                       f"{len(_prose(data.get('summary', '')))} characters besides placeholders")

    rules = _load(fs / "business_rules" / "business_rules.json") or {}
    checks.add("file-level business rules saved", sum(len(v) for v in rules.values()) >= 5,
               f"{sum(len(v) for v in rules.values())} rule(s)")

    # --- directories ------------------------------------------------------
    billing = _load(agent / "directory_agent_output" / CODEBASE / "Billing.json") or {}
    checks.add("directory summary keeps its content",
               "invoices above 5000 CAD need manager approval" in billing.get("purpose", "")
               and PLACEHOLDER.search(billing.get("purpose", "")))

    # --- business rules -----------------------------------------------------
    validated = _load(agent / "BR_agent_output" / CODEBASE / "validated_rules.json") or []
    discarded = _load(agent / "BR_agent_output" / CODEBASE / "discarded_rules.json") or []
    evidence = _strings([r.get("explanation", {}) for r in validated])
    checks.add("three rules validated, the rest discarded with reasons",
               len(validated) == 3 and discarded and all(r.get("reason") for r in discarded),
               f"{len(validated)} validated, {len(discarded)} discarded")
    checks.add("validated evidence keeps its code and replaces the secret",
               "public bool NeedsApproval(decimal amount) => amount > ApprovalThreshold;" in evidence
               and 'private const string AdminPassword = "[REDACTED_PASSWORD_' in evidence)

    # --- generated tests ran ------------------------------------------------
    for kind, folder, expect in (("unit", "UT_agent_output", {"kept": 3, "passed": 2, "failed": 1}),
                                 ("integration", "IT_agent_output", {"kept": 1, "passed": 1, "failed": 0})):
        report = _load(agent / folder / CODEBASE / "test_report.json") or {}
        got = {k: report.get(k) for k in expect}
        checks.add(f"{kind} tests built and ran as scripted", got == expect and not report.get("project_error"),
                   f"{got}, expected {expect}")
    unit_report = _load(agent / "UT_agent_output" / CODEBASE / "test_report.json") or {}
    checks.add("the broken unit test was repaired by the AI, then compiled", len(unit_report.get("repaired") or []) == 1)
    sources = {p.name: p.read_text(encoding="utf-8") for p in (ws.tmp / "checkpoint-generated-tests").rglob("*.cs")
               if p.name.startswith(("UT_", "IT_"))}
    checks.add("generated test files written: 3 unit, 1 integration",
               sum(n.startswith("UT_") for n in sources) == 3 and sum(n.startswith("IT_") for n in sources) == 1,
               ", ".join(sorted(sources)))
    checks.add("the test that hard-coded a secret holds a placeholder instead",
               any('CanApprove("[REDACTED_PASSWORD_' in text for text in sources.values()))

    # --- Consistency Review ------------------------------------------------
    review = _load(agent / "consistency_output" / CODEBASE / "consistency_result.json") or {}
    payment = ws.fixture.text("Integrations/PaymentClient.cs").splitlines()
    line = next((n for n, text in enumerate(payment, 1) if "IsLargePayment(decimal amount)" in text), None)
    accepted = [f for f in review.get("findings", []) if any(
        r.get("kind") == "source" and r.get("path") == "Integrations/PaymentClient.cs" for r in _evidence_refs(f))]
    problems = [a.get("problem") or "" for a in review.get("assessments", [])]
    result["consistency_review"] = {
        "status": review.get("status"), "findings": len(review.get("findings", [])),
        "errors": len(review.get("errors", [])),
        # Known gap B: a finding quoting a line that held a secret, as the model saw it.
        "rejected_source_excerpts": sum("source excerpt does not appear" in p for p in problems),
    }
    checks.add("the review finding keeps its source location",
               any(line in _lines_of(f) for f in accepted), f"expected line {line}")
    reviews = _load(agent / "consistency_output" / CODEBASE / "reviews.json") or {}
    checks.add("the review note is saved, its secret replaced", PLACEHOLDER.search(_strings(reviews)) is not None)

    # --- exports -------------------------------------------------------------
    report_html = ws.app / "run_reports" / CODEBASE / "run_report.html"
    checks.add("the run report keeps its content",
               report_html.is_file() and "need manager approval" in report_html.read_text(encoding="utf-8"))
    markdown = sorted((ws.app / MARKDOWN_DIR).rglob("*.md"))
    invoice_md = next((p for p in markdown if p.name.startswith("InvoiceService")), None)
    # The export shows the file name, summary and dependencies only.
    checks.add("the Markdown export ran and keeps its content",
               session.get("markdown_exit_code") == 0 and len(markdown) == len(SUMMARY_FILES) and invoice_md is not None
               and "Approves invoices above the 5000 threshold." in invoice_md.read_text(encoding="utf-8")
               and PLACEHOLDER.search(invoice_md.read_text(encoding="utf-8")),
               f"{len(markdown)} file(s)")
    drawn = sorted((ws.capture / "plantuml").glob("*_input.puml"))
    checks.add("PlantUML drew the UML diagrams and the project diagram", len(drawn) >= 5, f"{len(drawn)} drawing(s)")

    # --- failure paths reached the logs --------------------------------------
    stdout = "\n".join(_lines(ws.capture / "stdout.jsonl"))
    stderr = (ws.capture / "stderr.txt").read_text(encoding="utf-8", errors="replace") \
        if (ws.capture / "stderr.txt").is_file() else ""
    checks.add("the 503 retry was announced on stdout", "busy (503)" in stdout)
    checks.add("the caught summarizer error was announced and logged",
               "Error during summarization" in stdout and "Error during summarization" in stderr)
    errors = (_load(ws.app / "error_log.json") or [])
    commands = Counter(e.get("command") for e in errors)
    checks.add("error_log.json holds the scripted failures",
               commands.get("generate_directory_summaries") and commands.get("renderer")
               and any(str(c).startswith("export") for c in commands),
               f"{len(errors)} entr{'y' if len(errors) == 1 else 'ies'}")
    checks.add("every error_log.json message was cleaned, not dropped",
               all(e.get("message") and PLACEHOLDER.search(e["message"]) for e in errors
                   if e.get("command") in ("generate_directory_summaries", "renderer")
                   or str(e.get("command")).startswith("export")))

    # --- counts in the run log -------------------------------------------------
    if log is not None:
        categories = {"PASSWORD", "API_KEY", "TOKEN", "AUTHORIZATION", "PRIVATE_KEY"}
        def total(calls):
            return dict(sorted(sum((Counter(c.get("redacted_secrets") or {}) for c in calls), Counter()).items()))
        stages_ok = all(s["redacted_secrets"] == total([c for c in log["calls"] if c["stage"] == s["stage"]])
                        for s in log["stages"])
        safe = all(set(c.get("redacted_secrets") or {}) <= categories
                   and all(isinstance(n, int) and n > 0 for n in (c.get("redacted_secrets") or {}).values())
                   for c in log["calls"])
        checks.add("run log totals add up: each stage, and the run", stages_ok
                   and log["totals"]["redacted_secrets"] == total(log["calls"]))
        checks.add("run log counts are category names and numbers only", safe)

    # --- the echoes really happened ----------------------------------------
    # What the model sent back (inbound, never a sink): every planted value
    # must be in it, or zero leaks would say nothing about output cleaning.
    inbound = LeakScanner(ws.needles())
    for number, entry in enumerate(_jsonl(ws.capture / "model_responses.jsonl"), 1):
        inbound.scan_data("model_answers", entry, f"answer {number}")
    echoed = sorted({f.case_id for f in inbound.report().findings})
    result["echoes"] = {"cases": echoed, "occurrences": inbound.report().leaks}
    checks.add("the model's answers repeated every planted value", set(CASE_IDS) <= set(echoed),
               f"{len(set(CASE_IDS) & set(echoed))} of 10: missing {', '.join(sorted(set(CASE_IDS) - set(echoed))) or 'none'}")

    # Cleaned, not emptied: each sink fed by those answers holds placeholders.
    counts = result["placeholders_by_sink"] = placeholder_counts(ws)
    for sink in ("ipc_stdout", "stderr", "error_log", "run_logs", "file_summaries", "uml_pdf", "plantuml_io",
                 "directory_summaries", "business_rules", "unit_tests", "integration_tests",
                 "generated_test_sources", "consistency_review", "run_report", "markdown_export"):
        checks.add(f"placeholders where secrets were: {sink}", counts.get(sink, 0) > 0, f"{counts.get(sink, 0)}")

    result["not_assessed"] = NOT_ASSESSED


def placeholder_counts(ws):
    """Placeholders per output sink, read as the scanner reads each file (PDF page text, JSON as text)."""
    import leak_scanner

    counts = Counter()
    for sink, _, required, folders, suffixes in output_sinks(ws):
        files = {p for p in required if p.is_file()}
        for folder in folders:
            if folder.is_dir():
                files |= {p for p in folder.rglob("*") if p.is_file()
                          and (suffixes is None or p.suffix.lower() in suffixes)}
        for path in files:
            data = path.read_bytes()
            if path.suffix.lower() == ".pdf":
                try:
                    text = "\n".join(leak_scanner._pdf_parts(data)[0])
                except ValueError:
                    continue
            elif path.suffix.lower() == ".png":
                continue
            else:
                text = data.decode("utf-8", "replace")
            counts[sink] += len(PLACEHOLDER.findall(text))
    counts["ipc_stdout"] = sum(len(PLACEHOLDER.findall(line)) for line in _lines(ws.capture / "stdout.jsonl"))
    return dict(sorted(counts.items()))


def _evidence_refs(finding):
    """Every ArtifactRef a saved finding holds: its artifacts, claims and evidence."""
    refs = list(finding.get("artifacts") or [])
    refs += [c.get("artifact") or {} for c in finding.get("claims") or []]
    refs += [e.get("artifact") or {} for e in finding.get("evidence") or []]
    return [r for r in refs if isinstance(r, dict)]


def _lines_of(finding):
    """Source lines a saved finding points at."""
    lines = set()
    for ref in _evidence_refs(finding):
        start, end = ref.get("line_start"), ref.get("line_end")
        if ref.get("kind") == "source" and isinstance(start, int) and isinstance(end, int):
            lines |= set(range(start, end + 1))
    return lines


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

def _known_gap(finding, about):
    """Which known gap a finding is, if any: gap A is S01 sent from the PlantUML notes."""
    if finding.case_id == "S01" and finding.sink in ("provider_requests", "model_callbacks"):
        where = next((loc for loc in about if finding.location.startswith(loc + " ")), None)
        if where and about[where] == "docs/architecture.md":
            return "A"
    return ""


def build_report(ws, session, scanner, checks, result):
    report = scanner.report()
    about = result.pop("about", {})
    findings = []
    for f in report.findings:
        findings.append({**f.__dict__, "known_gap": _known_gap(f, about)})

    sinks = {}
    for sink in report.sinks():
        data = report.for_sink(sink)
        sinks[sink] = {
            "inspected": data["inspected"], "text_chars": data["text_chars"],
            "leaks": data["leaks"], "cases_leaked": data["cases_leaked"],
            "problems": data["problems"],
        }

    cases = {}
    for case_id in list(CASE_IDS) + ["K00", "K01"]:
        planted = [o for o in ws.fixture.occurrences if o.case_id == case_id]
        leaked = [f for f in findings if f["case_id"] == case_id]
        cases[case_id] = {
            "category": ws.fixture.secret(case_id).category if case_id in CASE_IDS else "credential configuration",
            "planted_occurrences": len(planted),
            "files": sorted({o.path for o in planted}),
            "contexts": sorted({o.context for o in planted}),
            "leaks": len(leaked),
            "leaked_in": sorted({f["sink"] for f in leaked}),
            "known_gaps": sorted({f["known_gap"] for f in leaked if f["known_gap"]}),
        }

    failed = checks.failed()
    leaks = len(findings)
    unexpected = [f for f in findings if not f["known_gap"]]
    complete = not failed and report.complete

    verdict = "PASS" if not leaks and complete else ("FAIL: leaks" if leaks else "INCOMPLETE")
    return {
        "task": "T-079 (US-034) ten-secret verification",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "seed": ws.seed,
        "verdict": verdict,
        "leaks": leaks,
        "unexpected_leaks": len(unexpected),
        "known_gap_leaks": leaks - len(unexpected),
        "known_gaps": KNOWN_GAPS,
        "complete": complete,
        "failed_checks": [c["check"] for c in failed],
        "session": {
            "seconds": session["seconds"], "exit_code": session["exit_code"],
            "stdout_lines": session["stdout_lines"], "malformed_lines": len(session["malformed"]),
            # A scripted command name can quote a secret: only real names are shown.
            "steps": [{**{k: s[k] for k in ("label", "phase", "answered", "success",
                                            "expected_success", "warning", "seconds")},
                       "command": s["command"] if re.fullmatch(r"[a-z_]+", s["command"]) else "(withheld)"}
                      for s in session["steps"]],
        },
        "app_copy": {k: ws.manifest[k] for k in ("files", "identical")},
        "substitutions": SUBSTITUTIONS,
        "cases": cases,
        "sinks": sinks,
        "findings": findings,
        "problems": [p.__dict__ for p in report.problems],
        "checks": checks.items,
        **result,
    }


def render_summary(data):
    lines = [
        f"# T-079 ten-secret verification: {data['verdict']}",
        "",
        f"Generated {data['generated_at']}, seed {data['seed']}, session {data['session']['seconds']} s.",
        f"Leaks: {data['leaks']} ({data['known_gap_leaks']} from known gaps, {data['unexpected_leaks']} unexpected). "
        f"Failed checks: {len(data['failed_checks'])}. Complete: {data['complete']}.",
        "Workspace (raw fixture and captures): " + (
            "kept on request" if data["workspace"]["kept"]
            else "NOT fully removed" if data["workspace"]["left_behind"] else "removed"
        ) + ".",
        "",
        "## Cases",
        "",
        "| Case | Category | Planted | Leaks | Leaked in | Known gap |",
        "|---|---|---|---|---|---|",
    ]
    for case_id, c in data["cases"].items():
        lines.append(f"| {case_id} | {c['category']} | {c['planted_occurrences']} | {c['leaks']} | "
                     f"{', '.join(c['leaked_in']) or '-'} | {', '.join(c['known_gaps']) or '-'} |")
    lines += ["", "## Sinks", "", "| Sink | Inspected | Leaks | Cases | Problems |", "|---|---|---|---|---|"]
    for sink, s in data["sinks"].items():
        lines.append(f"| {sink} | {s['inspected']} | {s['leaks']} | {', '.join(s['cases_leaked']) or '-'} | {len(s['problems'])} |")
    lines += ["", "## Checks", ""]
    for c in data["checks"]:
        mark = "ok" if c["ok"] else ("FAIL" if c["required"] else "note")
        lines.append(f"- [{mark}] {c['check']}" + (f" ({c['detail']})" if c["detail"] else ""))
    lines += ["", "## Not assessed here", ""]
    for item in data.get("not_assessed", []):
        lines.append(f"- {item['sink']} ({item['where']}): {item['why']}.")
    lines += ["", "## Diagnostics", ""]
    echoes = data.get("echoes")
    if echoes:
        lines.append(f"- The model's answers repeated {echoes['occurrences']} planted value(s): "
                     f"{', '.join(echoes['cases'])}.")
    if data.get("placeholders_by_sink"):
        lines.append("- Placeholders by sink: " + ", ".join(f"{k} {v}" for k, v in data["placeholders_by_sink"].items()) + ".")
    review = data.get("consistency_review")
    if review:
        lines.append(f"- Consistency Review: status {review['status']}, {review['findings']} finding(s), "
                     f"{review['rejected_source_excerpts']} rejected because the source excerpt did not match "
                     "(known gap B).")
    chain = data["diagnostics"].get("chain_callbacks", {})
    if chain:
        lines.append(f"- LangGraph chain callbacks (excluded): {chain['events']} event(s), {chain['leaks']} "
                     f"planted value(s) seen ({', '.join(chain['cases']) or 'none'}). {chain['why_excluded']}")
    lines += ["", "## Substitutions", ""]
    for s in data["substitutions"]:
        lines.append(f"- **{s['what']}**: {s['used']}. Not verified: {s['unverified']}.")
    return "\n".join(lines) + "\n"


def write_evidence(out, data, needles):
    """Write report.json and summary.md, after checking neither holds a planted value."""
    guard = LeakScanner(needles)
    text = json.dumps(data, indent=2, ensure_ascii=False, default=str)
    if guard.contains_planted(text):
        keys = [k for k, v in data.items() if guard.contains_planted(json.dumps(v, ensure_ascii=False, default=str))]
        raise RuntimeError(f"The evidence holds a planted value (in {', '.join(keys)}), so it was not written.")
    summary = render_summary(data)
    if guard.contains_planted(summary):
        raise RuntimeError("The evidence holds a planted value, so it was not written.")
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(text, encoding="utf-8")
    (out / "summary.md").write_text(summary, encoding="utf-8")


# ---------------------------------------------------------------------------

def run(out=None, workdir=None, keep=False, seed=79):
    root = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="t079_"))
    if workdir:
        root.mkdir(parents=True, exist_ok=False)

    try:
        ws = Workspace(root, seed)
        session = run_session(ws)
        scanner, checks, result = evaluate(ws, session)
        data = build_report(ws, session, scanner, checks, result)
        needles = ws.needles()
    finally:
        if not keep:
            shutil.rmtree(root, ignore_errors=True)

    # The workspace holds the raw fixture and what was captured: say if any of it is left.
    data["workspace"] = {"kept": keep, "left_behind": root.exists() and not keep}
    if out is not None:
        write_evidence(Path(out), data, needles)
    return data, ws


def exit_code(data):
    if data["leaks"]:
        return 1
    return 0 if data["complete"] else 2


def main(argv=None):
    parser = argparse.ArgumentParser(description="T-079 ten-secret verification (offline).")
    parser.add_argument("--out", help="where report.json and summary.md go (default: runs/<time>/)")
    parser.add_argument("--workdir", help="workspace folder to create (default: a new temporary folder)")
    parser.add_argument("--keep", action="store_true", help="keep the workspace (it holds the raw fixture and captures)")
    parser.add_argument("--seed", type=int, default=79)
    args = parser.parse_args(argv)

    out = Path(args.out) if args.out else HERE / "runs" / datetime.now().strftime("%Y%m%d-%H%M%S")
    data, ws = run(out, args.workdir, args.keep, args.seed)

    print(f"{data['verdict']}: {data['leaks']} leak(s) ({data['known_gap_leaks']} known gap, "
          f"{data['unexpected_leaks']} unexpected), {len(data['failed_checks'])} failed check(s).")
    for name in data["failed_checks"]:
        print(f"  failed: {name}")
    print(f"Evidence: {out}")
    if args.keep:
        print(f"Workspace kept (raw fixture and captures, do not share): {ws.root}")
    elif data["workspace"]["left_behind"]:
        print(f"Warning: the workspace could not be removed completely (raw fixture data): {ws.root}")
    return exit_code(data)


if __name__ == "__main__":
    raise SystemExit(main())
