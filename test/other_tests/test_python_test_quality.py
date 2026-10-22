import json
from pathlib import Path
from types import SimpleNamespace

from agent.python_test_harness import (
    CONTEXT_OMISSION_MARKER,
    MAX_TEST_CHARS,
    PYTHON_CODE_CONTEXT_CHARS,
    PYTHON_SUMMARY_CONTEXT_CHARS,
    bound_python_prompt_documents,
    check_tests,
    generation_context_texts,
    make_cases,
    persist_python_generated_tests,
    python_unit_test_size_reason,
)
from agent.structured_output.UT_output import UnitTest
from agent.testing_language import unit_test_generation_instructions


SAFE_BODY = (
    "def test_double_returns_twice_the_value():\n"
    "    assert double(4) == 8\n"
)


def _unit_test(**overrides):
    values = dict(
        id=1,
        rule="doubling a number returns twice the value",
        imports=["from math_lab import double"],
        source_directory=".",
        source_file_paths=["math_lab.py"],
        unit_test=SAFE_BODY,
        test_mode="function",
    )
    values.update(overrides)
    return UnitTest.model_validate(values)


def test_python_code_and_summary_budgets_are_not_exceeded():
    code_docs = [f"code-chunk-{index}-" + ("x" * 2000) for index in range(20)]
    summary_docs = [f"summary-{index}-" + ("y" * 800) for index in range(20)]
    code_text, summary_text = generation_context_texts("python", code_docs, summary_docs)
    assert len(code_text) <= PYTHON_CODE_CONTEXT_CHARS
    assert len(summary_text) <= PYTHON_SUMMARY_CONTEXT_CHARS


def test_complete_chunks_are_preserved_when_included():
    documents = ["def alpha():\n    return 1\n", "def beta():\n    return 2\n"]
    bounded = bound_python_prompt_documents(documents, budget=80)
    assert "def alpha():\n    return 1" in bounded
    assert bounded.count("def alpha") == 1
    assert "return 1\n" in bounded or "return 1" in bounded


def test_oversized_first_document_is_skipped_and_later_chunk_is_kept():
    huge = "H" * 500
    small = "def later():\n    return 3\n"
    bounded = bound_python_prompt_documents(
        [huge, small],
        budget=len(CONTEXT_OMISSION_MARKER) + 4 + len(small),
    )
    assert huge not in bounded
    assert CONTEXT_OMISSION_MARKER in bounded
    assert small in bounded
    assert len(bounded) <= len(CONTEXT_OMISSION_MARKER) + 4 + len(small)


def test_omission_marker_counts_toward_budget():
    huge = "Z" * 200
    bounded = bound_python_prompt_documents([huge], budget=len(CONTEXT_OMISSION_MARKER))
    assert bounded == CONTEXT_OMISSION_MARKER
    assert len(bounded) <= len(CONTEXT_OMISSION_MARKER)


def test_csharp_prompt_context_is_unbounded():
    huge = ["C" * (PYTHON_CODE_CONTEXT_CHARS + 50)]
    huge_summary = ["S" * (PYTHON_SUMMARY_CONTEXT_CHARS + 50)]
    code_text, summary_text = generation_context_texts("csharp", huge, huge_summary)
    assert code_text == huge[0]
    assert summary_text == huge_summary[0]
    assert CONTEXT_OMISSION_MARKER not in code_text
    python_code, _ = generation_context_texts("python", huge, huge_summary)
    assert len(python_code) <= PYTHON_CODE_CONTEXT_CHARS


def test_csharp_generation_instructions_unchanged():
    instructions = unit_test_generation_instructions("csharp")
    assert "[Fact]" in instructions
    assert "subprocess" not in instructions


def test_oversized_body_is_rejected_and_20000_remains_eligible():
    oversized = _unit_test(id=9, unit_test="x" * 40_001)
    reason = python_unit_test_size_reason(oversized)
    assert "rule 9" in reason
    assert "40001" in reason
    assert "not truncated" in reason

    exact = _unit_test(id=10, unit_test="y" * MAX_TEST_CHARS)
    assert python_unit_test_size_reason(exact) == ""


