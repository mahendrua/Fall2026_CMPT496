from types import SimpleNamespace

from agent.br_condensation_safety import apply_condenser_safety, merge_is_unsafe
from agent.structured_output.BR_output import RuleCandidate


def cand(rule, directory="src", files=(), origin="file"):
    return RuleCandidate(
        rule=rule,
        source_directory=directory,
        source_file_paths=list(files),
        origin=origin,
    )


def out(rule, numbers):
    return SimpleNamespace(rule=rule, source_rule_numbers=list(numbers))


def records(candidate):
    return [(item.origin, item.source_file_paths) for item in candidate.provenance]


def test_equivalent_paraphrases_are_eligible_for_merging():
    group = [
        cand("Orders need a customer.", files=["src/A.cs"]),
        cand("Every order must have a customer.", files=["src/A.cs"]),
    ]
    result = apply_condenser_safety(
        group,
        [out("Every order must have a customer.", [1, 2])],
    )
    assert len(result) == 1
    assert result[0].rule == "Every order must have a customer."
    assert not merge_is_unsafe([item.rule for item in group], result[0].rule)


def test_age_thresholds_18_and_21_remain_distinct():
    group = [
        cand("A person must be at least 18 years old."),
        cand("A person must be at least 21 years old."),
    ]
    result = apply_condenser_safety(
        group,
        [out("A person must meet a minimum age requirement.", [1, 2])],
    )
    assert [item.rule for item in result] == [item.rule for item in group]


def test_condensed_text_listing_18_and_21_still_cannot_merge():
    group = [
        cand("Minimum age is 18."),
        cand("Minimum age is 21."),
    ]
    result = apply_condenser_safety(
        group,
        [out("Customers must be at least 18 or 21 years old.", [1, 2])],
    )
    assert [item.rule for item in result] == [
        "Minimum age is 18.",
        "Minimum age is 21.",
    ]


def test_different_discounts_remain_distinct():
    group = [
        cand("Apply a 5% discount."),
        cand("Apply a 10% discount."),
    ]
    result = apply_condenser_safety(
        group,
        [out("Apply a discount of 5% or 10%.", [1, 2])],
    )
    assert [item.rule for item in result] == [item.rule for item in group]


def test_different_inequality_operators_remain_distinct():
    group = [
        cand("Quantity must be < 10."),
        cand("Quantity must be <= 10."),
    ]
    result = apply_condenser_safety(
        group,
        [out("Quantity must not exceed 10.", [1, 2])],
    )
    assert [item.rule for item in result] == [item.rule for item in group]


def test_different_explicit_formulas_remain_distinct():
    group = [
        cand("Convert with (f-32)*5/9."),
        cand("Convert with f-32."),
    ]
    result = apply_condenser_safety(
        group,
        [out("Convert Fahrenheit to Celsius.", [1, 2])],
    )
    assert [item.rule for item in result] == [item.rule for item in group]


def test_different_precision_literals_remain_distinct():
    group = [
        cand("Round to 2 decimal places."),
        cand("Round to 0 decimal places."),
    ]
    result = apply_condenser_safety(
        group,
        [out("Round the result.", [1, 2])],
    )
    assert [item.rule for item in result] == [item.rule for item in group]


def test_different_explicit_exception_types_remain_distinct():
    group = [
        cand("Invalid input raises ValueError."),
        cand("Invalid input raises IndexError."),
    ]
    result = apply_condenser_safety(
        group,
        [out("Invalid input raises an exception.", [1, 2])],
    )
    assert [item.rule for item in result] == [item.rule for item in group]


def test_general_plus_specific_numeric_rule_remains_distinct():
    group = [
        cand("Age must be validated."),
        cand("Age must be at least 18."),
    ]
    result = apply_condenser_safety(
        group,
        [out("Age must be validated and at least 18.", [1, 2])],
    )
    assert [item.rule for item in result] == [item.rule for item in group]


def test_valid_cross_file_paraphrases_preserve_combined_source_paths():
    group = [
        cand("Orders need a customer.", files=["src/A.cs"]),
        cand("Every order must have a customer.", files=["src/B.cs"]),
    ]
    result = apply_condenser_safety(
        group,
        [out("Every order must have a customer.", [1, 2])],
    )
    assert result[0].source_file_paths == ["src/A.cs", "src/B.cs"]
    assert records(result[0]) == [
        ("file", ["src/A.cs"]),
        ("file", ["src/B.cs"]),
    ]
    assert group[0].source_file_paths == ["src/A.cs"]


