"""G2-only language fallback and source-less target reporting."""

import shutil
from pathlib import Path
from types import SimpleNamespace

from agent.python_test_harness import (
    PythonTestRun,
    check_tests,
    make_cases,
    trusted_python_test_mode,
)
from agent.testing_language import detect_test_language

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "python_e2e"

SCRIPT_BODY = (
    "def test_must_not_invent_a_script_target():\n"
    "    assert True\n"
)
FUNCTION_BODY = (
    "def test_double_returns_twice_the_value():\n"
    "    assert double(4) == 8\n"
)


def _copy_lab(tmp_path: Path, name: str) -> Path:
    dest = tmp_path / name
    shutil.copytree(FIXTURE_ROOT / name, dest)
    return dest


def _item(**overrides):
    values = dict(
        id=1,
        unit_test=SCRIPT_BODY,
        imports=["import subprocess", "import sys"],
        source_file_paths=[],
        test_mode="script",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_function_lab_g2_paths_select_python_but_stay_script_gated(tmp_path):
    lab = _copy_lab(tmp_path, "function_lab")
    source_file_paths = []
    language = detect_test_language([source_file_paths], codebase_path=str(lab))
    assert language == "python"
    assert source_file_paths == []
    assert trusted_python_test_mode(source_file_paths, str(lab)) == "script"

    generated = _item(source_file_paths=source_file_paths)
    cases, rejected = make_cases(
        "UT",
        [(generated.id, generated)],
        body_of=lambda item: item.unit_test,
        imports_of=lambda item: item.imports,
        codebase_path=str(lab),
    )
    assert cases == []
    assert rejected[0].name == "UT_1"
    assert rejected[0].test_mode == "script"
    assert "no verified file-level test target" in rejected[0].reason
    assert "did not execute" in rejected[0].reason
    assert generated.source_file_paths == []
    assert (lab / "math_lab.py").is_file()


def test_script_lab_is_not_assigned_to_unattributed_g2_rule(tmp_path):
    lab = _copy_lab(tmp_path, "script_lab")
    generated = _item()
    cases, rejected = make_cases(
        "UT",
        [(generated.id, generated)],
        body_of=lambda item: item.unit_test,
        imports_of=lambda item: item.imports,
        codebase_path=str(lab),
    )
    assert cases == []
    assert generated.source_file_paths == []
    assert "no verified file-level test target" in rejected[0].reason
    assert "grade_script.py" not in rejected[0].reason


def test_multiple_source_files_do_not_select_an_arbitrary_g2_target(tmp_path):
    lab = tmp_path / "multi"
    lab.mkdir()
    (lab / "alpha.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    (lab / "beta.py").write_text("def b():\n    return 2\n", encoding="utf-8")
    generated = _item()
    cases, rejected = make_cases(
        "UT",
        [(generated.id, generated)],
        body_of=lambda item: item.unit_test,
        imports_of=lambda item: item.imports,
        codebase_path=str(lab),
    )
    assert cases == []
    assert generated.source_file_paths == []
    assert "no verified file-level test target" in rejected[0].reason
    assert "alpha.py" not in rejected[0].reason
    assert "beta.py" not in rejected[0].reason


def test_missing_named_source_path_uses_distinct_reason(tmp_path):
    lab = _copy_lab(tmp_path, "function_lab")
    generated = _item(source_file_paths=["missing_lab.py"])
    cases, rejected = make_cases(
        "UT",
        [(generated.id, generated)],
        body_of=lambda item: item.unit_test,
        imports_of=lambda item: item.imports,
        codebase_path=str(lab),
    )
    assert cases == []
    assert "attributed path" in rejected[0].reason
    assert "no verified file-level test target" not in rejected[0].reason


def test_source_path_outside_codebase_is_rejected(tmp_path):
    lab = _copy_lab(tmp_path, "function_lab")
    generated = _item(source_file_paths=["../escape.py"])
    cases, rejected = make_cases(
        "UT",
        [(generated.id, generated)],
        body_of=lambda item: item.unit_test,
        imports_of=lambda item: item.imports,
        codebase_path=str(lab),
    )
    assert cases == []
    assert "attributed path" in rejected[0].reason
    assert generated.source_file_paths == ["../escape.py"]


def test_source_less_g2_and_valid_g1_sibling_keep_attention(tmp_path, monkeypatch):
    lab = _copy_lab(tmp_path, "function_lab")
    test_dir = tmp_path / "generated_tests"
    results_dir = tmp_path / "results"
    monkeypatch.setattr(
        "agent.python_test_harness.test_dir_for",
        lambda codebase_path, codebase_name: str(test_dir),
    )
    run_calls = []
    monkeypatch.setattr(
        "agent.python_test_harness.run_tests",
        lambda **kwargs: run_calls.append(kwargs) or PythonTestRun(passed=1),
    )

    g2 = _item(id=1)
    g1 = _item(
        id=2,
        unit_test=FUNCTION_BODY,
        imports=["from math_lab import double"],
        source_file_paths=["math_lab.py"],
        test_mode="function",
    )
    cases, rejected = make_cases(
        "UT",
        [(g2.id, g2), (g1.id, g1)],
        body_of=lambda item: item.unit_test,
        imports_of=lambda item: item.imports,
        codebase_path=str(lab),
    )
    assert len(cases) == 1
    assert cases[0].name == "UT_2"
    assert len(rejected) == 1
    assert rejected[0].name == "UT_1"
    assert "no verified file-level test target" in rejected[0].reason
    assert g2.source_file_paths == []

    run = check_tests(
        "Unit",
        "UT",
        cases,
        rejected,
        str(lab),
        lab.name,
        str(results_dir),
    )
    assert run.passed == 1
    assert run.failed == 0
    assert len(run.kept) == 1
    assert len(run.dropped) == 1
    assert run.dropped[0].name == "UT_1"
    assert run.needs_attention() is True
    assert "rejected before execution" in run.message()
    assert "no verified file-level test target" in run.message()
    assert run_calls and Path(run_calls[0]["test_dir"]) == test_dir
    written = sorted(path.name for path in test_dir.glob("test_UT_*.py"))
    assert written == ["test_UT_2.py"]


def test_all_source_less_g2_tests_do_not_launch_pytest(tmp_path, monkeypatch):
    lab = _copy_lab(tmp_path, "function_lab")
    test_dir = tmp_path / "generated_tests"
    monkeypatch.setattr(
        "agent.python_test_harness.test_dir_for",
        lambda codebase_path, codebase_name: str(test_dir),
    )
    monkeypatch.setattr(
        "agent.python_test_harness.run_tests",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("pytest must not run")),
    )
    generated = _item()
    cases, rejected = make_cases(
        "UT",
        [(generated.id, generated)],
        body_of=lambda item: item.unit_test,
        imports_of=lambda item: item.imports,
        codebase_path=str(lab),
    )
    run = check_tests(
        "Unit",
        "UT",
        cases,
        rejected,
        str(lab),
        lab.name,
        str(tmp_path / "results"),
    )
    assert run.kept == []
    assert run.passed == 0
    assert run.report_path == ""
    assert run.needs_attention() is True
    assert "did not execute" in run.message()
    assert list(test_dir.glob("test_UT_*.py")) == []