def test_writer_omits_oversized_python_tests_and_keeps_siblings(tmp_path):
    sibling = _unit_test(id=1, unit_test=SAFE_BODY)
    oversized = _unit_test(
        id=2,
        unit_test="z" * 40_001,
        test_mode="script",
        source_file_paths=["math_lab.py"],
        source_directory="labs",
    )
    persistable, dropped = persist_python_generated_tests(
        tmp_path / "lab",
        [sibling, oversized],
    )
    saved = json.loads((tmp_path / "lab" / "unit_tests.json").read_text(encoding="utf-8"))
    dropped_records = json.loads(
        (tmp_path / "lab" / "dropped_generated_tests.json").read_text(encoding="utf-8")
    )
    assert [test.id for test in persistable] == [1]
    assert [item["id"] for item in saved] == [1]
    assert saved[0]["unit_test"] == SAFE_BODY
    assert saved[0]["test_mode"] == "function"
    assert saved[0]["source_file_paths"] == ["math_lab.py"]
    assert dropped_records[0]["id"] == 2
    assert dropped_records[0]["rule"] == oversized.rule
    assert dropped_records[0]["source_directory"] == "labs"
    assert dropped_records[0]["source_file_paths"] == ["math_lab.py"]
    assert dropped_records[0]["test_mode"] == "script"
    assert dropped_records[0]["character_count"] == 40_001
    assert "rule 2" in dropped_records[0]["reason"]
    assert "unit_test" not in dropped_records[0]
    assert dropped[0]["id"] == 2
    assert all(len(item.get("unit_test", "")) == 0 for item in dropped_records)


def test_all_generated_tests_oversized_still_writes_sidecar(tmp_path):
    oversized = _unit_test(id=3, unit_test="q" * 40_001)
    persistable, dropped = persist_python_generated_tests(tmp_path / "lab", [oversized])
    saved = json.loads((tmp_path / "lab" / "unit_tests.json").read_text(encoding="utf-8"))
    dropped_records = json.loads(
        (tmp_path / "lab" / "dropped_generated_tests.json").read_text(encoding="utf-8")
    )
    assert persistable == []
    assert saved == []
    assert [item["id"] for item in dropped] == [3]
    assert dropped_records[0]["id"] == 3
    assert "rule 3" in dropped_records[0]["reason"]


def test_successful_python_writer_run_clears_stale_dropped_report(tmp_path):
    stale = tmp_path / "lab" / "dropped_generated_tests.json"
    stale.parent.mkdir(parents=True)
    stale.write_text(json.dumps([{"id": 99, "reason": "stale"}], indent=2), encoding="utf-8")
    persistable, dropped = persist_python_generated_tests(
        tmp_path / "lab",
        [_unit_test(id=4, unit_test=SAFE_BODY)],
    )
    dropped_records = json.loads(stale.read_text(encoding="utf-8"))
    saved = json.loads((tmp_path / "lab" / "unit_tests.json").read_text(encoding="utf-8"))
    assert persistable[0].id == 4
    assert dropped == []
    assert dropped_records == []
    assert [item["id"] for item in saved] == [4]


def test_writer_state_for_runner_contains_only_accepted_tests(tmp_path):
    sibling = _unit_test(id=1, unit_test=SAFE_BODY)
    oversized = _unit_test(id=2, unit_test="z" * 40_001)
    persistable, _dropped = persist_python_generated_tests(
        tmp_path / "lab",
        [sibling, oversized],
    )
    runner_state = persistable
    assert [test.id for test in runner_state] == [1]
    assert all(python_unit_test_size_reason(test) == "" for test in runner_state)


def test_saved_script_test_mode_and_source_paths_are_preserved(tmp_path):
    script = _unit_test(
        id=18,
        test_mode="script",
        source_directory="labs",
        source_file_paths=["math_lab.py"],
        imports=["import subprocess", "import sys"],
        unit_test="def test_script():\n    assert True\n",
    )
    persist_python_generated_tests(tmp_path / "lab", [script])
    saved = json.loads((tmp_path / "lab" / "unit_tests.json").read_text(encoding="utf-8"))
    assert saved[0]["id"] == 18
    assert saved[0]["test_mode"] == "script"
    assert saved[0]["source_directory"] == "labs"
    assert saved[0]["source_file_paths"] == ["math_lab.py"]
    assert saved[0]["imports"] == ["import subprocess", "import sys"]


def test_csharp_split_does_not_apply_python_size_filter_to_generation_path():
    huge_body = "[Fact]\npublic void Adds() { Assert.Equal(2, 1 + 1); }\n" + ("w" * 40_001)
    code_text, summary_text = generation_context_texts(
        "csharp",
        [huge_body],
        ["summary"],
    )
    assert code_text == huge_body
    assert summary_text == "summary"


def test_identical_bodies_same_context_keep_first_and_report_duplicate():
    first = _unit_test(id=4, unit_test=SAFE_BODY)
    second = _unit_test(id=5, unit_test=SAFE_BODY)
    cases, rejected = make_cases(
        "UT",
        [(first.id, first), (second.id, second)],
        body_of=lambda test: test.unit_test,
        imports_of=lambda test: test.imports,
    )
    assert len(cases) == 1
    assert cases[0].source.id == 4
    assert rejected[0].source.id == 5
    assert "rule 5" in rejected[0].reason
    assert "rule 4" in rejected[0].reason


