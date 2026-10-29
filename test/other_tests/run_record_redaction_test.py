"""!
@file run_record_redaction_test.py
@brief Run records, redaction counts and tracing (T-078, US-034).
@details last_run_log.json and run_logs/ must keep no secret and still
record every call, with the count of secrets each call's request had
replaced. Inside a backend run LangSmith tracing must be off, without
changing the environment. A secret given away in one stage is cleaned where
a later stage repeats it without context, and concurrent runs never share
what they found. Every secret is fake. Tracing is switched on through the
environment with an unreachable endpoint, and the tracer's upload methods
are replaced, so nothing leaves the machine. absent() fails with a count,
never the value.
"""

import json
import logging
import sys
import threading
from pathlib import Path
from typing import TypedDict

import pytest
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.tracers.context import tracing_v2_enabled
from langchain_core.tracers.langchain import LangChainTracer
from langgraph.graph import END, START, StateGraph
from langsmith import Client, run_trees
from langsmith import utils as langsmith_utils
from langsmith.schemas import LangSmithInfo
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fake_chat_provider import FakeProvider  # noqa: E402

import agent.llm as llm_module  # noqa: E402
import backend.run_protection as run_protection  # noqa: E402
import utils.secret_redaction as secret_redaction  # noqa: E402
from agent.llm import REDACTION_METADATA_KEY, _retrying, redacting  # noqa: E402
from backend.commands import Commands  # noqa: E402
from backend.log_redaction import install_log_redaction  # noqa: E402
from backend.progress_logging import progress  # noqa: E402
from backend.run_protection import TRACING_NOTICE, protected_run  # noqa: E402
from backend.token_usage import RUN_HISTORY_DIR, RUN_LOG_NAME  # noqa: E402
from utils.output_redaction import WITHHELD, save_json  # noqa: E402

FAKE = "Fake-Pass-111!"
OTHER = "Fake-Pass-222!"
KEY = "Fake-Key-123456789"
PLANTED = (FAKE, OTHER, KEY)
P1 = "[REDACTED_PASSWORD_1]"

REQUEST = [("user", f'string password = "{FAKE}";\nstring apiKey = "{KEY}";\nLogin("ann", "{FAKE}");')]
REQUEST_COUNTS = {"API_KEY": 1, "PASSWORD": 2}

MARKER = "<<this text cannot be checked>>"


def absent(text, *secrets):
    """Fail, without quoting them, if any planted secret is in text."""
    __tracebackhide__ = True  # pytest would print text with the frame's arguments
    leaked = sum(secret in text for secret in secrets or PLANTED)
    if leaked:
        raise AssertionError(f"{leaked} planted secret(s) found in the output")


def kept(text, secret):
    """Fail, without quoting it, unless secret is still in text."""
    __tracebackhide__ = True  # pytest would print text with the frame's arguments
    if secret not in text:
        raise AssertionError("the value was expected to be left in place")


class Answer(BaseModel):
    summary: str


class Rejecting(FakeProvider):
    """A provider that turns the request down with an error quoting a secret."""

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self._record(messages, kwargs)
        raise RuntimeError(f"400 Bad Request: could not parse 'password = \"{FAKE}\"'")


@pytest.fixture
def commands(tmp_path):
    commands = Commands()
    commands.app_dir = tmp_path
    commands.current_codebase_name = "Shop"
    return commands


@pytest.fixture
def no_waiting(monkeypatch):
    monkeypatch.setattr(llm_module.time, "sleep", lambda seconds: None)


def run_logs(app):
    """The run log and its copy in run_logs/, as text, which must be identical."""
    latest = (app / RUN_LOG_NAME).read_text(encoding="utf-8")
    [history] = list((app / RUN_HISTORY_DIR).iterdir())
    assert history.read_text(encoding="utf-8") == latest
    return latest


# ---------------------------------------------------------------------------
# Run records and counts
# ---------------------------------------------------------------------------

