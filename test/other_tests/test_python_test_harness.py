from pathlib import Path

from agent.python_test_harness import (
    PythonTestCase,
    check_tests,
    run_tests,
    write_tests,
    clean_test,
    make_cases
)


def create_sample_lab(tmp_path: Path) -> Path:
    """Create a tiny student-style Python lab for the harness to test."""
    lab_dir = tmp_path / "sample_lab"
    lab_dir.mkdir()

    (lab_dir / "math_lab.py").write_text(
        "def double(value):\n"
        "    return value * 2\n",
        encoding="utf-8",
    )

    return lab_dir


def test_generated_python_test_runs_and_passes(tmp_path):
    lab_dir = create_sample_lab(tmp_path)
    test_dir = tmp_path / "generated_tests"
    results_dir = tmp_path / "results"

    case = PythonTestCase(
        name="UT_1",
        imports=["from math_lab import double"],
        body=(
            "def test_double_returns_twice_the_value():\n"
            "    assert double(4) == 8"
        ),
    )

    write_tests(str(test_dir), "UT", [case])

    result = run_tests(
        test_dir=str(test_dir),
        codebase_path=str(lab_dir),
        results_dir=str(results_dir),
    )

    assert result.project_error == ""
    assert result.passed == 1
    assert result.failed == 0
    assert result.errors == 0
    assert Path(result.report_path).is_file()
    summary = result.summary()
    assert summary["harness"] == "pytest"
    assert summary["validated_at"]


def test_failing_generated_python_test_is_reported(tmp_path):
    lab_dir = create_sample_lab(tmp_path)
    test_dir = tmp_path / "generated_tests"
    results_dir = tmp_path / "results"

    case = PythonTestCase(
        name="UT_2",
        imports=["from math_lab import double"],
        body=(
            "def test_double_with_wrong_expected_value():\n"
            "    assert double(4) == 99"
        ),
    )

    write_tests(str(test_dir), "UT", [case])

    result = run_tests(
        test_dir=str(test_dir),
        codebase_path=str(lab_dir),
        results_dir=str(results_dir),
    )

    assert result.project_error == ""
    assert result.passed == 0
    assert result.failed == 1
    assert result.errors == 0
    assert result.failed_tests

def test_clean_test_accepts_one_valid_pytest_function():
    body, imports, reason = clean_test(
        "def test_answer():\n    assert 2 + 2 == 4",
        ["from math import floor"],
    )

    assert reason == ""
    assert body.startswith("def test_answer")
    assert imports == ["from math import floor"]


def test_clean_test_rejects_invalid_python():
    body, _, reason = clean_test(
        "def test_broken(:\n    assert True",
        [],
    )

    assert body is None
    assert "invalid Python test syntax" in reason


def test_clean_test_rejects_non_pytest_function():
    body, _, reason = clean_test(
        "def calculate_total():\n    return 5",
        [],
    )

    assert body is None
    assert "test_*" in reason

def test_all_rejected_tests_are_not_sent_to_pytest(tmp_path):
    rejected_test = PythonTestCase(
        name="UT_1",
        imports=[],
        body="",
        reason="invalid Python test syntax",
    )

    run = check_tests(
        kind="Unit",
        prefix="UT",
        cases=[],
        rejected=[rejected_test],
        codebase_path=str(tmp_path),
        codebase_name="empty_lab",
        results_dir=str(tmp_path / "results"),
    )

    assert run.generated == 1
    assert run.kept == []
    assert run.dropped == [rejected_test]
    assert run.project_error == ""
    assert run.report_path == ""