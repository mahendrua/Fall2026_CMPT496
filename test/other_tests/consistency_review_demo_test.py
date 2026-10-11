"""
@file consistency_review_demo_test.py
@brief Offline check of the Consistency Review demo results (test/consistency_review_ui/make_demo.py).
@details Builds the demo in a temporary folder and reads every state back the
way the screen does (backend/consistency_review.py), so the states the screen
verification relies on stay what they claim to be. No model is called.
"""

import sys
import json
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "test" / "consistency_review_ui"))
import pytest

import agent.consistency_checker as checker_module
import agent.llm as llm_module
from backend import consistency_review as review


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    def refuse(*args, **kwargs):
        raise AssertionError("The demo must not create a model.")
    saved = (llm_module.make_llm, checker_module.make_llm)
    llm_module.make_llm = checker_module.make_llm = refuse
    try:
        import make_demo
        out = tmp_path_factory.mktemp("demo")
        make_demo.main(out)
    finally:
        llm_module.make_llm, checker_module.make_llm = saved
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    return manifest


def load(demo, state, scope="selected"):
    return review.load_for_review(demo["codebases"][demo["states"][state]], scope, demo["app"])


@pytest.mark.parametrize("state, status, outcome, findings", [
    ("findings", "completed", "findings", 2),
    ("clean", "completed", "no_findings", 0),
    ("skipped", "completed", "findings", 1),
    ("partial_clean", "partial", "incomplete", 0),
    ("partial_findings", "partial", "incomplete", 2),
    ("failed", "failed", "failed", 0),
    ("older", "completed", "findings", 1),
    ("injection", "completed", "findings", 1),
    ("missing_source", "completed", "findings", 3),
    ("undetermined", "partial", "incomplete", 0),
])
def test_each_demo_state_loads_as_described(demo, state, status, outcome, findings):
    loaded = load(demo, state)
    assert loaded["available"] is True
    assert (loaded["result"]["status"], loaded["outcome"], len(loaded["result"]["findings"])) == (status, outcome, findings)


def test_the_states_without_a_usable_result(demo):
    assert load(demo, "no_check")["result"]["status"] == "not_run"
    assert load(demo, "unreadable")["problem"] == "unreadable"


def test_the_findings_state_has_both_scopes_from_an_earlier_run(demo):
    full, selected = load(demo, "findings", "full"), load(demo, "findings")
    assert full["result"]["check_id"] != selected["result"]["check_id"]
    assert full["run_relation"] == selected["run_relation"] == "earlier_run"
    assert full["latest_run"]["run_id"] == "demo-run-2"


def test_the_older_state_lacks_the_newer_fields(demo):
    assert load(demo, "older")["missing_fields"] == ["undetermined", "assessments", "skipped"]


def test_the_skipped_and_undetermined_states_have_what_they_name(demo):
    assert len(load(demo, "skipped")["result"]["skipped"]) == 1
    assert len(load(demo, "undetermined")["result"]["undetermined"]) == 1


def test_current_content_is_changed_or_missing_where_the_demo_says(demo):
    for state, expected in (("skipped", "changed"), ("missing_source", "missing")):
        loaded = load(demo, state)
        finding = loaded["result"]["findings"][0]
        index = next(i for i, ref in enumerate(finding["artifacts"]) if ref["kind"] == "source")
        out = review.read_artifact(demo["codebases"][demo["states"][state]], "selected", loaded["result"]["check_id"],
                                   finding["id"], index, demo["app"])
        assert out["state"] == expected


def test_the_injection_state_carries_markup_as_plain_text(demo):
    loaded = load(demo, "injection")
    finding = loaded["result"]["findings"][0]
    assert demo["injected"] in finding["title"] and demo["injected"] in finding["review"]["note"]
    assert all(demo["injected"] in a["reason"] for a in loaded["result"]["assessments"])


def test_nothing_is_written_outside_the_demo_folder(demo):
    assert Path(demo["app"]).is_relative_to(Path(demo["app"]).parent)
    assert not (PROJECT_ROOT / "agent" / "consistency_output").exists()
