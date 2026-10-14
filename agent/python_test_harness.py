"""
Python test-harness support for Checkpoint.

Creates isolated pytest files outside the selected student codebase.
"""

import ast
import re
import hashlib
import os
import tempfile
import sys
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any

PYTEST_TIMEOUT_SECONDS = 60

@dataclass
class PythonTestRun:
    """
    The result of running generated pytest tests.
    """
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    errors: int = 0
    failed_tests: list[str] | None = None
    report_path: str = ""
    output: str = ""
    project_error: str = ""

    def __post_init__(self):
        if self.failed_tests is None:
            self.failed_tests = []

@dataclass
class PythonTestCase:
    """
    One generated pytest awaiting validation and execution.
    """
    name: str
    imports: list[str]
    body: str
    source: Any = None
    errors: list[str] | None = None
    reason: str = ""

MAX_TEST_CHARS = 20_000


def clean_test(
    body: str,
    imports: list[str] | None,
) -> tuple[str | None, list[str], str]:
    """
    Validate AI-generated pytest code before it is written to disk.

    Returns:
        body, imports, reason

    A blank reason means the test is safe to write and execute.
    """
    body = (body or "").strip()
    cleaned_imports = [
        statement.strip()
        for statement in (imports or [])
        if statement and statement.strip()
    ]

    body = re.sub(r"^```(?:python)?\s*\n?", "", body)
    body = re.sub(r"\n?```\s*$", "", body).strip()

    if not body:
        return None, cleaned_imports, "the AI returned an empty test"

    if len(body) > MAX_TEST_CHARS:
        return None, cleaned_imports, "the AI generated an unusually large test"

    try:
        ast.parse("\n".join(cleaned_imports))
    except SyntaxError as error:
        return None, cleaned_imports, f"invalid Python import: {error.msg}"

    try:
        tree = ast.parse(body)
    except SyntaxError as error:
        return None, cleaned_imports, f"invalid Python test syntax: {error.msg}"

    test_functions = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    ]

    if len(test_functions) != 1:
        return (
            None,
            cleaned_imports,
            "expected exactly one top-level pytest function named test_*",
        )

    return body, cleaned_imports, ""

def make_cases(prefix: str, items, body_of, imports_of):
    """
    Turn generated agent output into valid Python test cases.

    Invalid AI output is retained as rejected output with a clear reason,
    rather than being executed.
    """
    cases = []
    rejected = []
    used_names = set()

    for item_id, item in items:
        name = f"{prefix}_{item_id}"

        while name in used_names:
            name += "_b"

        used_names.add(name)

        body, imports, reason = clean_test(
            body_of(item),
            imports_of(item),
        )

        case = PythonTestCase(
            name=name,
            imports=imports,
            body=body or "",
            source=item,
            reason=reason,
        )

        if reason:
            rejected.append(case)
        else:
            cases.append(case)

    return cases, rejected

def test_dir_for(codebase_path: str, codebase_name: str) -> str:
    """
    Return a stable temporary folder for generated pytest files.

    The folder stays outside the selected lab so Checkpoint never modifies
    the student's original work.
    """
    source_path = Path(codebase_path).resolve()
    path_hash = hashlib.sha256(os.fsencode(source_path)).hexdigest()[:16]

    output_root = (
        Path(tempfile.gettempdir())
        / "checkpoint-generated-tests"
        / path_hash
    )

    return str(output_root / f"{codebase_name}_pytest")

def render_test(case: PythonTestCase) -> str:
    """
    Build one complete pytest file from a generated test case.
    """
    imports = "\n".join(case.imports).strip()
    body = case.body.strip()

    sections = [section for section in (imports, body) if section]
    return "\n\n".join(sections) + "\n"


def write_tests(test_dir: str, prefix: str, cases: list[PythonTestCase]) -> None:
    """
    Write one generated pytest file per test case.

    Only removes files belonging to this test type. For example, writing
    unit tests removes test_UT_*.py but preserves integration-test files.
    """
    folder = Path(test_dir)
    folder.mkdir(parents=True, exist_ok=True)

    for old_file in folder.glob(f"test_{prefix}_*.py"):
        old_file.unlink()

    for case in cases:
        test_file = folder / f"test_{case.name}.py"
        test_file.write_text(render_test(case), encoding="utf-8")

def _python_environment(codebase_path: str) -> dict[str, str]:
    """
    Let generated tests import modules from the selected lab directory,
    without moving or modifying the lab.
    """
    environment = os.environ.copy()
    source_directory = str(Path(codebase_path).resolve())

    existing_path = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = (
        source_directory
        if not existing_path
        else source_directory + os.pathsep + existing_path
    )

    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    return environment


def _read_junit_report(report_path: Path) -> tuple[int, int, int, int, list[str]]:
    """Read pytest's JUnit XML report into simple Checkpoint result values."""
    root = ET.parse(report_path).getroot()

    passed = failed = skipped = errors = 0
    failed_tests = []

    for test_case in root.iter("testcase"):
        test_name = test_case.get("name", "unknown test")
        class_name = test_case.get("classname", "")
        display_name = f"{class_name}.{test_name}" if class_name else test_name

        if test_case.find("failure") is not None:
            failed += 1
            failed_tests.append(display_name)
        elif test_case.find("error") is not None:
            errors += 1
            failed_tests.append(display_name)
        elif test_case.find("skipped") is not None:
            skipped += 1
        else:
            passed += 1

    return passed, failed, skipped, errors, failed_tests


def run_tests(
    test_dir: str,
    codebase_path: str,
    results_dir: str,
    timeout: int = PYTEST_TIMEOUT_SECONDS,
) -> PythonTestRun:
    """
    Run generated pytest files only.

    The selected lab is supplied through PYTHONPATH so test files stored in
    the temporary Checkpoint folder can import the lab module.
    """
    report_folder = Path(results_dir)
    report_folder.mkdir(parents=True, exist_ok=True)
    report_path = report_folder / "pytest_report.xml"

    command = [
        sys.executable,
        "-m",
        "pytest",
        test_dir,
        "-q",
        f"--junitxml={report_path}",
    ]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=_python_environment(codebase_path),
        )
    except subprocess.TimeoutExpired:
        return PythonTestRun(
            project_error=f"pytest exceeded the {timeout}-second timeout.",
        )

    output = (result.stdout or "") + "\n" + (result.stderr or "")

    if not report_path.exists():
        if "No module named pytest" in output:
            message = "pytest is not installed in Checkpoint's active Python environment."
        else:
            message = "pytest did not create a test report."
        return PythonTestRun(project_error=message, output=output)

    passed, failed, skipped, errors, failed_tests = _read_junit_report(report_path)

    return PythonTestRun(
        passed=passed,
        failed=failed,
        skipped=skipped,
        errors=errors,
        failed_tests=failed_tests,
        report_path=str(report_path),
        output=output,
    )