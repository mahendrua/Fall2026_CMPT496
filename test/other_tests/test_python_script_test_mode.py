import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from agent.python_test_harness import (
    CHECKPOINT_CODEBASE_PATH,
    PythonTestRun,
    check_tests,
    clean_test,
    classify_python_file,
    classify_python_test_mode,
    codebase_relative_path,
    make_cases,
    source_paths_are_import_safe,
    trusted_python_test_mode,
    trusted_script_source_paths,
    _python_environment,
)
from agent.structured_output.UT_output import UnitTest
from agent.structured_output.UTV_output import UnitTest as UTVUnitTest
from agent.testing_language import unit_test_generation_instructions


def write_py(path: Path, source: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


def test_script_only_input_print_selects_script_mode(tmp_path):
    script = write_py(
        tmp_path / "temp.py",
        "celsius = float(input('C: '))\nprint(celsius * 9 / 5 + 32)\n",
    )
    assert classify_python_file(script) == "script"
    assert classify_python_test_mode([str(script)], str(tmp_path)) == "script"


def test_pure_function_module_selects_function_mode(tmp_path):
    module = write_py(
        tmp_path / "math_lab.py",
        "def double(value):\n    return value * 2\n",
    )
    assert classify_python_file(module) == "function"
    assert classify_python_test_mode([str(module)], str(tmp_path)) == "function"
    assert source_paths_are_import_safe([str(module)], str(tmp_path))


def test_mixed_unconditional_input_selects_script_mode(tmp_path):
    mixed = write_py(
        tmp_path / "mixed.py",
        "def helper():\n    return 1\n\nvalue = input('x')\nprint(value)\n",
    )
    assert classify_python_file(mixed) == "script"


def test_function_with_main_guard_remains_function_mode(tmp_path):
    module = write_py(
        tmp_path / "app.py",
        "def main():\n    print('hi')\n\nif __name__ == '__main__':\n    main()\n",
    )
    assert classify_python_file(module) == "function"


def test_top_level_loop_selects_script_mode(tmp_path):
    module = write_py(
        tmp_path / "loop.py",
        "def add(a, b):\n    return a + b\n\nfor item in range(3):\n    print(item)\n",
    )
    assert classify_python_file(module) == "script"


def test_missing_paths_are_not_import_safe():
    assert classify_python_test_mode([], "/labs") == "function"
    assert classify_python_test_mode(["missing.py"], "/labs") == "function"
    assert source_paths_are_import_safe([], "/labs") is False
    assert source_paths_are_import_safe(["missing.py"], "/labs") is False
    assert trusted_python_test_mode([], "/labs") == "script"
    assert trusted_python_test_mode(["missing.py"], "/labs") == "script"


def test_g2_empty_paths_do_not_invent_a_script(tmp_path):
    write_py(tmp_path / "script.py", "print(1)\n")
    mode = classify_python_test_mode([], str(tmp_path))
    assert mode == "function"
    assert trusted_python_test_mode([], str(tmp_path)) == "script"
    assert trusted_script_source_paths([], str(tmp_path)) == []
    assert codebase_relative_path("", str(tmp_path)) is None


def test_ambiguous_function_and_script_files_stay_function(tmp_path):
    function_file = write_py(tmp_path / "lib.py", "def add(a, b):\n    return a + b\n")
    script_file = write_py(tmp_path / "run.py", "print(1)\n")
    assert classify_python_test_mode(
        [str(function_file), str(script_file)],
        str(tmp_path),
    ) == "function"
    assert source_paths_are_import_safe(
        [str(function_file), str(script_file)],
        str(tmp_path),
    ) is False
    assert trusted_python_test_mode(
        [str(function_file), str(script_file)],
        str(tmp_path),
    ) == "script"


def test_script_prompt_requires_subprocess():
    instructions = unit_test_generation_instructions("python", test_mode="script")
    assert "subprocess" in instructions
    assert "CHECKPOINT_CODEBASE_PATH" in instructions
    assert "shell=True" in instructions
    assert "student" in instructions.lower()


def test_function_prompt_still_allows_real_imports():
    instructions = unit_test_generation_instructions("python")
    assert "Import only real functions or classes" in instructions
    assert "subprocess.run" not in instructions


def test_csharp_instructions_unchanged():
    instructions = unit_test_generation_instructions("csharp")
    assert "[Fact]" in instructions
    assert "subprocess" not in instructions


def test_script_clean_test_accepts_safe_stdlib_and_one_test():
    body, imports, reason = clean_test(
        (
            "def test_script_exits_cleanly():\n"
            "    result = subprocess.run([sys.executable, str(script)], "
            "input='1\\n', capture_output=True, text=True, timeout=5)\n"
            "    assert result.returncode == 0\n"
        ),
        ["import subprocess", "import sys", "from pathlib import Path"],
        test_mode="script",
    )
    assert reason == ""
    assert body.startswith("def test_script_exits_cleanly")
    assert "import subprocess" in imports


def test_script_clean_test_rejects_student_import():
    _, _, reason = clean_test(
        "def test_x():\n    assert True\n",
        ["from homework import convert"],
        test_mode="script",
    )
    assert "subprocess" in reason


def test_script_clean_test_rejects_dynamic_import_and_exec():
    _, _, exec_reason = clean_test(
        "exec('x=1')\ndef test_x():\n    assert True\n",
        ["import subprocess"],
        test_mode="script",
    )
    assert exec_reason

    _, _, import_reason = clean_test(
        "def test_x():\n    assert True\n",
        ["import importlib"],
        test_mode="script",
    )
    assert "pytest" in import_reason or "subprocess" in import_reason


def test_script_clean_test_rejects_module_calls_comprehensions_decorators_defaults():
    _, _, call_reason = clean_test(
        "subprocess.run(['true'])\ndef test_x():\n    assert True\n",
        ["import subprocess"],
        test_mode="script",
    )
    assert call_reason

    _, _, comp_reason = clean_test(
        "values = [n for n in range(3)]\ndef test_x():\n    assert True\n",
        ["import subprocess"],
        test_mode="script",
    )
    assert "comprehension" in comp_reason

    _, _, deco_reason = clean_test(
        "@pytest.mark.skip\ndef test_x():\n    assert True\n",
        ["import pytest"],
        test_mode="script",
    )
    assert "decorator" in deco_reason

    _, _, default_reason = clean_test(
        "def test_x(value=input('x')):\n    assert True\n",
        ["import subprocess"],
        test_mode="script",
    )
    assert "default" in default_reason


def test_script_clean_test_rejects_invalid_syntax():
    body, _, reason = clean_test(
        "def test_broken(:\n    assert True",
        ["import subprocess"],
        test_mode="script",
    )
    assert body is None
    assert "syntax" in reason


def test_function_mode_still_allows_math_lab_import():
    body, imports, reason = clean_test(
        "def test_double_returns_twice_the_value():\n    assert double(4) == 8\n",
        ["from math_lab import double"],
        test_mode="function",
    )
    assert reason == ""
    assert imports == ["from math_lab import double"]
    assert body.startswith("def test_double")


def test_unit_test_mode_json_roundtrip_and_make_cases():
    payload = {
        "id": 3,
        "rule": "Print the scaled value.",
        "imports": ["import subprocess", "import sys"],
        "source_directory": ".",
        "source_file_paths": ["temp.py"],
        "unit_test": (
            "def test_scaled_value():\n"
            "    result = subprocess.run([sys.executable, 'temp.py'], "
            "input='1\\n', capture_output=True, text=True, timeout=5)\n"
            "    assert result.returncode == 0\n"
        ),
        "test_mode": "script",
    }
    loaded = UnitTest.model_validate(payload)
    assert loaded.test_mode == "script"
    assert UnitTest.model_validate(
        json.loads(loaded.model_dump_json())
    ).test_mode == "script"

    legacy = UnitTest.model_validate(
        {key: value for key, value in payload.items() if key != "test_mode"}
    )
    assert legacy.test_mode == "function"

    cases, rejected = make_cases(
        "UT",
        [(loaded.id, loaded)],
        body_of=lambda test: test.unit_test,
        imports_of=lambda test: test.imports,
    )
    assert rejected == []
    assert cases[0].test_mode == "script"

    unsafe = SimpleNamespace(
        test_mode="script",
        unit_test="def test_x():\n    assert True\n",
        imports=["from homework import convert"],
    )
    cases, rejected = make_cases(
        "UT",
        [(9, unsafe)],
        body_of=lambda test: test.unit_test,
        imports_of=lambda test: test.imports,
    )
    assert cases == []
    assert rejected[0].test_mode == "script"
    assert rejected[0].reason


def test_paths_with_spaces_are_resolved(tmp_path):
    script = write_py(
        tmp_path / "student lab" / "temp converter.py",
        "print(32)\n",
    )
    relative = codebase_relative_path(str(script), str(tmp_path))
    assert relative == "student lab/temp converter.py"
    assert classify_python_test_mode([str(script)], str(tmp_path)) == "script"


def test_harness_sets_checkpoint_codebase_path(tmp_path):
    environment = _python_environment(str(tmp_path))
    assert environment[CHECKPOINT_CODEBASE_PATH] == str(tmp_path.resolve())
    assert environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"


def _fake_unit_test(**overrides):
    values = dict(
        id=1,
        rule="Print a converted value.",
        imports=["import subprocess", "import sys"],
        source_directory=".",
        source_file_paths=[],
        unit_test=(
            "def test_script_exits_cleanly():\n"
            "    result = subprocess.run([sys.executable, 'guess.py'], "
            "input='1\\n', capture_output=True, text=True, timeout=5)\n"
            "    assert result.returncode == 0\n"
        ),
        test_mode="function",
    )
    values.update(overrides)
    return UnitTest.model_validate(values)


def test_g2_empty_student_import_is_rejected_by_make_cases(tmp_path):
    generated = _fake_unit_test(
        imports=["from student_lab import fn"],
        unit_test="def test_fn():\n    assert fn() == 1\n",
        test_mode="function",
        source_file_paths=[],
    )
    assert trusted_python_test_mode(generated.source_file_paths, str(tmp_path)) == "script"
    assert trusted_script_source_paths(generated.source_file_paths, str(tmp_path)) == []

    cases, rejected = make_cases(
        "UT",
        [(generated.id, generated)],
        body_of=lambda test: test.unit_test,
        imports_of=lambda test: test.imports,
        codebase_path=str(tmp_path),
    )
    assert cases == []
    assert rejected[0].test_mode == "script"
    assert rejected[0].reason


def test_mixed_sources_with_student_import_are_script_gated(tmp_path):
    function_file = write_py(tmp_path / "lib.py", "def add(a, b):\n    return a + b\n")
    script_file = write_py(tmp_path / "run.py", "print(1)\n")
    generated = _fake_unit_test(
        imports=["from lib import add"],
        unit_test="def test_add():\n    assert add(1, 2) == 3\n",
        source_file_paths=[str(function_file), str(script_file)],
        test_mode="function",
    )
    cases, rejected = make_cases(
        "UT",
        [(generated.id, generated)],
        body_of=lambda test: test.unit_test,
        imports_of=lambda test: test.imports,
        codebase_path=str(tmp_path),
    )
    assert cases == []
    assert rejected[0].test_mode == "script"
    assert "subprocess" in rejected[0].reason or "import" in rejected[0].reason.lower()


def test_valid_function_file_plus_missing_path_is_script_gated(tmp_path):
    function_file = write_py(tmp_path / "lib.py", "def add(a, b):\n    return a + b\n")
    generated = _fake_unit_test(
        imports=["from lib import add"],
        unit_test="def test_add():\n    assert add(1, 2) == 3\n",
        source_file_paths=[str(function_file), "missing.py"],
        test_mode="function",
    )
    assert source_paths_are_import_safe(
        generated.source_file_paths,
        str(tmp_path),
    ) is False
    cases, rejected = make_cases(
        "UT",
        [(generated.id, generated)],
        body_of=lambda test: test.unit_test,
        imports_of=lambda test: test.imports,
        codebase_path=str(tmp_path),
    )
    assert cases == []
    assert rejected[0].test_mode == "script"


def test_verified_function_module_still_accepts_math_lab_import(tmp_path):
    module = write_py(tmp_path / "math_lab.py", "def double(value):\n    return value * 2\n")
    generated = _fake_unit_test(
        imports=["from math_lab import double"],
        unit_test="def test_double_returns_twice_the_value():\n    assert double(4) == 8\n",
        source_file_paths=[str(module)],
        test_mode="function",
    )
    assert trusted_python_test_mode(generated.source_file_paths, str(tmp_path)) == "function"
    cases, rejected = make_cases(
        "UT",
        [(generated.id, generated)],
        body_of=lambda test: test.unit_test,
        imports_of=lambda test: test.imports,
        codebase_path=str(tmp_path),
    )
    assert rejected == []
    assert cases[0].test_mode == "function"
    assert cases[0].imports == ["from math_lab import double"]


def test_verified_script_only_module_stays_script(tmp_path):
    script = write_py(tmp_path / "temp.py", "print(32)\n")
    generated = _fake_unit_test(
        source_file_paths=[str(script)],
        test_mode="script",
        unit_test=(
            "def test_script_exits_cleanly():\n"
            "    result = subprocess.run([sys.executable, str(script)], "
            "input='1\\n', capture_output=True, text=True, timeout=5)\n"
            "    assert result.returncode == 0\n"
        ),
    )
    assert trusted_python_test_mode(generated.source_file_paths, str(tmp_path)) == "script"
    cases, rejected = make_cases(
        "UT",
        [(generated.id, generated)],
        body_of=lambda test: test.unit_test,
        imports_of=lambda test: test.imports,
        codebase_path=str(tmp_path),
    )
    assert rejected == []
    assert cases[0].test_mode == "script"


def test_script_without_trustworthy_target_is_dropped(tmp_path):
    generated = _fake_unit_test(
        source_file_paths=[],
        test_mode="script",
    )
    cases, rejected = make_cases(
        "UT",
        [(generated.id, generated)],
        body_of=lambda test: test.unit_test,
        imports_of=lambda test: test.imports,
        codebase_path=str(tmp_path),
    )
    assert cases == []
    assert "trustworthy target" in rejected[0].reason


def test_saved_script_mode_is_not_downgraded_by_make_cases(tmp_path):
    script = write_py(tmp_path / "temp.py", "print(32)\n")
    payload = _fake_unit_test(
        source_file_paths=[str(script)],
        test_mode="script",
        unit_test=(
            "def test_scaled_value():\n"
            "    result = subprocess.run([sys.executable, 'temp.py'], "
            "input='1\\n', capture_output=True, text=True, timeout=5)\n"
            "    assert result.returncode == 0\n"
        ),
    )
    loaded = UnitTest.model_validate(json.loads(payload.model_dump_json()))
    assert loaded.test_mode == "script"
    cases, rejected = make_cases(
        "UT",
        [(loaded.id, loaded)],
        body_of=lambda test: test.unit_test,
        imports_of=lambda test: test.imports,
        codebase_path=str(tmp_path),
    )
    assert rejected == []
    assert cases[0].test_mode == "script"


def _generator_record(**overrides):
    values = dict(
        id=4,
        rule="Print the converted value.",
        imports=["import subprocess", "import sys"],
        source_directory=".",
        source_file_paths=["temp.py"],
        unit_test=(
            "def test_scaled_value():\n"
            "    result = subprocess.run([sys.executable, 'temp.py'], "
            "input='1\\n', capture_output=True, text=True, timeout=5)\n"
            "    assert result.returncode == 0\n"
        ),
        test_mode="script",
    )
    values.update(overrides)
    return UnitTest.model_validate(values)


def test_ut_dump_loads_as_utv_script_and_function_records():
    script = _generator_record(test_mode="script")
    function = _generator_record(
        imports=["from math_lab import double"],
        unit_test="def test_double():\n    assert double(4) == 8\n",
        source_file_paths=["math_lab.py"],
        test_mode="function",
    )
    loaded_script = UTVUnitTest.model_validate(script.model_dump())
    loaded_function = UTVUnitTest.model_validate(function.model_dump())
    assert loaded_script.test_mode == "script"
    assert loaded_function.test_mode == "function"


def test_utv_legacy_json_defaults_to_function_and_rejects_invalid_mode():
    payload = _generator_record(test_mode="script").model_dump()
    del payload["test_mode"]
    assert UTVUnitTest.model_validate(payload).test_mode == "function"

    payload["test_mode"] = "template"
    with pytest.raises(ValidationError):
        UTVUnitTest.model_validate(payload)


def test_csharp_function_mode_record_loads_on_utv_model():
    csharp = UnitTest.model_validate(
        {
            "id": 1,
            "rule": "adding one and one returns two",
            "imports": ["using Xunit;"],
            "source_directory": ".",
            "source_file_paths": ["Calculator.cs"],
            "unit_test": "[Fact]\npublic void Adds()\n{\n    Assert.Equal(2, 1 + 1);\n}\n",
            "test_mode": "function",
        }
    )
    loaded = UTVUnitTest.model_validate(csharp.model_dump())
    assert loaded.test_mode == "function"
    assert loaded.imports == ["using Xunit;"]


def test_utv_loaded_script_record_keeps_collection_safety(tmp_path, monkeypatch):
    script = write_py(tmp_path / "temp.py", "print(32)\n")
    generated = _generator_record(
        source_file_paths=[str(script)],
        test_mode="script",
    )
    loaded = UTVUnitTest.model_validate(generated.model_dump())
    cases, rejected = make_cases(
        "UT",
        [(loaded.id, loaded)],
        body_of=lambda test: test.unit_test,
        imports_of=lambda test: test.imports,
        codebase_path=str(tmp_path),
    )
    assert rejected == []
    assert cases[0].test_mode == "script"

    written = []
    monkeypatch.setattr(
        "agent.python_test_harness.write_tests",
        lambda test_dir, prefix, safe: written.append(list(safe)),
    )
    monkeypatch.setattr(
        "agent.python_test_harness.run_tests",
        lambda **kwargs: PythonTestRun(passed=0),
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
    assert run.dropped == []
    assert run.kept[0].test_mode == "script"
    assert written and written[0][0].test_mode == "script"


def test_empty_path_script_student_import_is_not_written(tmp_path, monkeypatch):
    loaded = UTVUnitTest.model_validate(
        _generator_record(
            imports=["from student_lab import fn"],
            unit_test="def test_fn():\n    assert fn() == 1\n",
            source_file_paths=[],
            test_mode="script",
        ).model_dump()
    )
    cases, rejected = make_cases(
        "UT",
        [(loaded.id, loaded)],
        body_of=lambda test: test.unit_test,
        imports_of=lambda test: test.imports,
        codebase_path=str(tmp_path),
    )
    assert cases == []
    assert rejected[0].test_mode == "script"

    written = []
    monkeypatch.setattr(
        "agent.python_test_harness.write_tests",
        lambda test_dir, prefix, safe: written.append(list(safe)),
    )
    monkeypatch.setattr(
        "agent.python_test_harness.run_tests",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("pytest must not run")),
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
    assert run.kept == []
    assert written == [[]]


def test_unit_tests_json_loads_with_the_utv_validate_contract(tmp_path):
    json_path = tmp_path / "unit_tests.json"
    json_path.write_text(
        json.dumps([_generator_record(test_mode="script").model_dump()], indent=2),
        encoding="utf-8",
    )
    raw_tests = json.loads(json_path.read_text(encoding="utf-8"))
    loaded = [UTVUnitTest.model_validate(test) for test in raw_tests]
    assert loaded[0].test_mode == "script"
