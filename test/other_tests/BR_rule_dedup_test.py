"""
@file BR_rule_dedup_test.py
@brief Tests for deduplicate_rule_candidates() and RuleCandidate provenance.
@details Uses in-memory candidates and temporary JSON fixtures. No AI calls or vector stores are used.
"""

import sys
import json
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
import pytest
from agent.BR_input_loader import deduplicate_rule_candidates, load_rule_candidates
from agent.structured_output.BR_output import RuleCandidate, RuleProvenance

CODEBASE = "MyCodebase"


def cand(rule, directory="src", files=(), origin="file"):
    return RuleCandidate(rule=rule, source_directory=directory, source_file_paths=list(files), origin=origin)


def records(candidate):
    return [(p.origin, p.source_file_paths) for p in candidate.provenance]


# ---------------------------------------------------------
# Provenance defaults
# ---------------------------------------------------------

def test_candidate_defaults_to_single_provenance_record():
    c = cand("Rule.", files=["src/A.cs"])
    assert c.provenance == [RuleProvenance(origin="file", source_file_paths=["src/A.cs"])]

    d = cand("Inference: Rule.", files=[], origin="directory_inferred")
    assert records(d) == [("directory_inferred", [])]


# ---------------------------------------------------------
# Candidates that merge
# ---------------------------------------------------------

def test_identical_rules_in_same_directory_merge():
    out = deduplicate_rule_candidates([
        cand("Orders need a customer.", files=["src/A.cs"]),
        cand("Orders need a customer.", files=["src/A.cs"]),
    ])

    assert len(out) == 1
    assert out[0].source_file_paths == ["src/A.cs"]
    assert records(out[0]) == [("file", ["src/A.cs"])]


def test_whitespace_variants_merge_and_keep_first_text():
    out = deduplicate_rule_candidates([
        cand("Orders  need a\tcustomer.", files=["src/A.cs"]),
        cand("  Orders need a customer.\n", files=["src/B.cs"]),
        cand("Orders need\na customer.", files=["src/C.cs"]),
    ])

    assert len(out) == 1
    assert out[0].rule == "Orders  need a\tcustomer."
    assert out[0].source_file_paths == ["src/A.cs", "src/B.cs", "src/C.cs"]


def test_file_and_folder_duplicates_retain_both_origins():
    out = deduplicate_rule_candidates([
        cand("Rentals cost 5.", files=["src/Rental.cs"]),
        cand("Rentals cost 5.", origin="directory_observed"),
    ])

    assert len(out) == 1
    assert out[0].origin == "file"
    assert out[0].source_file_paths == ["src/Rental.cs"]
    assert records(out[0]) == [("file", ["src/Rental.cs"]), ("directory_observed", [])]


def test_multiple_source_paths_keep_their_origin_association():
    out = deduplicate_rule_candidates([
        cand("Rule.", origin="directory_observed"),
        cand("Rule.", files=["src/A.cs"]),
        cand("Rule.", files=["src/B.cs"]),
    ])

    assert out[0].origin == "directory_observed"
    assert out[0].source_file_paths == ["src/A.cs", "src/B.cs"]
    assert records(out[0]) == [
        ("directory_observed", []),
        ("file", ["src/A.cs"]),
        ("file", ["src/B.cs"]),
    ]


def test_repeated_provenance_references_are_removed():
    repeated = RuleCandidate(
        rule="Rule.", source_directory="src", source_file_paths=["src/A.cs", "src/A.cs"], origin="file",
        provenance=[
            RuleProvenance(origin="file", source_file_paths=["src/A.cs", "src/A.cs"]),
            RuleProvenance(origin="file", source_file_paths=["src/A.cs"]),
        ],
    )
    out = deduplicate_rule_candidates([
        repeated,
        cand("Rule.", files=["src/A.cs"]),
        cand("Rule.", origin="directory_observed"),
        cand("Rule.", origin="directory_observed"),
    ])

    assert out[0].source_file_paths == ["src/A.cs"]
    assert records(out[0]) == [("file", ["src/A.cs"]), ("directory_observed", [])]


