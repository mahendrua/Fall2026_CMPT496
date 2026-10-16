import pytest

from agent.llm import get_active_config


def test_agent_assignment_uses_its_saved_key_and_model(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "AGENT_LLM_KEY__file_summary=GOOGLE__summaries\n"
        "APIKEY__GOOGLE__summaries=summary-key\n"
        "APIMODEL__GOOGLE__summaries=gemini-summary-model\n"
        "ACTIVE_LLM_API_KEY=default-key\n"
        "ACTIVE_LLM_PROVIDER=openai\n"
        "ACTIVE_LLM_MODEL=gpt-default\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("agent.llm.ENV_PATH", env_path)

    config = get_active_config("file_summary")

    assert (config.provider, config.model, config.api_key) == (
        "google",
        "gemini-summary-model",
        "summary-key",
    )


def test_unassigned_agent_falls_back_to_active_key(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "ACTIVE_LLM_API_KEY=default-key\n"
        "ACTIVE_LLM_PROVIDER=openai\n"
        "ACTIVE_LLM_MODEL=gpt-default\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("agent.llm.ENV_PATH", env_path)

    config = get_active_config("directory")

    assert (config.provider, config.model, config.api_key) == (
        "openai",
        "gpt-default",
        "default-key",
    )


def test_agent_assignment_to_missing_key_reports_configuration_error(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "AGENT_LLM_KEY__unit_tests=GOOGLE__deleted\n"
        "ACTIVE_LLM_API_KEY=default-key\n"
        "ACTIVE_LLM_PROVIDER=openai\n"
        "ACTIVE_LLM_MODEL=gpt-default\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("agent.llm.ENV_PATH", env_path)

    with pytest.raises(ValueError, match="assigned to 'unit_tests' is missing"):
        get_active_config("unit_tests")
