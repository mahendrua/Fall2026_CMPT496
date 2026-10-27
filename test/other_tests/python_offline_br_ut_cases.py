"""
Offline Python BR/UT graph integration cases.

Not collected by default pytest discovery (filename is not test_*.py).
Run only via test_python_offline_br_ut.py's isolated subprocess, or by
passing this file path explicitly to pytest.

G1/G2 JSON is seeded, not produced by FileSummaryAgent or DirectoryAgent.
LLM and Chroma collection objects are fakes. pytest subprocesses are stubbed.
"""

import test.other_tests._chromadb_stub  # noqa: F401  # install before agent imports

import asyncio
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.BR_agent import BRAgent
from agent.BR_input_loader import deduplicate_rule_candidates, load_rule_candidates
from agent.python_test_harness import CONTEXT_OMISSION_MARKER, PythonTestRun
from agent.structured_output.BR_output import (
    CondenserOutput,
    CondenserRuleOutput,
    Explanation as BRExplanation,
    ValidatorOutput,
)
from agent.structured_output.UT_output import Explanation as UTExplanation
from agent.structured_output.UT_output import ValidatedRule
from agent.structured_output.UTV_output import UnitTest as UTVUnitTest
from agent.testing_language import detect_test_language

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "python_e2e"

FUNCTION_BODY = (
    "def test_double_returns_twice_the_value():\n"
    "    assert double(4) == 8\n"
)
SCRIPT_SAFE_BODY = (
    "def test_grade_script_reports_pass():\n"
    "    assert True\n"
)
UNSAFE_SCRIPT_BODY = (
    "def test_imports_student_code():\n"
    "    assert True\n"
)


class FakeCollection:
    def __init__(self, documents=None, metadatas=None):
        self.documents = documents or ["snippet"]
        self.metadatas = metadatas or [{"file": "math_lab.py", "path": "math_lab.py"}]

    def count(self):
        return max(len(self.documents), 1)

    def query(self, query_texts, n_results):
        limit = min(n_results, len(self.documents))
        return {
            "documents": [self.documents[:limit]],
            "metadatas": [self.metadatas[:limit]],
        }


class FakeStructuredLLM:
    def __init__(self, schema, llm):
        self.schema = schema
        self.llm = llm

    async def ainvoke(self, messages):
        prompt = messages[-1][1]
        return self.llm.handle(self.schema, prompt)


class FakeLLM:
    def __init__(self, handle):
        self._handle = handle
        self.prompts = []

    def with_structured_output(self, schema, **kwargs):
        return FakeStructuredLLM(schema, self)

    def handle(self, schema, prompt):
        self.prompts.append(prompt)
        return self._handle(schema, prompt)


def _copy_lab(tmp_path, name):
    dest = tmp_path / name
    shutil.copytree(FIXTURE_ROOT / name, dest)
    return dest


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def _file_rules(root, entries):
    return {
        str(root / rel): [
            {"rule": text, "source_file": str(root / rel)} for text in texts
        ]
        for rel, texts in entries.items()
    }


def _dir_rules(root, name, observed=(), inferred=()):
    return {
        str(root): {
            "directory_name": name,
            "directory_path": ".",
            "observed_rules": list(observed),
            "inferred_rules": list(inferred),
        }
    }


def _br_handle(schema, prompt, condense):
    if schema is CondenserOutput:
        listing = prompt.split("Business rules to condense:\n", 1)[1].split("\n\nMERGING", 1)[0]
        rules = [line.split(". ", 1)[1] for line in listing.splitlines() if line.strip()]
        reply = condense(rules)
        if isinstance(reply, Exception):
            raise reply
        return reply
    rule_text = prompt.split("\nRule: ", 1)[1].split("\n", 1)[0]
    return ValidatorOutput(
        decision="valid",
        explanation=BRExplanation(evidence={"math_lab.py": ["def double"]}, reasoning="Supported."),
    )


def _run_br(tmp_path, monkeypatch, lab, file_entries, dir_observed, dir_inferred, condense):
    app = tmp_path / "app"
    app.mkdir()
    monkeypatch.chdir(app)

    file_json = app / "agent" / "file_summary_agent_output" / lab.name / "business_rules" / "business_rules.json"
    dir_json = app / "agent" / "directory_agent_output" / lab.name / "business_rules" / "business_rules.json"
    _write_json(file_json, _file_rules(lab, file_entries))
    _write_json(dir_json, _dir_rules(lab, lab.name, dir_observed, dir_inferred))

    inputs = load_rule_candidates(lab, app_dir=app)
    candidates = deduplicate_rule_candidates(inputs.candidates)

    llm = FakeLLM(lambda schema, prompt: _br_handle(schema, prompt, condense))
    monkeypatch.setattr(
        BRAgent,
        "_load_collections",
        lambda self, name: (FakeCollection(), FakeCollection()),
    )
    BRAgent(model=llm).run(candidates, lab.name)
    out = app / "agent" / "BR_agent_output" / lab.name
    validated = json.loads((out / "validated_rules.json").read_text(encoding="utf-8"))
    discarded = json.loads((out / "discarded_rules.json").read_text(encoding="utf-8"))
    return candidates, validated, discarded, llm


