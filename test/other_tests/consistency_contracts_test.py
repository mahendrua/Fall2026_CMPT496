"""
@file consistency_contracts_test.py
@brief Offline tests for the Consistency Review contracts, references and storage (Part 1).
@details Uses a temporary app directory and codebase laid out the way the
pipeline writes them. No AI calls, vector stores or detection: findings are
built by hand to check how they are represented, not how they are found.
"""

import sys
import json
import os
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
import pytest
from pydantic import ValidationError

from agent.consistency_refs import (
    business_rule_ref, content_hash, refresh_ref, source_ref, summary_ref, unit_test_ref,
)
from agent.consistency_store import (
    ConsistencyStoreError, load_check_result, load_reviews, output_dir,
    save_check_result, set_review,
)
from agent.structured_output.consistency_output import (
    ArtifactRef, Claim, ConsistencyCheckResult, Evidence, Finding, Undetermined,
)

CODEBASE = "Shop"

DISCOUNT_CS = """namespace Shop
{
    public class Pricing
    {
        public decimal Discount(decimal price)
        {
            return price + price * 0.10m;
        }
    }
}
"""

DISCOUNT_RULE = "A 10% discount is applied to the price."


# ---------------------------------------------------------
# Fixture: an app dir and codebase as the pipeline leaves them
# ---------------------------------------------------------

def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def age(path, seconds=100):
    """Make a file look older than everything written after it."""
    stamp = os.path.getmtime(path) - seconds
    os.utime(path, (stamp, stamp))


def summary_json(root, methods=None, path=None):
    return {
        "path": path or str(root / "src" / "Pricing.cs"),
        "summary": "Pricing calculations.",
        "dependencies": [],
        "functions": [],
        "types": [{
            "name": "Pricing",
            "kind": "class",
            "description": "Calculates prices.",
            "properties": [],
            "methods": methods if methods is not None else [{
                "name": "Discount",
                "description": "Applies a 10% discount, reducing the price.",
                "parameters": [{"name": "price", "type": "decimal", "direction": "in"}],
            }],
            "plantuml": "",
        }],
        "business_rules": [],
    }


def validated_rule(rule_id=1, rule=DISCOUNT_RULE, files=("src/Pricing.cs",), provenance=None):
    return {
        "id": rule_id,
        "rule": rule,
        "source_directory": "src",
        "source_file_paths": list(files),
        "explanation": {"evidence": {"src/Pricing.cs": ["return price + price * 0.10m;"]}, "reasoning": "Discount method."},
        "provenance": provenance if provenance is not None else [
            {"origin": "file", "source_file_paths": list(files)},
        ],
    }


def unit_test(rule_id=1, rule=DISCOUNT_RULE):
    return {
        "id": rule_id,
        "rule": rule,
        "imports": ["using Xunit;"],
        "source_directory": "src",
        "source_file_paths": ["src/Pricing.cs"],
        "unit_test": "[Fact]\npublic void Discount_Reduces_Price()\n{\n    Assert.Equal(90m, new Pricing().Discount(100m));\n}",
    }


@pytest.fixture
def env(tmp_path):
    app = tmp_path / "app"
    root = tmp_path / "targets" / CODEBASE
    (root / "src").mkdir(parents=True)
    app.mkdir()

    source = root / "src" / "Pricing.cs"
    source.write_text(DISCOUNT_CS, encoding="utf-8")
    age(source)

    class Env:
        pass
    e = Env()
    e.app, e.root, e.source = app, root, source
    e.summary_dir = app / "agent" / "file_summary_agent_output" / CODEBASE
    e.rules_dir = app / "agent" / "BR_agent_output" / CODEBASE
    e.tests_dir = app / "agent" / "UT_agent_output" / CODEBASE
    return e


@pytest.fixture
def artifacts(env):
    """A summary, validated rule and compiled test, all written after the source."""
    write_json(env.summary_dir / "Pricing-cs__1.json", summary_json(env.root))
    write_json(env.rules_dir / "validated_rules.json", [validated_rule()])
    write_json(env.tests_dir / "validated_tests.json", [unit_test()])
    return env


