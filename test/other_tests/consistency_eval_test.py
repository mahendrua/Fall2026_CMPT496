"""
@file consistency_eval_test.py
@brief Offline tests of the Consistency Review evaluation fixtures, scorer and runner.
@details The oracle answers from each case's expectations, so these tests show
that the fixtures, the checker's validation and the scorer agree with one
another. They do not measure how well a real model detects anything. No model
calls are made: make_llm is replaced with a function that fails if called.
"""

import sys
import dataclasses
import json
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "test" / "consistency_eval"))
import pytest

import agent.llm as llm_module
from agent.consistency_checker import run_consistency_check
from agent.consistency_refs import business_rule_ref
from agent.structured_output.file_summary_output import FileSummaryOutput
from eval_cases import CASES, CASES_BY_NAME, Expect
from eval_fakes import AgreeingLLM, OracleLLM, RecordingLLM
from eval_fixtures import build_case
import run_eval
from eval_scoring import score_run, summarize

def real_consistency_outputs():
    """Files in the repository's real consistency output folder, with their modification times."""
    folder = PROJECT_ROOT / "agent" / "consistency_output"
    return sorted((str(p), p.stat().st_mtime_ns) for p in folder.rglob("*") if p.is_file()) if folder.is_dir() else []


GOOD = {"detected", "correct", "acceptable", "ambiguous"}