def _validated_rule(**overrides):
    values = dict(
        id=1,
        rule="doubling a number returns twice the value",
        source_directory=".",
        source_file_paths=["math_lab.py"],
        explanation=UTExplanation(
            evidence={"math_lab.py": ["def double"]},
            reasoning="Supported.",
        ),
    )
    values.update(overrides)
    return ValidatedRule.model_validate(values)


def _invoke_ut(tmp_path, monkeypatch, lab, rules, handle, collection=None):
    pytest_dir = tmp_path / "generated_pytest"
    run_calls = []

    def fake_run_tests(**kwargs):
        run_calls.append(kwargs)
        return PythonTestRun(kind="Unit", passed=0, failed=0)

    monkeypatch.setattr(
        "agent.python_test_harness.test_dir_for",
        lambda *args, **kwargs: str(pytest_dir),
    )
    monkeypatch.setattr("agent.python_test_harness.run_tests", fake_run_tests)
    monkeypatch.setattr(
        "agent.python_test_harness.write_tests",
        lambda *args, **kwargs: None,
    )

    from agent.UT_agent import DEFAULT_CODEBASE_K, DEFAULT_FILE_SUMMARY_K, UTAgent

    code = collection or FakeCollection(
        documents=[(lab / rules[0].source_file_paths[0]).read_text(encoding="utf-8")],
        metadatas=[{"file": rules[0].source_file_paths[0], "path": rules[0].source_file_paths[0]}],
    )
    agent = UTAgent(model=FakeLLM(handle))
    output_dir = tmp_path / "ut_out"
    state = {
        "target_language": detect_test_language([rule.source_file_paths for rule in rules]),
        "validated_rules": rules,
        "unit_tests": [],
        "rule_contexts": {},
        "test_imports": set(),
        "codebase_k": DEFAULT_CODEBASE_K,
        "file_summary_k": DEFAULT_FILE_SUMMARY_K,
        "code_collection": code,
        "summary_collection": FakeCollection(
            documents=["summary"],
            metadatas=[{"file": rules[0].source_file_paths[0], "path": rules[0].source_file_paths[0]}],
        ),
        "codebase_name": lab.name,
        "codebase_path": str(lab),
        "output_directory": str(output_dir),
    }
    agent._loop = asyncio.new_event_loop()
    try:
        final = agent.graph.invoke(state)
    finally:
        agent._loop.close()
        agent._loop = None
    return final, output_dir / lab.name, run_calls, agent.llm


def test_seeded_g1_g2_python_paths_and_paraphrase_merge(tmp_path, monkeypatch):
    """Seeded G1/G2 JSON tests loading and BR graph, not FileSummary/Directory generation."""
    lab = _copy_lab(tmp_path, "function_lab")

    def condense(rules):
        if any("customer" in rule for rule in rules):
            numbers = [index + 1 for index, rule in enumerate(rules) if "customer" in rule]
            others = [
                CondenserRuleOutput(rule=rule, source_rule_numbers=[index + 1])
                for index, rule in enumerate(rules)
                if "customer" not in rule
            ]
            return CondenserOutput(
                condensed_rules=[
                    CondenserRuleOutput(
                        rule="Every order must have a customer.",
                        source_rule_numbers=numbers,
                    ),
                    *others,
                ]
            )
        return CondenserOutput(
            condensed_rules=[
                CondenserRuleOutput(rule=rule, source_rule_numbers=[index + 1])
                for index, rule in enumerate(rules)
            ]
        )

    candidates, validated, discarded, _llm = _run_br(
        tmp_path,
        monkeypatch,
        lab,
        {
            "math_lab.py": [
                "Orders need a customer.",
                "doubling a number returns twice the value",
            ]
        },
        ["Every order must have a customer."],
        ["Inference: grades are recorded."],
        condense,
    )
    texts = {item.rule for item in candidates}
    assert "Orders need a customer." in texts or "Every order must have a customer." in texts
    assert {rule["id"] for rule in validated + discarded} == set(
        range(1, len(validated) + len(discarded) + 1)
    )
    customer = next(rule for rule in validated if "customer" in rule["rule"])
    assert customer["source_file_paths"] == ["math_lab.py"]
    origins = [item["origin"] for item in customer["provenance"]]
    assert "file" in origins
    assert "directory_observed" in origins