def discount_finding(env, run_id=None):
    """The Discount example: code adds 10%, the summary says it reduces."""
    source = source_ref(env.root, "src/Pricing.cs", symbol="Pricing.Discount", line_start=5, line_end=8)
    summary = summary_ref(env.root, "src/Pricing.cs", symbol="Pricing.Discount", app_dir=env.app)
    return Finding(
        codebase=CODEBASE,
        run_id=run_id,
        category="source_summary",
        title="Summary says Discount reduces the price; the code increases it",
        explanation=(
            "The summary describes a 10% reduction. The implementation returns "
            "price + price * 0.10, which is 110 for an input of 100."
        ),
        claims=[
            Claim(artifact=source, statement="Returns the price increased by 10% (100 -> 110).",
                  excerpt="return price + price * 0.10m;"),
            Claim(artifact=summary, statement="Reduces the price by 10%.",
                  excerpt="Applies a 10% discount, reducing the price."),
        ],
        evidence=[Evidence(
            origin="code_inspection",
            description="Read from the method body; the method was not executed.",
            artifact=source,
            excerpt="return price + price * 0.10m;",
        )],
        uncertainty=[
            "No confirmed requirement says whether Discount should reduce or increase the price.",
            "The name 'Discount' suggests a reduction but is not a requirement.",
        ],
    )


# ---------------------------------------------------------
# The Discount example
# ---------------------------------------------------------

def test_discount_example_round_trips_with_intended_behaviour_unknown(artifacts):
    finding = discount_finding(artifacts, run_id="20261010-120000-abc123")
    result = ConsistencyCheckResult(
        codebase=CODEBASE,
        run_id="20261010-120000-abc123",
        status="completed",
        artifacts_checked=[claim.artifact for claim in finding.claims],
        findings=[finding],
    )

    save_check_result(result, artifacts.app)
    loaded = load_check_result(CODEBASE, artifacts.app, run_id="20261010-120000-abc123")

    assert loaded == result
    got = loaded.findings[0]
    assert got.category == "source_summary"
    assert got.intended_behaviour == "unknown"
    assert [item.origin for item in got.evidence] == ["code_inspection"]
    assert got.review.status == "unresolved"
    assert {claim.artifact.kind for claim in got.claims} == {"source", "summary"}
    assert all(ref.status == "resolved" for ref in got.artifacts)
    assert loaded.outcome == "findings"


def test_discount_example_cannot_claim_intended_behaviour_without_a_confirmed_requirement(artifacts):
    data = discount_finding(artifacts).model_dump()
    data["intended_behaviour"] = "conflicts_with_requirement"
    data["id"] = ""
    with pytest.raises(ValidationError, match="confirmed requirement"):
        Finding.model_validate(data)


def test_a_generated_rule_cannot_stand_in_for_a_confirmed_requirement(artifacts):
    source = source_ref(artifacts.root, "src/Pricing.cs", symbol="Pricing.Discount")
    rule = business_rule_ref(artifacts.root, 1, artifacts.app)
    with pytest.raises(ValidationError, match="confirmed requirement"):
        Finding(
            codebase=CODEBASE, category="source_rule", title="t", explanation="e",
            claims=[Claim(artifact=source, statement="adds"), Claim(artifact=rule, statement="discount")],
            intended_behaviour="conflicts_with_requirement",
        )
    with pytest.raises(ValidationError, match="Human confirmation must reference"):
        Evidence(origin="human_confirmation", description="the rule says so", artifact=rule)


def test_confirmed_requirement_allows_an_intended_behaviour_finding(artifacts):
    source = source_ref(artifacts.root, "src/Pricing.cs", symbol="Pricing.Discount")
    requirement = ArtifactRef(kind="requirement", confirmed_by="Product owner, 2026-10-01",
                              detail="Discounts reduce the price by 10%.")
    finding = Finding(
        codebase=CODEBASE, category="implementation_requirement",
        title="Discount increases the price but the confirmed requirement is a reduction",
        explanation="e",
        claims=[Claim(artifact=source, statement="adds 10%"),
                Claim(artifact=requirement, statement="reduces by 10%")],
        evidence=[Evidence(origin="human_confirmation", description="Confirmed requirement.", artifact=requirement)],
        intended_behaviour="conflicts_with_requirement",
    )
    assert finding.intended_behaviour == "conflicts_with_requirement"

    with pytest.raises(ValidationError, match="who confirmed"):
        ArtifactRef(kind="requirement")


# ---------------------------------------------------------
# Source references
# ---------------------------------------------------------

def test_source_ref_resolves_any_path_form_to_a_root_relative_path(env):
    for given in ("src/Pricing.cs", "src\\Pricing.cs", str(env.source), f"{CODEBASE}/src/Pricing.cs"):
        ref = source_ref(env.root, given, symbol="Pricing.Discount", line_start=5, line_end=8)
        assert ref.status == "resolved", given
        assert ref.path == "src/Pricing.cs"
        assert ref.content_hash == content_hash(env.source.read_bytes())
        assert (ref.line_start, ref.line_end) == (5, 8)


