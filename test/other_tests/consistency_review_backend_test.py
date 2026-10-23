"""
@file consistency_review_backend_test.py
@brief Offline tests for the Consistency Review screen's backend (result access and review decisions).
@details Saved results are made from the tracked evaluation fixtures
(test/consistency_eval) with the real checker and the offline oracle, in
temporary folders. No model is created or called: make_llm fails if used.
"""

import sys
import json
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "test" / "consistency_eval"))
import pytest

import agent.consistency_checker as checker_module
import agent.llm as llm_module
from agent.consistency_checker import run_consistency_check
from backend import consistency_review as review
from backend.consistency_review import ReviewRequestError
from backend.dispatcher import CommandDispatcher
from eval_cases import CASES_BY_NAME
from eval_fakes import OracleLLM
from eval_fixtures import build_case


@pytest.fixture(autouse=True)
def no_model(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("The review screen must not create a model.")
    monkeypatch.setattr(llm_module, "make_llm", refuse)
    monkeypatch.setattr(checker_module, "make_llm", refuse)


class Saved:
    """One or more fixture codebases sharing an app folder, with checks saved."""

    def __init__(self, base):
        self.base = base
        self.roots = {}
        self.app = None

    def check(self, name, scope="selected", run_id="run-1"):
        case = CASES_BY_NAME[name]
        root, app = build_case(case, self.base) if name not in self.roots else (self.roots[name], self.app)
        self.roots[name], self.app = root, app
        targets = None if scope == "full" else [
            {"path": t.path, "symbol": t.symbol} if t.symbol else t.path for t in case.targets
        ]
        return run_consistency_check(root, run_id=run_id, targets=targets, llm=OracleLLM(case), app_dir=app, save=True)

    def load(self, name, scope="selected"):
        return review.load_for_review(self.roots[name], scope, self.app)


@pytest.fixture
def saved(tmp_path):
    return Saved(tmp_path)


# ---------------------------------------------------------
# Loading results
# ---------------------------------------------------------

def test_no_saved_check_is_reported_as_not_run(saved, tmp_path):
    case = CASES_BY_NAME["ignored_parameter"]
    root, app = build_case(case, tmp_path)
    loaded = review.load_for_review(root, "full", app)
    assert loaded["available"] is False
    assert loaded["result"]["status"] == "not_run" and loaded["outcome"] == "not_run"
    assert loaded["available_scopes"] == []


def test_a_saved_check_loads_with_identity_counts_and_scope(saved):
    result = saved.check("ignored_parameter")
    loaded = saved.load("ignored_parameter")
    assert loaded["available"] is True and loaded["codebase"] == "ignored_parameter"
    assert loaded["result"]["check_id"] == result.check_id and loaded["result"]["run_id"] == "run-1"
    assert loaded["result"]["selection"] == ["src/Refunds.cs"]
    assert loaded["outcome"] == "findings"
    assert loaded["review_counts"] == {"unresolved": 2}
    assert loaded["available_scopes"] == ["selected"]
    assert loaded["missing_fields"] == []
    assert len(loaded["result"]["assessments"]) == 3


def test_full_and_selected_results_stay_apart(saved):
    saved.check("inclusive_boundary", scope="selected", run_id="sel")
    assert saved.load("inclusive_boundary", "full")["available"] is False
    saved.check("inclusive_boundary", scope="full", run_id="full")
    assert saved.load("inclusive_boundary", "full")["result"]["run_id"] == "full"
    assert saved.load("inclusive_boundary", "selected")["result"]["run_id"] == "sel"
    assert saved.load("inclusive_boundary", "full")["available_scopes"] == ["full", "selected"]


@pytest.mark.parametrize("log, run_id, relation", [
    ({"run_id": "run-1", "codebase": "ignored_parameter"}, "run-1", "same_run"),
    ({"run_id": "run-2", "codebase": "ignored_parameter"}, "run-1", "earlier_run"),
    ({"run_id": "run-1", "codebase": "something_else"}, "run-1", "unknown"),
    (None, "run-1", "unknown"),
    ({"run_id": "run-1", "codebase": "ignored_parameter"}, None, "no_run"),
])
def test_a_result_is_labelled_against_the_latest_logged_run(saved, log, run_id, relation):
    saved.check("ignored_parameter", run_id=run_id)
    if log:
        (saved.app / "last_run_log.json").write_text(json.dumps(log), encoding="utf-8")
    loaded = saved.load("ignored_parameter")
    assert loaded["run_relation"] == relation
    if relation in ("same_run", "earlier_run"):
        assert loaded["latest_run"]["run_id"] == log["run_id"]


def test_a_corrupt_result_is_reported_not_raised(saved):
    saved.check("ignored_parameter")
    path = saved.app / "agent" / "consistency_output" / "ignored_parameter" / "consistency_result_selected.json"
    path.write_text("{ not json", encoding="utf-8")
    loaded = saved.load("ignored_parameter")
    assert loaded["available"] is False and loaded["problem"] == "unreadable"
    assert "Could not read" in loaded["error"]


def test_an_older_result_lists_the_fields_it_lacks(saved):
    saved.check("ignored_parameter")
    path = saved.app / "agent" / "consistency_output" / "ignored_parameter" / "consistency_result_selected.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for key in ("assessments", "skipped", "undetermined"):
        data.pop(key)
    path.write_text(json.dumps(data), encoding="utf-8")
    loaded = saved.load("ignored_parameter")
    assert loaded["available"] is True
    assert loaded["missing_fields"] == ["undetermined", "assessments", "skipped"]
    assert loaded["result"]["assessments"] == []


# The codebase is sent as a path, as for every other command; only paths
# without a usable folder name are refused.
@pytest.mark.parametrize("bad", ["", "..", "C:/", "/"])
def test_unusable_codebase_names_are_refused(saved, bad):
    with pytest.raises(Exception, match="Not a codebase name"):
        review.load_for_review(bad, "full", saved.base)


def test_unknown_scopes_are_refused(saved):
    saved.check("ignored_parameter")
    with pytest.raises(ReviewRequestError, match="Unknown result scope"):
        saved.load("ignored_parameter", "everything")


# ---------------------------------------------------------
# Saving review decisions
# ---------------------------------------------------------

def test_a_review_is_saved_reloaded_and_changes_nothing_else(saved):
    result = saved.check("ignored_parameter")
    finding = result.findings[0]
    before = saved.load("ignored_parameter")["result"]

    out = review.save_review(saved.roots["ignored_parameter"], "selected", result.check_id, finding.id,
                             "reviewed", "  Asked the team; intent still unconfirmed.  ", saved.app)
    assert out["review"]["status"] == "reviewed" and out["review"]["note"] == "Asked the team; intent still unconfirmed."
    assert out["review_counts"] == {"reviewed": 1, "unresolved": 1}

    after = saved.load("ignored_parameter")["result"]
    reviewed = next(f for f in after["findings"] if f["id"] == finding.id)
    assert (reviewed["review"]["status"], reviewed["review"]["note"]) == ("reviewed", "Asked the team; intent still unconfirmed.")
    assert reviewed["intended_behaviour"] == "unknown"

    def strip(data):
        return dict(data, findings=[dict(f, review=None) for f in data["findings"]])
    assert strip(after) == strip(before)                 # findings, verdicts and evidence untouched


def test_dismissing_and_clearing_a_note_round_trip(saved):
    result = saved.check("ignored_parameter")
    root, fid = saved.roots["ignored_parameter"], result.findings[0].id
    review.save_review(root, "selected", result.check_id, fid, "dismissed", "first", saved.app)
    review.save_review(root, "selected", result.check_id, fid, "unresolved", "   ", saved.app)
    finding = next(f for f in saved.load("ignored_parameter")["result"]["findings"] if f["id"] == fid)
    assert (finding["review"]["status"], finding["review"]["note"]) == ("unresolved", None)


def test_invalid_review_requests_are_refused(saved):
    result = saved.check("ignored_parameter")
    root, fid = saved.roots["ignored_parameter"], result.findings[0].id

    with pytest.raises(ReviewRequestError, match="has no finding"):
        review.save_review(root, "selected", result.check_id, "0000000000000000", "reviewed", None, saved.app)
    with pytest.raises(ReviewRequestError, match="changed since this screen loaded it"):
        review.save_review(root, "selected", "an-older-check", fid, "reviewed", None, saved.app)
    with pytest.raises(ReviewRequestError, match="Unknown review status"):
        review.save_review(root, "selected", result.check_id, fid, "correct", None, saved.app)
    with pytest.raises(ReviewRequestError, match="limit is 4,000"):
        review.save_review(root, "selected", result.check_id, fid, "reviewed", "x" * 4001, saved.app)
    with pytest.raises(ReviewRequestError, match="no saved check"):
        review.save_review(root, "full", result.check_id, fid, "reviewed", None, saved.app)


def test_a_finding_from_one_project_cannot_be_reviewed_in_another(saved):
    a = saved.check("ignored_parameter")
    b = saved.check("summary_injection")
    with pytest.raises(ReviewRequestError, match="has no finding"):
        review.save_review(saved.roots["summary_injection"], "selected", b.check_id, a.findings[0].id,
                           "reviewed", None, saved.app)
    review.save_review(saved.roots["ignored_parameter"], "selected", a.check_id, a.findings[0].id,
                       "dismissed", None, saved.app)
    assert saved.load("summary_injection")["review_counts"] == {"unresolved": 1}


# ---------------------------------------------------------
# Current artifact content
# ---------------------------------------------------------

def source_index(finding):
    return next(i for i, ref in enumerate(finding.artifacts) if ref.kind == "source")


def test_unchanged_source_content_is_returned_around_the_cited_lines(saved):
    result = saved.check("ignored_parameter")
    finding = result.findings[0]
    out = review.read_artifact(saved.roots["ignored_parameter"], "selected", result.check_id, finding.id,
                               source_index(finding), saved.app)
    assert out["state"] == "unchanged"
    assert "return daysSincePurchase <= 30;" in out["content"]
    assert out["lines"][0] <= 7 <= out["lines"][1]


def test_changed_and_deleted_source_are_reported_not_substituted(saved):
    result = saved.check("ignored_parameter")
    finding = result.findings[0]
    source = saved.roots["ignored_parameter"] / "src" / "Refunds.cs"
    source.write_text(source.read_text(encoding="utf-8").replace("<= 30", "<= 14"), encoding="utf-8")

    out = review.read_artifact(saved.roots["ignored_parameter"], "selected", result.check_id, finding.id,
                               source_index(finding), saved.app)
    assert out["state"] == "changed"
    assert "<= 14" in out["content"]
    assert out["stored"]["content_hash"] != out["current"]["content_hash"]

    source.unlink()
    out = review.read_artifact(saved.roots["ignored_parameter"], "selected", result.check_id, finding.id,
                               source_index(finding), saved.app)
    assert out["state"] == "missing" and out["content"] is None


def test_generated_artifact_content_is_read_from_its_output_file(saved):
    result = saved.check("ignored_parameter")
    finding = next(f for f in result.findings if f.category == "source_test")
    index = next(i for i, ref in enumerate(finding.artifacts) if ref.kind == "unit_test")
    out = review.read_artifact(saved.roots["ignored_parameter"], "selected", result.check_id, finding.id, index, saved.app)
    assert out["state"] == "unchanged"
    assert "Assert.False(new Refunds().CanRefund(10, true));" in out["content"]


def test_stored_paths_outside_the_codebase_are_never_read(saved, tmp_path):
    result = saved.check("ignored_parameter")
    secret = tmp_path / "secret.txt"
    secret.write_text("do not read", encoding="utf-8")
    path = saved.app / "agent" / "consistency_output" / "ignored_parameter" / "consistency_result_selected.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for ref in data["findings"][0]["artifacts"]:
        if ref["kind"] == "source":
            ref["path"] = "../../../secret.txt"
    path.write_text(json.dumps(data), encoding="utf-8")

    finding = result.findings[0]
    out = review.read_artifact(saved.roots["ignored_parameter"], "selected", result.check_id, finding.id,
                               source_index(finding), saved.app)
    assert out["state"] == "missing" and out["content"] is None

    with pytest.raises(ReviewRequestError, match="not a pipeline output"):
        review._output_file(saved.app, "agent/file_summary_agent_output/ignored_parameter/../../../../secret.txt",
                            "ignored_parameter")
    with pytest.raises(ReviewRequestError, match="not a pipeline output"):
        review._output_file(saved.app, "agent/BR_agent_output/other_codebase/validated_rules.json", "ignored_parameter")


def test_bad_artifact_requests_are_refused(saved):
    result = saved.check("ignored_parameter")
    finding = result.findings[0]
    root = saved.roots["ignored_parameter"]
    with pytest.raises(ReviewRequestError, match="has no artifact"):
        review.read_artifact(root, "selected", result.check_id, finding.id, 99, saved.app)
    with pytest.raises(ReviewRequestError, match="has no artifact"):
        review.read_artifact(root, "selected", result.check_id, finding.id, "0", saved.app)
    moved = root.parent / "ignored_parameter_moved"
    with pytest.raises(Exception):
        review.read_artifact(moved, "selected", result.check_id, finding.id, 0, saved.app)


# ---------------------------------------------------------
# Through the dispatcher, as the screen calls it
# ---------------------------------------------------------

def dispatcher_for(app):
    dispatcher = CommandDispatcher()
    dispatcher.commands.app_dir = app
    dispatcher.error_log = []
    return dispatcher


def test_the_commands_load_and_save_through_the_dispatcher(saved):
    result = saved.check("ignored_parameter")
    d = dispatcher_for(saved.app)
    root = str(saved.roots["ignored_parameter"])

    loaded = d.dispatch("get_consistency_result", codebase=root, scope="selected", request_id="r1")
    assert loaded["success"] is True and loaded["request_id"] == "r1"
    assert loaded["review"]["result"]["check_id"] == result.check_id

    fid = result.findings[0].id
    saved_out = d.dispatch("save_consistency_review", codebase=root, scope="selected", check_id=result.check_id,
                           finding_id=fid, status="reviewed", note="ok")
    assert saved_out["success"] is True and saved_out["saved"]["review"]["status"] == "reviewed"

    artifact = d.dispatch("get_consistency_artifact", codebase=root, scope="selected", check_id=result.check_id,
                          finding_id=fid, artifact_index=0)
    assert artifact["success"] is True and artifact["artifact"]["state"] in ("unchanged", "changed", "missing", "unavailable")


def test_command_errors_are_returned_as_failures(saved):
    result = saved.check("ignored_parameter")
    d = dispatcher_for(saved.app)
    out = d.dispatch("save_consistency_review", codebase=str(saved.roots["ignored_parameter"]), scope="selected",
                     check_id=result.check_id, finding_id="nope", status="reviewed")
    assert out["success"] is False and "has no finding" in out["error"]
    out = d.dispatch("get_consistency_result", codebase="a/..", scope="full")
    assert out["success"] is False
