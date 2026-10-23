"""!
@file mutation_mapping_test.py
@brief Tests for backend/mutation_mapping.py, which turns Stryker.NET's JSON report
into mutation numbers per rule and per test.
@details The fake reports below have the shape Stryker.NET 5.0.0 wrote for a small
probe project on 2026-10-14: full Windows paths as file keys, test ids (GUIDs)
in coveredBy / killedBy, test names under testFiles, and an empty coveredBy
for static mutants.
"""

from backend.mutation_mapping import build, counts, names_by_test_id


ROOT = r"C:\work\Shop Code"
CART = ROOT + r"\src\Shop\Cart.cs"
TAX = ROOT + r"\src\Shop\Tax.cs"

SOURCE = "\n".join([
    "public class Cart",
    "{",
    "    public static readonly string[] Kinds = { \"food\", \"toy\" };",
    "    public int Total(int a, int b) => a + b;",
    "    public bool Big(int x) { return x > 10; }",
    "}",
])


def at(line, start, end):
    return {"start": {"line": line, "column": start}, "end": {"line": line, "column": end}}


def mutant(id, status, covered, killed, location, replacement="x", static=False):
    return {"id": str(id), "mutatorName": "Equality mutation", "replacement": replacement,
            "location": location, "status": status, "static": static,
            "coveredBy": covered, "killedBy": killed}


def report(**extra):
    data = {
        "schemaVersion": "2",
        "thresholds": {"high": 80, "low": 60},
        "projectRoot": ROOT + r"\src\Shop",
        "files": {
            CART: {"language": "cs", "source": SOURCE, "mutants": [
                # Static: coveredBy comes back empty, but every test ran it.
                mutant(0, "Survived", [], [], at(3, 47, 53), '""', static=True),
                mutant(1, "Killed", ["t4", "t7"], ["t4", "t7"], at(4, 39, 44), "a - b"),
                mutant(2, "Killed", ["t4", "t7"], ["t7"], at(5, 37, 43), "x >= 10"),
                mutant(3, "Survived", ["t4"], [], at(5, 37, 43), "x < 10"),
                mutant(4, "Timeout", ["t4"], [], at(5, 37, 43)),
                mutant(5, "Ignored", ["t4"], [], at(5, 28, 46)),
                mutant(6, "CompileError", [], [], at(4, 39, 44)),
            ]},
            TAX: {"language": "cs", "source": "class Tax {}", "mutants": [
                mutant(7, "NoCoverage", [], [], at(1, 1, 6)),
            ]},
        },
        "testFiles": {
            ROOT + r"\gen\Shop.Tests\UT_4.cs": {"tests": [
                {"id": "t4", "name": "Shop.Tests.UT_4.TotalAdds"},
                {"id": "t4b", "name": "Shop.Tests.UT_4.SecondMethod"},
            ]},
            ROOT + r"\gen\Shop.Tests\UT_7.cs": {"tests": [{"id": "t7", "name": "Shop.Tests.UT_7.BigIsOverTen"}]},
            ROOT + r"\gen\Shop.Tests\IT_1.cs": {"tests": []},
            ROOT + r"\gen\Shop.Tests\Helpers.cs": {"tests": [{"id": "h", "name": "Shop.Tests.Helpers.Check"}]},
        },
    }
    data.update(extra)
    return data


USED = [{"name": "UT_4", "rule_id": 4}, {"name": "UT_7", "rule_id": 7}]
RULES = [
    {"id": 4, "files": ["src/Shop/Cart.cs"]},
    {"id": 7, "files": ["src/Shop/Cart.cs", "src/Shop/Tax.cs"]},
    {"id": 9, "files": ["src/Shop/Tax.cs"]},
    {"id": 12, "files": []},
]


def mapped():
    return build([report()], ROOT, RULES, USED)


def rule(result, rule_id):
    return next(r for r in result["rules"] if r["id"] == rule_id)


# ---------- tests and files ----------

def test_test_ids_become_checkpoint_test_names_from_the_file_name():
    names = names_by_test_id(report())
    assert names["t4"] == "UT_4" and names["t4b"] == "UT_4"  # two methods, one test file
    assert names["t7"] == "UT_7"
    assert "h" not in names  # not a generated test


def test_falls_back_to_the_name_inside_the_method_name():
    data = report(testFiles={r"C:\x\Combined.cs": {"tests": [{"id": "a", "name": "Shop.Tests.UT_5_b.Works"}]}})
    assert names_by_test_id(data) == {"a": "UT_5_b"}


def test_files_are_made_relative_to_the_codebase():
    assert set(mapped()["by_file"]) == {"src/Shop/Cart.cs", "src/Shop/Tax.cs"}


def test_relative_file_keys_are_read_from_the_project_root():
    data = report(files={"Cart.cs": report()["files"][CART]})
    assert set(build([data], ROOT, RULES, USED)["by_file"]) == {"src/Shop/Cart.cs"}


# ---------- scores ----------