@pytest.mark.parametrize("given, reason", [
    ("src/Missing.cs", "does not exist"),
    ("C:/elsewhere/Other.cs", "not inside the codebase"),
])
def test_source_ref_reports_missing_files(env, given, reason):
    ref = source_ref(env.root, given)
    assert ref.status == "missing"
    assert reason in ref.detail


def test_source_ref_reports_a_line_range_past_the_end_of_the_file(env):
    ref = source_ref(env.root, "src/Pricing.cs", line_start=50, line_end=60)
    assert ref.status == "missing"
    assert "fewer than 60 lines" in ref.detail


def test_line_range_must_be_in_order():
    with pytest.raises(ValidationError, match="line_end"):
        ArtifactRef(kind="source", path="a.cs", line_start=9, line_end=3)


# ---------------------------------------------------------
# Summary references
# ---------------------------------------------------------

def test_summary_ref_matches_by_the_path_inside_the_file_not_the_file_name(artifacts):
    (artifacts.summary_dir / "Pricing-cs__1.json").rename(artifacts.summary_dir / "Unrelated-cs__7.json")
    ref = summary_ref(artifacts.root, "src/Pricing.cs", symbol="Pricing.Discount", app_dir=artifacts.app)
    assert ref.status == "resolved"
    assert ref.artifact_file == f"agent/file_summary_agent_output/{CODEBASE}/Unrelated-cs__7.json"
    assert ref.symbol == "Pricing.Discount"


def test_summary_ref_by_bare_method_name_stays_within_that_files_summary(artifacts):
    other = artifacts.root / "src" / "Other.cs"
    other.write_text("class Other { decimal Discount(decimal p) => p; }", encoding="utf-8")
    age(other)
    write_json(artifacts.summary_dir / "Other-cs__1.json", summary_json(
        artifacts.root, path=str(other),
        methods=[{"name": "Discount", "description": "Returns the price unchanged."}],
    ))

    ref = summary_ref(artifacts.root, "src/Pricing.cs", symbol="Discount", app_dir=artifacts.app)
    assert ref.status == "resolved"
    assert ref.artifact_file.endswith("Pricing-cs__1.json")


def test_summary_ref_reports_overloads_as_ambiguous(env):
    write_json(env.summary_dir / "Pricing-cs__1.json", summary_json(env.root, methods=[
        {"name": "Discount", "description": "a", "parameters": [{"name": "p", "type": "decimal", "direction": "in"}]},
        {"name": "Discount", "description": "b", "parameters": [{"name": "p", "type": "int", "direction": "in"}]},
    ]))
    ref = summary_ref(env.root, "src/Pricing.cs", symbol="Pricing.Discount", app_dir=env.app)
    assert ref.status == "ambiguous"
    assert ref.candidates == ["Pricing.Discount(decimal)", "Pricing.Discount(int)"]


def test_summary_ref_reports_two_summaries_of_one_file_as_ambiguous(artifacts):
    write_json(artifacts.summary_dir / "Pricing-cs__2.json", summary_json(artifacts.root))
    ref = summary_ref(artifacts.root, "src/Pricing.cs", app_dir=artifacts.app)
    assert ref.status == "ambiguous"
    assert len(ref.candidates) == 2


def test_summary_ref_reports_missing_summaries_and_members(env):
    assert "No file summaries exist" in summary_ref(env.root, "src/Pricing.cs", app_dir=env.app).detail

    write_json(env.summary_dir / "Pricing-cs__1.json", summary_json(env.root))
    (env.summary_dir / "Broken-cs__1.json").write_text("{not json", encoding="utf-8")
    write_json(env.summary_dir / "business_rules" / "business_rules.json", {})

    missing_file = summary_ref(env.root, "src/Other.cs", app_dir=env.app)
    assert missing_file.status == "missing"
    assert "1 summary file(s) could not be read" in missing_file.detail

    missing_member = summary_ref(env.root, "src/Pricing.cs", symbol="Pricing.Markup", app_dir=env.app)
    assert missing_member.status == "missing"
    assert "does not describe Pricing.Markup" in missing_member.detail