def test_the_run_log_counts_each_call_once_and_keeps_no_secret(commands, tmp_path, no_waiting, capsys):
    def stage():
        model = redacting(_retrying(FakeProvider, "Fake"))(failures=1, answers={"Answer": {"summary": "fine"}})
        model.invoke(REQUEST)                                  # rate limited once, then retried
        model.with_structured_output(Answer).invoke(REQUEST)   # cleaned twice on its way in
        model.invoke([("user", "Summarize the cart.")])        # nothing to replace
        with pytest.raises(RuntimeError):
            redacting(Rejecting)().invoke(REQUEST)
        raise RuntimeError(f"Validation stopped after {FAKE}")   # repeats it with no context

    result = commands._run_command(
        "full_pipeline", lambda: commands._run_command("generate_file_summaries", stage, individualStep=False),
    )

    absent(json.dumps(result))
    absent(capsys.readouterr().out)
    text = run_logs(tmp_path)
    absent(text)
    log = json.loads(text)

    assert [c["redacted_secrets"] for c in log["calls"]] == [REQUEST_COUNTS, REQUEST_COUNTS, {}, REQUEST_COUNTS]
    assert [c["status"] for c in log["calls"]] == ["success", "success", "success", "failed"]
    assert log["calls"][3]["error"] == f"400 Bad Request: could not parse 'password = \"{P1}\"'"
    assert log["totals"]["redacted_secrets"] == {"API_KEY": 3, "PASSWORD": 6}
    assert log["totals"]["calls"] == 4 and log["totals"]["waits"] == 1

    [step] = log["stages"]
    assert (step["stage"], step["status"]) == ("generate_file_summaries", "failed")
    assert step["error"] == f"Validation stopped after {P1}"
    assert step["redacted_secrets"] == log["totals"]["redacted_secrets"]

    # What the Insights "AI Usage" view reads.
    assert commands.get_run_usage()["run_usage"] == log


def test_counts_travel_only_as_counts(commands, tmp_path):
    seen = []

    class Metadata(BaseCallbackHandler):
        def on_chat_model_start(self, serialized, messages, **kwargs):
            seen.append(kwargs.get("metadata"))

    model = redacting(FakeProvider)()
    commands._run_command("generate_file_summaries", lambda: model.invoke(REQUEST, config={"callbacks": [Metadata()]}))

    assert [m[REDACTION_METADATA_KEY] for m in seen] == [REQUEST_COUNTS]
    absent(repr(seen))
    assert json.loads(run_logs(tmp_path))["calls"][0]["redacted_secrets"] == REQUEST_COUNTS


def test_a_run_log_that_cannot_be_checked_still_records_the_run(commands, tmp_path, monkeypatch):
    real = secret_redaction._find

    def find(text):
        if MARKER in text:
            raise RuntimeError(f"could not read {text!r}")
        return real(text)

    monkeypatch.setattr(secret_redaction, "_find", find)

    def stage():
        redacting(FakeProvider)().invoke(REQUEST)
        raise RuntimeError(f'Login failed: password = "{FAKE}" {MARKER}')

    commands._run_command("generate_file_summaries", stage)

    text = run_logs(tmp_path)
    absent(text)
    assert MARKER not in text
    log = json.loads(text)
    assert (log["status"], log["error"]) == ("failed", WITHHELD)
    assert log["calls"][0]["redacted_secrets"] == REQUEST_COUNTS


# ---------------------------------------------------------------------------
# Tracing
# ---------------------------------------------------------------------------

@pytest.fixture
def tracing_on(monkeypatch):
    """
    LangSmith tracing switched on through the environment, as a developer
    might, pointed at an endpoint nothing listens on. The tracer's upload
    methods only record, so nothing is sent anywhere.
    """
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "not-a-real-langsmith-key")
    monkeypatch.setenv("LANGSMITH_ENDPOINT", "http://127.0.0.1:9")
    for name in ("LANGCHAIN_TRACING", "LANGCHAIN_HANDLER", "LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING_V2"):
        monkeypatch.delenv(name, raising=False)
    langsmith_utils.get_env_var.cache_clear()
    # The client tracers use, told the server's details up front so it never
    # asks for them, and sending nothing in the background.
    monkeypatch.setattr(run_trees, "_CLIENT", Client(
        api_url="http://127.0.0.1:9", api_key="not-a-real-langsmith-key",
        info=LangSmithInfo(), auto_batch_tracing=False,
    ))
    monkeypatch.setattr(run_protection, "_noticed", False)

    uploaded = []
    monkeypatch.setattr(LangChainTracer, "_persist_run_single", lambda self, run: uploaded.append(run))
    monkeypatch.setattr(LangChainTracer, "_update_run_single", staticmethod(lambda run: uploaded.append(run)))

    yield uploaded
    langsmith_utils.get_env_var.cache_clear()


