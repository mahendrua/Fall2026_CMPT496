"""Offline API-key verification: fake models only, no live provider calls."""

from types import SimpleNamespace

from agent.llm import LLMConfig, build_chat_model, verify_llm_key


FAKE_KEY = "test-placeholder-key"
FAKE_MODEL = "gemini-3-flash-preview"


class FakeReply:
    def __init__(self, model_name="gemini-fake", tokens=2):
        self.usage_metadata = {"total_tokens": tokens}
        self.response_metadata = {"model_name": model_name}


class FakeLLM:
    def __init__(self, *, reply=None, error=None):
        self.reply = reply or FakeReply()
        self.error = error
        self.prompts = []

    def invoke(self, prompt):
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        return self.reply


class CapturingGoogleModel:
    kwargs = None

    def __init__(self, **kwargs):
        type(self).kwargs = kwargs

    def invoke(self, prompt):
        return FakeReply()


class AuthRejected(Exception):
    status_code = 401


class RateLimited(Exception):
    status_code = 429


class ServerError(Exception):
    status_code = 503


def test_valid_key_returns_accepted(monkeypatch):
    llm = FakeLLM()
    monkeypatch.setattr(
        "agent.llm.build_chat_model",
        lambda *args, **kwargs: llm,
    )

    result = verify_llm_key("google", FAKE_MODEL, FAKE_KEY, timeout=15)

    assert result["status"] == "accepted"
    assert llm.prompts == ["hi"]
    assert "verified" in result["message"].lower()
    assert "not saved" not in result["message"].lower()


def test_invalid_key_returns_rejected(monkeypatch):
    llm = FakeLLM(error=AuthRejected("API key not valid"))
    monkeypatch.setattr(
        "agent.llm.build_chat_model",
        lambda *args, **kwargs: llm,
    )

    result = verify_llm_key("google", FAKE_MODEL, FAKE_KEY, timeout=15)

    assert result["status"] == "rejected"
    assert "rejected" in result["message"].lower()
    assert result["model"] is None


def test_provider_timeout_returns_unverified(monkeypatch):
    llm = FakeLLM(error=TimeoutError("request timed out after 15 seconds"))
    monkeypatch.setattr(
        "agent.llm.build_chat_model",
        lambda *args, **kwargs: llm,
    )

    result = verify_llm_key("google", FAKE_MODEL, FAKE_KEY, timeout=15)

    assert result["status"] == "unverified"
    assert "not saved" in result["message"].lower()
    assert "rejected" not in result["message"].lower()


def test_provider_network_failure_returns_unverified(monkeypatch):
    llm = FakeLLM(error=ConnectionError("Name or service not known"))
    monkeypatch.setattr(
        "agent.llm.build_chat_model",
        lambda *args, **kwargs: llm,
    )

    result = verify_llm_key("google", FAKE_MODEL, FAKE_KEY, timeout=15)

    assert result["status"] == "unverified"
    assert "not saved" in result["message"].lower()


def test_rate_limit_is_unverified_not_invalid_credential(monkeypatch):
    llm = FakeLLM(error=RateLimited("429 Too Many Requests"))
    monkeypatch.setattr(
        "agent.llm.build_chat_model",
        lambda *args, **kwargs: llm,
    )

    result = verify_llm_key("google", FAKE_MODEL, FAKE_KEY, timeout=15)

    assert result["status"] == "unverified"


def test_verify_passes_timeout_and_disables_checkpoint_retries(monkeypatch):
    seen = {}

    def fake_build(config, max_output_tokens=None, retry=True, timeout=None):
        seen["retry"] = retry
        seen["timeout"] = timeout
        seen["max_output_tokens"] = max_output_tokens
        seen["provider"] = config.provider
        return FakeLLM()

    monkeypatch.setattr("agent.llm.build_chat_model", fake_build)

    verify_llm_key("google", FAKE_MODEL, FAKE_KEY, timeout=15)

    assert seen == {
        "retry": False,
        "timeout": 15,
        "max_output_tokens": 32,
        "provider": "google",
    }


def test_google_sdk_timeout_option_is_passed_to_constructor(monkeypatch):
    import langchain_google_genai
    from langchain_google_genai._common import _BaseGoogleGenerativeAI

    assert "timeout" in _BaseGoogleGenerativeAI.model_fields
    timeout_field = _BaseGoogleGenerativeAI.model_fields["timeout"]
    assert timeout_field.alias == "request_timeout"

    CapturingGoogleModel.kwargs = None
    monkeypatch.setattr(
        langchain_google_genai,
        "ChatGoogleGenerativeAI",
        CapturingGoogleModel,
    )

    build_chat_model(
        LLMConfig("google", FAKE_MODEL, FAKE_KEY),
        max_output_tokens=32,
        retry=False,
        timeout=15,
    )

    kwargs = CapturingGoogleModel.kwargs
    assert kwargs["timeout"] == 15
    assert kwargs["max_retries"] == 1
    assert kwargs["max_output_tokens"] == 32


def test_google_max_retries_one_means_no_extra_sdk_retries(monkeypatch):
    import langchain_google_genai
    from langchain_google_genai._common import _BaseGoogleGenerativeAI

    assert _BaseGoogleGenerativeAI.model_fields["max_retries"].default == 6

    CapturingGoogleModel.kwargs = None
    monkeypatch.setattr(
        langchain_google_genai,
        "ChatGoogleGenerativeAI",
        CapturingGoogleModel,
    )
    build_chat_model(
        LLMConfig("google", FAKE_MODEL, FAKE_KEY),
        retry=False,
        timeout=15,
    )
    assert CapturingGoogleModel.kwargs["max_retries"] == 1
    assert CapturingGoogleModel.kwargs["max_retries"] != 0


def test_commands_verify_uses_validation_timeout():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[2] / "backend" / "commands.py"
    ).read_text(encoding="utf-8")
    assert "VALIDATION_TIMEOUT_SECONDS = 15" in source
    assert "timeout=self.VALIDATION_TIMEOUT_SECONDS" in source
