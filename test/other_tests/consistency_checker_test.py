"""
@file consistency_checker_test.py
@brief Offline tests for the Consistency Review checker (Part 2).
@details The model is a fake that returns scripted answers, so these tests check
what the checker sends, how it validates answers, and how it records results.
They say nothing about how well a real model detects disagreements: the fake
decides what is "inconsistent". No AI calls, network or code execution.
"""

import sys
import json
import os
import re
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
import pytest

import agent.consistency_checker as checker_module
from agent.consistency_checker import MAX_UNIT_CHARS, TEST_RESULTS_UNUSED, run_consistency_check
from agent.consistency_store import load_check_result, set_review
from agent.llm import DailyLimitError
from agent.structured_output.consistency_checker_output import (
    ArtifactClaimOutput, AssessmentOutput, ConsistencyAnalysisOutput, FindingOutput, SourceClaimOutput,
)
from backend.token_usage import current_run_id, track_command

CODEBASE = "Shop"

PRICING_CS = """namespace Shop
{
    public class Pricing
    {
        public decimal Discount(decimal price)
        {
            return price + price * 0.10m;
        }

        public decimal Tax(decimal price)
        {
            return price * 0.05m;
        }
    }
}
"""
ADD_LINE = "return price + price * 0.10m;"          # line 7

CART_CS = """namespace Shop
{
    public class Cart
    {
        public int Count(int items)
        {
            return items;
        }
    }
}
"""

REDUCES = "Applies a 10% discount, reducing the price."
INCREASES = "Increases the price by 10%."
RULE_REDUCES = "A 10% discount reduces the price."
RULE_INCREASES = "The Discount operation raises the price by 10%."
ASSERT_90 = "Assert.Equal(90m, new Pricing().Discount(100m));"
ASSERT_110 = "Assert.Equal(110m, new Pricing().Discount(100m));"


# ---------------------------------------------------------
# Fixture: a codebase and its pipeline outputs
# ---------------------------------------------------------

def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def age(path, seconds=100):
    stamp = os.path.getmtime(path) - seconds
    os.utime(path, (stamp, stamp))


def summary(root, rel, type_name, methods, text="Pricing calculations."):
    return {
        "path": str(root / rel),
        "summary": text,
        "dependencies": [], "functions": [], "relationships": [], "external_relationships": [],
        "types": [{
            "name": type_name, "kind": "class", "description": f"The {type_name} class.",
            "properties": [], "plantuml": "",
            "methods": [
                {"name": name, "description": description,
                 "parameters": [{"name": "x", "type": "decimal", "direction": "in"}]}
                for name, description in methods
            ],
        }],
        "business_rules": [],
    }


def rule(rule_id, text, files):
    return {
        "id": rule_id, "rule": text, "source_directory": "src", "source_file_paths": files,
        "explanation": {"evidence": {}, "reasoning": "r"},
        "provenance": [{"origin": "file", "source_file_paths": files}],
    }


def generated_test(rule_id, text, body):
    return {
        "id": rule_id, "rule": text, "imports": ["using Xunit;"], "source_directory": "src",
        "source_file_paths": ["src/Pricing.cs"],
        "unit_test": f"[Fact]\npublic void UT()\n{{\n    {body}\n}}",
    }


class Env:
    def __init__(self, tmp_path):
        self.app = tmp_path / "app"
        self.root = tmp_path / "targets" / CODEBASE
        (self.root / "src").mkdir(parents=True)
        self.app.mkdir()
        for name, text in (("Pricing.cs", PRICING_CS), ("Cart.cs", CART_CS)):
            path = self.root / "src" / name
            path.write_text(text, encoding="utf-8")
            age(path)
        out = self.app / "agent"
        self.summary_dir = out / "file_summary_agent_output" / CODEBASE
        self.rules_file = out / "BR_agent_output" / CODEBASE / "validated_rules.json"
        self.tests_dir = out / "UT_agent_output" / CODEBASE

    def write(self, pricing=REDUCES, rule_text=RULE_INCREASES, assertion=ASSERT_110, cart=True, tests=True):
        write_json(self.summary_dir / "Pricing-cs__1.json", summary(
            self.root, "src/Pricing.cs", "Pricing", [("Discount", pricing), ("Tax", "Adds 5% tax.")],
        ))
        rules = [rule(1, rule_text, ["src/Pricing.cs"])]
        if cart:
            write_json(self.summary_dir / "Cart-cs__1.json", summary(
                self.root, "src/Cart.cs", "Cart", [("Count", "Returns the number of items.")], text="Cart helpers.",
            ))
            rules.append(rule(2, "The item count is returned unchanged.", ["src/Cart.cs"]))
        write_json(self.rules_file, rules)
        if tests:
            write_json(self.tests_dir / "validated_tests.json", [generated_test(1, rule_text, assertion)])
        return self


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