def test_a_newer_source_modification_time_is_a_caveat_not_proof_of_staleness(artifacts):
    # The pipeline records no hash of the source a summary was made from, so a
    # later modification time (a checkout does this too) only adds a caveat.
    age(artifacts.summary_dir / "Pricing-cs__1.json", seconds=1000)
    ref = summary_ref(artifacts.root, "src/Pricing.cs", app_dir=artifacts.app)
    assert ref.status == "resolved"
    assert "Possibly out of date: src/Pricing.cs was modified after" in ref.detail
    assert "content may be unchanged" in ref.detail


def test_modification_time_caveat_keeps_the_uncompiled_test_note(env):
    write_json(env.rules_dir / "validated_rules.json", [validated_rule()])
    tests = write_json(env.tests_dir / "unit_tests.json", [unit_test()])
    age(tests, seconds=1000)
    ref = unit_test_ref(env.root, 1, env.app)
    assert ref.status == "resolved"
    assert "not been compiled or run" in ref.detail
    assert "Possibly out of date" in ref.detail


# ---------------------------------------------------------
# Business rule references and provenance
# ---------------------------------------------------------

def test_business_rule_ref_preserves_every_provenance_record(env):
    provenance = [
        {"origin": "file", "source_file_paths": ["src/Pricing.cs"]},
        {"origin": "directory_observed", "source_file_paths": []},
        {"origin": "directory_inferred", "source_file_paths": []},
    ]
    write_json(env.rules_dir / "validated_rules.json", [validated_rule(provenance=provenance)])

    ref = business_rule_ref(env.root, 1, env.app)
    assert ref.status == "resolved"
    assert ref.path == "src/Pricing.cs"
    assert ref.directory == "src"
    assert [p.model_dump() for p in ref.provenance] == provenance

    finding = Finding(
        codebase=CODEBASE, category="source_rule", title="t", explanation="e",
        claims=[Claim(artifact=source_ref(env.root, "src/Pricing.cs"), statement="adds"),
                Claim(artifact=ref, statement="discount")],
    )
    result = ConsistencyCheckResult(codebase=CODEBASE, status="completed", findings=[finding])
    save_check_result(result, env.app)
    loaded = load_check_result(CODEBASE, env.app)
    rule = next(a for a in loaded.findings[0].artifacts if a.kind == "business_rule")
    assert [p.model_dump() for p in rule.provenance] == provenance


def test_business_rule_ref_for_a_folder_rule_has_no_file(env):
    write_json(env.rules_dir / "validated_rules.json", [validated_rule(
        files=(), provenance=[{"origin": "directory_observed", "source_file_paths": []}],
    )])
    ref = business_rule_ref(env.root, 1, env.app)
    assert ref.status == "resolved"
    assert ref.path is None
    assert ref.directory == "src"
    assert "Folder-level rule" in ref.detail


def test_business_rule_ref_reports_missing_discarded_duplicate_and_invalid_rules(env):
    assert business_rule_ref(env.root, 1, env.app).status == "missing"

    write_json(env.rules_dir / "validated_rules.json", [validated_rule(1), validated_rule(1, rule="Other")])
    write_json(env.rules_dir / "discarded_rules.json", [
        {"id": 2, "rule": "r", "source_directory": ".", "reason": "No code evidence found."},
    ])
    duplicate = business_rule_ref(env.root, 1, env.app)
    assert duplicate.status == "ambiguous"
    assert duplicate.candidates == [DISCOUNT_RULE, "Other"]

    discarded = business_rule_ref(env.root, 2, env.app)
    assert discarded.status == "missing"
    assert "discarded during validation: No code evidence found." in discarded.detail

    write_json(env.rules_dir / "validated_rules.json", [{"id": 3, "rule": "no explanation"}])
    assert business_rule_ref(env.root, 3, env.app).status == "unverified"


# ---------------------------------------------------------
# Unit test references
# ---------------------------------------------------------

def test_unit_test_ref_uses_the_compiled_test_and_its_harness_name(artifacts):
    ref = unit_test_ref(artifacts.root, 1, artifacts.app)
    assert ref.status == "resolved"
    assert ref.test_id == "UT_1"
    assert ref.artifact_file == f"agent/UT_agent_output/{CODEBASE}/validated_tests.json"
    assert ref.detail is None


def test_unit_test_ref_falls_back_to_the_uncompiled_test_and_says_so(env):
    write_json(env.rules_dir / "validated_rules.json", [validated_rule()])
    write_json(env.tests_dir / "unit_tests.json", [unit_test()])
    ref = unit_test_ref(env.root, 1, env.app)
    assert ref.status == "resolved"
    assert ref.artifact_file.endswith("unit_tests.json")
    assert "not been compiled or run" in ref.detail


