import json

from backend.dispatcher import CommandDispatcher
from backend.progress_logging import pipeline_progress, progress, request_scope
from backend.token_usage import emit_live_total, emit_stage_total


def dispatcher_with(handler):
    dispatcher = CommandDispatcher.__new__(CommandDispatcher)
    dispatcher.routes = {"test_command": handler}
    dispatcher.commands = type("Commands", (), {"current_codebase_name": "unknown"})()
    dispatcher._record_error = lambda *args: None
    return dispatcher


def test_dispatcher_adds_request_id_without_passing_it_to_existing_handler():
    calls = []
    dispatcher = dispatcher_with(lambda **kwargs: calls.append(kwargs) or {"success": True})

    result = dispatcher.dispatch("test_command", request_id="request-a", value=3)

    assert result == {"success": True, "request_id": "request-a"}
    assert calls == [{"value": 3}]


def test_dispatcher_remains_compatible_without_request_id():
    calls = []
    dispatcher = dispatcher_with(lambda **kwargs: calls.append(kwargs) or {"success": True})

    result = dispatcher.dispatch("test_command", value=3)

    assert result == {"success": True}
    assert calls == [{"value": 3}]


def test_command_scoped_progress_and_token_events_include_request_id(capsys):
    with request_scope("request-a"):
        progress("Working", 25)
        pipeline_progress("Stage", 50)
        emit_live_total(1, 10, 5)
        emit_stage_total({
            "stage": "generate_unit_tests",
            "calls": 1,
            "input_tokens": 10,
            "output_tokens": 5,
        })

    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [event["type"] for event in events] == [
        "progress",
        "pipeline_progress",
        "token_usage",
        "token_usage_stage",
    ]
    assert all(event["request_id"] == "request-a" for event in events)


def test_progress_outside_command_has_no_request_id(capsys):
    progress("Standalone")
    event = json.loads(capsys.readouterr().out)
    assert event == {"type": "progress", "stage": "Standalone"}