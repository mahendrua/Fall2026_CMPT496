"""
@file no_validated_rules_test.py
@brief Offline tests for the unit and integration test steps when no business
rule passed validation: the step is skipped with a warning instead of failing,
so the full pipeline carries on. The test agents are replaced with a stand-in
that fails if started, so no AI, database or build is used.
"""

import sys
import json
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
import pytest

import backend.commands as commands_module

CODEBASE = "Shop"
STEPS = [
    ("generate_unit_tests", "unit"),
    ("generate_integration_tests", "integration"),
]


class AgentMustNotStart:
    def __init__(self, *args, **kwargs):
        raise AssertionError("A test agent started although there were no rules.")


@pytest.fixture
def commands(tmp_path, monkeypatch):
    """Commands with a temporary app dir (also the working directory)."""
    app = tmp_path / "app"
    app.mkdir()
    monkeypatch.chdir(app)
    monkeypatch.setattr(commands_module, "UTAgent", AgentMustNotStart)
    monkeypatch.setattr(commands_module, "ITAgent", AgentMustNotStart)

    commands = commands_module.Commands()
    commands.app_dir = app
    return commands


def write_rules(app, rules):
    path = app / "agent" / "BR_agent_output" / CODEBASE / "validated_rules.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rules), encoding="utf-8")
    return path


@pytest.mark.parametrize("step, kind", STEPS)
def test_no_validated_rules_skips_the_step_with_a_warning(commands, tmp_path, step, kind):
    write_rules(commands.app_dir, [])

    result = getattr(commands, step)(str(tmp_path / "targets" / CODEBASE), [])

    assert result["success"] is True
    assert f"no {kind} tests were generated" in result["result"]["warning"]


@pytest.mark.parametrize("step, kind", STEPS)
def test_missing_rules_file_still_fails(commands, tmp_path, step, kind):
    result = getattr(commands, step)(str(tmp_path / "targets" / CODEBASE), [])

    assert result["success"] is False
    assert "Validated rules not found" in result["error"]