def test_score_counts_like_stryker_does():
    overall = mapped()["overall"]
    # Detected: 2 killed + 1 timeout. Undetected: 2 survived + 1 no coverage.
    # Ignored and compile errors are left out of both.
    assert (overall["killed"], overall["survived"], overall["timeout"], overall["no_coverage"]) == (2, 2, 1, 1)
    assert (overall["ignored"], overall["compile_error"], overall["total"]) == (1, 1, 8)
    assert overall["scored"] == 6 and overall["score"] == 50.0


def test_nothing_scored_has_no_score_rather_than_zero():
    assert counts([])["score"] is None
    assert build([], ROOT, RULES, USED)["overall"] is None


def test_each_test_says_how_many_changes_it_ran_and_caught():
    tests = {t["name"]: t for t in mapped()["tests"]}
    # UT_4 ran mutants 0 (static), 1, 2, 3, 4; it failed on 1 only.
    assert (tests["UT_4"]["covered"], tests["UT_4"]["killed"]) == (5, 1)
    assert (tests["UT_7"]["covered"], tests["UT_7"]["killed"]) == (3, 2)


# ---------- per rule ----------

def test_rule_is_scored_by_its_own_test_not_by_its_file():
    own = rule(mapped(), 4)["own_tests"]
    assert own == {"covered": 5, "caught": 1, "caught_by_other_tests_only": 1, "survived": 2, "timeout": 1}


def test_static_mutant_counts_as_run_by_every_test():
    # Mutant 0 has no coveredBy, but Stryker ran every test against it.
    assert rule(mapped(), 7)["own_tests"]["survived"] == 1


def test_rule_files_view_counts_every_test():
    in_files = rule(mapped(), 7)["in_rule_files"]
    assert in_files["total"] == 8 and in_files["no_coverage"] == 1


def test_rule_without_its_own_test_still_gets_its_files():
    entry = rule(mapped(), 9)
    assert entry["own_tests"] is None and entry["tests"] == []
    assert entry["in_rule_files"]["no_coverage"] == 1
    # With no test of its own, the examples come from its files.
    assert [e["status"] for e in entry["surviving_examples"]] == ["NoCoverage"]


def test_rule_with_no_code_files():
    entry = rule(mapped(), 12)
    assert entry["in_rule_files"] is None and entry["own_tests"] is None


def test_rule_ids_are_kept_exactly():
    rules = [{"id": "R-1", "files": ["src/Shop/Cart.cs"]}]
    result = build([report()], ROOT, rules, [{"name": "UT_4", "rule_id": "R-1"}])
    assert result["rules"][0]["id"] == "R-1" and result["rules"][0]["tests"] == ["UT_4"]


def test_a_rule_with_two_tests_counts_both():
    used = [{"name": "UT_4", "rule_id": 4}, {"name": "UT_7", "rule_id": 4}]
    own = rule(build([report()], ROOT, RULES, used), 4)["own_tests"]
    assert own["caught"] == 2  # mutant 1 by both, mutant 2 by UT_7


def test_surviving_examples_show_the_code_before_and_after():
    examples = rule(mapped(), 4)["surviving_examples"]
    assert {(e["line"], e["original"], e["mutated"]) for e in examples} == {
        (3, '"food"', '""'),
        (5, "x > 10", "x < 10"),
    }
    assert all(e["file"] == "src/Shop/Cart.cs" for e in examples)
    # The whole line too, so a one-word change makes sense to a reader.
    assert {e["line_text"] for e in examples} == {
        'public static readonly string[] Kinds = { "food", "toy" };',
        "public bool Big(int x) { return x > 10; }",
    }


def test_long_code_is_shortened_and_bad_locations_do_not_crash():
    data = report()
    data["files"][CART]["mutants"] = [
        mutant(1, "Survived", ["t4"], [], at(4, 1, 999), replacement="y" * 500),
        mutant(2, "Survived", ["t4"], [], {"start": {}, "end": None}),
        mutant(3, "Survived", ["t4"], [], at(99, 1, 2)),
    ]
    examples = {e["line"]: e for e in rule(build([data], ROOT, RULES, USED), 4)["surviving_examples"]}
    assert len(examples[4]["mutated"]) <= 120 and examples[4]["mutated"].endswith("...")
    assert examples[None]["original"] == "" and examples[99]["original"] == ""


def test_several_reports_are_combined():
    second = {"projectRoot": ROOT + r"\src\Tax", "files": {ROOT + r"\src\Tax\Tax.cs": {
        "source": "", "mutants": [mutant(9, "Killed", ["t7"], ["t7"], at(1, 1, 2))]}},
        "testFiles": report()["testFiles"]}
    result = build([report(), second], ROOT, RULES, USED)
    assert result["overall"]["total"] == 9 and len(result["by_file"]) == 3


def test_odd_report_shapes_do_not_crash():
    result = build([{}, {"files": None, "testFiles": []}, "not a report"], ROOT, RULES, USED)
    assert result["overall"] is None and len(result["rules"]) == len(RULES)