# ---------------------------------------------------------
# Fake model
# ---------------------------------------------------------

def prompt_of(messages):
    return messages[-1][1]


def file_of(messages):
    return re.search(r"^File: (.+)$", prompt_of(messages), re.M).group(1)


def handles_of(messages):
    return re.search(r"^Generated artifacts to assess: (.*)$", prompt_of(messages), re.M).group(1).split(", ")


class FakeLLM:
    """Stands in for make_llm(): records every prompt and returns what respond() gives."""

    def __init__(self, respond=None):
        self.respond = respond or (lambda messages: answer(handles_of(messages)))
        self.calls = []

    def with_structured_output(self, schema):
        assert schema is ConsistencyAnalysisOutput
        return self

    def invoke(self, messages):
        self.calls.append(messages)
        reply = self.respond(messages)
        if isinstance(reply, Exception):
            raise reply
        return reply


def answer(handles, verdicts=None, findings=()):
    verdicts = verdicts or {}
    return ConsistencyAnalysisOutput(
        assessments=[AssessmentOutput(artifact=h, verdict=verdicts.get(h, "consistent"), reason="Checked.") for h in handles],
        findings=list(findings),
    )


def proposed(category, claims, excerpt=ADD_LINE, lines=(7, 7), title="Artifact disagrees with Discount"):
    return FindingOutput(
        category=category,
        title=title,
        explanation="The code adds 10% (100 -> 110); the artifact describes a reduction.",
        source=SourceClaimOutput(
            statement="Returns the price increased by 10% (110 for an input of 100).",
            excerpt=excerpt, line_start=lines[0], line_end=lines[1],
        ),
        claims=[ArtifactClaimOutput(artifact=h, statement=s, excerpt=e) for h, s, e in claims],
    )


SUMMARY_CLAIM = ("SUMMARY", "Reduces the price by 10%.", REDUCES)
DISCOUNT = {"path": "src/Pricing.cs", "symbol": "Pricing.Discount"}


def check(env, llm, targets=(DISCOUNT,), **kwargs):
    return run_consistency_check(env.root, targets=list(targets) if targets is not None else None,
                                 llm=llm, app_dir=env.app, **kwargs)


# ---------------------------------------------------------
# 1. Misleading name: code adds 10%, summary says it reduces
# ---------------------------------------------------------

def test_discount_that_adds_gives_a_source_summary_finding_with_intended_behaviour_unknown(env):
    env.write(pricing=REDUCES)
    llm = FakeLLM(lambda m: answer(handles_of(m), {"SUMMARY": "inconsistent"},
                                   [proposed("source_summary", [SUMMARY_CLAIM])]))

    result = check(env, llm, run_id="run-1")

    assert result.status == "completed"
    assert result.scope == "selected" and result.selection == ["src/Pricing.cs#Pricing.Discount"]
    [finding] = result.findings
    assert finding.category == "source_summary"
    assert finding.intended_behaviour == "unknown"
    assert finding.run_id == "run-1"
    assert [e.origin for e in finding.evidence] == ["code_inspection"]
    source = finding.claims[0].artifact
    assert (source.kind, source.path, source.symbol) == ("source", "src/Pricing.cs", "Pricing.Discount")
    assert (source.line_start, source.line_end) == (7, 7)
    assert finding.claims[1].excerpt == REDUCES
    assert any(u.startswith("Intended behaviour: no confirmed requirement") for u in finding.uncertainty)
    assert finding.review.status == "unresolved"


