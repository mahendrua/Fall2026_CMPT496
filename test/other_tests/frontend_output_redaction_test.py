"""!
@file frontend_output_redaction_test.py
@brief Tests that what the backend sends Electron, and the errors it keeps,
are cleaned of secrets (T-078, US-034).
@details Covers progress and token messages, command responses through
main.serve(), command errors and error_log.json. The frontend's framing must
survive: one JSON object per line, the same fields, the request ID, and
progress on stdout, never stderr. Every secret is fake; the dispatcher is a
stand-in, and no model or network is involved. absent() fails with a count,
never the value.
"""

import io
import json
from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage

import backend.progress_logging as progress_logging
import main
import utils.secret_redaction as secret_redaction
from agent.request_redaction import redact_messages
from backend.commands import Commands
from backend.dispatcher import ERROR_LOG_NAME, CommandDispatcher
from backend.progress_logging import (
    PROGRESS_WITHHELD,
    RESPONSE_WITHHELD,
    configure_progress_logging,
    pipeline_progress,
    progress,
    request_scope,
    send_response,
)
from backend.token_usage import emit_live_total, emit_stage_total
from utils.output_redaction import secret_scope

FAKE = "Fake-Pass-111!"
# Assembled at run time so the repository never holds a real-format token.
GITHUB = "ghp" + "_" + "A1b2C3d4E5f6G7h8I9j0" * 2
P1 = "[REDACTED_PASSWORD_1]"


def absent(text, *secrets):
    """Fail, without quoting them, if any planted secret is in text."""
    __tracebackhide__ = True  # pytest would print text with the frame's arguments
    leaked = sum(secret in text for secret in secrets or (FAKE, GITHUB))
    if leaked:
        raise AssertionError(f"{leaked} planted secret(s) found in the output")


def kept(text, secret):
    """Fail, without quoting it, unless secret is still in text (a documented limitation)."""
    __tracebackhide__ = True  # pytest would print text with the frame's arguments
    if secret not in text:
        raise AssertionError("the value was expected to be left in place")


def learn_from_request(text):
    """What T-077 does with a request: clean it, and tell the open scope what it found."""
    redact_messages([HumanMessage(content=text)])


def sent(capsys):
    """Every line the backend wrote to stdout, parsed: each must be one JSON object."""
    captured = capsys.readouterr()
    absent(captured.out)
    absent(captured.err)
    assert captured.err == "", "nothing may go to stderr instead"
    return [json.loads(line) for line in captured.out.splitlines()]


@pytest.fixture
def broken_detector(monkeypatch):
    """The detector fails, quoting what it was reading, as a bug might."""
    def fail(text):
        raise RuntimeError(f"could not read {text!r}")

    monkeypatch.setattr(secret_redaction, "_find", fail)


# ---------------------------------------------------------------------------
# Progress and token messages
# ---------------------------------------------------------------------------

def test_progress_messages_are_cleaned_and_keep_their_fields(capsys):
    with request_scope("r-9"), secret_scope():
        progress(f'Condensation error for Auth: password = "{FAKE}"', 50, step_complete=True)
        learn_from_request(f'string AdminPassword = "{FAKE}";')
        pipeline_progress(f"Retrying {FAKE}...", 65, step_percent=False)
        progress("Generating file summaries...", 35)
        emit_live_total(3, 1200, 80)
        emit_stage_total({"stage": "generate_file_summaries", "calls": 3,
                          "input_tokens": 1200, "output_tokens": 80})

    assert sent(capsys) == [
        {"type": "progress", "stage": f'Condensation error for Auth: password = "{P1}"',
         "progress": 50, "step_complete": True, "request_id": "r-9"},
        {"type": "pipeline_progress", "stage": f"Retrying {P1}...", "progress": 65,
         "step_percent": False, "request_id": "r-9"},
        {"type": "progress", "stage": "Generating file summaries...", "progress": 35, "request_id": "r-9"},
        {"type": "token_usage", "calls": 3, "input_tokens": 1200, "output_tokens": 80,
         "total_tokens": 1280, "request_id": "r-9"},
        {"type": "token_usage_stage", "stage": "generate_file_summaries", "calls": 3,
         "input_tokens": 1200, "output_tokens": 80, "request_id": "r-9"},
    ]


def test_progress_logged_through_the_frontend_handler_is_cleaned(capsys):
    configure_progress_logging()
    # WARNING, so the record passes whatever level the test run left on root.
    progress_logging.logger.warning("Validation error for rule 4: %s", f"token {GITHUB}")

    assert sent(capsys) == [{"type": "progress", "stage": "Validation error for rule 4: token [REDACTED_TOKEN_1]"}]


def test_a_progress_message_that_cannot_be_checked_still_moves_the_bars(capsys, broken_detector):
    with request_scope("r-9"):
        progress(f'password = "{FAKE}"', 50, step_complete=True)

    assert sent(capsys) == [{"type": "progress", "stage": PROGRESS_WITHHELD, "progress": 50,
                             "step_complete": True, "request_id": "r-9"}]


# ---------------------------------------------------------------------------
# Command responses
# ---------------------------------------------------------------------------

def test_a_response_is_cleaned_and_serialized_as_before(capsys):
    send_response({
        "success": True,
        "request_id": "r-2",
        "result": {
            "summary": f'The service logs in with password "{FAKE}".',
            "files": {"Db.cs"},
            "report": Path("run_reports/Shop/run_report.html"),
            "source_file_paths": ["Shop/Db.cs"],
            "calls": 4,
        },
    })

    assert sent(capsys) == [{
        "success": True,
        "request_id": "r-2",
        "result": {
            "summary": f'The service logs in with password "{P1}".',
            "files": ["Db.cs"],
            "report": str(Path("run_reports/Shop/run_report.html")),
            "source_file_paths": ["Shop/Db.cs"],
            "calls": 4,
        },
    }]