def test_conflicting_age_thresholds_survive_unsafe_condenser_merge(tmp_path, monkeypatch):
    lab = _copy_lab(tmp_path, "function_lab")

    def condense(rules):
        return CondenserOutput(
            condensed_rules=[
                CondenserRuleOutput(
                    rule="A person must meet a minimum age of 18 or 21.",
                    source_rule_numbers=list(range(1, len(rules) + 1)),
                )
            ]
        )

    _candidates, validated, discarded, _llm = _run_br(
        tmp_path,
        monkeypatch,
        lab,
        {
            "math_lab.py": [
                "A person must be at least 18 years old.",
                "A person must be at least 21 years old.",
            ]
        },
        [],
        [],
        condense,
    )
    texts = {rule["rule"] for rule in validated + discarded}
    assert "A person must be at least 18 years old." in texts
    assert "A person must be at least 21 years old." in texts
    assert "18 or 21" not in "".join(texts)
    assert [rule["id"] for rule in validated + discarded] == [1, 2]


def test_invalid_condenser_indexes_keep_original_group(tmp_path, monkeypatch):
    lab = _copy_lab(tmp_path, "function_lab")

    def condense(rules):
        return CondenserOutput(
            condensed_rules=[
                CondenserRuleOutput(rule="Merged incorrectly.", source_rule_numbers=[1, 2, 9])
            ]
        )

    _candidates, validated, discarded, _llm = _run_br(
        tmp_path,
        monkeypatch,
        lab,
        {
            "math_lab.py": [
                "A person must be at least 18 years old.",
                "A person must be at least 21 years old.",
            ]
        },
        [],
        [],
        condense,
    )
    texts = {rule["rule"] for rule in validated + discarded}
    assert texts == {
        "A person must be at least 18 years old.",
        "A person must be at least 21 years old.",
    }


def test_overlapping_condenser_indexes_keep_original_group(tmp_path, monkeypatch):
    lab = _copy_lab(tmp_path, "function_lab")

    def condense(rules):
        return CondenserOutput(
            condensed_rules=[
                CondenserRuleOutput(rule="First.", source_rule_numbers=[1, 2]),
                CondenserRuleOutput(rule="Second.", source_rule_numbers=[2]),
            ]
        )

    _candidates, validated, discarded, _llm = _run_br(
        tmp_path,
        monkeypatch,
        lab,
        {
            "math_lab.py": [
                "A person must be at least 18 years old.",
                "A person must be at least 21 years old.",
            ]
        },
        [],
        [],
        condense,
    )
    texts = {rule["rule"] for rule in validated + discarded}
    assert "A person must be at least 18 years old." in texts
    assert "A person must be at least 21 years old." in texts


def test_g2_only_empty_paths_cannot_select_python_language():
    """Known UT limitation: language detection requires source file suffixes."""
    with pytest.raises(ValueError, match="do not identify source files"):
        detect_test_language([[]])
    with pytest.raises(ValueError, match="do not identify source files"):
        detect_test_language([[""]])


def test_function_lab_writer_sidecar_and_accepted_runner_state(tmp_path, monkeypatch):
    lab = _copy_lab(tmp_path, "function_lab")
    rules = [
        _validated_rule(id=1),
        _validated_rule(
            id=2,
            rule="classify uses a numeric branch",
            source_file_paths=["math_lab.py"],
        ),
    ]

    def handle(schema, prompt):
        rule_id = int(prompt.split("- ID: ", 1)[1].split("\n", 1)[0])
        if rule_id == 2:
            body = "x" * 40_001
        else:
            body = FUNCTION_BODY
        return SimpleNamespace(imports=["from math_lab import double"], unit_test=body)

    final, out_dir, run_calls, llm = _invoke_ut(tmp_path, monkeypatch, lab, rules, handle)
    saved = json.loads((out_dir / "unit_tests.json").read_text(encoding="utf-8"))
    dropped = json.loads((out_dir / "dropped_generated_tests.json").read_text(encoding="utf-8"))
    assert [item["id"] for item in saved] == [1]
    assert saved[0]["test_mode"] == "function"
    assert saved[0]["source_file_paths"] == ["math_lab.py"]
    assert saved[0]["unit_test"] == FUNCTION_BODY
    assert dropped[0]["id"] == 2
    assert dropped[0]["character_count"] == 40_001
    assert "rule 2" in dropped[0]["reason"]
    assert "unit_test" not in dropped[0]
    assert [test.id for test in final["unit_tests"]] == [1]
    parsed = [UTVUnitTest.model_validate(item) for item in saved]
    assert parsed[0].test_mode == "function"
    assert any("python" in prompt for prompt in llm.prompts)
    assert len(run_calls) == 1