def summarizing_graph(model):
    class State(TypedDict):
        code: str
        answer: str

    def summarize(state):
        return {"answer": model.invoke([("user", state["code"])]).content}

    builder = StateGraph(State)
    builder.add_node("summarize", summarize)
    builder.add_edge(START, "summarize")
    builder.add_edge("summarize", END)
    return builder.compile()


STATE = {"code": f'password = "{FAKE}"', "answer": ""}


def traced_secret(uploaded):
    """Whether any traced run carried the raw secret (the graph state, before the model)."""
    return any(FAKE in repr(run.inputs) or FAKE in repr(run.outputs) for run in uploaded)


def test_without_protection_tracing_would_upload_the_raw_graph_state(tracing_on):
    # The exposure T-077 left open, shown offline: the model call is clean,
    # but the node's input is traced as it is.
    model = redacting(FakeProvider)()
    summarizing_graph(model).invoke(STATE)

    assert tracing_on, "tracing did not switch on; the protected tests below would prove nothing"
    assert traced_secret(tracing_on)
    absent(model.sent_text())


def test_a_protected_run_traces_nothing_and_says_why_once(tracing_on, monkeypatch):
    notices = []
    handler = logging.Handler()
    handler.emit = lambda record: notices.append(record.getMessage())
    monkeypatch.setattr(run_protection.logger, "handlers", [handler])
    graph = summarizing_graph(redacting(FakeProvider)())

    with protected_run():
        graph.invoke(STATE)
        with protected_run():
            graph.invoke(STATE)
    with protected_run():
        graph.invoke(STATE)

    assert tracing_on == []
    assert notices == [TRACING_NOTICE]
    # Only the runs were untraced: the environment, and code outside them, are as they were.
    assert langsmith_utils.get_env_var("TRACING") == "true"
    graph.invoke(STATE)
    assert tracing_on


def test_backend_commands_are_never_traced(tracing_on, commands, tmp_path):
    graph = summarizing_graph(redacting(FakeProvider)())

    result = commands._run_command("generate_directory_summaries", lambda: graph.invoke(STATE)["answer"])

    assert result["success"] and result["result"] == "ok"
    assert tracing_on == []
    # The app's own callbacks still see the call.
    assert json.loads(run_logs(tmp_path))["totals"]["calls"] == 1


def test_a_tracer_set_around_a_protected_run_is_set_aside(tracing_on):
    graph = summarizing_graph(redacting(FakeProvider)())

    with tracing_v2_enabled():
        with protected_run():
            graph.invoke(STATE)
        assert tracing_on == []
        graph.invoke(STATE)

    assert traced_secret(tracing_on)


# ---------------------------------------------------------------------------
# Secrets repeated without context, across a run and between runs
# ---------------------------------------------------------------------------

@pytest.fixture
def clean_logs():
    previous = logging.getLogRecordFactory()
    install_log_redaction()
    records = []
    handler = logging.Handler()
    handler.emit = lambda record: records.append(handler.format(record))
    logger = logging.getLogger("t078.stage")
    logger.handlers, logger.propagate = [handler], False
    yield logger, records
    logger.handlers = []
    logging.setLogRecordFactory(previous)