@pytest.fixture(autouse=True)
def no_model_calls(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("A real model was requested in an offline test.")
    monkeypatch.setattr(llm_module, "make_llm", refuse)
    import agent.consistency_checker as checker
    monkeypatch.setattr(checker, "make_llm", refuse)


def run_case(case, base, llm):
    root, app = build_case(case, base)
    targets = [{"path": t.path, "symbol": t.symbol} if t.symbol else t.path for t in case.targets]
    return run_consistency_check(root, run_id="offline", targets=targets, llm=llm, app_dir=app)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_every_case_scores_as_expected_with_the_oracle(case, tmp_path):
    result = run_case(case, tmp_path, OracleLLM(case))
    scored = score_run(case, result)

    assert result.status in case.expected_status
    assert result.errors == [], result.errors
    bad = [r for r in scored["outcomes"] if r["outcome"] not in GOOD]
    assert bad == []
    for finding in result.findings:
        assert finding.intended_behaviour == "unknown"
        assert [e.origin for e in finding.evidence] == ["code_inspection"]


def test_the_cases_cover_the_planned_ground():
    expected = {e.expected for c in CASES for t in c.targets for e in t.expects}
    assert expected == {"consistent", "inconsistent", "insufficient_evidence", "ambiguous", "not_assessed"}
    categories = {e.category for c in CASES for t in c.targets for e in t.expects if e.category}
    assert categories == {"source_summary", "source_rule", "source_test"}
    assert any(t.symbol for c in CASES for t in c.targets) and any(not t.symbol for c in CASES for t in c.targets)
    assert any(len({t.path for t in c.targets}) > 1 for c in CASES)                    # multi-file
    assert any(path.endswith(".js") for c in CASES for path in c.files)
    held_out = [c for c in CASES if not c.tuning]
    assert len(held_out) >= 10


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_every_written_artifact_loads_back_with_the_pipeline_schemas(case, tmp_path):
    from agent.structured_output.BR_output import ValidatedRule
    from agent.structured_output.UTV_output import UnitTest
    root, app = build_case(case, tmp_path)
    assert root.is_relative_to(tmp_path) and app.is_relative_to(tmp_path)
    out = app / "agent"
    for file in (out / "file_summary_agent_output" / case.name).glob("*.json"):
        FileSummaryOutput.model_validate_json(file.read_text(encoding="utf-8"))
    for file, model in (("BR_agent_output/{}/validated_rules.json", ValidatedRule),
                        ("UT_agent_output/{}/validated_tests.json", UnitTest)):
        path = out / file.format(case.name)
        for entry in json.loads(path.read_text(encoding="utf-8")) if path.exists() else []:
            model.model_validate(entry)


def test_a_historical_absolute_rule_path_is_kept_on_disk_and_mapped(tmp_path):
    case = CASES_BY_NAME["exclusive_boundary_comments"]
    root, app = build_case(case, tmp_path)
    rules = json.loads((app / "agent" / "BR_agent_output" / case.name / "validated_rules.json").read_text(encoding="utf-8"))
    assert rules[0]["source_file_paths"][0].startswith("D:\\Old Workstation")     # historical form kept on disk
    assert business_rule_ref(root, 1, app).path == "src/Shipping.cs"


def test_an_agreeing_model_is_scored_as_missing_every_contradiction(tmp_path):
    case = CASES_BY_NAME["validation_and_state"]
    scored = score_run(case, run_case(case, tmp_path, AgreeingLLM()))
    by_handle = {r["handle"]: r["outcome"] for r in scored["outcomes"]}
    assert by_handle == {"SUMMARY": "missed", "RULE_1": "correct", "RULE_2": "correct",
                         "TEST_1": "correct", "TEST_2": "missed"}


def test_flagging_a_matching_artifact_is_a_false_alarm(tmp_path):
    case = CASES_BY_NAME["arithmetic_shared_mistake"]
    result = run_case(case, tmp_path, OracleLLM(case))
    target = case.targets[0]
    wrong = dataclasses.replace(case, targets=(dataclasses.replace(target, expects=(
        Expect("SUMMARY", "pretend it matched", "consistent"), *target.expects[1:],
    )),))
    assert score_run(wrong, result)["outcomes"][0]["outcome"] == "false_alarm"


def test_a_weak_finding_is_not_counted_as_a_clean_detection(tmp_path):
    case = CASES_BY_NAME["side_effects"]
    target = case.targets[0]
    moved = dataclasses.replace(case, targets=(dataclasses.replace(target, expects=tuple(
        dataclasses.replace(e, evidence=("_stock[sku] = quantity;",)) if e.handle == "RULE_1" else e
        for e in target.expects
    )),))
    result = run_case(case, tmp_path, OracleLLM(case))
    rule_1 = next(r for r in score_run(moved, result)["outcomes"] if r["handle"] == "RULE_1")
    assert (rule_1["outcome"], rule_1["evidence_ok"]) == ("detected_weak", False)


def test_a_failed_run_is_reported_as_not_assessed_never_as_clean(tmp_path):
    class Failing:
        def with_structured_output(self, schema):
            return self

        def invoke(self, messages):
            raise ValueError("unreadable")

    case = CASES_BY_NAME["ignored_parameter"]
    scored = score_run(case, run_case(case, tmp_path, Failing()))
    assert scored["status"] == "failed" and scored["errors"]
    assert {r["outcome"] for r in scored["outcomes"]} == {"not_assessed"}
    totals = summarize([scored])
    assert totals["runs_with_errors"] and totals["scores"]["held_out"]["explicit_contradictions"] == {"not_assessed": 2}


def test_repeated_runs_report_identical_verdicts_and_finding_ids(tmp_path):
    case = CASES_BY_NAME["side_effects"]
    runs = [score_run(case, run_case(case, tmp_path / str(n), OracleLLM(case))) for n in (1, 2)]
    repeat = summarize(runs)["repeatability"]
    assert repeat["verdict_groups"] == 4 and repeat["verdicts_identical"] == 4
    assert repeat["finding_id_sets_identical"] == 1 and repeat["unstable"] == []


def test_the_recorder_counts_calls_and_proposed_findings(tmp_path):
    case = CASES_BY_NAME["arithmetic_shared_mistake"]
    recorder = RecordingLLM(OracleLLM(case))
    run_case(case, tmp_path, recorder)
    assert recorder.stats()["calls"] == 1 and recorder.stats()["proposed_findings"] == 3


def test_the_runner_is_offline_by_default_and_keeps_outputs_in_its_folder(tmp_path, capsys):
    out = tmp_path / "out"
    real_before = real_consistency_outputs()
    assert run_eval.main(["--cases", "inclusive_boundary,reference_problems", "--repeat", "2", "--out", str(out)]) == 0
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["mode"] == "offline" and report["totals"]["runs"] == 4
    assert (out / "inclusive_boundary" / "run2" / "app" / "agent" / "consistency_output").is_dir()
    assert real_consistency_outputs() == real_before        # nothing written to the real outputs
    assert "offline oracle (no model calls)" in capsys.readouterr().out


@pytest.mark.parametrize("argv, message", [
    (["--real"], "--real needs --max-calls"),
    (["--real", "--max-calls", "3"], "needs 16 model call(s)"),
    (["--real", "--max-calls", "500"], "the ceiling is 60"),
    (["--cases", "nope"], "Unknown case"),
])
def test_the_runner_refuses_unbounded_or_unknown_batches_before_any_call(argv, message, capsys, tmp_path):
    if argv == ["--real", "--max-calls", "500"]:
        argv = argv + ["--repeat", "4"]
    with pytest.raises(SystemExit):
        run_eval.main(argv + ["--out", str(tmp_path)])
    assert message in capsys.readouterr().err


# ---------------------------------------------------------
# Provider call cap (retries included)
# ---------------------------------------------------------

from eval_fakes import CallBudget, CallBudgetExhausted  # noqa: E402


class CountingLLM:
    """A provider stand-in that counts every request it receives."""

    def __init__(self, respond):
        self.respond, self.requests = respond, 0

    def with_structured_output(self, schema):
        return self

    def invoke(self, messages):
        self.requests += 1
        reply = self.respond(messages)
        if isinstance(reply, Exception):
            raise reply
        return reply


def test_the_budget_refuses_requests_beyond_the_cap_before_sending(tmp_path):
    case = CASES_BY_NAME["cross_file_dependency"]                       # two targets
    provider = CountingLLM(OracleLLM(case).respond)
    budget = CallBudget(1)
    result = run_case(case, tmp_path, RecordingLLM(provider, budget))
    assert provider.requests == 1
    assert (budget.used, budget.refused) == (1, 2)                       # the re-ask was refused too
    assert any("call budget of 1 used up" in e for e in result.errors)
    assert result.status == "partial"


def test_re_asks_count_against_the_budget(tmp_path):
    case = CASES_BY_NAME["inclusive_boundary"]
    provider = CountingLLM(lambda m: ValueError("unreadable"))
    budget = CallBudget(5)
    run_case(case, tmp_path, RecordingLLM(provider, budget))
    assert provider.requests == budget.used == 2                        # first attempt + one re-ask


def test_real_mode_builds_the_model_without_hidden_retries_and_stops_at_the_cap(monkeypatch, tmp_path):
    from agent import llm as llm_module
    built = {}
    provider = CountingLLM(lambda m: ValueError("unreadable"))         # forces re-asks

    def fake_build(config, max_output_tokens=None, retry=True):
        built["retry"] = retry
        return provider

    monkeypatch.setattr(llm_module, "build_chat_model", fake_build)
    monkeypatch.setattr(llm_module, "get_active_config", lambda: llm_module.LLMConfig("google", "test-model", "k"))

    out = tmp_path / "out"
    argv = ["--real", "--cases", "inclusive_boundary,ignored_parameter,summary_injection",
            "--max-calls", "3", "--out", str(out)]
    assert run_eval.main(argv) == 0
    assert built["retry"] is False
    assert provider.requests == 3                                        # never more than --max-calls
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["totals"]["budget"] == {"limit": 3, "provider_requests": 3, "refused": 1}
    assert [r["case"] for r in report["totals"]["not_run"]] == ["summary_injection"]
    assert all(r["status"] == "failed" for r in report["runs"])          # failures, never clean results