def test_g1_g2_origins_are_preserved_on_safe_merge():
    group = [
        cand("Orders need a customer.", files=["src/A.cs"], origin="file"),
        cand("Every order must have a customer.", origin="directory_inferred"),
    ]
    result = apply_condenser_safety(
        group,
        [out("Every order must have a customer.", [1, 2])],
    )
    assert result[0].origin == "file"
    assert records(result[0]) == [
        ("file", ["src/A.cs"]),
        ("directory_inferred", []),
    ]


def test_invalid_indexes_fall_back_to_original_group():
    group = [cand("Rule A."), cand("Rule B.")]
    assert apply_condenser_safety(group, [out("Merged.", [1, 2, 9])]) is None
    assert apply_condenser_safety(group, []) is None
    assert apply_condenser_safety(group, [out("   ", [1, 2])]) is None


def test_missing_indexes_fall_back_to_original_group():
    group = [cand("Rule A."), cand("Rule B.")]
    assert apply_condenser_safety(group, [out("Only A.", [1])]) is None


def test_overlapping_indexes_fall_back_to_original_group():
    group = [cand("Rule A."), cand("Rule B."), cand("Rule C.")]
    assert apply_condenser_safety(
        group,
        [out("A and B.", [1, 2]), out("B and C.", [2, 3])],
    ) is None


def test_duplicate_indexes_inside_one_output_fall_back():
    group = [cand("Rule A."), cand("Rule B.")]
    assert apply_condenser_safety(group, [out("Merged.", [1, 1, 2])]) is None


def test_one_unsafe_merge_does_not_invalidate_a_separate_safe_merge():
    group = [
        cand("Minimum age is 18."),
        cand("Minimum age is 21."),
        cand("Orders need a customer."),
        cand("Every order must have a customer."),
    ]
    result = apply_condenser_safety(
        group,
        [
            out("Customers must be 18 or 21.", [1, 2]),
            out("Every order must have a customer.", [3, 4]),
        ],
    )
    assert [item.rule for item in result] == [
        "Minimum age is 18.",
        "Minimum age is 21.",
        "Every order must have a customer.",
    ]
    assert result[2].source_file_paths == []


def test_unsafe_directory_group_does_not_affect_other_groups():
    unsafe = [cand("Minimum age is 18."), cand("Minimum age is 21.")]
    safe = [
        cand("Orders need a customer.", directory="other"),
        cand("Every order must have a customer.", directory="other"),
    ]
    unsafe_result = apply_condenser_safety(
        unsafe,
        [out("Minimum age required.", [1, 2])],
    )
    safe_result = apply_condenser_safety(
        safe,
        [out("Every order must have a customer.", [1, 2])],
    )
    assert [item.rule for item in unsafe_result] == [item.rule for item in unsafe]
    assert len(safe_result) == 1
    assert safe_result[0].rule == "Every order must have a customer."


def test_same_wording_from_different_directories_is_not_collapsed_by_helper():
    src = cand("Root rule.", directory="src", files=["src/A.cs"])
    root = cand("Root rule.", directory=".", files=["A.cs"])
    src_result = apply_condenser_safety([src], [out("Root rule.", [1])])
    root_result = apply_condenser_safety([root], [out("Root rule.", [1])])
    assert src_result[0].source_directory == "src"
    assert root_result[0].source_directory == "."
    mixed = apply_condenser_safety(
        [src, root],
        [out("Root rule.", [1, 2])],
    )
    assert [item.source_directory for item in mixed] == ["src", "."]
    assert [item.rule for item in mixed] == ["Root rule.", "Root rule."]


def test_csharp_style_text_follows_the_same_conservative_rules():
    group = [
        cand("Invoice totals under 18 are rejected."),
        cand("Invoice totals under 21 are rejected."),
    ]
    result = apply_condenser_safety(
        group,
        [out("Reject invoices under a minimum total of 18 or 21.", [1, 2])],
    )
    assert [item.rule for item in result] == [item.rule for item in group]


def test_helper_does_not_import_br_agent_or_chromadb():
    import agent.br_condensation_safety as module

    assert "agent.BR_agent" not in module.__dict__
    assert "chromadb" not in module.__dict__
    source = open(module.__file__, encoding="utf-8").read()
    assert "chromadb" not in source
    assert "BR_agent" not in source
    assert "backend.commands" not in source
    assert "make_llm" not in source