def test_unit_test_ref_is_stale_when_rule_ids_were_renumbered(artifacts):
    write_json(artifacts.rules_dir / "validated_rules.json", [validated_rule(1, rule="Orders over $50 ship free.")])
    ref = unit_test_ref(artifacts.root, 1, artifacts.app)
    assert ref.status == "stale"
    assert "different rule" in ref.detail

    write_json(artifacts.rules_dir / "validated_rules.json", [validated_rule(2)])
    ref = unit_test_ref(artifacts.root, 1, artifacts.app)
    assert ref.status == "stale"
    assert "no longer exists" in ref.detail


def test_unit_test_ref_reports_missing_discarded_and_duplicate_tests(env):
    assert "No unit tests exist" in unit_test_ref(env.root, 1, env.app).detail

    write_json(env.tests_dir / "validated_tests.json", [unit_test(1), unit_test(1)])
    write_json(env.tests_dir / "discarded_tests.json", [dict(unit_test(2), reason="does not compile")])

    duplicate = unit_test_ref(env.root, 1, env.app)
    assert duplicate.status == "ambiguous"
    assert duplicate.candidates == ["UT_1", "UT_1_b"]

    discarded = unit_test_ref(env.root, 2, env.app)
    assert discarded.status == "missing"
    assert "discarded: does not compile" in discarded.detail


# ---------------------------------------------------------
# Refreshing saved references
# ---------------------------------------------------------

def test_refresh_ref_detects_changed_and_deleted_artifacts(artifacts):
    ref = source_ref(artifacts.root, "src/Pricing.cs")
    assert refresh_ref(ref, artifacts.root, artifacts.app).status == "resolved"

    artifacts.source.write_text(DISCOUNT_CS.replace("+", "-"), encoding="utf-8")
    changed = refresh_ref(ref, artifacts.root, artifacts.app)
    assert changed.status == "stale"
    assert "changed since" in changed.detail

    artifacts.source.unlink()
    assert refresh_ref(ref, artifacts.root, artifacts.app).status == "missing"


# ---------------------------------------------------------
# Finding contract
# ---------------------------------------------------------

def test_finding_id_is_stable_for_the_same_content_and_changes_with_it(artifacts):
    first = discount_finding(artifacts)
    assert discount_finding(artifacts).id == first.id

    artifacts.source.write_text(DISCOUNT_CS + "\n// edited\n", encoding="utf-8")
    assert discount_finding(artifacts).id != first.id

    data = first.model_dump()
    data.update(id="", discriminator="second aspect")
    assert Finding.model_validate(data).id != first.id


def test_claim_artifacts_are_listed_among_the_finding_artifacts(artifacts):
    finding = discount_finding(artifacts)
    assert {ref.kind for ref in finding.artifacts} == {"source", "summary"}
    assert len(finding.artifacts) == 2


@pytest.mark.parametrize("category, kinds", [
    ("source_summary", ("source", "business_rule")),
    ("source_rule", ("summary", "business_rule")),
    ("source_test", ("source", "summary")),
    ("artifact_artifact", ("source", "summary")),
    ("implementation_requirement", ("source", "summary")),
])
def test_finding_claims_must_fit_the_category(category, kinds):
    refs = {
        "source": ArtifactRef(kind="source", path="a.cs"),
        "summary": ArtifactRef(kind="summary", path="a.cs"),
        "business_rule": ArtifactRef(kind="business_rule", rule_id=1),
    }
    with pytest.raises(ValidationError):
        Finding(
            codebase=CODEBASE, category=category, title="t", explanation="e",
            claims=[Claim(artifact=refs[kind], statement="x") for kind in kinds],
        )


def test_generated_artifacts_repeating_one_mistake_are_still_compared_with_source(artifacts):
    source = source_ref(artifacts.root, "src/Pricing.cs", symbol="Pricing.Discount")
    summary = summary_ref(artifacts.root, "src/Pricing.cs", symbol="Pricing.Discount", app_dir=artifacts.app)
    rule = business_rule_ref(artifacts.root, 1, artifacts.app)
    test = unit_test_ref(artifacts.root, 1, artifacts.app)

    findings = [
        Finding(codebase=CODEBASE, category=category, title="t", explanation="e",
                claims=[Claim(artifact=source, statement="adds 10%"), Claim(artifact=other, statement="reduces by 10%")])
        for category, other in (("source_summary", summary), ("source_rule", rule), ("source_test", test))
    ]
    result = ConsistencyCheckResult(codebase=CODEBASE, status="completed", findings=findings)
    assert len({finding.id for finding in result.findings}) == 3