def test_same_body_different_source_paths_are_kept():
    first = _unit_test(id=6, source_file_paths=["a.py"])
    second = _unit_test(id=7, source_file_paths=["b.py"])
    cases, rejected = make_cases(
        "UT",
        [(first.id, first), (second.id, second)],
        body_of=lambda test: test.unit_test,
        imports_of=lambda test: test.imports,
    )
    assert rejected == []
    assert {case.source.id for case in cases} == {6, 7}


def test_same_body_script_and_function_are_not_merged(tmp_path):
    module = tmp_path / "math_lab.py"
    module.write_text("def double(value):\n    return value * 2\n", encoding="utf-8")
    shared_body = "def test_exits_cleanly():\n    assert True\n"
    function_test = _unit_test(
        id=8,
        imports=[],
        source_file_paths=[str(module)],
        test_mode="function",
        unit_test=shared_body,
    )
    script_test = _unit_test(
        id=9,
        imports=["import subprocess", "import sys"],
        source_file_paths=[str(module)],
        test_mode="script",
        unit_test=shared_body,
    )
    cases, rejected = make_cases(
        "UT",
        [(function_test.id, function_test), (script_test.id, script_test)],
        body_of=lambda test: test.unit_test,
        imports_of=lambda test: test.imports,
        codebase_path=str(tmp_path),
    )
    assert rejected == []
    assert {case.test_mode for case in cases} == {"function", "script"}


def test_same_body_different_source_directory_are_kept():
    first = _unit_test(id=11, source_directory="lab_a")
    second = _unit_test(id=12, source_directory="lab_b")
    cases, rejected = make_cases(
        "UT",
        [(first.id, first), (second.id, second)],
        body_of=lambda test: test.unit_test,
        imports_of=lambda test: test.imports,
    )
    assert rejected == []
    assert {case.source.source_directory for case in cases} == {"lab_a", "lab_b"}


def test_unsafe_script_is_rejected_before_duplicate_comparison():
    unsafe = SimpleNamespace(
        id=13,
        test_mode="script",
        source_directory=".",
        source_file_paths=["temp.py"],
        unit_test=SAFE_BODY,
        imports=["from student_lab import fn"],
    )
    duplicate_unsafe = SimpleNamespace(
        id=14,
        test_mode="script",
        source_directory=".",
        source_file_paths=["temp.py"],
        unit_test=SAFE_BODY,
        imports=["from student_lab import fn"],
    )
    cases, rejected = make_cases(
        "UT",
        [(13, unsafe), (14, duplicate_unsafe)],
        body_of=lambda test: test.unit_test,
        imports_of=lambda test: test.imports,
    )
    assert cases == []
    assert all("duplicate" not in case.reason for case in rejected)


def test_g2_empty_paths_remain_script_gated():
    generated = _unit_test(
        id=15,
        source_file_paths=[],
        test_mode="function",
        imports=["from student_lab import fn"],
        unit_test="def test_fn():\n    assert fn() == 1\n",
    )
    cases, rejected = make_cases(
        "UT",
        [(generated.id, generated)],
        body_of=lambda test: test.unit_test,
        imports_of=lambda test: test.imports,
        codebase_path="/labs",
    )
    assert cases == []
    assert rejected[0].test_mode == "script"


def test_check_tests_does_not_write_duplicates(tmp_path, monkeypatch):
    (tmp_path / "math_lab.py").write_text(
        "def double(value):\n    return value * 2\n",
        encoding="utf-8",
    )
    first = _unit_test(id=16)
    second = _unit_test(id=17)
    cases, rejected = make_cases(
        "UT",
        [(first.id, first), (second.id, second)],
        body_of=lambda test: test.unit_test,
        imports_of=lambda test: test.imports,
        codebase_path=str(tmp_path),
    )
    written = []
    monkeypatch.setattr(
        "agent.python_test_harness.write_tests",
        lambda test_dir, prefix, safe: written.append([case.source.id for case in safe]),
    )
    monkeypatch.setattr(
        "agent.python_test_harness.run_tests",
        lambda **kwargs: SimpleNamespace(
            passed=0,
            failed=0,
            skipped=0,
            errors=0,
            failed_tests=[],
            report_path="",
            output="",
            project_error="",
        ),
    )
    run = check_tests(
        "Unit",
        "UT",
        cases,
        rejected,
        str(tmp_path),
        "lab",
        str(tmp_path / "results"),
    )
    assert written == [[16]]
    assert run.dropped
    assert any("duplicate" in case.reason for case in run.dropped)
