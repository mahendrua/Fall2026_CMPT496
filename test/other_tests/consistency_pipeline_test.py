"""
@file consistency_pipeline_test.py
@brief Offline tests of the full pipeline's Consistency Review stage.
@details The pipeline runs for real (full_pipeline, _run_command, the run log
and usage tracking); only the generation steps are replaced. Each fake step
runs as its own stage and "generates" by copying a tracked evaluation fixture's
outputs (test/consistency_eval) into an isolated app folder. The model is the
offline oracle, which also reports a usage event through the real LangChain
callback path, so per-stage usage is measured by the existing tracking code.
No real model or agent is ever called.
"""

import sys
import json
import shutil
import uuid
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "test" / "consistency_eval"))
import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

import agent.consistency_checker as checker_module
import backend.commands as commands_module
import backend.token_usage as token_usage
from agent.consistency_store import load_check_result, save_check_result, set_review
from agent.llm import DailyLimitError
from backend import consistency_review
from backend.commands import Commands
from eval_cases import CASES_BY_NAME
from eval_fakes import OracleLLM
from eval_fixtures import build_case

GENERATION_STAGES = [
    "build_database", "generate_file_summaries", "build_summary_database", "generate_directory_summaries",
    "validate_business_rules", "generate_unit_tests", "generate_integration_tests", "generate_all_uml",
]
OUTPUT_OF_STAGE = {
    "generate_file_summaries": "file_summary_agent_output",
    "validate_business_rules": "BR_agent_output",
    "generate_unit_tests": "UT_agent_output",
}


def report_usage(input_tokens=100, output_tokens=20):
    """One model call's usage, through the same callback the real models trigger."""
    handler = token_usage._usage_var.get()
    if handler is None:
        return
    run_id = uuid.uuid4()
    handler.on_chat_model_start({}, [], run_id=run_id)
    message = AIMessage(content="", usage_metadata={
        "input_tokens": input_tokens, "output_tokens": output_tokens, "total_tokens": input_tokens + output_tokens,
    }, response_metadata={"model_name": "offline-oracle"})
    handler.on_llm_end(LLMResult(generations=[[ChatGeneration(message=message)]]), run_id=run_id)


class Models:
    """make_llm stand-in: the oracle, counting models created and calls made."""

    def __init__(self, case, fail=None):
        self.case, self.fail = case, fail
        self.created, self.calls = 0, 0

    def __call__(self, *args, **kwargs):
        self.created += 1
        return self

    def with_structured_output(self, schema):
        return self

    def invoke(self, messages):
        self.calls += 1
        if self.fail:
            raise self.fail
        report_usage()
        return OracleLLM(self.case)._answer(messages)


class Pipeline:
    """A full pipeline whose generation steps copy fixture outputs into an isolated app folder."""

    def __init__(self, tmp_path, monkeypatch, case_name="ignored_parameter"):
        self.case = CASES_BY_NAME[case_name]
        self.root, self.generated = build_case(self.case, tmp_path / "generated")
        self.app = tmp_path / "app"
        self.app.mkdir()
        self.monkeypatch = monkeypatch
        self.stages_run = []
        self.write = set(OUTPUT_OF_STAGE)          # stages that write their outputs this run
        self.fail_at = None
        self.models = Models(self.case)
        monkeypatch.setattr(checker_module, "make_llm", self.models)

        def refuse(*args, **kwargs):
            raise AssertionError("The pipeline must not run the independent check command.")
        monkeypatch.setattr(Commands, "check_consistency", refuse)

        for name in GENERATION_STAGES:
            monkeypatch.setattr(Commands, name, self._step(name))
        self.commands = Commands()
        self.commands.app_dir = self.app

    def _step(self, name):
        pipeline = self

        def step(commands, *args, **kwargs):
            def task():
                pipeline.stages_run.append(name)
                if pipeline.fail_at == name:
                    raise RuntimeError(f"{name} failed")
                folder = OUTPUT_OF_STAGE.get(name)
                if folder and name in pipeline.write:
                    source = pipeline.generated / "agent" / folder / pipeline.case.name
                    target = commands.app_dir / "agent" / folder / pipeline.case.name
                    for file in source.rglob("*"):          # written now, as a real step would
                        if file.is_file():
                            copy = target / file.relative_to(source)
                            copy.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copyfile(file, copy)
                            copy.touch()
                return {"message": f"{name} done"}
            return commands._run_command(name, task, individualStep=False)
        return step

    def run(self):
        # The dispatcher names the codebase for the run log before every command.
        self.commands.current_codebase_name = self.root.name
        return self.commands.full_pipeline(str(self.root))

    def run_log(self):
        return json.loads((self.app / "last_run_log.json").read_text(encoding="utf-8"))

    def full_result(self):
        return load_check_result(self.case.name, self.app)


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    return Pipeline(tmp_path, monkeypatch)


