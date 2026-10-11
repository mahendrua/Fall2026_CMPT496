"""
@file consistency_check_command_test.py
@brief Offline tests for running a Consistency Review check from the app (check_consistency).
@details Codebases and outputs come from the tracked evaluation fixtures
(test/consistency_eval) in temporary folders. make_llm is replaced with a
factory that hands out stand-in models and counts how many were created, so
no model is ever called. Generation agents are replaced with ones that fail
if used.
"""

import sys
import hashlib
import json
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "test" / "consistency_eval"))
import pytest

import agent.consistency_checker as checker_module
import backend.commands as commands_module
from agent.consistency_store import load_check_result
from agent.llm import DailyLimitError
from backend import consistency_review
from backend.consistency_review import ReviewRequestError
from backend.dispatcher import CommandDispatcher
from eval_cases import CASES_BY_NAME
from eval_fakes import OracleLLM
from eval_fixtures import build_case


class Models:
    """make_llm stand-in: hands out one model per check and counts them."""

    def __init__(self, make):
        self.make, self.created = make, 0

    def __call__(self, *args, **kwargs):
        self.created += 1
        return self.make()


@pytest.fixture(autouse=True)
def no_generation(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("A generation agent was started by a consistency check.")
    for name in ("FileSummaryAgent", "DirectoryAgent", "BRAgent", "UTAgent", "UTVAgent", "ITAgent"):
        monkeypatch.setattr(commands_module, name, refuse)
    for name in ("build_database", "build_summary_database"):
        monkeypatch.setattr(commands_module, name, refuse)


@pytest.fixture
def env(tmp_path, monkeypatch):
    class Env:
        def build(self, name):
            case = CASES_BY_NAME[name]
            root, app = build_case(case, tmp_path)
            self.case, self.root, self.app = case, root, app
            self.commands = commands_module.Commands()
            self.commands.app_dir = app
            self.models = Models(lambda: OracleLLM(case))
            monkeypatch.setattr(checker_module, "make_llm", self.models)
            return self

        def run(self, scope="full", targets=None):
            return self.commands.check_consistency(str(self.root), scope, targets)

        def outputs(self):
            out = self.app / "agent"
            return {p.relative_to(out).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in out.rglob("*") if p.is_file() and "consistency_output" not in p.parts}
    return Env()


def selected(case):
    return [{"path": t.path, "symbol": t.symbol} if t.symbol else t.path for t in case.targets]


# ---------------------------------------------------------
# Running a check
# ---------------------------------------------------------

def test_a_whole_codebase_check_runs_saves_and_reports_its_identity(env):
    env.build("ignored_parameter")
    before = env.outputs()
    out = env.run("full")

    assert out["success"] is True and out["command"] == "check_consistency"
    summary = out["result"]
    assert summary["scope"] == "full" and summary["status"] == "completed" and summary["findings"] == 2
    assert summary["message"] == "Consistency check completed: 2 mismatches found."
    assert "warning" not in summary
    saved = load_check_result("ignored_parameter", env.app)
    assert saved.check_id == summary["check_id"] and saved.run_id == summary["run_id"]
    assert summary["run_id"]                                    # its own run, from the run log
    assert env.models.created == 1
    assert env.outputs() == before                              # summaries, rules and tests untouched


def test_a_selected_method_check_is_saved_apart_from_the_full_check(env):
    env.build("validation_and_state")
    full = env.run("full")["result"]
    picked = env.run("selected", [{"path": "src/Account.cs", "symbol": "Account.Withdraw"}])["result"]

    assert picked["scope"] == "selected" and picked["selection"] == ["src/Account.cs#Account.Withdraw"]
    assert picked["check_id"] != full["check_id"]
    assert load_check_result("validation_and_state", env.app).check_id == full["check_id"]
    assert load_check_result("validation_and_state", env.app, scope="selected").check_id == picked["check_id"]


def test_each_check_gets_its_own_run_identity(env):
    env.build("ignored_parameter")
    first = env.run("selected", selected(env.case))["result"]["run_id"]
    second = env.run("selected", selected(env.case))["result"]["run_id"]
    assert first and second and first != second


def test_selected_paths_are_normalized_and_deduplicated(env):
    env.build("ignored_parameter")
    root = str(env.root)
    picked = env.run("selected", ["src\\Refunds.cs", f"{root}\\src\\Refunds.cs", "src/Refunds.cs"])["result"]
    assert picked["selection"] == ["src/Refunds.cs"]


# ---------------------------------------------------------
# Missing artifacts and uncheckable scope
# ---------------------------------------------------------

def test_a_codebase_without_outputs_fails_honestly_without_a_model(env, tmp_path):
    env.build("ignored_parameter")
    bare = tmp_path / "codebase" / "empty_project"
    (bare / "src").mkdir(parents=True)
    (bare / "src" / "A.cs").write_text("class A {}", encoding="utf-8")
    out = env.commands.check_consistency(str(bare), "full")

    summary = out["result"]
    assert summary["status"] == "failed" and "Nothing to check" in summary["errors"][-1]
    assert summary["warning"].startswith("Consistency check failed")
    assert env.models.created == 0


def test_a_selected_file_with_no_artifacts_is_reported_without_a_model(env):
    env.build("ignored_parameter")
    (env.root / "src" / "Extra.cs").write_text("class Extra {}", encoding="utf-8")
    summary = env.run("selected", ["src/Extra.cs"])["result"]
    assert summary["status"] == "partial" and summary["findings"] == 0
    assert summary["missing_artifacts"] >= 1
    assert env.models.created == 0


def test_missing_rule_and_test_outputs_make_the_check_incomplete(env):
    env.build("missing_outputs")
    summary = env.run("full")["result"]
    assert summary["status"] == "partial" and summary["missing_artifacts"] == 2
    assert "warning" in summary


def test_a_selected_file_that_does_not_exist_is_reported_missing(env):
    env.build("ignored_parameter")
    summary = env.run("selected", ["src/Nope.cs"])["result"]
    assert summary["status"] == "partial" and summary["missing_artifacts"] >= 1
    assert env.models.created == 0


def test_an_unknown_method_is_reported_not_guessed(env):
    env.build("validation_and_state")
    summary = env.run("selected", [{"path": "src/Account.cs", "symbol": "Account.Deposit"}])["result"]
    assert summary["status"] == "partial" and summary["findings"] == 0
    assert env.models.created == 0


# ---------------------------------------------------------
# Provider errors
# ---------------------------------------------------------

def test_a_daily_limit_fails_the_check_visibly_after_one_attempt(env, monkeypatch):
    env.build("cross_file_dependency")                       # two files to check
    attempts = []

    class Limited:
        def with_structured_output(self, schema):
            return self

        def invoke(self, messages):
            attempts.append(1)
            raise DailyLimitError("This Google API key has hit its daily limit or is out of credit.")

    monkeypatch.setattr(checker_module, "make_llm", Models(Limited))
    summary = env.run("full")["result"]
    assert summary["status"] == "failed"
    assert len(attempts) == 1                                   # the second file is not tried
    assert all("daily limit" in e for e in summary["errors"]) and len(summary["errors"]) == 2
    assert "daily limit" in summary["warning"]
    assert load_check_result("cross_file_dependency", env.app).status == "failed"


def test_no_api_key_fails_the_check_with_the_reason(env, monkeypatch):
    env.build("ignored_parameter")

    def no_key(*args, **kwargs):
        raise ValueError("No API key is selected. Open Manage API Keys to add or select one.")
    monkeypatch.setattr(checker_module, "make_llm", no_key)
    summary = env.run("full")["result"]
    assert summary["status"] == "failed" and "No API key is selected" in summary["errors"][0]


# ---------------------------------------------------------
# Validation before anything runs
# ---------------------------------------------------------

@pytest.mark.parametrize("scope, targets, message", [
    ("everything", None, "Unknown check scope"),
    ("full", ["src/Refunds.cs"], "takes no selected files"),
    ("selected", None, "Select at least one file"),
    ("selected", [], "Select at least one file"),
    ("selected", "src/Refunds.cs", "must be a list"),
    ("selected", ["../outside.cs"], "is not inside the codebase"),
    ("selected", ["C:/elsewhere/Other.cs"], "is not inside the codebase"),
    ("selected", ["   "], "has no path"),
    ("selected", [{"path": "src/Refunds.cs", "symbol": "Refunds.CanRefund(); rm"}], "is not a method name"),
    ("selected", [{"path": "src/Refunds.cs", "extra": 1}], "Not a valid selection"),
    ("selected", [42], "Not a valid selection"),
    ("selected", ["src/Refunds.cs"] * 101, "more than the 100"),
])
def test_invalid_requests_are_refused_before_a_run_or_model(env, scope, targets, message):
    env.build("ignored_parameter")
    out = env.run(scope, targets)
    assert out["success"] is False and message in out["error"]
    assert env.models.created == 0
    assert not (env.app / "agent" / "consistency_output").exists()


def test_a_missing_codebase_folder_is_refused(env, tmp_path):
    env.build("ignored_parameter")
    out = env.commands.check_consistency(str(tmp_path / "nowhere" / "Project"), "full")
    assert out["success"] is False and "does not exist" in out["error"]


def test_blank_symbols_mean_the_whole_file(env):
    root, targets = consistency_review.prepare_check(
        str(env.build("ignored_parameter").root), "selected", [{"path": "src/Refunds.cs", "symbol": "  "}])
    assert targets == ["src/Refunds.cs"]


# ---------------------------------------------------------
# Run labels and transport
# ---------------------------------------------------------

def test_a_check_newer_than_the_latest_logged_run_is_not_called_earlier(env):
    env.build("ignored_parameter")
    (env.app / "last_run_log.json").write_text(json.dumps({
        "run_id": "pipeline-1", "codebase": "ignored_parameter", "command": "full_pipeline",
        "started_at": "2000-01-01T00:00:00", "finished_at": "2000-01-01T00:10:00",
    }), encoding="utf-8")
    env.run("full")
    loaded = consistency_review.load_for_review(env.root, "full", env.app)
    assert loaded["run_relation"] == "later_run"

    (env.app / "last_run_log.json").write_text(json.dumps({
        "run_id": "pipeline-2", "codebase": "ignored_parameter", "command": "full_pipeline",
        "started_at": "2999-01-01T00:00:00",
    }), encoding="utf-8")
    assert consistency_review.load_for_review(env.root, "full", env.app)["run_relation"] == "earlier_run"


def test_the_command_runs_through_the_dispatcher(env):
    env.build("ignored_parameter")
    dispatcher = CommandDispatcher()
    dispatcher.commands = env.commands
    dispatcher.routes["check_consistency"] = env.commands.check_consistency
    dispatcher.error_log = []
    out = dispatcher.dispatch("check_consistency", codebase=str(env.root), scope="selected",
                              targets=["src/Refunds.cs"], request_id="r-1")
    assert out["success"] is True and out["request_id"] == "r-1"
    assert out["result"]["scope"] == "selected"