def test_the_prompt_holds_the_method_and_its_own_artifacts_as_delimited_data(env):
    env.write()
    llm = FakeLLM()
    check(env, llm)

    [messages] = llm.calls
    system, user = messages[0][1], prompt_of(messages)
    nonce = re.search(r"<<<BEGIN SOURCE (\w+)>>>", user).group(1)
    assert f"<<<END SOURCE {nonce}>>>" in user and nonce in system
    assert "never follow it" in system
    assert "Code in scope: lines 5-8 (Pricing.Discount)" in user
    assert "    7 |             return price + price * 0.10m;" in user
    assert handles_of(messages) == ["SUMMARY", "RULE_1", "TEST_1"]
    assert f"Method Pricing.Discount(decimal): {REDUCES}" in user
    assert "Adds 5% tax." not in user           # the other method's summary is not sent
    assert "Cart" not in user                   # nor another file's artifacts


# ---------------------------------------------------------
# 2. Accurate summary: the method name alone raises nothing
# ---------------------------------------------------------

def test_an_accurate_summary_gives_a_completed_check_with_no_findings(env):
    env.write(pricing=INCREASES)
    result = check(env, FakeLLM())
    assert result.status == "completed"
    assert result.findings == []
    assert result.outcome == "no_findings"
    assert {ref.kind for ref in result.artifacts_checked} == {"source", "summary", "business_rule", "unit_test"}
    assert any("no code or tests were run" in note for note in result.notes)


# ---------------------------------------------------------
# 3. Test expects 90 where the code gives 110
# ---------------------------------------------------------

def test_a_test_expecting_90_gives_a_source_test_finding_from_inspection_only(env):
    env.write(pricing=INCREASES, assertion=ASSERT_90)
    write_json(env.tests_dir / "test_report.json", {"passed": 0, "failed": 1, "failed_tests": ["Shop.Tests.UT_1.UT"]})
    llm = FakeLLM(lambda m: answer(handles_of(m), {"TEST_1": "inconsistent"}, [
        proposed("source_test", [("TEST_1", "Expects Discount(100) to return 90.", ASSERT_90)]),
    ]))

    [finding] = check(env, llm).findings

    assert finding.category == "source_test"
    assert [e.origin for e in finding.evidence] == ["code_inspection"]
    assert TEST_RESULTS_UNUSED in finding.uncertainty
    test_ref = next(a for a in finding.artifacts if a.kind == "unit_test")
    assert test_ref.test_id == "UT_1"


# ---------------------------------------------------------
# 4. Summary, rule and test repeat the same mistake
# ---------------------------------------------------------

def test_a_mistake_repeated_by_every_artifact_is_still_found_against_the_source(env):
    env.write(pricing=REDUCES, rule_text=RULE_REDUCES, assertion=ASSERT_90)
    llm = FakeLLM(lambda m: answer(
        handles_of(m), {"SUMMARY": "inconsistent", "RULE_1": "inconsistent", "TEST_1": "inconsistent"}, [
            proposed("source_summary", [SUMMARY_CLAIM]),
            proposed("source_rule", [("RULE_1", "Says the price is reduced.", RULE_REDUCES)]),
            proposed("source_test", [("TEST_1", "Expects 90.", ASSERT_90)]),
        ]))

    result = check(env, llm)

    assert result.status == "completed"
    assert sorted(f.category for f in result.findings) == ["source_rule", "source_summary", "source_test"]
    assert len({f.id for f in result.findings}) == 3
    for finding in result.findings:
        assert finding.claims[0].artifact.kind == "source"
        assert finding.claims[0].excerpt == ADD_LINE


# ---------------------------------------------------------
# 5. Missing context and unresolved links
# ---------------------------------------------------------

