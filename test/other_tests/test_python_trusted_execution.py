"""Trusted, hand-authored pytest execution through Checkpoint's Python harness."""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from agent.python_test_harness import (
    CHECKPOINT_CODEBASE_PATH,
    PythonTestRun,
    check_tests,
    clean_test,
    make_cases,
    run_tests,
    write_tests,
)
from agent.structured_output.UT_output import UnitTest

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "python_e2e"

FUNCTION_BODY = (
    "import os\n"
    "from pathlib import Path\n"
    "from math_lab import classify, double\n"
    "\n"
    "def test_double_and_classify_use_the_trusted_lab():\n"
    "    lab = Path(os.environ['CHECKPOINT_CODEBASE_PATH'])\n"
    "    assert lab.name == 'function_lab'\n"
    "    assert (lab / 'math_lab.py').is_file()\n"
    "    assert double(4) == 8\n"
    "    assert classify(-1) == 'negative'\n"
)

FAILING_BODY = (
    "from math_lab import double\n"
    "\n"
    "def test_double_with_wrong_expected_value():\n"
    "    assert double(4) == 99\n"
)

SCRIPT_BODY = (
    "def test_grade_script_reports_pass_for_score_72():\n"
    "    script = Path(os.environ['CHECKPOINT_CODEBASE_PATH']) / 'grade_script.py'\n"
    "    result = subprocess.run(\n"
    "        [sys.executable, str(script)],\n"
    "        input='72\\n',\n"
    "        capture_output=True,\n"
    "        text=True,\n"
    "        timeout=5,\n"
    "    )\n"
    "    assert result.returncode == 0\n"
    "    assert result.stdout.strip() == 'PASS'\n"
    "    assert 'grade_script' not in sys.modules\n"
)

SCRIPT_IMPORTS = [
    "import os",
    "import subprocess",
    "import sys",
    "from pathlib import Path",
]

SLEEPER_BODY = (
    "import os\n"
    "import time\n"
    "from pathlib import Path\n"
    "\n"
    "def test_sleeps_until_harness_timeout():\n"
    "    marker = Path(os.environ['CHECKPOINT_CODEBASE_PATH']) / 'sleeper_pids.txt'\n"
    "    group = os.getpgrp() if hasattr(os, 'getpgrp') else os.getpid()\n"
    "    marker.write_text(f'{os.getpid()} {group}\\n', encoding='utf-8')\n"
    "    time.sleep(30)\n"
)

UNSAFE_SCRIPT_BODY = (
    "def test_must_not_import_student_code():\n"
    "    assert True\n"
)


def _copy_lab(tmp_path: Path, name: str) -> Path:
    dest = tmp_path / name
    shutil.copytree(FIXTURE_ROOT / name, dest)
    return dest


def _generated_test(**overrides) -> UnitTest:
    values = dict(
        id=1,
        rule="trusted execution fixture",
        imports=["from math_lab import classify, double"],
        source_directory=".",
        source_file_paths=["math_lab.py"],
        unit_test=FUNCTION_BODY,
        test_mode="function",
    )
    values.update(overrides)
    return UnitTest.model_validate(values)


def _make(lab: Path, tests: list[UnitTest]):
    return make_cases(
        "UT",
        [(test.id, test) for test in tests],
        body_of=lambda item: item.unit_test,
        imports_of=lambda item: item.imports,
        codebase_path=str(lab),
    )


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        # os.kill(pid, 0) checks nothing on Windows: signal 0 is CTRL_C_EVENT,
        # and it raises no error whether or not the process exists.
        listing = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
        ).stdout
        return f'"{pid}"' in listing
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def test_function_lab_tests_execute_and_pass(tmp_path):
    lab = _copy_lab(tmp_path, "function_lab")
    original = (lab / "math_lab.py").read_bytes()
    test_dir = tmp_path / "generated_tests"
    results_dir = tmp_path / "results"

    cases, rejected = _make(lab, [_generated_test()])
    assert rejected == []
    assert cases[0].test_mode == "function"

    write_tests(str(test_dir), "UT", cases)
    result = run_tests(
        test_dir=str(test_dir),
        codebase_path=str(lab),
        results_dir=str(results_dir),
    )

    assert result.project_error == ""
    assert result.passed == 1
    assert result.failed == 0
    assert result.errors == 0
    assert Path(result.report_path).is_file()
    assert (lab / "math_lab.py").read_bytes() == original
    assert not (FIXTURE_ROOT / "function_lab" / "sleeper_pids.txt").exists()


def test_function_lab_failure_is_not_reported_as_success(tmp_path):
    lab = _copy_lab(tmp_path, "function_lab")
    test_dir = tmp_path / "generated_tests"
    results_dir = tmp_path / "results"

    cases, rejected = _make(
        lab,
        [_generated_test(id=2, unit_test=FAILING_BODY, imports=["from math_lab import double"])],
    )
    assert rejected == []
    write_tests(str(test_dir), "UT", cases)
    result = run_tests(
        test_dir=str(test_dir),
        codebase_path=str(lab),
        results_dir=str(results_dir),
    )

    assert result.project_error == ""
    assert result.passed == 0
    assert result.failed == 1
    assert result.failed_tests
    assert result.needs_attention() is True