def test_a_later_stage_repeating_a_secret_cleans_it_everywhere(commands, tmp_path, capsys, clean_logs):
    logger, records = clean_logs
    summary = tmp_path / "out" / "summary.json"

    def learn():
        redacting(FakeProvider)().invoke([("user", f'private const string AdminPassword = "{FAKE}";')])

    def repeat():
        progress(f"Retrying the login with {FAKE}...")
        logger.warning("Login rejected for %s", FAKE)
        save_json(summary, {"summary": f"Compares the input with {FAKE}."})
        raise RuntimeError(f"Stopped: {FAKE} was rejected")

    def pipeline():
        commands._run_command("generate_file_summaries", learn, individualStep=False)
        return commands._run_command("validate_business_rules", repeat, individualStep=False)

    result = commands._run_command("full_pipeline", pipeline)

    out = capsys.readouterr().out
    for text in (out, "\n".join(records), summary.read_text(encoding="utf-8"), json.dumps(result), run_logs(tmp_path)):
        absent(text)
    messages = [json.loads(line) for line in out.splitlines()]
    assert [m["stage"] for m in messages if m["type"] == "progress"] == [f"Retrying the login with {P1}..."]
    assert records == [f"Login rejected for {P1}"]
    assert json.loads(summary.read_text(encoding="utf-8")) == {"summary": f"Compares the input with {P1}."}
    assert result["result"]["error"] == f"Stopped: {P1} was rejected"