def test_reference_status_rules():
    with pytest.raises(ValidationError, match="explain why"):
        ArtifactRef(kind="summary", path="a.cs", status="missing")
    with pytest.raises(ValidationError, match="two candidates"):
        ArtifactRef(kind="summary", path="a.cs", status="ambiguous", detail="d", candidates=["x"])
    with pytest.raises(ValidationError, match="provenance"):
        ArtifactRef(kind="summary", path="a.cs", provenance=[{"origin": "file"}])
    with pytest.raises(ValidationError, match="rule_id"):
        ArtifactRef(kind="business_rule")


def test_execution_evidence_must_reference_a_test():
    source = ArtifactRef(kind="source", path="a.cs")
    with pytest.raises(ValidationError, match="test that was run"):
        Evidence(origin="execution", description="it ran", artifact=source)
    test = ArtifactRef(kind="unit_test", rule_id=1, test_id="UT_1")
    assert Evidence(origin="execution", description="UT_1 failed", artifact=test).origin == "execution"


# ---------------------------------------------------------
# Check result contract
# ---------------------------------------------------------

MISSING_RULES = ArtifactRef(kind="business_rule", rule_id=1, status="missing", detail="validated_rules.json does not exist.")
AMBIGUOUS = ArtifactRef(kind="summary", path="a.cs", status="ambiguous", detail="d", candidates=["x", "y"])


def test_completed_with_no_findings_is_distinct_from_incomplete_failed_and_not_run():
    assert ConsistencyCheckResult(codebase=CODEBASE, status="completed").outcome == "no_findings"
    assert ConsistencyCheckResult(codebase=CODEBASE, status="partial", missing_artifacts=[MISSING_RULES]).outcome == "incomplete"
    assert ConsistencyCheckResult(codebase=CODEBASE, status="partial", unresolved_links=[AMBIGUOUS]).outcome == "incomplete"
    assert ConsistencyCheckResult(codebase=CODEBASE, status="failed", errors=["boom"]).outcome == "failed"
    assert ConsistencyCheckResult(codebase=CODEBASE, status="not_run").outcome == "not_run"


@pytest.mark.parametrize("fields, message", [
    (dict(status="completed", missing_artifacts=[MISSING_RULES]), "partial, not completed"),
    (dict(status="completed", errors=["timeout"]), "partial, not completed"),
    (dict(status="partial"), "must say what"),
    (dict(status="failed"), "must list its errors"),
    (dict(status="not_run", errors=["x"]), "did not run"),
    (dict(status="partial", missing_artifacts=[AMBIGUOUS]), "only hold missing"),
    (dict(status="partial", unresolved_links=[MISSING_RULES]), "ambiguous, stale or unverified"),
    (dict(status="completed", artifacts_checked=[AMBIGUOUS]), "only hold resolved"),
])
def test_check_result_status_must_match_its_contents(fields, message):
    with pytest.raises(ValidationError, match=message):
        ConsistencyCheckResult(codebase=CODEBASE, **fields)


def test_check_result_rejects_findings_from_another_codebase_or_run_and_duplicates(artifacts):
    finding = discount_finding(artifacts, run_id="run-a")
    with pytest.raises(ValidationError, match="codebase"):
        ConsistencyCheckResult(codebase="Other", status="completed", findings=[finding])
    with pytest.raises(ValidationError, match="run-a"):
        ConsistencyCheckResult(codebase=CODEBASE, run_id="run-b", status="completed", findings=[finding])
    with pytest.raises(ValidationError, match="Duplicate"):
        ConsistencyCheckResult(codebase=CODEBASE, status="completed", findings=[finding, finding])


# ---------------------------------------------------------
# Storage
# ---------------------------------------------------------

def test_saved_result_round_trips_and_is_not_mistaken_for_another_artifact(artifacts):
    result = ConsistencyCheckResult(
        codebase=CODEBASE, status="partial", scope="selected", selection=["src/Pricing.cs"],
        started_at="2026-10-10T12:00:00", finished_at="2026-10-10T12:01:00",
        artifacts_checked=[source_ref(artifacts.root, "src/Pricing.cs")],
        missing_artifacts=[MISSING_RULES], unresolved_links=[AMBIGUOUS],
        findings=[discount_finding(artifacts)], errors=["One comparison timed out."],
        notes=["Selected Pricing.cs only."],
    )
    path = save_check_result(result, artifacts.app)
    assert path == artifacts.app / "agent" / "consistency_output" / CODEBASE / "consistency_result_selected.json"

    assert load_check_result(CODEBASE, artifacts.app, scope="selected") == result

    # main.js detectFileType() reads top-level arrays as business rules and
    # objects with summary/functions/types as file summaries.
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(stored, dict)
    assert not {"summary", "functions", "dependencies", "types", "directory_name", "purpose"} & stored.keys()
    assert "review" not in stored["findings"][0]


