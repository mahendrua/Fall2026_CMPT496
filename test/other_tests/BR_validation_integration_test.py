"""
@file BR_validation_integration_test.py
@brief Offline integration tests for combined business rule validation (US-028).
@details Runs the real loader, duplicate merging, Commands.validate_business_rules(),
full_pipeline() call path and BRAgent graph (condenser, retriever, validator, writer).
Only the LLM (make_llm) and the vector stores (_load_collections) are replaced with
fakes, so no AI calls or databases are used. Fake responses are deterministic.
"""

import sys
import json
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
import pytest

import agent.BR_agent as br_agent_module
import backend.commands as commands_module
from backend.dispatcher import CommandDispatcher
from agent.BR_agent import BRAgent
from agent.BR_input_loader import load_rule_candidates, merge_rule_candidates
from agent.structured_output.BR_output import (
    RuleCandidate, CondenserOutput, CondenserRuleOutput, ValidatorOutput, Explanation,
    ValidatedRule as BRValidatedRule, DiscardedRule,
)
from agent.structured_output.UT_output import ValidatedRule as UTValidatedRule
from agent.structured_output.file_summary_output import BusinessRule

CODEBASE = "Shop"


# ---------------------------------------------------------
# Fakes for the external boundaries (LLM and vector stores)
# ---------------------------------------------------------

def parse_condenser_prompt(prompt):
    directory = prompt.split("Directory: ", 1)[1].split("\n", 1)[0]
    listing = prompt.split("Business rules to condense:\n", 1)[1].split("\n\nMERGING", 1)[0]
    rules = [line.split(". ", 1)[1] for line in listing.splitlines() if line.strip()]
    return directory, rules


def keep_all(directory, rules):
    return CondenserOutput(condensed_rules=[
        CondenserRuleOutput(rule=rule, source_rule_numbers=[i + 1]) for i, rule in enumerate(rules)
    ])


def default_decision(rule_text):
    if rule_text.startswith("Inference:"):
        return ValidatorOutput(decision="discard", discard_reason="No code evidence found.")
    return ValidatorOutput(decision="valid", explanation=Explanation(evidence={"A.cs": ["check()"]}, reasoning="Enforced."))


class FakeStructuredLLM:
    def __init__(self, schema, llm):
        self.schema, self.llm = schema, llm

    async def ainvoke(self, messages):
        prompt = messages[-1][1]
        if self.schema is CondenserOutput:
            directory, rules = parse_condenser_prompt(prompt)
            self.llm.condensed_groups.append((directory, rules))
            reply = self.llm.condense(directory, rules)
            if isinstance(reply, Exception):
                raise reply
            return reply
        rule_text = prompt.split("\nRule: ", 1)[1].split("\n", 1)[0]
        self.llm.validated_texts.append(rule_text)
        reply = self.llm.decide(rule_text)
        if isinstance(reply, Exception):
            raise reply
        return reply


class FakeLLM:
    def __init__(self, condense=keep_all, decide=default_decision):
        self.condense, self.decide = condense, decide
        self.condensed_groups, self.validated_texts = [], []

    def with_structured_output(self, schema):
        return FakeStructuredLLM(schema, self)