# ---------------------------------------------------------
# The stage runs once, last, on this run's outputs
# ---------------------------------------------------------

def test_the_review_runs_once_after_every_generation_stage(pipeline):
    out = pipeline.run()
    assert out["success"] is True
    assert pipeline.stages_run == GENERATION_STAGES
    log = pipeline.run_log()
    assert [s["stage"] for s in log["stages"]] == GENERATION_STAGES + ["consistency_review"]
    assert log["command"] == "full_pipeline" and log["status"] == "success"
    assert pipeline.models.created == 1 and pipeline.models.calls == 1


def test_the_saved_review_belongs_to_the_pipeline_run(pipeline):
    out = pipeline.run()
    review = out["result"]["consistency_review"]
    assert review["success"] is True and review["command"] == "consistency_review"
    summary = review["result"]
    saved = pipeline.full_result()
    assert summary["check_id"] == saved.check_id and summary["scope"] == "full"
    assert saved.run_id == pipeline.run_log()["run_id"]
    assert saved.status == "completed" and len(saved.findings) == 2
    assert summary["current_run_outputs"] > 0 and summary["leftover_outputs"] == 0
    assert any("Compared only the outputs this pipeline run wrote" in n for n in saved.notes)
    assert not any("does not record which run wrote them" in n for n in saved.notes)
    loaded = consistency_review.load_for_review(pipeline.root, "full", pipeline.app)
    assert loaded["run_relation"] == "same_run"


def test_review_usage_is_its_own_stage_and_counted_once(pipeline):
    pipeline.run()
    log = pipeline.run_log()
    stage = next(s for s in log["stages"] if s["stage"] == "consistency_review")
    assert stage["calls"] == 1 and stage["input_tokens"] == 100 and stage["output_tokens"] == 20
    assert all(s["calls"] == 0 for s in log["stages"] if s["stage"] != "consistency_review")
    assert log["totals"]["calls"] == 1 and log["totals"]["input_tokens"] == 100
    usage_log = json.loads((pipeline.app / token_usage.USAGE_LOG_NAME).read_text(encoding="utf-8"))
    assert usage_log[pipeline.case.name]["consistency_review"]["calls"] == 1


def test_artifacts_are_not_regenerated_by_the_review(pipeline):
    pipeline.run()
    assert pipeline.stages_run.count("generate_file_summaries") == 1
    assert pipeline.stages_run.count("validate_business_rules") == 1


# ---------------------------------------------------------
# Leftovers from earlier runs are never compared as new
# ---------------------------------------------------------

def test_outputs_left_over_from_an_earlier_run_are_not_compared(pipeline):
    pipeline.run()                                  # an earlier run leaves outputs behind
    pipeline.write = {"generate_file_summaries"}    # this run writes summaries only
    out = pipeline.run()

    summary = out["result"]["consistency_review"]["result"]
    saved = pipeline.full_result()
    assert summary["leftover_outputs"] > 0
    assert saved.status == "partial"
    missing = " ".join(ref.detail for ref in saved.missing_artifacts)
    assert "No validated business rules exist" in missing and "No generated unit tests exist" in missing
    assert not any(ref.kind in ("business_rule", "unit_test") for ref in saved.artifacts_checked)
    assert any("left over from an earlier run" in n and "validated_rules.json" in n for n in saved.notes)
    assert "warning" in out["result"]


def test_a_stale_summary_of_a_removed_file_is_not_compared(pipeline):
    old = pipeline.app / "agent" / "file_summary_agent_output" / pipeline.case.name / "Old-cs__1.json"
    old.parent.mkdir(parents=True)
    data = json.loads(next((pipeline.generated / "agent" / "file_summary_agent_output" / pipeline.case.name).glob("*.json"))
                      .read_text(encoding="utf-8"))
    data["path"] = str(pipeline.root / "src" / "Old.cs")
    old.write_text(json.dumps(data), encoding="utf-8")
    pipeline.run()
    saved = pipeline.full_result()
    assert all(ref.path != "src/Old.cs" for ref in saved.artifacts_checked + saved.missing_artifacts)
    assert any("Old-cs__1.json" in n for n in saved.notes)