def test_missing_outputs_make_the_check_partial_and_are_named(env):
    write_json(env.summary_dir / "Pricing-cs__1.json", summary(env.root, "src/Pricing.cs", "Pricing", [("Discount", INCREASES)]))
    result = check(env, FakeLLM())
    assert result.status == "partial"
    assert result.outcome == "incomplete"
    details = " ".join(ref.detail for ref in result.missing_artifacts)
    assert "No validated business rules exist" in details
    assert "No generated unit tests exist" in details


def test_two_summaries_of_one_file_are_not_compared_but_the_rule_still_is(env):
    env.write(tests=False)
    write_json(env.summary_dir / "Pricing-cs__2.json", summary(env.root, "src/Pricing.cs", "Pricing", [("Discount", INCREASES)]))
    llm = FakeLLM()
    result = check(env, llm, targets=["src/Pricing.cs"])

    assert result.status == "partial"
    assert [ref.status for ref in result.unresolved_links] == ["ambiguous"]
    assert handles_of(llm.calls[0]) == ["RULE_1"]


@pytest.mark.parametrize("symbol, status", [("Pricing.Markup", "missing"), ("Discount", "resolved")])
def test_a_method_scope_is_used_only_when_it_resolves(env, symbol, status):
    env.write()
    llm = FakeLLM()
    result = check(env, llm, targets=[{"path": "src/Pricing.cs", "symbol": symbol}])
    if status == "missing":
        assert llm.calls == []
        assert "has no method Pricing.Markup" in result.missing_artifacts[0].detail
    else:
        assert result.status == "completed"


def test_overloaded_methods_are_reported_ambiguous_not_guessed(env):
    env.write()
    overloaded = PRICING_CS.replace(
        "        public decimal Tax(decimal price)",
        "        public decimal Discount(int price)\n        {\n            return price;\n        }\n\n        public decimal Tax(decimal price)",
    )
    (env.root / "src" / "Pricing.cs").write_text(overloaded, encoding="utf-8")
    llm = FakeLLM()
    result = check(env, llm)
    assert llm.calls == []
    [link] = result.unresolved_links
    assert link.status == "ambiguous" and len(link.candidates) == 2
    assert result.status == "partial"
    assert result.outcome == "incomplete"


def test_insufficient_evidence_is_undetermined_not_agreement(env):
    env.write()
    llm = FakeLLM(lambda m: answer(handles_of(m), {"RULE_1": "insufficient_evidence"}))
    result = check(env, llm)
    assert result.status == "partial"
    [item] = result.undetermined
    assert item.artifact.rule_id == 1


def test_not_applicable_rules_are_noted_not_counted_as_checked(env):
    env.write()
    llm = FakeLLM(lambda m: answer(handles_of(m), {"RULE_1": "not_applicable"}))
    result = check(env, llm)
    assert result.status == "completed"
    assert not any(ref.kind == "business_rule" for ref in result.artifacts_checked)
    assert any("RULE_1 was not compared" in note for note in result.notes)


def test_a_test_for_a_renumbered_rule_is_not_compared(env):
    env.write()
    write_json(env.tests_dir / "validated_tests.json", [generated_test(1, "Some older rule 1.", ASSERT_110)])
    llm = FakeLLM()
    result = check(env, llm)
    assert handles_of(llm.calls[0]) == ["SUMMARY", "RULE_1"]
    assert [ref.status for ref in result.unresolved_links] == ["stale"]


def test_folder_level_rules_are_not_assumed_to_apply_to_a_file(env):
    env.write()
    rules = json.loads(env.rules_file.read_text(encoding="utf-8"))
    folder = rule(3, "Prices are never negative.", [])
    folder["provenance"] = [{"origin": "directory_observed", "source_file_paths": []}]
    write_json(env.rules_file, rules + [folder])
    llm = FakeLLM()
    result = check(env, llm)
    assert "RULE_3" not in handles_of(llm.calls[0])
    assert any("1 folder-level business rule(s) for src" in note for note in result.notes)