class FakeCollection:
    def count(self):
        return 1

    def query(self, query_texts, n_results):
        return {"documents": [["snippet"]], "metadatas": [[{"file": "src/A.cs", "path": "src/A.cs"}]]}


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Temporary app dir (also the working directory, where BRAgent writes) with fakes installed."""
    app = tmp_path / "app"
    root = tmp_path / "targets" / CODEBASE
    (root / "src" / "Models").mkdir(parents=True)
    app.mkdir()
    monkeypatch.chdir(app)

    llm = FakeLLM()
    db_loads = []

    def load_collections(self, codebase_name):
        db_loads.append(codebase_name)
        return FakeCollection(), FakeCollection()

    monkeypatch.setattr(br_agent_module, "make_llm", lambda: llm)
    monkeypatch.setattr(BRAgent, "_load_collections", load_collections)

    commands = commands_module.Commands()
    commands.app_dir = app

    class Env:
        pass
    e = Env()
    e.app, e.root, e.llm, e.db_loads, e.commands = app, root, llm, db_loads, commands
    e.file_json = app / "agent" / "file_summary_agent_output" / CODEBASE / "business_rules" / "business_rules.json"
    e.dir_json = app / "agent" / "directory_agent_output" / CODEBASE / "business_rules" / "business_rules.json"
    e.out_dir = app / "agent" / "BR_agent_output" / CODEBASE
    return e


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def file_rules(root, entries):
    """entries: {relative file path: [rule text, ...]} -> file-level output keyed by absolute Windows-style paths."""
    return {
        str(root / rel).replace("/", "\\"): [{"rule": text, "source_file": str(root / rel)} for text in texts]
        for rel, texts in entries.items()
    }


def dir_rules(root, entries):
    """entries: {relative dir: (observed, inferred)} -> directory-level output keyed by absolute paths."""
    return {
        str(root if rel == "." else root / rel): {
            "directory_name": Path(rel).name or CODEBASE,
            "directory_path": rel,
            "observed_rules": list(observed),
            "inferred_rules": list(inferred),
        }
        for rel, (observed, inferred) in entries.items()
    }


def read_outputs(env):
    validated = json.loads((env.out_dir / "validated_rules.json").read_text(encoding="utf-8"))
    discarded = json.loads((env.out_dir / "discarded_rules.json").read_text(encoding="utf-8"))
    return validated, discarded


def by_rule(rules):
    return {r["rule"]: r for r in rules}


def records(rule):
    return [(p["origin"], p["source_file_paths"]) for p in rule["provenance"]]


# ---------------------------------------------------------
# Individual validation (Commands.validate_business_rules)
# ---------------------------------------------------------

def test_file_and_folder_rules_reach_validator_with_provenance(env):
    write_json(env.file_json, file_rules(env.root, {
        "src/A.cs": ["Orders need a customer.", "Orders need a customer."],   # exact duplicate
        "src/B.cs": ["Stock cannot go negative."],
    }))
    write_json(env.dir_json, dir_rules(env.root, {
        "src": (["Orders need a customer."], ["Inference: Orders are audited."]),
        ".": (["Staff must log in."], []),
    }))

    result = env.commands.validate_business_rules(str(env.root))

    assert result["success"], result
    assert "warning" not in result["result"]
    assert sorted(env.llm.validated_texts) == sorted([
        "Orders need a customer.", "Stock cannot go negative.",
        "Inference: Orders are audited.", "Staff must log in.",
    ])

    validated, discarded = read_outputs(env)
    rules = by_rule(validated + discarded)
    assert len(validated) + len(discarded) == 4

    merged = rules["Orders need a customer."]
    assert merged["source_directory"] == "src"
    assert merged["source_file_paths"] == ["src/A.cs"]
    assert records(merged) == [("file", ["src/A.cs"]), ("directory_observed", [])]

    assert records(rules["Stock cannot go negative."]) == [("file", ["src/B.cs"])]
    assert rules["Staff must log in."]["source_directory"] == "."
    assert records(rules["Staff must log in."]) == [("directory_observed", [])]

    inferred = rules["Inference: Orders are audited."]
    assert inferred in discarded
    assert inferred["source_directory"] == "src"
    assert inferred["source_file_paths"] == []
    assert records(inferred) == [("directory_inferred", [])]


def test_folder_only_defaults_validate_observed_and_inferred(env):
    write_json(env.dir_json, dir_rules(env.root, {
        ".": (["Staff must log in."], ["Inference: Rentals are tracked."]),
    }))

    result = env.commands.validate_business_rules(str(env.root))

    assert result["success"], result
    assert "No file-level business rules were found" in result["result"]["warning"]
    validated, discarded = read_outputs(env)
    assert [(r["rule"], r["source_directory"], records(r)) for r in validated] == [
        ("Staff must log in.", ".", [("directory_observed", [])]),
    ]
    assert [(r["rule"], r["source_directory"], records(r)) for r in discarded] == [
        ("Inference: Rentals are tracked.", ".", [("directory_inferred", [])]),
    ]


def test_file_only_defaults_still_work(env):
    write_json(env.file_json, file_rules(env.root, {"src/A.cs": ["Orders need a customer."]}))

    result = env.commands.validate_business_rules(str(env.root))

    assert result["success"], result
    assert "No directory-level business rules were found" in result["result"]["warning"]
    validated, _ = read_outputs(env)
    assert [(r["rule"], r["source_file_paths"], records(r)) for r in validated] == [
        ("Orders need a customer.", ["src/A.cs"], [("file", ["src/A.cs"])]),
    ]


def test_nested_and_root_directories_are_grouped_separately(env):
    write_json(env.file_json, file_rules(env.root, {
        "src/Models/Order.cs": ["Models rule 1."],
        "src/Order.cs": ["Src rule 1."],
        "Program.cs": ["Root rule 1."],
    }))
    write_json(env.dir_json, dir_rules(env.root, {
        "src/Models": (["Models rule 2."], []),
        "src": (["Src rule 2."], []),
        ".": (["Root rule 2."], []),
    }))

    assert env.commands.validate_business_rules(str(env.root))["success"]

    assert sorted(env.llm.condensed_groups) == [
        (".", ["Root rule 1.", "Root rule 2."]),
        ("src", ["Src rule 1.", "Src rule 2."]),
        ("src/Models", ["Models rule 1.", "Models rule 2."]),
    ]
    validated, _ = read_outputs(env)
    assert {r["rule"]: r["source_directory"] for r in validated} == {
        "Root rule 1.": ".", "Root rule 2.": ".",
        "Src rule 1.": "src", "Src rule 2.": "src",
        "Models rule 1.": "src/Models", "Models rule 2.": "src/Models",
    }


def test_real_format_file_keys_merge_with_matching_folder_rule(env):
    # FileSummaryAgent keys rules by the LLM-written path (often just the file name);
    # source_file holds the real path, as in the outputs under agent/file_summary_agent_output.
    real = str(env.root / "src" / "Order.cs")
    write_json(env.file_json, {"Order.cs": [{"rule": "Orders need a customer.", "source_file": real}]})
    write_json(env.dir_json, dir_rules(env.root, {"src": (["Orders need a customer."], [])}))

    assert env.commands.validate_business_rules(str(env.root))["success"]

    assert env.llm.validated_texts == ["Orders need a customer."]
    (rule,) = read_outputs(env)[0]
    assert rule["source_directory"] == "src"
    assert rule["source_file_paths"] == ["src/Order.cs"]
    assert records(rule) == [("file", ["src/Order.cs"]), ("directory_observed", [])]


def test_dispatcher_route_with_ui_arguments(env):
    dispatcher = CommandDispatcher()
    assert dispatcher.routes["validate_business_rules"].__func__ is commands_module.Commands.validate_business_rules

    # Point the route at the test Commands (temporary app dir) and send what the UI sends.
    dispatcher.commands = env.commands
    dispatcher.error_log = []
    dispatcher.routes["validate_business_rules"] = env.commands.validate_business_rules
    write_json(env.dir_json, dir_rules(env.root, {".": (["Folder rule."], [])}))

    result = dispatcher.dispatch("validate_business_rules", request_id="r1", codebase=str(env.root))

    assert result["success"] and result["request_id"] == "r1"
    assert env.llm.validated_texts == ["Folder rule."]


# ---------------------------------------------------------
# Every validator outcome keeps provenance
# ---------------------------------------------------------

def test_final_pass_discard_keeps_provenance(env):
    env.llm.decide = lambda text: ValidatorOutput(decision="need_more_context")
    write_json(env.dir_json, dir_rules(env.root, {"src": ([], ["Inference: Needs more code."])}))

    assert env.commands.validate_business_rules(str(env.root))["success"]

    assert env.llm.validated_texts == ["Inference: Needs more code."] * 2   # first pass + final pass
    validated, discarded = read_outputs(env)
    assert validated == []
    (rule,) = discarded
    assert rule["reason"] == "Insufficient evidence after maximum context retrieval."
    assert rule["source_directory"] == "src"
    assert records(rule) == [("directory_inferred", [])]


def test_validator_error_discard_keeps_sources_and_provenance(env):
    env.llm.decide = lambda text: RuntimeError("model unavailable")
    write_json(env.file_json, file_rules(env.root, {"src/A.cs": ["Rule A."]}))

    assert env.commands.validate_business_rules(str(env.root))["success"]

    (rule,) = read_outputs(env)[1]
    assert rule["reason"].startswith("Validation failed with error: model unavailable")
    assert rule["source_file_paths"] == ["src/A.cs"]
    assert records(rule) == [("file", ["src/A.cs"])]


# ---------------------------------------------------------
# Provenance through semantic condensation
# ---------------------------------------------------------

def test_condensed_rules_keep_only_their_sources(env):
    def merge_first_two(directory, rules):
        return CondenserOutput(condensed_rules=[
            CondenserRuleOutput(rule="Orders must have a customer.", source_rule_numbers=[1, 2]),
            CondenserRuleOutput(rule=rules[2], source_rule_numbers=[3]),
        ])
    env.llm.condense = merge_first_two
    write_json(env.file_json, file_rules(env.root, {
        "src/A.cs": ["Orders need a customer."],
        "src/B.cs": ["An order requires a customer."],
        "src/C.cs": ["Stock cannot go negative."],
    }))

    assert env.commands.validate_business_rules(str(env.root))["success"]

    rules = by_rule(read_outputs(env)[0])
    assert set(rules) == {"Orders must have a customer.", "Stock cannot go negative."}
    merged = rules["Orders must have a customer."]
    assert merged["source_file_paths"] == ["src/A.cs", "src/B.cs"]
    assert records(merged) == [("file", ["src/A.cs"]), ("file", ["src/B.cs"])]
    assert rules["Stock cannot go negative."]["source_file_paths"] == ["src/C.cs"]
    assert records(rules["Stock cannot go negative."]) == [("file", ["src/C.cs"])]


def test_folder_rule_merged_by_condenser_keeps_folder_origin(env):
    env.llm.condense = lambda directory, rules: CondenserOutput(condensed_rules=[
        CondenserRuleOutput(rule="Orders must have a customer.", source_rule_numbers=[1, 2]),
    ])
    write_json(env.file_json, file_rules(env.root, {"src/A.cs": ["Orders need a customer."]}))
    write_json(env.dir_json, dir_rules(env.root, {"src": ([], ["Inference: Every order has a customer."])}))

    assert env.commands.validate_business_rules(str(env.root))["success"]

    (rule,) = read_outputs(env)[0]
    assert rule["source_file_paths"] == ["src/A.cs"]
    assert records(rule) == [("file", ["src/A.cs"]), ("directory_inferred", [])]


@pytest.mark.parametrize("reply", [
    CondenserOutput(condensed_rules=[CondenserRuleOutput(rule="Merged.", source_rule_numbers=[1])]),      # rule 2 uncovered
    CondenserOutput(condensed_rules=[CondenserRuleOutput(rule="Merged.", source_rule_numbers=[1, 2, 9])]),  # out of range
    CondenserOutput(condensed_rules=[CondenserRuleOutput(rule="Merged.", source_rule_numbers=[])]),       # no sources
    CondenserOutput(condensed_rules=[]),                                                                  # nothing returned
    RuntimeError("condenser unavailable"),                                                                # LLM error
])
def test_untrustworthy_condenser_output_passes_rules_through(env, reply):
    env.llm.condense = lambda directory, rules: reply
    write_json(env.file_json, file_rules(env.root, {"src/A.cs": ["Rule A."]}))
    write_json(env.dir_json, dir_rules(env.root, {"src": (["Rule B."], [])}))

    assert env.commands.validate_business_rules(str(env.root))["success"]

    rules = by_rule(read_outputs(env)[0])
    assert set(rules) == {"Rule A.", "Rule B."}
    assert records(rules["Rule A."]) == [("file", ["src/A.cs"])]
    assert records(rules["Rule B."]) == [("directory_observed", [])]


# ---------------------------------------------------------
# Custom paths, missing and invalid inputs, empty input
# ---------------------------------------------------------

def test_custom_rules_path_positional_does_not_add_default_folder_rules(env):
    write_json(env.dir_json, dir_rules(env.root, {".": (["Default folder rule."], [])}))
    custom = write_json(env.app / "custom.json", {"src/A.cs": [{"rule": "Custom rule.", "source_file": None}]})

    result = env.commands.validate_business_rules(str(env.root), str(custom), True)

    assert result["success"], result
    assert "warning" not in result["result"]
    assert env.llm.validated_texts == ["Custom rule."]


def test_explicit_directory_rules_path(env):
    write_json(env.file_json, file_rules(env.root, {"src/A.cs": ["Default file rule."]}))
    custom = write_json(env.app / "folders.json", dir_rules(env.root, {"src": (["Custom folder rule."], [])}))

    result = env.commands.validate_business_rules(str(env.root), directory_rules_path=str(custom))

    assert result["success"], result
    assert env.llm.validated_texts == ["Custom folder rule."]


def test_both_default_inputs_missing(env):
    result = env.commands.validate_business_rules(str(env.root))

    assert not result["success"]
    assert "No business rule inputs found" in result["error"]
    assert str(env.file_json) in result["error"] and str(env.dir_json) in result["error"]


@pytest.mark.parametrize("kwargs, expected", [
    ({"rules_path": "missing.json"}, "File-level business rules not found"),
    ({"directory_rules_path": "missing.json"}, "Directory-level business rules not found"),
])
def test_missing_explicit_input(env, kwargs, expected):
    result = env.commands.validate_business_rules(str(env.root), **kwargs)

    assert not result["success"]
    assert expected in result["error"] and "missing.json" in result["error"]


def test_malformed_and_invalid_inputs_are_reported(env):
    env.file_json.parent.mkdir(parents=True)
    env.file_json.write_text('{"src/A.cs": [', encoding="utf-8")
    result = env.commands.validate_business_rules(str(env.root))
    assert not result["success"]
    assert str(env.file_json) in result["error"] and "malformed JSON" in result["error"]

    write_json(env.file_json, {"src/A.cs": [{"text": "wrong field"}]})
    result = env.commands.validate_business_rules(str(env.root))
    assert not result["success"]
    assert "entry 'src/A.cs', rule #0" in result["error"]

    write_json(env.file_json, {})
    write_json(env.dir_json, {"src": {"observed_rules": "not a list", "inferred_rules": []}})
    result = env.commands.validate_business_rules(str(env.root))
    assert not result["success"]
    assert str(env.dir_json) in result["error"] and "entry 'src'" in result["error"]
    assert env.llm.validated_texts == []


def test_empty_inputs_write_empty_outputs_without_ai_or_database(env):
    write_json(env.file_json, {})
    write_json(env.dir_json, dir_rules(env.root, {".": ([], [])}))

    result = env.commands.validate_business_rules(str(env.root))

    assert result["success"], result
    assert read_outputs(env) == ([], [])
    assert env.llm.condensed_groups == [] and env.llm.validated_texts == []
    assert env.db_loads == []


# ---------------------------------------------------------
# Full pipeline call path
# ---------------------------------------------------------

def test_full_pipeline_validates_combined_rules(env, monkeypatch):
    calls = []
    for name in ["build_database", "generate_file_summaries", "build_summary_database",
                 "generate_directory_summaries", "generate_unit_tests",
                 "generate_integration_tests", "generate_all_uml"]:
        monkeypatch.setattr(env.commands, name, lambda *a, _n=name, **k: calls.append(_n) or {"success": True, "result": None})

    write_json(env.dir_json, dir_rules(env.root, {"src": (["Folder rule."], ["Inference: Inferred rule."])}))

    result = env.commands.full_pipeline(str(env.root))

    assert result["success"], result
    assert calls == ["build_database", "generate_file_summaries", "build_summary_database",
                     "generate_directory_summaries", "generate_unit_tests",
                     "generate_integration_tests", "generate_all_uml"]
    assert sorted(env.llm.validated_texts) == ["Folder rule.", "Inference: Inferred rule."]
    assert "No file-level business rules were found" in result["result"]["warning"]
    validated, discarded = read_outputs(env)
    assert [records(r) for r in validated] == [[("directory_observed", [])]]
    assert [records(r) for r in discarded] == [[("directory_inferred", [])]]


# ---------------------------------------------------------
# Output compatibility and older callers
# ---------------------------------------------------------

def test_outputs_parse_with_downstream_models(env):
    write_json(env.file_json, file_rules(env.root, {"src/A.cs": ["Rule A.", "Inference: Rule B."]}))
    write_json(env.dir_json, dir_rules(env.root, {".": (["Root rule."], [])}))
    assert env.commands.validate_business_rules(str(env.root))["success"]

    validated, discarded = read_outputs(env)
    assert validated and discarded
    for raw in validated:
        ut = UTValidatedRule.model_validate(raw)          # as generate_unit_tests/integration_tests parse it
        assert [p.model_dump() for p in ut.provenance] == raw["provenance"]
        assert BRValidatedRule.model_validate(raw).model_dump() == raw
    for raw in discarded:
        assert DiscardedRule.model_validate(raw).model_dump() == raw


class FakeTestRun:
    def message(self):
        return "ok"

    def summary(self):
        return {}

    def needs_attention(self):
        return False


def test_unit_and_integration_test_commands_read_validation_output(env, monkeypatch):
    received = {}

    def fake_agent(name):
        class Agent:
            def run(self, rules, codebase_name, codebase_path):
                received[name] = rules
                return {"test_run": FakeTestRun()}
        return Agent

    monkeypatch.setattr(commands_module, "UTAgent", fake_agent("unit"))
    monkeypatch.setattr(commands_module, "ITAgent", fake_agent("integration"))
    write_json(env.file_json, file_rules(env.root, {"src/A.cs": ["File rule."]}))
    write_json(env.dir_json, dir_rules(env.root, {".": (["Folder rule."], [])}))
    assert env.commands.validate_business_rules(str(env.root))["success"]
    folder_id = by_rule(read_outputs(env)[0])["Folder rule."]["id"]

    assert env.commands.generate_unit_tests(str(env.root), [])["success"]
    assert env.commands.generate_integration_tests(str(env.root), [folder_id])["success"]

    unit = {r.rule: r for r in received["unit"]}
    assert set(unit) == {"File rule.", "Folder rule."}
    assert [(p.origin, p.source_file_paths) for p in unit["Folder rule."].provenance] == [("directory_observed", [])]
    assert [(p.origin, p.source_file_paths) for p in unit["File rule."].provenance] == [("file", ["src/A.cs"])]
    assert [r.rule for r in received["integration"]] == ["Folder rule."]


def test_older_outputs_without_provenance_still_parse():
    old = {"id": 1, "rule": "R.", "source_directory": "Shop/src", "source_file_paths": ["D:\\Shop\\src\\A.cs"],
           "explanation": {"evidence": {"A.cs": ["x"]}, "reasoning": "y"}}
    assert UTValidatedRule.model_validate(old).provenance == []
    assert BRValidatedRule.model_validate(old).provenance == []
    old_discard = {"id": 2, "rule": "R.", "source_directory": "Shop/src", "source_file_paths": [], "reason": "z"}
    assert DiscardedRule.model_validate(old_discard).provenance == []


def test_older_dict_input_to_brAgent_still_runs(env):
    path_a = str(env.root / "src" / "A.cs")
    path_b = str(env.root / "src" / "B.cs")
    BRAgent().run({path_a: [BusinessRule(rule="Rule A.")], path_b: [BusinessRule(rule="Rule B.")]}, CODEBASE)

    rules = by_rule(read_outputs(env)[0])
    assert rules["Rule A."]["source_directory"] == f"{CODEBASE}/src"
    assert rules["Rule A."]["source_file_paths"] == [path_a]
    assert rules["Rule B."]["source_file_paths"] == [path_b]


# ---------------------------------------------------------
# Loader options used by the command
# ---------------------------------------------------------

def test_loader_false_skips_input_without_reporting_it(env):
    write_json(env.dir_json, dir_rules(env.root, {".": (["Folder rule."], [])}))
    custom = write_json(env.app / "custom.json", {"A.cs": [{"rule": "File rule."}]})

    result = load_rule_candidates(env.root, custom, False, app_dir=env.app)

    assert [c.rule for c in result.candidates] == ["File rule."]
    assert result.missing_inputs == {}
    with pytest.raises(FileNotFoundError, match="No business rule inputs were requested"):
        load_rule_candidates(env.root, False, False, app_dir=env.app)


def test_merge_rule_candidates_uses_given_text_and_group_provenance():
    a = RuleCandidate(rule="A", source_directory="src", source_file_paths=["src/A.cs"], origin="file")
    b = RuleCandidate(rule="B", source_directory="src", origin="directory_inferred")

    merged = merge_rule_candidates([a, b], rule="A and B")

    assert merged.rule == "A and B"
    assert merged.source_file_paths == ["src/A.cs"]
    assert [(p.origin, p.source_file_paths) for p in merged.provenance] == [
        ("file", ["src/A.cs"]), ("directory_inferred", []),
    ]
    assert a.provenance[0].source_file_paths == ["src/A.cs"] and b.source_file_paths == []
