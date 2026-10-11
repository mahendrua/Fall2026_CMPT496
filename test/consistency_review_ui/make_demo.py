"""
@file make_demo.py
@brief Writes saved Consistency Review results for every screen state into an isolated folder.

    .venv\\Scripts\\python.exe test\\consistency_review_ui\\make_demo.py <out_dir>

Each codebase comes from the tracked evaluation fixtures (test/consistency_eval),
checked by the real checker with offline stand-in models, so no model is
called. Everything is written under <out_dir>:

    <out_dir>/codebase/<name>/...   the fixture source
    <out_dir>/app/agent/...          its pipeline outputs and saved checks
    <out_dir>/manifest.json          app folder and codebase paths, by state

States written:
    findings          ignored_parameter: selected and full checks, from an earlier run
    clean             misleading_name_accurate: completed, no mismatches
    skipped           inclusive_boundary: findings plus a skipped folder-level rule;
                      its source is edited afterwards, so current content differs
    partial_clean     reference_problems: incomplete, no findings
    partial_findings  validation_and_state: incomplete, with findings
    failed            summary_injection: every model call failed
    unreadable        side_effects: the saved file is damaged
    older             same_file_helper: saved without the newer fields
    injection         exclusive_boundary_comments: HTML and instructions in
                      model text and a review note
    missing_source    arithmetic_shared_mistake: source deleted after the check
    no_check          collection_order_js: built but never checked
    undetermined      cross_file_dependency: one comparison undetermined
    no_outputs        no_outputs: source code with no pipeline outputs
    missing_outputs   missing_outputs: a summary but no rule or test outputs, never checked
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "test" / "consistency_eval"))

from agent.consistency_checker import run_consistency_check  # noqa: E402
from agent.consistency_store import set_review  # noqa: E402
from eval_cases import CASES_BY_NAME  # noqa: E402
from eval_fakes import OracleLLM  # noqa: E402
from eval_fixtures import build_case  # noqa: E402

INJECTED = '<img src=x onerror="window.__crInjected=1"><script>window.__crInjected=2</script>'


class Dropping(OracleLLM):
    """The oracle, but it forgets one assessment, so the check is partial."""

    def _answer(self, messages):
        answer = super()._answer(messages)
        answer.assessments = [a for a in answer.assessments if a.artifact != "RULE_1"]
        return answer


class Failing:
    def with_structured_output(self, schema):
        return self

    def invoke(self, messages):
        raise ValueError("The model's answer could not be read (offline demo).")


def targets(case):
    return [{"path": t.path, "symbol": t.symbol} if t.symbol else t.path for t in case.targets]


def result_file(app, name, scope="selected"):
    suffix = "_selected" if scope == "selected" else ""
    return app / "agent" / "consistency_output" / name / f"consistency_result{suffix}.json"


def edit_saved(path, change):
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def main(out_dir):
    out = Path(out_dir).resolve()
    roots, app = {}, None

    def build(name):
        nonlocal app
        root, app = build_case(CASES_BY_NAME[name], out)
        roots[name] = root
        return root

    def check(name, llm=None, scope="selected", run_id="demo-run-1"):
        case = CASES_BY_NAME[name]
        root = roots.get(name) or build(name)
        return run_consistency_check(
            root, run_id=run_id, targets=None if scope == "full" else targets(case),
            llm=llm or OracleLLM(case), app_dir=app, save=True,
        )

    check("ignored_parameter")
    check("ignored_parameter", scope="full")
    check("misleading_name_accurate")
    check("inclusive_boundary")
    check("reference_problems")
    check("validation_and_state", llm=Dropping(CASES_BY_NAME["validation_and_state"]))
    check("summary_injection", llm=Failing())
    check("side_effects")
    check("same_file_helper")
    injected = check("exclusive_boundary_comments")
    check("arithmetic_shared_mistake")
    check("cross_file_dependency")
    build("collection_order_js")
    build("missing_outputs")

    # Source code with no pipeline outputs at all.
    bare = out / "codebase" / "no_outputs"
    (bare / "src").mkdir(parents=True, exist_ok=True)
    (bare / "src" / "Plain.cs").write_text("class Plain {}\n", encoding="utf-8")
    roots["no_outputs"] = bare

    # The most recent logged run of ignored_parameter is a later one, started
    # after the checks above, so their results are from an earlier run.
    # One second later: after the checks above, before any check run afterwards.
    later = datetime.now() + timedelta(seconds=1)
    (app / "last_run_log.json").write_text(json.dumps({
        "run_id": "demo-run-2", "codebase": "ignored_parameter", "command": "full_pipeline", "status": "success",
        "started_at": later.isoformat(timespec="seconds"),
        "finished_at": (later + timedelta(minutes=20)).isoformat(timespec="seconds"),
    }), encoding="utf-8")

    # Current content differs from the evidence the check stored.
    source = roots["inclusive_boundary"] / "src" / "Eligibility.cs"
    source.write_text(source.read_text(encoding="utf-8").replace("age >= 18", "age >= 21"), encoding="utf-8")
    (roots["arithmetic_shared_mistake"] / "src" / "Pricing.cs").unlink()

    result_file(app, "side_effects").write_text('{"schema_version": 1, "codebase": "side_effects", "status": ', encoding="utf-8")

    def older(data):
        for key in ("assessments", "skipped", "undetermined"):
            data.pop(key, None)
    edit_saved(result_file(app, "same_file_helper"), older)

    def inject(data):
        finding = data["findings"][0]
        finding["title"] = "Summary " + INJECTED
        finding["explanation"] = "Explanation " + INJECTED
        finding["claims"][1]["statement"] = "Claim " + INJECTED
        for assessment in data["assessments"]:
            assessment["reason"] = "Reason " + INJECTED
    edit_saved(result_file(app, "exclusive_boundary_comments"), inject)
    set_review("exclusive_boundary_comments", injected.findings[0].id, "reviewed", "Note " + INJECTED, app_dir=app)

    manifest = dict(
        app=str(app),
        injected=INJECTED,
        codebases={name: str(root) for name, root in roots.items()},
        states=dict(
            findings="ignored_parameter", clean="misleading_name_accurate", skipped="inclusive_boundary",
            partial_clean="reference_problems", partial_findings="validation_and_state", failed="summary_injection",
            unreadable="side_effects", older="same_file_helper", injection="exclusive_boundary_comments",
            missing_source="arithmetic_shared_mistake", no_check="collection_order_js",
            undetermined="cross_file_dependency", no_outputs="no_outputs", missing_outputs="missing_outputs",
        ),
    )
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(out / "manifest.json")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: make_demo.py <out_dir>")
    main(sys.argv[1])