def test_a_newer_source_file_is_a_caveat_on_findings_not_a_reason_to_skip(env):
    env.write()
    os.utime(env.root / "src" / "Pricing.cs")      # touched after the outputs were written
    for file in (env.summary_dir / "Pricing-cs__1.json", env.rules_file, env.tests_dir / "validated_tests.json"):
        age(file, 1000)
    llm = FakeLLM(lambda m: answer(handles_of(m), {"SUMMARY": "inconsistent"},
                                   [proposed("source_summary", [SUMMARY_CLAIM])]))
    result = check(env, llm)
    [finding] = result.findings
    assert any("SUMMARY: Possibly out of date" in u for u in finding.uncertainty)
    assert any("Possibly out of date" in note for note in result.notes)


def test_oversized_evidence_is_reported_not_truncated(env, monkeypatch):
    env.write()
    monkeypatch.setattr(checker_module, "MAX_UNIT_CHARS", 200)
    llm = FakeLLM()
    result = check(env, llm, targets=["src/Pricing.cs"])
    assert llm.calls == []
    assert result.status == "failed"
    assert "over the 200 limit" in result.errors[0] and "nothing was truncated" in result.errors[0]


def test_a_codebase_with_no_outputs_fails_explicitly(env):
    result = check(env, FakeLLM(), targets=None)
    assert result.status == "failed"
    assert "Nothing to check" in result.errors[-1]


def test_no_api_key_fails_explicitly(env, monkeypatch):
    env.write()
    def no_key():
        raise ValueError("No API key is selected.")
    monkeypatch.setattr(checker_module, "make_llm", no_key)
    result = check(env, None)
    assert result.status == "failed"
    assert "No API key is selected." in result.errors[0]


# ---------------------------------------------------------
# 6. Malformed answers and invented references
# ---------------------------------------------------------

@pytest.mark.parametrize("make, message", [
    (lambda h: answer(h, {"SUMMARY": "inconsistent"}, [proposed("source_summary", [("RULE_99", "x", RULE_INCREASES)])]),
     "cites 'RULE_99', which was not supplied"),
    (lambda h: answer(h, {"SUMMARY": "inconsistent"}, [proposed("source_summary", [("SUMMARY", "x", "Halves the price.")])]),
     "excerpt for SUMMARY does not appear"),
    (lambda h: answer(h, {"SUMMARY": "inconsistent"}, [proposed("source_summary", [SUMMARY_CLAIM], excerpt="return price - price * 0.10m;")]),
     "source excerpt does not appear"),
    (lambda h: answer(h, {"SUMMARY": "inconsistent"}, [proposed("source_summary", [SUMMARY_CLAIM], excerpt="return price * 0.05m;", lines=(12, 12))]),
     "source excerpt does not appear in the code in scope"),
    (lambda h: answer(h, {}, [proposed("source_summary", [SUMMARY_CLAIM])]),
     "which the model's own assessment calls consistent"),
    (lambda h: answer(h, {"SUMMARY": "inconsistent"}, [proposed("source_test", [SUMMARY_CLAIM])]),
     "must cite a TEST artifact"),
    (lambda h: answer(h, {"SUMMARY": "inconsistent"}, [proposed("source_summary", [("SUMMARY", "x", "10%")])]),
     "too short to verify"),
])
def test_invented_or_unverifiable_findings_are_rejected_with_an_error(env, make, message):
    env.write()
    result = check(env, FakeLLM(lambda m: make(handles_of(m))))
    assert result.findings == []
    assert result.status == "partial"
    assert result.outcome == "incomplete"
    assert any(message in error for error in result.errors), result.errors


def test_missing_duplicate_and_unknown_assessments_are_errors(env):
    env.write()
    def respond(m):
        out = answer(["SUMMARY", "SUMMARY", "RULE_7"])
        return out
    result = check(env, FakeLLM(respond))
    errors = " ".join(result.errors)
    # Nothing was actually compared, so this is a failed check, not "no findings".
    assert result.status == "failed"
    assert "assessed 'RULE_7', which was not supplied" in errors
    assert "assessed SUMMARY more than once" in errors
    assert "did not assess RULE_1" in errors and "did not assess TEST_1" in errors
    assert not any(ref.kind == "summary" for ref in result.artifacts_checked)