def test_inferred_rules_keep_origin_and_text():
    text = "Inference: Staff must be logged in to rent."
    out = deduplicate_rule_candidates([
        cand(text, origin="directory_inferred"),
        cand(text, origin="directory_inferred"),
    ])

    assert len(out) == 1
    assert out[0].rule == text
    assert out[0].origin == "directory_inferred"
    assert records(out[0]) == [("directory_inferred", [])]


def test_root_directory_candidates_merge():
    out = deduplicate_rule_candidates([
        cand("Root rule.", directory=".", files=["Program.cs"]),
        cand("Root rule.", directory=".", origin="directory_observed"),
    ])

    assert len(out) == 1
    assert out[0].source_directory == "."
    assert records(out[0]) == [("file", ["Program.cs"]), ("directory_observed", [])]


# ---------------------------------------------------------
# Candidates that stay separate
# ---------------------------------------------------------

@pytest.mark.parametrize("other", [
    "Orders need a customer",                       # punctuation differs
    "orders need a customer.",                      # case differs
    "Orders require a customer.",                   # wording differs
    "Inference: Orders need a customer.",           # inference prefix kept
])
def test_distinct_rule_text_stays_separate(other):
    out = deduplicate_rule_candidates([cand("Orders need a customer."), cand(other)])

    assert [c.rule for c in out] == ["Orders need a customer.", other]


def test_same_text_in_different_directories_stays_separate():
    out = deduplicate_rule_candidates([
        cand("Rule.", directory="src", files=["src/A.cs"]),
        cand("Rule.", directory=".", files=["A.cs"]),
        cand("Rule.", directory="src/Models", origin="directory_observed"),
    ])

    assert [c.source_directory for c in out] == ["src", ".", "src/Models"]
    assert [records(c) for c in out] == [
        [("file", ["src/A.cs"])],
        [("file", ["A.cs"])],
        [("directory_observed", [])],
    ]


# ---------------------------------------------------------
# Behaviour guarantees
# ---------------------------------------------------------

def sample():
    return [
        cand("B rule.", files=["src/B.cs"]),
        cand("A rule.", files=["src/A.cs"]),
        cand("B rule.", origin="directory_observed"),
        cand("Inference: C rule.", directory=".", origin="directory_inferred"),
        cand("A rule.", files=["src/A2.cs"]),
    ]


def test_empty_input():
    assert deduplicate_rule_candidates([]) == []


def test_first_seen_order_and_determinism():
    out = deduplicate_rule_candidates(sample())

    assert [c.rule for c in out] == ["B rule.", "A rule.", "Inference: C rule."]
    assert out == deduplicate_rule_candidates(sample())


def test_input_candidates_are_not_mutated():
    candidates = sample()
    before = [c.model_copy(deep=True) for c in candidates]

    out = deduplicate_rule_candidates(candidates)
    out[0].source_file_paths.append("x")
    out[0].provenance[0].source_file_paths.append("x")

    assert candidates == before


def test_idempotent():
    once = deduplicate_rule_candidates(sample())
    assert deduplicate_rule_candidates(once) == once


# ---------------------------------------------------------
# With the T-160 loader
# ---------------------------------------------------------

def test_equivalent_path_forms_merge_after_loading(tmp_path):
    root = tmp_path / CODEBASE
    (root / "src").mkdir(parents=True)
    file_json = tmp_path / "file.json"
    dir_json = tmp_path / "dir.json"
    file_json.write_text(json.dumps({
        str(root / "src" / "A.cs").replace("/", "\\"): [{"rule": "Rule.", "source_file": None}],
        f"{CODEBASE}/src/A.cs": [{"rule": "Rule.", "source_file": None}],
        "./src/A.cs": [{"rule": "Rule.", "source_file": None}],
    }), encoding="utf-8")
    dir_json.write_text(json.dumps({
        f"C:\\Other\\{CODEBASE}\\src": {"directory_name": "src", "directory_path": "src",
                                         "observed_rules": ["Rule."], "inferred_rules": []},
    }), encoding="utf-8")

    raw = load_rule_candidates(root, file_json, dir_json).candidates
    out = deduplicate_rule_candidates(raw)

    # The loader itself still returns every candidate.
    assert len(raw) == 4
    assert len(out) == 1
    assert out[0].source_directory == "src"
    assert out[0].source_file_paths == ["src/A.cs"]
    assert records(out[0]) == [("file", ["src/A.cs"]), ("directory_observed", [])]