def test_generation_prompt_applies_python_context_budget(tmp_path, monkeypatch):
    lab = _copy_lab(tmp_path, "function_lab")
    huge = "H" * 20_000
    collection = FakeCollection(
        documents=[huge],
        metadatas=[{"file": "math_lab.py", "path": "math_lab.py"}],
    )

    def handle(schema, prompt):
        assert huge not in prompt
        assert CONTEXT_OMISSION_MARKER in prompt or len(prompt) < 30_000
        return SimpleNamespace(imports=["from math_lab import double"], unit_test=FUNCTION_BODY)

    _invoke_ut(
        tmp_path,
        monkeypatch,
        lab,
        [_validated_rule()],
        handle,
        collection=collection,
    )


def test_script_mode_and_unsafe_sibling_without_execution(tmp_path, monkeypatch):
    lab = _copy_lab(tmp_path, "script_lab")
    rules = [
        _validated_rule(
            id=1,
            rule="a score of 50 or more prints PASS",
            source_file_paths=["grade_script.py"],
        ),
        _validated_rule(
            id=2,
            rule="unsafe companion",
            source_file_paths=["grade_script.py"],
        ),
    ]

    def handle(schema, prompt):
        rule_id = int(prompt.split("- ID: ", 1)[1].split("\n", 1)[0])
        if rule_id == 1:
            return SimpleNamespace(
                imports=["import subprocess", "import sys"],
                unit_test=SCRIPT_SAFE_BODY,
            )
        return SimpleNamespace(
            imports=["from student_lab import fn"],
            unit_test=UNSAFE_SCRIPT_BODY,
        )

    final, out_dir, run_calls, _llm = _invoke_ut(tmp_path, monkeypatch, lab, rules, handle)
    saved = json.loads((out_dir / "unit_tests.json").read_text(encoding="utf-8"))
    assert {item["test_mode"] for item in saved} == {"script"}
    assert all(item["source_file_paths"] == ["grade_script.py"] for item in saved)
    run = final["test_run"]
    assert isinstance(run, PythonTestRun)
    assert any("may only import" in (case.reason or "") for case in run.dropped)
    assert run.needs_attention() is True


def test_identical_bodies_are_deduplicated_before_stubbed_run(tmp_path, monkeypatch):
    lab = _copy_lab(tmp_path, "function_lab")
    rules = [_validated_rule(id=1), _validated_rule(id=2, rule="same doubling rule copy")]

    def handle(schema, prompt):
        return SimpleNamespace(imports=["from math_lab import double"], unit_test=FUNCTION_BODY)

    final, out_dir, run_calls, _llm = _invoke_ut(tmp_path, monkeypatch, lab, rules, handle)
    saved = json.loads((out_dir / "unit_tests.json").read_text(encoding="utf-8"))
    assert [item["id"] for item in saved] == [1, 2]
    run = final["test_run"]
    assert any("duplicate" in (case.reason or "") for case in run.dropped)
    assert len(run.dropped) >= 1


def test_all_oversized_writes_empty_json_without_false_success(tmp_path, monkeypatch):
    lab = _copy_lab(tmp_path, "function_lab")

    def handle(schema, prompt):
        return SimpleNamespace(imports=[], unit_test="z" * 40_001)

    final, out_dir, run_calls, _llm = _invoke_ut(
        tmp_path,
        monkeypatch,
        lab,
        [_validated_rule()],
        handle,
    )
    saved = json.loads((out_dir / "unit_tests.json").read_text(encoding="utf-8"))
    dropped = json.loads((out_dir / "dropped_generated_tests.json").read_text(encoding="utf-8"))
    assert saved == []
    assert dropped[0]["id"] == 1
    assert final["unit_tests"] == []
    run = final["test_run"]
    assert run.kept == []
    assert run.needs_attention() is True
    assert run_calls == []


def test_multi_file_source_paths_reach_unit_tests_json(tmp_path, monkeypatch):
    lab = _copy_lab(tmp_path, "multi_lab")
    rules = [
        _validated_rule(
            id=1,
            rule="GST is five percent of the amount",
            source_file_paths=["tax.py", "rates.py"],
        )
    ]

    def handle(schema, prompt):
        return SimpleNamespace(
            imports=["from tax import gst_on"],
            unit_test="def test_gst_on_uses_rate():\n    assert gst_on(100) == 5\n",
        )

    _final, out_dir, _calls, _llm = _invoke_ut(tmp_path, monkeypatch, lab, rules, handle)
    saved = json.loads((out_dir / "unit_tests.json").read_text(encoding="utf-8"))
    parsed = UTVUnitTest.model_validate(saved[0])
    assert parsed.source_file_paths == ["tax.py", "rates.py"]
    assert parsed.test_mode == "function"


def test_utv_loader_filename_matches_production_default():
    source = (
        Path(__file__).resolve().parents[2] / "backend" / "commands.py"
    ).read_text(encoding="utf-8")
    assert ' / "UT_agent_output"' in source
    assert ' / "unit_tests.json"' in source