def test_script_lab_runs_grade_script_over_stdin(tmp_path):
    lab = _copy_lab(tmp_path, "script_lab")
    test_dir = tmp_path / "generated_tests"
    results_dir = tmp_path / "results"

    body, imports, reason = clean_test(
        SCRIPT_BODY,
        SCRIPT_IMPORTS,
        test_mode="script",
    )
    assert reason == ""
    assert body is not None

    generated = _generated_test(
        rule="a score of 72 prints PASS",
        imports=imports,
        source_file_paths=["grade_script.py"],
        unit_test=body,
        test_mode="script",
    )
    cases, rejected = _make(lab, [generated])
    assert rejected == []
    assert cases[0].test_mode == "script"
    assert cases[0].source.source_file_paths == ["grade_script.py"]

    write_tests(str(test_dir), "UT", cases)
    result = run_tests(
        test_dir=str(test_dir),
        codebase_path=str(lab),
        results_dir=str(results_dir),
    )

    assert result.project_error == ""
    assert result.passed == 1
    assert result.failed == 0
    assert "grade_script" not in sys.modules


def test_sleeping_pytest_is_timed_out_and_process_group_is_killed(tmp_path):
    lab = _copy_lab(tmp_path, "function_lab")
    test_dir = tmp_path / "generated_tests"
    results_dir = tmp_path / "results"
    marker = lab / "sleeper_pids.txt"

    cases, rejected = _make(
        lab,
        [_generated_test(id=3, imports=[], unit_test=SLEEPER_BODY)],
    )
    assert rejected == []
    write_tests(str(test_dir), "UT", cases)

    started = time.monotonic()
    result = run_tests(
        test_dir=str(test_dir),
        codebase_path=str(lab),
        results_dir=str(results_dir),
        # Long enough for pytest to start the sleeper (over a second on
        # Windows), far below the 30 seconds it would otherwise sleep.
        timeout=5,
    )
    elapsed = time.monotonic() - started

    assert "timeout" in result.project_error
    assert result.passed == 0
    assert result.failed == 0
    assert result.needs_attention() is True
    assert elapsed < 10
    assert marker.is_file()

    pid_text, pgid_text = marker.read_text(encoding="utf-8").split()
    pid = int(pid_text)
    pgid = int(pgid_text)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and _pid_is_running(pid):
        time.sleep(0.05)
    assert _pid_is_running(pid) is False
    if os.name != "nt":
        assert _pid_is_running(pgid) is False


def test_unsafe_script_is_rejected_before_pytest(tmp_path, monkeypatch):
    lab = _copy_lab(tmp_path, "script_lab")
    test_dir = tmp_path / "generated_tests"
    monkeypatch.setattr(
        "agent.python_test_harness.test_dir_for",
        lambda codebase_path, codebase_name: str(test_dir),
    )
    run_calls = []
    monkeypatch.setattr(
        "agent.python_test_harness.run_tests",
        lambda **kwargs: run_calls.append(kwargs) or PythonTestRun(passed=9),
    )

    unsafe = _generated_test(
        imports=["from math_lab import double"],
        source_file_paths=["grade_script.py"],
        unit_test=UNSAFE_SCRIPT_BODY,
        test_mode="script",
    )
    cases, rejected = _make(lab, [unsafe])
    assert cases == []
    assert rejected[0].test_mode == "script"
    assert "subprocess" in rejected[0].reason or "import" in rejected[0].reason

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
    assert len(run.dropped) == 1
    assert run.needs_attention() is True
    assert run_calls == []
    assert list(test_dir.glob("test_UT_*.py")) == []


def test_rejected_sibling_does_not_block_valid_function_execution(tmp_path, monkeypatch):
    lab = _copy_lab(tmp_path, "function_lab")
    test_dir = tmp_path / "generated_tests"
    results_dir = tmp_path / "results"
    monkeypatch.setattr(
        "agent.python_test_harness.test_dir_for",
        lambda codebase_path, codebase_name: str(test_dir),
    )

    valid = _generated_test(id=1)
    unsafe = _generated_test(
        id=2,
        imports=["from math_lab import double"],
        source_file_paths=["math_lab.py"],
        unit_test=UNSAFE_SCRIPT_BODY,
        test_mode="script",
    )
    cases, rejected = _make(lab, [valid, unsafe])
    assert len(cases) == 1
    assert cases[0].test_mode == "function"
    assert len(rejected) == 1
    assert rejected[0].test_mode == "script"

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
    assert run.project_error == ""
    assert len(run.kept) == 1
    assert len(run.dropped) == 1
    assert run.needs_attention() is True
    assert Path(run.report_path).is_file()
    written = list(test_dir.glob("test_UT_*.py"))
    assert len(written) == 1
