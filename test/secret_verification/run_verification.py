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
from offline_answers import FAIL_CONSISTENCY, FAIL_DIRECTORY, NORMAL  # noqa: E402
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


def session_steps(ws):
    codebase = str(ws.fixture.root)
    return [
        # (label, phase, command, args, expected success, timeout seconds)
        ("full pipeline", NORMAL, "full_pipeline", {"codebase": codebase}, True, 1800),
        ("run usage", NORMAL, "get_run_usage", {}, True, 60),
        ("review result", NORMAL, "get_consistency_result", {"codebase": codebase}, True, 60),
        ("mutation testing", NORMAL, "run_mutation_tests", {"codebase": codebase}, True, 300),
        # A run of its own, so it knows nothing the pipeline found: what the
        # detector misses in this file (known gap A) shows here.
        ("review check of the PlantUML notes", NORMAL, "check_consistency",
         {"codebase": codebase, "scope": "selected", "targets": ["docs/architecture.md"]}, True, 300),
        ("failing directory step", FAIL_DIRECTORY, "generate_directory_summaries", {"codebase": codebase}, False, 600),
        ("failing review check", FAIL_CONSISTENCY, "check_consistency",
         {"codebase": codebase, "scope": "selected", "targets": ["Integrations/PaymentClient.cs"]}, True, 300),
        ("error log", NORMAL, "get_errors", {}, True, 60),
    ]


def run_session(ws):
    backend = Backend(ws)
    steps = []
    started = time.monotonic()

    for number, (label, phase, command, args, expect, timeout) in enumerate(session_steps(ws), 1):
        (ws.capture / PHASE_FILE).write_text(phase, encoding="utf-8")
        t0 = time.monotonic()
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
    return {
        "steps": steps, "exit_code": code, "stdout_lines": len(backend.lines),
        "malformed": backend.malformed, "seconds": round(time.monotonic() - started, 1),
        "lines": backend.lines,
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

    return scanner, checks, result


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
            "steps": [{k: s[k] for k in ("label", "command", "phase", "answered", "success",
                                         "expected_success", "warning", "seconds")} for s in session["steps"]],
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
    lines += ["", "## Diagnostics", ""]
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
        raise RuntimeError("The evidence holds a planted value, so it was not written.")
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