def test_no_outputs_this_run_means_no_model_call(pipeline):
    pipeline.write = set()
    out = pipeline.run()
    summary = out["result"]["consistency_review"]["result"]
    assert out["success"] is True
    assert summary["status"] == "failed" and "Nothing to check" in summary["errors"][-1]
    assert pipeline.models.created == 0 and pipeline.models.calls == 0


# ---------------------------------------------------------
# Failures
# ---------------------------------------------------------

def test_a_failed_generation_step_stops_before_the_review(pipeline):
    pipeline.run()
    earlier = pipeline.full_result().check_id
    pipeline.fail_at = "generate_unit_tests"
    out = pipeline.run()

    assert out["success"] is False
    assert "consistency_review" not in [s["stage"] for s in pipeline.run_log()["stages"]]
    assert pipeline.full_result().check_id == earlier
    # The old review is labelled as not belonging to the failed run.
    loaded = consistency_review.load_for_review(pipeline.root, "full", pipeline.app)
    assert loaded["run_relation"] == "earlier_run"
    assert loaded["latest_run"]["status"] == "failed"


def test_a_daily_limit_fails_the_review_not_the_pipeline(pipeline):
    pipeline.models.fail = DailyLimitError("This Google API key has hit its daily limit or is out of credit.")
    out = pipeline.run()
    assert out["success"] is True
    review = out["result"]["consistency_review"]["result"]
    assert review["status"] == "failed" and "daily limit" in review["errors"][0]
    assert pipeline.models.calls == 1                                   # bounded: no retry loop
    assert "daily limit" in out["result"]["warning"]
    assert (pipeline.app / "agent" / "BR_agent_output" / pipeline.case.name / "validated_rules.json").is_file()


def test_a_crashed_review_stage_keeps_the_generated_outputs(pipeline, monkeypatch):
    def crash(*args, **kwargs):
        raise RuntimeError("checker exploded")
    monkeypatch.setattr(commands_module, "run_consistency_check", crash)
    out = pipeline.run()

    assert out["success"] is True
    review = out["result"]["consistency_review"]
    assert review["success"] is False and "checker exploded" in review["error"]
    assert "The Consistency Review stage did not finish: checker exploded" in out["result"]["warning"]
    stage = next(s for s in pipeline.run_log()["stages"] if s["stage"] == "consistency_review")
    assert stage["status"] == "failed"
    for folder in OUTPUT_OF_STAGE.values():
        assert (pipeline.app / "agent" / folder / pipeline.case.name).is_dir()


def test_a_mismatch_is_a_finding_not_a_failure(pipeline):
    out = pipeline.run()
    assert out["success"] is True and out["result"]["consistency_review"]["result"]["findings"] == 2
    assert "warning" not in out["result"]


# ---------------------------------------------------------
# Selected results and reviews are kept
# ---------------------------------------------------------

def test_selected_results_and_reviews_survive_a_pipeline_run(pipeline):
    pipeline.run()
    first = pipeline.full_result()
    set_review(pipeline.case.name, first.findings[0].id, "reviewed", "Still to confirm.", app_dir=pipeline.app)
    selected = first.model_copy(update=dict(scope="selected", selection=["src/Refunds.cs"], check_id="selected-1"))
    save_check_result(selected, pipeline.app)

    pipeline.run()
    again = pipeline.full_result()
    assert again.check_id != first.check_id
    kept = next(f for f in again.findings if f.id == first.findings[0].id)        # same content, same finding
    assert (kept.review.status, kept.review.note) == ("reviewed", "Still to confirm.")
    assert load_check_result(pipeline.case.name, pipeline.app, scope="selected").check_id == "selected-1"


def test_the_independent_operation_still_works_alongside(pipeline, monkeypatch):
    pipeline.run()
    pipeline_check = pipeline.full_result().check_id
    monkeypatch.undo()                                  # the real check_consistency again
    commands = Commands()
    commands.app_dir = pipeline.app
    monkeypatch.setattr(checker_module, "make_llm", Models(pipeline.case))
    out = commands.check_consistency(str(pipeline.root), "selected", ["src/Refunds.cs"])
    assert out["success"] is True and out["result"]["scope"] == "selected"
    assert load_check_result(pipeline.case.name, pipeline.app).check_id == pipeline_check