def test_no_saved_check_loads_as_not_run_not_as_zero_findings(env):
    result = load_check_result(CODEBASE, env.app)
    assert result.status == "not_run"
    assert result.outcome == "not_run"
    assert result.notes


def test_a_check_from_another_run_is_never_returned_for_the_requested_run(artifacts):
    save_check_result(ConsistencyCheckResult(codebase=CODEBASE, run_id="run-a", status="completed"), artifacts.app)

    other = load_check_result(CODEBASE, artifacts.app, run_id="run-b")
    assert other.status == "not_run"
    assert other.run_id == "run-b"
    assert "belongs to run run-a" in other.notes[0]

    assert load_check_result(CODEBASE, artifacts.app, run_id="run-a").status == "completed"


def test_codebases_are_stored_apart(artifacts):
    save_check_result(ConsistencyCheckResult(codebase=CODEBASE, status="completed", findings=[discount_finding(artifacts)]), artifacts.app)
    save_check_result(ConsistencyCheckResult(codebase="Billing", status="completed"), artifacts.app)

    assert len(load_check_result(CODEBASE, artifacts.app).findings) == 1
    assert load_check_result("Billing", artifacts.app).findings == []

    # A result file copied into the wrong codebase's folder is refused.
    wrong = output_dir("Billing", artifacts.app) / "consistency_result.json"
    wrong.write_text((output_dir(CODEBASE, artifacts.app) / "consistency_result.json").read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(ConsistencyStoreError, match="not 'Billing'"):
        load_check_result("Billing", artifacts.app)


@pytest.mark.parametrize("name", ["", "..", "a/b", "a\\b"])
def test_codebase_names_cannot_escape_the_output_folder(env, name):
    with pytest.raises(ConsistencyStoreError):
        output_dir(name, env.app)


def test_unreadable_or_invalid_stored_files_raise_a_clear_error(env):
    path = output_dir(CODEBASE, env.app) / "consistency_result.json"
    path.parent.mkdir(parents=True)
    path.write_text("{oops", encoding="utf-8")
    with pytest.raises(ConsistencyStoreError, match="Could not read"):
        load_check_result(CODEBASE, env.app)

    path.write_text(json.dumps({"codebase": CODEBASE, "status": "completed", "errors": ["x"]}), encoding="utf-8")
    with pytest.raises(ConsistencyStoreError, match="not a valid consistency result"):
        load_check_result(CODEBASE, env.app)


# ---------------------------------------------------------
# Review decisions
# ---------------------------------------------------------

def test_review_status_and_note_persist_and_survive_a_rerun(artifacts):
    finding = discount_finding(artifacts)
    save_check_result(ConsistencyCheckResult(codebase=CODEBASE, status="completed", findings=[finding]), artifacts.app)

    review = set_review(CODEBASE, finding.id, "reviewed", "  Asked the client; behaviour unconfirmed.  ", app_dir=artifacts.app)
    assert review.note == "Asked the client; behaviour unconfirmed."
    assert review.updated_at

    # Running the check again with the same finding keeps the decision.
    save_check_result(ConsistencyCheckResult(codebase=CODEBASE, status="completed", findings=[discount_finding(artifacts)]), artifacts.app)
    loaded = load_check_result(CODEBASE, artifacts.app).findings[0]
    assert loaded.review.status == "reviewed"
    assert loaded.review.note == "Asked the client; behaviour unconfirmed."
    # Reviewing a finding does not change what is known about intended behaviour.
    assert loaded.intended_behaviour == "unknown"

    set_review(CODEBASE, finding.id, "dismissed", "   ", app_dir=artifacts.app)
    loaded = load_check_result(CODEBASE, artifacts.app).findings[0]
    assert (loaded.review.status, loaded.review.note) == ("dismissed", None)

    set_review(CODEBASE, finding.id, "unresolved", app_dir=artifacts.app)
    assert load_check_result(CODEBASE, artifacts.app).findings[0].review.status == "unresolved"


def test_review_is_not_carried_to_a_finding_about_changed_content(artifacts):
    finding = discount_finding(artifacts)
    save_check_result(ConsistencyCheckResult(codebase=CODEBASE, status="completed", findings=[finding]), artifacts.app)
    set_review(CODEBASE, finding.id, "dismissed", app_dir=artifacts.app)

    artifacts.source.write_text(DISCOUNT_CS + "\n// edited\n", encoding="utf-8")
    save_check_result(ConsistencyCheckResult(codebase=CODEBASE, status="completed", findings=[discount_finding(artifacts)]), artifacts.app)

    assert load_check_result(CODEBASE, artifacts.app).findings[0].review.status == "unresolved"
    assert finding.id in load_reviews(CODEBASE, artifacts.app)


def test_set_review_rejects_unknown_findings_and_statuses(artifacts):
    finding = discount_finding(artifacts)
    save_check_result(ConsistencyCheckResult(codebase=CODEBASE, status="completed", findings=[finding]), artifacts.app)

    with pytest.raises(ConsistencyStoreError, match="no finding"):
        set_review(CODEBASE, "0000000000000000", "reviewed", app_dir=artifacts.app)
    with pytest.raises(ConsistencyStoreError, match="Invalid review"):
        set_review(CODEBASE, finding.id, "correct", app_dir=artifacts.app)
    with pytest.raises(ConsistencyStoreError, match="no finding"):
        set_review("Billing", finding.id, "reviewed", app_dir=artifacts.app)


# ---------------------------------------------------------
# Corrections made for the checker (Part 2)
# ---------------------------------------------------------

def test_a_selected_check_is_stored_apart_from_the_full_check(artifacts):
    full = ConsistencyCheckResult(codebase=CODEBASE, status="completed", findings=[discount_finding(artifacts)])
    selected = ConsistencyCheckResult(codebase=CODEBASE, status="completed", scope="selected", selection=["src/Pricing.cs"])
    save_check_result(full, artifacts.app)
    save_check_result(selected, artifacts.app)

    assert load_check_result(CODEBASE, artifacts.app) == full
    assert load_check_result(CODEBASE, artifacts.app, scope="selected") == selected

    # A finding in either latest check can be reviewed.
    set_review(CODEBASE, full.findings[0].id, "reviewed", app_dir=artifacts.app)
    assert load_check_result(CODEBASE, artifacts.app).findings[0].review.status == "reviewed"


def test_selection_must_match_the_scope():
    with pytest.raises(ValidationError, match="record its selection"):
        ConsistencyCheckResult(codebase=CODEBASE, status="completed", scope="selected")
    with pytest.raises(ValidationError, match="no selection"):
        ConsistencyCheckResult(codebase=CODEBASE, status="completed", selection=["a.cs"])


def test_a_scope_mismatch_in_a_stored_file_is_refused(artifacts):
    selected = ConsistencyCheckResult(codebase=CODEBASE, status="completed", scope="selected", selection=["a.cs"])
    path = save_check_result(selected, artifacts.app)
    path.replace(path.with_name("consistency_result.json"))
    with pytest.raises(ConsistencyStoreError, match="selected check, not a full one"):
        load_check_result(CODEBASE, artifacts.app)


def test_undetermined_comparisons_make_a_check_partial(artifacts):
    rule = business_rule_ref(artifacts.root, 1, artifacts.app)
    undetermined = [Undetermined(artifact=rule, reason="Depends on TaxService, which was not supplied.")]
    with pytest.raises(ValidationError, match="partial, not completed"):
        ConsistencyCheckResult(codebase=CODEBASE, status="completed", undetermined=undetermined)
    result = ConsistencyCheckResult(codebase=CODEBASE, status="partial", undetermined=undetermined)
    assert result.outcome == "incomplete"


def test_a_missing_output_file_needs_no_id(env):
    assert ArtifactRef(kind="business_rule", status="missing", detail="validated_rules.json does not exist.").rule_id is None
    assert ArtifactRef(kind="unit_test", status="missing", detail="No unit tests exist.").test_id is None
    with pytest.raises(ValidationError, match="rule_id"):
        ArtifactRef(kind="business_rule", status="ambiguous", detail="d", candidates=["a", "b"])


def test_finding_id_ignores_artifact_order(artifacts):
    finding = discount_finding(artifacts)
    data = finding.model_dump()
    data.update(id="", claims=list(reversed(data["claims"])), artifacts=list(reversed(data["artifacts"])))
    assert Finding.model_validate(data).id == finding.id
