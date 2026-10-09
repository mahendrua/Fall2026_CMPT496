"""!
@file token_usage_test.py
@brief Tests for the thinking-token count in backend/token_usage.py (T-136).
@details A fake chat model stands in for Gemini, so these need no API key.
Its replies carry usage shaped the way langchain-google-genai reports it:
thinking is already inside output_tokens and listed again under
output_token_details["reasoning"].
"""

import json

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from backend.token_usage import RUN_LOG_NAME, RunLog, record_usage, track_command


def reply(input_tokens, answer_tokens, thinking_tokens=0):
    usage = {
        "input_tokens": input_tokens,
        "output_tokens": answer_tokens + thinking_tokens,
        "total_tokens": input_tokens + answer_tokens + thinking_tokens,
    }

    # Gemini leaves the details out when the model did not think.
    if thinking_tokens:
        usage["output_token_details"] = {"reasoning": thinking_tokens}

    return AIMessage(content="ok", usage_metadata=usage)


def fake_model(*replies):
    return GenericFakeChatModel(messages=iter(replies))


def test_thinking_is_part_of_output_not_added_to_total():
    model = fake_model(reply(100, 20, thinking_tokens=30), reply(200, 10))

    with record_usage("generate_file_summaries") as usage:
        model.invoke("summarize")
        model.invoke("summarize")

    summary = usage.summary()
    assert summary["thinking_tokens"] == 30
    assert summary["output_tokens"] == 60
    assert summary["total_tokens"] == 360


def test_provider_that_does_not_split_thinking_reports_zero():
    # Anthropic counts thinking in output_tokens but gives no breakdown.
    message = AIMessage(
        content="ok",
        usage_metadata={"input_tokens": 50, "output_tokens": 40, "total_tokens": 90},
    )

    with record_usage("validate_business_rules") as usage:
        fake_model(message).invoke("check")

    summary = usage.summary()
    assert summary["thinking_tokens"] == 0
    assert summary["output_tokens"] == 40


def test_run_log_saves_thinking_per_call_stage_and_run(tmp_path):
    model = fake_model(reply(100, 20, thinking_tokens=30), reply(200, 10, thinking_tokens=5))

    with track_command(tmp_path, "ConsoleTables", "full_pipeline"):
        with track_command(tmp_path, "ConsoleTables", "generate_file_summaries"):
            with record_usage("generate_file_summaries"):
                model.invoke("summarize")
                model.invoke("summarize")

    log = json.loads((tmp_path / RUN_LOG_NAME).read_text(encoding="utf-8"))

    assert [call["thinking_tokens"] for call in log["calls"]] == [30, 5]
    assert log["stages"][0]["thinking_tokens"] == 35
    assert log["totals"]["thinking_tokens"] == 35
    assert log["totals"]["total_tokens"] == 365


def test_failed_call_has_no_thinking_and_totals_still_add_up(tmp_path):
    run = RunLog(tmp_path, "ConsoleTables", "full_pipeline")
    run.call_started("a")
    run.call_ended("a", "generate_unit_tests", error=RuntimeError("429 Too Many Requests"))

    log = run.to_dict()
    assert log["calls"][0]["thinking_tokens"] is None
    assert log["totals"]["thinking_tokens"] == 0
