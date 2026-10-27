"""Parent wrapper: run BR/UT graph cases in an isolated pytest subprocess."""

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CASES = Path(__file__).resolve().parent / "python_offline_br_ut_cases.py"
CHILD_TIMEOUT_SECONDS = 300
EXPECTED_CHILD_PASSES = 12
WATCHED_MODULES = (
    "chromadb",
    "chromadb.utils",
    "chromadb.utils.embedding_functions",
    "test.other_tests._chromadb_stub",
    "agent.BR_agent",
    "agent.UT_agent",
)


def _relevant_modules():
    snapshot = {name: sys.modules.get(name) for name in WATCHED_MODULES}
    for name, module in sys.modules.items():
        if name == "chromadb" or name.startswith("chromadb."):
            snapshot[name] = module
    return snapshot


def test_python_offline_br_ut_cases_run_in_isolated_subprocess():
    before = _relevant_modules()
    env = os.environ.copy()
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env["HF_HUB_OFFLINE"] = "1"

    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                str(CASES),
                "-q",
                "-p",
                "no:cacheprovider",
            ],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=CHILD_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        output = (error.stdout or "") + "\n" + (error.stderr or "")
        raise AssertionError(
            f"Child pytest exceeded {CHILD_TIMEOUT_SECONDS} seconds.\n{output}"
        ) from error

    child_output = (result.stdout or "") + "\n" + (result.stderr or "")
    assert result.returncode == 0, (
        f"Child pytest failed with exit {result.returncode}.\n{child_output}"
    )
    assert f"{EXPECTED_CHILD_PASSES} passed" in child_output, child_output
    assert "collected 0" not in child_output

    after = _relevant_modules()
    assert after == before, (
        "Parent chroma/agent module state changed after the child pytest.\n"
        f"before={sorted(before)}\nafter={sorted(after)}\n{child_output}"
    )