def test_concurrent_runs_clean_only_what_they_found(tmp_path):
    both_learned = threading.Barrier(2, timeout=20)
    results, errors = {}, []

    def run(name, mine):
        try:
            commands = Commands()
            commands.app_dir = tmp_path / name
            commands.current_codebase_name = name

            def stage():
                redacting(FakeProvider)().invoke([("user", f'private const string AdminPassword = "{mine}";')])
                both_learned.wait()
                save_json(tmp_path / name / "summary.json", {"summary": f"Compares with {FAKE} and {OTHER}."})
                raise RuntimeError(f"Login rejected for {FAKE} and {OTHER}")

            results[name] = commands._run_command("generate_file_summaries", stage)
        except Exception as exc:  # reported below, without the values
            errors.append(type(exc).__name__)

    threads = [threading.Thread(target=run, args=args) for args in (("a", FAKE), ("b", OTHER))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    for name, mine, theirs in (("a", FAKE, OTHER), ("b", OTHER, FAKE)):
        for text in (
            (tmp_path / name / "summary.json").read_text(encoding="utf-8"),
            results[name]["error"],
            run_logs(tmp_path / name),
        ):
            absent(text, mine)
            # Never learned in this run, so a bare repeat of it is not recognised.
            kept(text, theirs)


# ---------------------------------------------------------------------------
# The count shown after a run (US-034)
# ---------------------------------------------------------------------------

import backend.commands as commands_module  # noqa: E402
from backend.token_usage import current_run_redactions  # noqa: E402

GENERATION_STAGES = (
    "build_database", "generate_file_summaries", "build_summary_database", "generate_directory_summaries",
    "validate_business_rules", "generate_unit_tests", "generate_integration_tests", "generate_all_uml",
)


def fake_pipeline(monkeypatch, requests_per_stage):
    """
    Commands.full_pipeline as it is, with each generation stage replaced by
    a task that sends requests_per_stage[stage] through a protected fake
    provider. The review stage and the run report make no calls.
    """
    def stage(name):
        def run(self, *args, **kwargs):
            def task():
                model = redacting(_retrying(FakeProvider, "Fake"))(failures=1 if name == "generate_file_summaries" else 0)
                for request in requests_per_stage.get(name, []):
                    model.invoke(request)
                return {"message": f"{name} done"}
            return self._run_command(name, task, individualStep=False)
        return run

    for name in GENERATION_STAGES:
        monkeypatch.setattr(Commands, name, stage(name))
    monkeypatch.setattr(Commands, "_consistency_review_stage", lambda self, root, before: self._run_command(
        "consistency_review", lambda: {"message": "Reviewed."}, individualStep=False))
    monkeypatch.setattr(commands_module, "write_run_report", lambda codebase, app_dir: {"path": None, "error": "not built in this test"})


def pipeline_log(app, run_id):
    [path] = (app / RUN_HISTORY_DIR).glob(f"*_{run_id}.json")
    return json.loads(path.read_text(encoding="utf-8"))


def test_the_pipeline_response_carries_its_own_runs_count(commands, tmp_path, monkeypatch, no_waiting):
    fake_pipeline(monkeypatch, {
        "generate_file_summaries": [REQUEST, REQUEST],          # the first is rate limited, then retried
        "validate_business_rules": [REQUEST, [("user", "Nothing to replace here.")]],
    })
    root = tmp_path / "Shop"
    root.mkdir()

    result = commands.full_pipeline(str(root))

    assert result["success"], result.get("error")
    shown = result["result"]["redactions"]
    log = pipeline_log(tmp_path, shown["run_id"])
    # Exactly what this run's own record saved: each call once, retries included.
    assert shown == {
        "run_id": log["run_id"], "calls": 4, "total": 9,
        "by_category": {"API_KEY": 3, "PASSWORD": 6},
    }
    assert shown["by_category"] == log["totals"]["redacted_secrets"]
    assert shown["calls"] == log["totals"]["calls"]
    absent(json.dumps(result))


def test_a_run_with_nothing_to_replace_shows_zero(commands, tmp_path, monkeypatch, no_waiting):
    fake_pipeline(monkeypatch, {"generate_file_summaries": [[("user", "Summarize the cart.")]]})
    root = tmp_path / "Shop"
    root.mkdir()

    shown = commands.full_pipeline(str(root))["result"]["redactions"]

    assert (shown["total"], shown["by_category"], shown["calls"]) == (0, {}, 1)


def test_the_count_is_not_read_from_a_stale_run_log(commands, tmp_path, monkeypatch, no_waiting):
    # An older run's record, already on disk, says something else entirely.
    (tmp_path / RUN_LOG_NAME).write_text(json.dumps({
        "run_id": "20250101-000000-old000", "command": "full_pipeline",
        "totals": {"calls": 40, "redacted_secrets": {"PASSWORD": 99}},
    }), encoding="utf-8")
    fake_pipeline(monkeypatch, {"generate_file_summaries": [REQUEST]})
    root = tmp_path / "Shop"
    root.mkdir()

    shown = commands.full_pipeline(str(root))["result"]["redactions"]

    assert shown["run_id"] != "20250101-000000-old000"
    assert (shown["total"], shown["by_category"]) == (3, REQUEST_COUNTS)


def test_concurrent_runs_each_get_their_own_count(tmp_path, monkeypatch, no_waiting):
    fake_pipeline(monkeypatch, {"generate_file_summaries": [REQUEST], "validate_business_rules": [REQUEST]})
    started = threading.Barrier(2, timeout=30)
    shown, errors = {}, []

    def run(name, requests):
        try:
            commands = Commands()
            commands.app_dir = tmp_path / name
            commands.current_codebase_name = name
            root = tmp_path / name / name
            root.mkdir(parents=True)
            original = commands.generate_file_summaries

            def first_stage(*args, **kwargs):
                started.wait()          # both runs are open at the same time
                return original(*args, **kwargs)

            commands.generate_file_summaries = first_stage
            if requests == 0:
                commands.validate_business_rules = lambda *a, **k: commands._run_command(
                    "validate_business_rules", lambda: {"message": "none"}, individualStep=False)
            shown[name] = commands.full_pipeline(str(root))["result"]["redactions"]
        except Exception as exc:  # reported below, without the values
            errors.append(type(exc).__name__)

    threads = [threading.Thread(target=run, args=args) for args in (("A", 2), ("B", 0))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert shown["A"]["total"] == 6 and shown["B"]["total"] == 3
    assert shown["A"]["run_id"] != shown["B"]["run_id"]
    for name in ("A", "B"):
        assert shown[name]["by_category"] == pipeline_log(tmp_path / name, shown[name]["run_id"])["totals"]["redacted_secrets"]


def test_outside_a_run_there_is_no_count():
    assert current_run_redactions() is None
