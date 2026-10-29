"""!
@file secret_verification_pipeline_test.py
@brief The T-079 offline pipeline harness (US-034).
@details The fast tests check the harness's own parts in this process: the
offline model is built by the app's build_chat_model() with its real
retries and redaction around it, what it records is what it was sent, the
network guard refuses and records, and the scripted answers cover every
request the pipeline makes. The full run (the real backend, every stage,
dotnet and PlantUML; a few minutes) runs only with T079_FULL=1:

    $env:T079_FULL = "1"; .venv\\Scripts\\python.exe -m pytest test/other_tests/secret_verification_pipeline_test.py

No real model or network is used. Assertions report IDs and counts, never values.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
VERIFICATION = PROJECT_ROOT / "test" / "secret_verification"
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(VERIFICATION))

import agent.llm as llm_module  # noqa: E402
import offline_model  # noqa: E402
import run_verification  # noqa: E402
from agent.structured_output.file_summary_output import FileSummaryOutput  # noqa: E402
from leak_scanner import LeakScanner, missing_controls  # noqa: E402
from offline_answers import Answers  # noqa: E402
from offline_model import PROVIDER_CLASS, PROVIDER_MODULE, OfflineProvider, read_requests  # noqa: E402
from secret_fixture import build_fixture, make_secrets  # noqa: E402

from langchain_core.callbacks import BaseCallbackHandler  # noqa: E402


@pytest.fixture
def offline(tmp_path, monkeypatch):
    """The offline model installed as the Google provider, for this test only."""
    fixture = build_fixture(tmp_path / "inputs")
    capture = tmp_path / "capture"
    monkeypatch.setitem(sys.modules, PROVIDER_MODULE, None)   # restored afterwards
    offline_model.install(capture, fixture.root, fixture.seed)
    model = llm_module.build_chat_model(llm_module.LLMConfig("google", "offline-gemini", "unused-key-123"))
    return fixture, capture, model


def summary_request(fixture, relative):
    path = fixture.path(relative)
    return [
        ("system", "You are a senior software architect."),
        ("user", f"Analyze the file.\n\nFile path:\n{path}\n\nCode:\n{fixture.text(relative)}\n"),
    ]


class Starts(BaseCallbackHandler):
    def __init__(self):
        super().__init__()
        self.metadata = []

    def on_chat_model_start(self, serialized, messages, *, metadata=None, **kwargs):
        self.metadata.append(metadata or {})


def test_the_app_builds_the_offline_model_with_its_own_protection(offline):
    _, _, model = offline
    mro = type(model).__mro__

    assert isinstance(model, OfflineProvider)
    assert getattr(sys.modules[PROVIDER_MODULE], PROVIDER_CLASS) is OfflineProvider
    # redacting() outermost, then the app's retries, then the provider.
    assert mro[0] is llm_module.redacting(llm_module._retrying(OfflineProvider, "Google"))
    assert mro[1] is llm_module._retrying(OfflineProvider, "Google")
    # The key is held as the real SDKs hold it, so it never shows in a repr.
    assert "unused-key-123" not in repr(model)


def test_what_the_provider_records_is_the_cleaned_request(offline):
    fixture, capture, model = offline
    relative = "Billing/InvoiceService.cs"
    starts = Starts()

    answer = model.with_structured_output(FileSummaryOutput).invoke(
        summary_request(fixture, relative), config={"callbacks": [starts]},
    )

    assert isinstance(answer, FileSummaryOutput)
    sent = read_requests(capture)
    assert [r["tool"] for r in sent] == ["FileSummaryOutput"]

    scanner = LeakScanner(fixture.needles())
    scanner.scan_data("provider_requests", sent[0]["messages"])
    assert scanner.report().leaks == 0

    text = "\n".join(m["content"] for m in sent[0]["messages"])
    assert missing_controls(text, {c.control_id: c.text for c in fixture.controls_in(relative)}) == []
    assert starts.metadata[0]["redacted_secrets"] == fixture.expected_counts(relative)


def test_a_scripted_503_is_retried_by_the_app(offline, monkeypatch):
    fixture, capture, model = offline
    monkeypatch.setattr(llm_module.time, "sleep", lambda seconds: None)
    starts = Starts()

    model.with_structured_output(FileSummaryOutput).invoke(
        summary_request(fixture, "tools/report_export.py"), config={"callbacks": [starts]},
    )

    sent = read_requests(capture)
    assert [r["outcome"].get("status") for r in sent] == [503, None]
    assert sent[0]["messages"] == sent[1]["messages"]
    # One call as far as callbacks (and so the run log) are concerned.
    assert len(starts.metadata) == 1


def test_answers_cover_every_request_kind_the_harness_requires(tmp_path):
    answers = Answers(tmp_path, 79)
    for kind in run_verification.REQUEST_KINDS:
        assert callable(getattr(answers, f"_{kind}", None)), kind


def test_network_guard_refuses_and_records(tmp_path):
    code = (
        "import socket, sys; sys.path.insert(0, sys.argv[1]); from pathlib import Path\n"
        "import offline_backend as b\n"
        "b.guard_network(Path(sys.argv[2]))\n"
        "blocked = 0\n"
        "for attempt in (lambda: socket.socket().connect(('198.51.100.7', 9)),\n"
        "                lambda: socket.create_connection(('198.51.100.8', 9), timeout=1),\n"
        "                lambda: socket.getaddrinfo('example.com', 443)):\n"
        "    try: attempt()\n"
        "    except OSError: blocked += 1\n"
        "a, b2 = socket.socketpair(); a.sendall(b'x'); assert b2.recv(1) == b'x'\n"
        "print(blocked)\n"
    )
    result = subprocess.run([sys.executable, "-c", code, str(VERIFICATION), str(tmp_path)],
                            capture_output=True, text=True, check=True)

    # Loopback (the socketpair) still works; everything else is refused and noted.
    assert result.stdout.strip() == "3"
    recorded = [json.loads(line) for line in (tmp_path / "network.jsonl").read_text().splitlines()]
    assert [(e["kind"], e["host"]) for e in recorded] == [
        ("connect", "198.51.100.7"), ("lookup", "198.51.100.8"), ("lookup", "example.com"),
    ]


def test_evidence_is_refused_when_it_holds_a_value(tmp_path):
    fixture = build_fixture(tmp_path / "inputs")
    needles = [run_verification.Needle(*n) for n in fixture.needles()]
    with pytest.raises(RuntimeError):
        run_verification.write_evidence(tmp_path / "out", {"note": fixture.secret("S04").value,
                                                            "checks": [], "cases": {}, "sinks": {},
                                                            "substitutions": [], "diagnostics": {}}, needles)
    assert not (tmp_path / "out").exists()


# ---------------------------------------------------------------------------
# The full run
# ---------------------------------------------------------------------------

FULL = pytest.mark.skipif(not os.environ.get("T079_FULL"), reason="the full offline run takes minutes; set T079_FULL=1")


@pytest.fixture(scope="module")
def full_run(tmp_path_factory):
    out = tmp_path_factory.mktemp("t079_evidence")
    data, _ = run_verification.run(out=out)
    return data, out


@FULL
def test_full_run_is_complete(full_run):
    data, _ = full_run
    failed = [c["check"] for c in data["checks"] if c["required"] and not c["ok"]]
    assert failed == []
    assert data["problems"] == []
    assert data["workspace"] == {"kept": False, "left_behind": False}


@FULL
def test_full_run_leaks_only_through_known_gaps(full_run):
    data, _ = full_run
    unexpected = sorted({(f["case_id"], f["sink"]) for f in data["findings"] if not f["known_gap"]})
    assert unexpected == []

    # Known gap A (fixed in T-079) leaked S01 from docs/architecture.md, in
    # the review check of it that runs on its own; nothing leaks now.
    assert data["findings"] == []
    assert data["verdict"] == "PASS"


@FULL
def test_full_run_evidence_holds_no_value(full_run):
    data, out = full_run
    text = (out / "report.json").read_text(encoding="utf-8") + (out / "summary.md").read_text(encoding="utf-8")
    assert "S10" in text

    values = [s.value for s in make_secrets(data["seed"])] + [
        run_verification._random_key("AIza", 35, data["seed"], "provider"),
        run_verification._random_key("lsv2_pt_", 32, data["seed"], "tracing"),
    ]
    assert sum(value in text for value in values) == 0