def test_inconsistent_without_a_finding_is_an_error(env):
    env.write()
    result = check(env, FakeLLM(lambda m: answer(handles_of(m), {"SUMMARY": "inconsistent"})))
    assert result.status == "partial"
    assert any("marked SUMMARY inconsistent but reported no finding" in e for e in result.errors)


def test_unreadable_answers_are_retried_once_then_reported(env):
    env.write()
    replies = iter([ValueError("bad json"), None])
    llm = FakeLLM(lambda m: next(replies))
    result = check(env, llm)
    assert len(llm.calls) == 2
    assert result.status == "failed"
    assert "could not be read after 2 attempts" in result.errors[0]

    replies2 = iter([ValueError("bad json"), answer(["SUMMARY", "RULE_1", "TEST_1"])])
    assert check(env, FakeLLM(lambda m: next(replies2))).status == "completed"


def test_a_dict_answer_must_match_the_schema_and_cannot_add_evidence_or_requirements(env):
    env.write()
    good = answer(["SUMMARY", "RULE_1", "TEST_1"]).model_dump()
    assert check(env, FakeLLM(lambda m: good)).status == "completed"

    forged = dict(good, intended_behaviour="conflicts_with_requirement", evidence=[{"origin": "execution"}])
    result = check(env, FakeLLM(lambda m: forged))
    assert result.status == "failed"
    assert "did not match the expected format" in result.errors[0]


def test_wrong_line_numbers_are_corrected_only_when_the_excerpt_is_unique(env):
    env.write()
    llm = FakeLLM(lambda m: answer(handles_of(m), {"SUMMARY": "inconsistent"},
                                   [proposed("source_summary", [SUMMARY_CLAIM], lines=(5, 5))]))
    [finding] = check(env, llm).findings
    source = finding.claims[0].artifact
    assert (source.line_start, source.line_end) == (7, 7)
    assert finding.evidence[0].excerpt.strip() == ADD_LINE


# ---------------------------------------------------------
# 7. Finding identity and reviews across reruns
# ---------------------------------------------------------

def summary_finding_llm(*findings):
    return FakeLLM(lambda m: answer(handles_of(m), {"SUMMARY": "inconsistent"}, list(findings)))


def test_an_unchanged_rerun_keeps_reviews_and_changed_content_needs_review_again(env):
    env.write()
    llm = summary_finding_llm(proposed("source_summary", [SUMMARY_CLAIM]))
    [first] = check(env, llm, save=True).findings
    set_review(CODEBASE, first.id, "reviewed", "Unconfirmed with the client.", app_dir=env.app)

    [again] = check(env, llm, save=True).findings
    assert again.id == first.id
    stored = load_check_result(CODEBASE, env.app, scope="selected").findings[0]
    assert (stored.review.status, stored.review.note) == ("reviewed", "Unconfirmed with the client.")

    env.write(pricing=REDUCES + " Callers rely on it.")
    llm2 = summary_finding_llm(proposed("source_summary", [("SUMMARY", "Reduces.", REDUCES)]))
    [changed] = check(env, llm2, save=True).findings
    assert changed.id != first.id
    assert load_check_result(CODEBASE, env.app, scope="selected").findings[0].review.status == "unresolved"


def test_distinct_disagreements_about_the_same_artifacts_get_distinct_ids(env):
    env.write()
    signature = "public decimal Discount(decimal price)"
    llm = summary_finding_llm(
        proposed("source_summary", [SUMMARY_CLAIM], title="Direction of change"),
        proposed("source_summary", [SUMMARY_CLAIM], excerpt=signature, lines=(5, 5), title="Second aspect"),
        proposed("source_summary", [SUMMARY_CLAIM], title="Same lines again"),
    )
    result = check(env, llm)
    assert result.status == "completed"
    assert len({f.id for f in result.findings}) == 3
    assert [f.discriminator for f in result.findings] == ["lines 7-7", "lines 5-5", "lines 7-7 #2"]


# ---------------------------------------------------------
# 8. Several files, codebases and runs
# ---------------------------------------------------------

