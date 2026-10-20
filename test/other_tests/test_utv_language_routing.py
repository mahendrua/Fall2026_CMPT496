from pathlib import Path

import pytest

from agent.UTV_agent import UTVAgent
from agent.structured_output.UTV_output import UnitTest
from agent.test_harness import TestRun
from backend.commands import Commands


def python_unit_test(**overrides):
    values = dict(
        id=1,
        rule="doubling a number returns twice the value",
        imports=["from math_lab import double"],
        source_directory=".",
        source_file_paths=["math_lab.py"],
        unit_test=(
            "def test_double_returns_twice_the_value():\n"
            "    assert double(4) == 8\n"
        ),
    )
    values.update(overrides)
    return UnitTest(**values)


def csharp_unit_test(**overrides):
    values = dict(
        id=1,
        rule="adding one and one returns two",
        imports=["using Xunit;"],
        source_directory=".",
        source_file_paths=["Calculator.cs"],
        unit_test="[Fact]\npublic void Adds()\n{\n    Assert.Equal(2, 1 + 1);\n}\n",
    )
    values.update(overrides)
    return UnitTest(**values)


def create_sample_lab(tmp_path: Path) -> Path:
    lab_dir = tmp_path / "sample_lab"
    lab_dir.mkdir()
    (lab_dir / "math_lab.py").write_text(
        "def double(value):\n"
        "    return value * 2\n",
        encoding="utf-8",
    )
    return lab_dir


def write_input_artifacts(output_root: Path, codebase_name: str) -> Path:
    output_dir = output_root / codebase_name
    output_dir.mkdir(parents=True)
    unit_tests_json = output_dir / "unit_tests.json"
    unit_tests_txt = output_dir / "unit_tests.txt"
    unit_tests_json.write_text('[{"id": 1}]\n', encoding="utf-8")
    unit_tests_txt.write_text("original generated tests\n", encoding="utf-8")
    return output_dir


def test_python_validation_runs_pytest_without_llm(tmp_path, monkeypatch):
    lab_dir = create_sample_lab(tmp_path)
    output_root = tmp_path / "UT_agent_output"
    output_dir = write_input_artifacts(output_root, lab_dir.name)
    original_json = (output_dir / "unit_tests.json").read_bytes()
    original_txt = (output_dir / "unit_tests.txt").read_bytes()

    def boom(*args, **kwargs):
        raise AssertionError("make_llm should not be called for Python validation")

    monkeypatch.setattr("agent.UTV_agent.make_llm", boom)

    state = UTVAgent().run(
        [python_unit_test()],
        lab_dir.name,
        str(lab_dir),
        output_dir=str(output_root),
    )

    report = output_dir / "test_report.json"
    report_data = report.read_text(encoding="utf-8")
    assert '"harness": "pytest"' in report_data
    assert "[Fact]" not in report_data
    assert ".csproj" not in report_data
    assert state["test_run"].passed == 1
    assert state["test_run"].project_error == ""
    assert (output_dir / "unit_tests.json").read_bytes() == original_json
    assert (output_dir / "unit_tests.txt").read_bytes() == original_txt


def test_invalid_python_import_is_dropped_not_executed(tmp_path, monkeypatch):
    lab_dir = create_sample_lab(tmp_path)
    output_root = tmp_path / "UT_agent_output"
    monkeypatch.setattr("agent.UTV_agent.make_llm", lambda *a, **k: (_ for _ in ()).throw(AssertionError("llm")))

    state = UTVAgent().run(
        [python_unit_test(imports=["from math import ("])],
        lab_dir.name,
        str(lab_dir),
        output_dir=str(output_root),
    )

    run = state["test_run"]
    assert run.kept == []
    assert run.dropped
    assert "invalid Python import" in run.dropped[0].reason
    assert run.passed == 0
    report = (output_root / lab_dir.name / "test_report.json").read_text(encoding="utf-8")
    assert '"harness": "pytest"' in report


def test_csharp_validation_uses_xunit_harness(tmp_path, monkeypatch):
    called = {"python": False, "csharp": False}

    def fake_csharp(*args, **kwargs):
        called["csharp"] = True
        return TestRun(kind="Unit", generated=1)

    def fake_python(*args, **kwargs):
        called["python"] = True
        raise AssertionError("Python harness should not run for C# tests")

    monkeypatch.setattr("agent.UTV_agent.check_csharp_tests", fake_csharp)
    monkeypatch.setattr("agent.UTV_agent.check_python_tests", fake_python)
    monkeypatch.setattr("agent.UTV_agent.make_fixer", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "agent.UTV_agent.make_llm",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("use injected model")),
    )
    monkeypatch.setattr(
        "agent.UTV_agent.SentenceTransformerEmbeddingFunction",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("no chroma in this test")),
    )

    output_root = tmp_path / "UT_agent_output"
    state = UTVAgent(model=object()).run(
        [csharp_unit_test()],
        "csharp_lab",
        str(tmp_path),
        output_dir=str(output_root),
    )

    assert called == {"python": False, "csharp": True}
    report = (output_root / "csharp_lab" / "test_report.json").read_text(encoding="utf-8")
    assert '"harness": "xunit"' in report
    assert state["test_run"].generated == 1


def test_detection_error_does_not_write_reports(tmp_path, monkeypatch):
    output_root = tmp_path / "UT_agent_output"
    output_dir = write_input_artifacts(output_root, "mixed_lab")
    report = output_dir / "test_report.json"
    report.write_text('{"harness": "previous"}\n', encoding="utf-8")
    before_report = report.read_bytes()
    before_json = (output_dir / "unit_tests.json").read_bytes()
    before_txt = (output_dir / "unit_tests.txt").read_bytes()

    monkeypatch.setattr("agent.UTV_agent.make_llm", lambda *a, **k: (_ for _ in ()).throw(AssertionError("llm")))

    with pytest.raises(ValueError, match="disagree|one language at a time"):
        UTVAgent().run(
            [
                python_unit_test(),
                csharp_unit_test(id=2),
            ],
            "mixed_lab",
            str(tmp_path),
            output_dir=str(output_root),
        )

    assert report.read_bytes() == before_report
    assert (output_dir / "unit_tests.json").read_bytes() == before_json
    assert (output_dir / "unit_tests.txt").read_bytes() == before_txt
    assert not (output_dir / "validated_tests.json").exists()
    assert not (output_dir / "discarded_tests.json").exists()


def test_empty_test_list_does_not_write_reports(tmp_path, monkeypatch):
    output_root = tmp_path / "UT_agent_output"
    monkeypatch.setattr("agent.UTV_agent.make_llm", lambda *a, **k: (_ for _ in ()).throw(AssertionError("llm")))

    with pytest.raises(ValueError, match="no unit tests were provided"):
        UTVAgent().run([], "empty_lab", str(tmp_path), output_dir=str(output_root))

    assert not (output_root / "empty_lab" / "test_report.json").exists()


def test_missing_unit_tests_file_names_unit_tests_not_rules(tmp_path):
    missing = tmp_path / "no-such-unit-tests.json"
    result = Commands().validate_unit_tests(
        str(tmp_path / "missing_lab"),
        test_path=str(missing),
    )
    assert result["success"] is False
    assert "unit tests not found" in result["error"].lower()
    assert "Validated rules" not in result["error"]
