from types import SimpleNamespace

import pytest

from agent.testing_language import (
    detect_test_language,
    detect_unit_test_language,
    unit_test_generation_instructions,
)


def make_test(
    source_file_paths=None,
    imports=None,
    unit_test="",
):
    return SimpleNamespace(
        source_file_paths=list(source_file_paths or []),
        imports=list(imports or []),
        unit_test=unit_test,
    )

def test_detects_python_from_rule_source_files():
    source_file_groups = [
        ["labs/time_lab.py", "labs/helpers.py"],
    ]

    assert detect_test_language(source_file_groups) == "python"


def test_rejects_mixed_language_rule_selection():
    source_file_groups = [
        ["labs/time_lab.py"],
        ["src/Program.cs"],
    ]

    with pytest.raises(ValueError, match="one language at a time"):
        detect_test_language(source_file_groups)

def test_python_generation_instructions_request_pytest():
    instructions = unit_test_generation_instructions("python")

    assert "pytest" in instructions.lower()
    assert "test_*" in instructions
    assert "assert" in instructions


def test_csharp_generation_instructions_request_xunit():
    instructions = unit_test_generation_instructions("csharp")

    assert "xunit" in instructions.lower()
    assert "[Fact]" in instructions


def test_detect_test_language_still_rejects_missing_source_files():
    with pytest.raises(ValueError, match="do not identify source files"):
        detect_test_language([[], [""]])


def test_unit_test_language_from_python_paths_and_pytest_body():
    tests = [
        make_test(
            source_file_paths=["labs/math_lab.py"],
            imports=["from math_lab import double"],
            unit_test="def test_double_returns_twice_the_value():\n    assert double(2) == 4\n",
        )
    ]

    assert detect_unit_test_language(tests) == "python"


def test_unit_test_language_from_async_pytest_function():
    tests = [
        make_test(
            source_file_paths=["labs/fetch.py"],
            imports=["import pytest"],
            unit_test=(
                "async def test_fetch_grade():\n"
                "    assert await fetch_grade() == 95\n"
            ),
        )
    ]

    assert detect_unit_test_language(tests) == "python"


def test_unit_test_language_from_csharp_paths_and_xunit_body():
    tests = [
        make_test(
            source_file_paths=["src/Calculator.cs"],
            imports=["using Xunit;"],
            unit_test="[Fact]\npublic void Adds()\n{\n    Assert.Equal(2, 1 + 1);\n}\n",
        )
    ]

    assert detect_unit_test_language(tests) == "csharp"


def test_unit_test_language_from_pytest_body_when_source_paths_are_missing():
    tests = [
        make_test(
            source_file_paths=[],
            unit_test="def test_identity():\n    assert True\n",
        )
    ]

    assert detect_unit_test_language(tests) == "python"


def test_unit_test_language_from_xunit_body_when_source_paths_are_missing():
    tests = [
        make_test(
            source_file_paths=[],
            imports=["using Xunit;"],
            unit_test="[Theory]\npublic void Multiplies()\n{\n}\n",
        )
    ]

    assert detect_unit_test_language(tests) == "csharp"


def test_unit_test_language_ignores_markers_in_comments_and_strings():
    tests = [
        make_test(
            source_file_paths=["labs/math_lab.py"],
            unit_test=(
                "# do not emit [Fact] or using Xunit;\n"
                "def test_comment_is_ignored():\n"
                "    text = '[Fact] using Xunit;'\n"
                "    assert text\n"
            ),
        )
    ]

    assert detect_unit_test_language(tests) == "python"


def test_unit_test_language_rejects_python_paths_with_xunit_body():
    tests = [
        make_test(
            source_file_paths=["labs/math_lab.py"],
            imports=["using Xunit;"],
            unit_test="[Fact]\npublic void Adds()\n{\n    Assert.Equal(2, 1 + 1);\n}\n",
        )
    ]

    with pytest.raises(ValueError, match="disagree"):
        detect_unit_test_language(tests)


def test_unit_test_language_rejects_csharp_paths_with_pytest_body():
    tests = [
        make_test(
            source_file_paths=["src/Program.cs"],
            unit_test="def test_from_csharp_path():\n    assert True\n",
        )
    ]

    with pytest.raises(ValueError, match="disagree"):
        detect_unit_test_language(tests)


def test_unit_test_language_rejects_mixed_source_suffixes():
    tests = [
        make_test(source_file_paths=["labs/math_lab.py"], unit_test="def test_a():\n    assert True\n"),
        make_test(
            source_file_paths=["src/Program.cs"],
            imports=["using Xunit;"],
            unit_test="[Fact]\npublic void Adds() {}",
        ),
    ]

    with pytest.raises(ValueError, match="one language at a time"):
        detect_unit_test_language(tests)


def test_unit_test_language_rejects_mixed_python_and_csharp_bodies():
    tests = [
        make_test(unit_test="def test_python():\n    assert True\n"),
        make_test(
            imports=["using Xunit;"],
            unit_test="[Fact]\npublic void Adds() {}",
        ),
    ]

    with pytest.raises(ValueError, match="mix Python and C#"):
        detect_unit_test_language(tests)


def test_unit_test_language_rejects_empty_list():
    with pytest.raises(ValueError, match="no unit tests were provided"):
        detect_unit_test_language([])


def test_unit_test_language_rejects_undecidable_tests():
    tests = [
        make_test(source_file_paths=[], imports=[], unit_test=""),
        make_test(source_file_paths=[""], unit_test="// no markers here"),
    ]

    with pytest.raises(ValueError, match="do not identify Python or C#"):
        detect_unit_test_language(tests)