def test_each_selected_file_is_compared_only_with_its_own_artifacts(env):
    env.write()
    llm = FakeLLM()
    result = check(env, llm, targets=["src/Pricing.cs", "src/Cart.cs", "src\\Cart.cs"])

    assert [file_of(m) for m in llm.calls] == ["src/Pricing.cs", "src/Cart.cs"]
    pricing, cart = (prompt_of(m) for m in llm.calls)
    assert "RULE_1" in pricing and "RULE_2" not in pricing
    assert "RULE_2" in cart and "RULE_1" not in cart and "TEST_1" not in cart
    assert result.selection == ["src/Pricing.cs", "src/Cart.cs"]


def test_a_full_check_covers_every_file_with_artifacts_and_is_stored_apart(env):
    env.write()
    llm = FakeLLM()
    full = check(env, llm, targets=None, save=True)
    assert full.scope == "full" and full.selection == []
    assert sorted(file_of(m) for m in llm.calls) == ["src/Cart.cs", "src/Pricing.cs"]

    check(env, FakeLLM(), save=True)          # a selected check afterwards
    assert load_check_result(CODEBASE, env.app).check_id == full.check_id


def test_a_full_check_skips_the_folders_the_crawler_skips(env):
    env.write()
    generated = env.root / "Shop.Tests"
    generated.mkdir()
    (generated / ".checkpoint-generated").write_text("", encoding="utf-8")
    (generated / "UT_1.cs").write_text("class UT_1 {}", encoding="utf-8")
    write_json(env.summary_dir / "UT_1-cs__1.json", summary(env.root, "Shop.Tests/UT_1.cs", "UT_1", []))

    llm = FakeLLM()
    result = check(env, llm, targets=None)
    assert "Shop.Tests/UT_1.cs" not in [file_of(m) for m in llm.calls]
    assert any("folders the crawler skips" in note for note in result.notes)


def test_another_codebases_outputs_are_never_used(env, tmp_path):
    env.write()
    other_root = tmp_path / "targets" / "Billing"
    (other_root / "src").mkdir(parents=True)
    write_json(env.app / "agent" / "file_summary_agent_output" / "Billing" / "Pricing-cs__1.json",
               summary(other_root, "src/Pricing.cs", "Pricing", [("Discount", "BILLING SUMMARY")]))
    llm = FakeLLM()
    result = check(env, llm)
    assert "BILLING SUMMARY" not in prompt_of(llm.calls[0])
    assert result.codebase == CODEBASE


def test_run_identity_is_explicit_or_taken_from_the_run_being_logged(env):
    env.write()
    assert check(env, FakeLLM()).run_id is None
    assert check(env, FakeLLM(), run_id="explicit").run_id == "explicit"

    with track_command(env.app, CODEBASE, "consistency_check"):
        active = current_run_id()
        result = check(env, FakeLLM())
    assert active and result.run_id == active
    assert current_run_id() is None


# ---------------------------------------------------------
# 9. One failure does not discard the rest
# ---------------------------------------------------------

class HTTPError(Exception):
    status_code = 400


def test_a_failed_comparison_keeps_the_findings_of_the_others(env):
    env.write()
    def respond(m):
        if file_of(m) == "src/Cart.cs":
            return HTTPError("Bad request")
        return answer(handles_of(m), {"SUMMARY": "inconsistent"}, [proposed("source_summary", [SUMMARY_CLAIM])])
    result = check(env, FakeLLM(respond), targets=["src/Pricing.cs", "src/Cart.cs"])

    assert result.status == "partial"
    assert len(result.findings) == 1
    assert result.errors == ["src/Cart.cs: not checked: the model call failed: Bad request"]


def test_a_daily_limit_stops_the_remaining_comparisons(env):
    env.write()
    llm = FakeLLM(lambda m: DailyLimitError("This API key has hit its daily limit."))
    result = check(env, llm, targets=["src/Pricing.cs", "src/Cart.cs"])
    assert len(llm.calls) == 1
    assert result.status == "failed"
    assert len(result.errors) == 2
    assert all("daily limit" in error for error in result.errors)