def test_a_response_that_cannot_be_checked_becomes_a_failure_with_its_id(capsys, broken_detector):
    send_response({"success": True, "request_id": "r-2", "result": f'password = "{FAKE}"'})

    assert sent(capsys) == [{"success": False, "error": RESPONSE_WITHHELD, "request_id": "r-2"}]


def test_a_response_that_cannot_be_serialized_still_answers(capsys):
    loop = {"success": True}
    loop["self"] = loop
    send_response(loop)

    assert sent(capsys) == [{"success": False, "error": "Circular reference detected"}]


class StubDispatcher:
    """Stands in for CommandDispatcher: each command does what a real one might."""

    def dispatch(self, command, request_id=None, **kwargs):
        if command == "summarize":
            progress(f'Summarizing Db.cs: password = "{FAKE}"', 35)
            learn_from_request(f'string AdminPassword = "{FAKE}";')
            return {"success": True, "command": command,
                    "result": {"summary": f"Compares the input with {FAKE}.", "path": "Shop/Db.cs"}}
        if command == "echo":
            return {"success": True, "command": command, "result": {"summary": f"Compares the input with {FAKE}."}}
        raise RuntimeError(f"[{command}] provider said: bad credentials {GITHUB}")


def test_the_request_loop_answers_each_request_in_clean_json_lines(monkeypatch, capsys):
    monkeypatch.setattr(main, "dispatcher", StubDispatcher())
    requests = "".join(json.dumps(r) + "\n" for r in (
        {"type": "command", "command": "summarize", "request_id": "r-1", "args": {}},
        {"type": "command", "command": "fail", "request_id": "r-2"},
        {"type": "command", "command": "echo", "request_id": "r-3"},
    ))

    main.serve(io.StringIO(requests))
    captured = capsys.readouterr()
    lines = [json.loads(line) for line in captured.out.splitlines()]

    absent(json.dumps(lines[:3]))
    assert captured.err == ""
    assert len(lines) == 4
    assert lines[:3] == [
        {"type": "progress", "stage": f'Summarizing Db.cs: password = "{P1}"', "progress": 35, "request_id": "r-1"},
        {"success": True, "command": "summarize", "request_id": "r-1",
         "result": {"summary": f"Compares the input with {P1}.", "path": "Shop/Db.cs"}},
        {"success": False, "error": "[fail] provider said: bad credentials [REDACTED_TOKEN_1]", "request_id": "r-2"},
    ]

    # Each request is its own scope: what r-1 found is gone by r-3, so a
    # bare repeat there is not recognised (a request never sees another's).
    assert lines[3]["request_id"] == "r-3"
    kept(lines[3]["result"]["summary"], FAKE)


# ---------------------------------------------------------------------------
# Errors kept by the backend
# ---------------------------------------------------------------------------

def test_a_command_error_is_cleaned_inside_its_run(tmp_path):
    commands = Commands()
    commands.app_dir = tmp_path

    def task():
        learn_from_request(f'string AdminPassword = "{FAKE}";')
        raise RuntimeError(f"Login rejected for {FAKE}")

    result = commands._run_command("generate_file_summaries", task)

    absent(json.dumps(result))
    assert result["success"] is False
    assert result["error"] == f"Login rejected for {P1}"


def test_error_log_keeps_only_cleaned_messages(tmp_path):
    dispatcher = CommandDispatcher()
    dispatcher.commands.app_dir = tmp_path
    dispatcher.error_log = []

    def boom():
        raise ValueError(f'could not parse password = "{FAKE}"')

    dispatcher.routes["boom"] = boom
    response = dispatcher.dispatch("boom")
    dispatcher.record_error("generate_unit_tests", f"401: bad credentials {GITHUB}", 401)

    saved = (tmp_path / ERROR_LOG_NAME).read_text(encoding="utf-8")
    absent(saved)
    entries = json.loads(saved)
    assert [(e["command"], e["code"], e["message"]) for e in entries] == [
        ("boom", None, f'could not parse password = "{P1}"'),
        ("generate_unit_tests", 401, "401: bad credentials [REDACTED_TOKEN_1]"),
    ]
    assert all(e["time"] for e in entries)
    # The response itself is cleaned on its way out, by send_response().
    assert response["success"] is False and response["command"] == "boom"


def test_error_log_cleans_the_name_of_an_unknown_command(tmp_path):
    # T-079: the name is whatever the request sent, and it is saved with the error.
    dispatcher = CommandDispatcher()
    dispatcher.commands.app_dir = tmp_path
    dispatcher.error_log = []

    response = dispatcher.dispatch(f"export --password={FAKE}")

    saved = (tmp_path / ERROR_LOG_NAME).read_text(encoding="utf-8")
    absent(saved)
    [entry] = json.loads(saved)
    assert entry["command"] == f"export --password={P1}"
    assert entry["message"] == f"Unknown command: export --password={P1}"
    # Known commands are recorded by name, unchanged.
    dispatcher.record_error("generate_unit_tests", "failed", 500)
    assert json.loads((tmp_path / ERROR_LOG_NAME).read_text(encoding="utf-8"))[1]["command"] == "generate_unit_tests"
    assert response["success"] is False
