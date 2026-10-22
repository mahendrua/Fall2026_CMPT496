"""
Python test-harness support for Checkpoint.

Creates isolated pytest files outside the selected student codebase.
"""

import ast
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import tree_sitter_language_pack as tslp
from tree_sitter import Node, Parser

from utils.tree_parse import LANGUAGE_DICT, parse_file

PYTEST_TIMEOUT_SECONDS = 60

@dataclass
class PythonTestRun:
    """The result of validating and running generated pytest tests."""

    kind: str = "Unit"
    generated: int = 0
    kept: list["PythonTestCase"] = field(default_factory=list)
    dropped: list["PythonTestCase"] = field(default_factory=list)
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    errors: int = 0
    failed_tests: list[str] = field(default_factory=list)
    report_path: str = ""
    output: str = ""
    project_error: str = ""

    def summary(self):
        return {
            "generated": self.generated,
            "kept": len(self.kept),
            "dropped": [
                {"name": case.name, "reason": case.reason}
                for case in self.dropped
            ],
            "passed": self.passed,
            "failed": self.failed,
            "skipped": self.skipped,
            "errors": self.errors,
            "failed_tests": self.failed_tests,
            "pytest_report": self.report_path,
            "project_error": self.project_error,
            "harness": "pytest",
            "validated_at": datetime.now(timezone.utc).isoformat(),
        }

    def message(self):
        if self.project_error:
            return (
                f"{self.kind} tests were written but pytest could not run them: "
                f"{self.project_error}"
            )

        if not self.generated:
            return f"No {self.kind.lower()} tests were generated."

        message = (
            f"{self.kind} tests: {len(self.kept)} of "
            f"{self.generated} accepted."
        )

        if self.kept:
            message += (
                f" {self.passed} passed, {self.failed} failed, "
                f"{self.errors} errored."
            )

        if self.dropped:
            message += f" {len(self.dropped)} rejected before execution."

        return message

    def needs_attention(self):
        return bool(
            self.project_error
            or self.dropped
            or self.failed
            or self.errors
            or not self.kept
        )

@dataclass
class PythonTestCase:
    """
    One generated pytest awaiting validation and execution.
    """
    name: str
    imports: list[str]
    body: str
    source: Any = None
    errors: list[str] = field(default_factory=list)
    reason: str = ""
    test_mode: str = "function"

MAX_TEST_CHARS = 20_000
SCRIPT_TIMEOUT_SECONDS = 5
CHECKPOINT_CODEBASE_PATH = "CHECKPOINT_CODEBASE_PATH"
PYTHON_CODE_CONTEXT_CHARS = 16_000
PYTHON_SUMMARY_CONTEXT_CHARS = 6_000
CONTEXT_OMISSION_MARKER = (
    "[OMITTED: retrieved document exceeded remaining context budget]"
)
NO_CONTEXT_PROVIDED = "NO CONTEXT PROVIDED"
_SCRIPT_IMPORT_MODULES = frozenset({"subprocess", "sys", "os", "pathlib", "pytest"})
_PYTHON_SKIP_TYPES = frozenset({
    "import_statement",
    "import_from_statement",
    "future_import_statement",
    "function_definition",
    "class_definition",
    "decorated_definition",
})
_LITERAL_ASSIGNMENT_TYPES = frozenset({
    "assignment",
    "augmented_assignment",
    "typed_assignment",
})


def resolve_source_path(source_path: str, codebase_path: str | None) -> Path | None:
    """Resolve a trusted lab path without creating files."""
    raw = str(source_path or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if path.is_absolute():
        return path if path.is_file() else None
    if not codebase_path:
        return None
    candidate = Path(codebase_path).resolve() / path
    return candidate if candidate.is_file() else None


def codebase_relative_path(source_path: str, codebase_path: str | None) -> str | None:
    resolved = resolve_source_path(source_path, codebase_path)
    if resolved is None:
        return None
    if not codebase_path:
        return resolved.as_posix()
    try:
        return resolved.resolve().relative_to(Path(codebase_path).resolve()).as_posix()
    except ValueError:
        return resolved.name


def _node_text(node: Node) -> str:
    return node.text.decode("utf-8") if node.text else ""


def _walk_named(node: Node):
    yield node
    for child in node.named_children:
        yield from _walk_named(child)


def _contains_call(node: Node) -> bool:
    return any(child.type == "call" for child in _walk_named(node))


def _is_main_guard(node: Node) -> bool:
    if node.type != "if_statement":
        return False
    text = " ".join(_node_text(node).split())
    return "__name__" in text and "__main__" in text and "==" in text


def _has_import_time_side_effects(root: Node) -> bool:
    for child in root.named_children:
        if child.type in _PYTHON_SKIP_TYPES:
            continue
        if _is_main_guard(child):
            continue
        if child.type in _LITERAL_ASSIGNMENT_TYPES and not _contains_call(child):
            continue
        return True
    return False


def _has_function_definitions(root: Node) -> bool:
    return any(
        child.type in {"function_definition", "decorated_definition", "class_definition"}
        for child in root.named_children
    )


def classify_python_file(path: Path) -> str:
    """
    Return function or script from Tree-sitter, never by executing the file.

    function: importable defs/classes and no import-time side effects.
    script: no import-safe functions, or top-level I/O, loops, or calls.
    """
    parser = Parser(tslp.get_language(LANGUAGE_DICT[".py"]["name"]))
    try:
        bundle = parse_file(path, parser, LANGUAGE_DICT[".py"]["name"])
    except (OSError, TypeError, FileNotFoundError):
        return "script"

    root = bundle.tree.root_node
    if root.has_error:
        return "script"
    if _has_function_definitions(root) and not _has_import_time_side_effects(root):
        return "function"
    return "script"


def classify_python_test_mode(
    source_file_paths: list[str] | None,
    codebase_path: str | None = None,
) -> str:
    """
    Classify a rule from trusted paths. Missing or unresolvable paths stay
    function for JSON compatibility and are not treated as import-safe.
    Multiple files that disagree stay function; no script path is invented.
    """
    resolved = []
    for raw in source_file_paths or []:
        path = resolve_source_path(raw, codebase_path)
        if path is not None and path.suffix.lower() == ".py":
            resolved.append(path)

    if not resolved:
        return "function"

    modes = {classify_python_file(path) for path in resolved}
    if modes == {"script"}:
        return "script"
    if modes == {"function"}:
        return "function"
    return "function"


def source_paths_are_import_safe(
    source_file_paths: list[str] | None,
    codebase_path: str | None = None,
) -> bool:
    resolved = [
        resolve_source_path(raw, codebase_path)
        for raw in source_file_paths or []
        if str(raw or "").strip()
    ]
    files = [path for path in resolved if path is not None and path.suffix.lower() == ".py"]
    if not files or any(path is None for path in resolved):
        return False
    return all(classify_python_file(path) == "function" for path in files)


def trusted_python_test_mode(
    source_file_paths: list[str] | None,
    codebase_path: str | None = None,
) -> str:
    """
    Generation and collection mode. Function only when every required path
    resolves to an import-safe Python module. Unknown provenance is script.
    """
    if source_paths_are_import_safe(source_file_paths, codebase_path):
        return "function"
    return "script"


def trusted_script_source_paths(
    source_file_paths: list[str] | None,
    codebase_path: str | None = None,
) -> list[str]:
    """Codebase-relative paths that actually resolve. Never invents a file."""
    relative_paths = []
    for source_path in source_file_paths or []:
        relative = codebase_relative_path(source_path, codebase_path)
        if relative:
            relative_paths.append(relative)
    return relative_paths


def resolved_python_source_files(
    source_file_paths: list[str] | None,
    codebase_path: str | None = None,
) -> list[Path]:
    files = []
    for raw in source_file_paths or []:
        path = resolve_source_path(raw, codebase_path)
        if path is not None and path.suffix.lower() == ".py":
            files.append(path)
    return files


_NO_SCRIPT_TARGET_REASON = (
    "script tests require a resolved source file; no trustworthy target path was provided"
)


def _item_source_paths(item) -> list[str]:
    return list(getattr(item, "source_file_paths", None) or [])


def effective_python_test_mode(item, codebase_path: str | None = None) -> str:
    """
    Honor a saved script stamp. Never treat a function default as import-safe
    when provenance is empty, mixed, stale, or unresolved.

    If codebase_path is missing, empty provenance still becomes script.
    Named but unresolved relative paths keep the saved mode until a caller
    that has the selected lab (check_tests / UT runner) re-validates them.
    """
    saved = getattr(item, "test_mode", None) or "function"
    if saved == "script":
        return "script"
    paths = _item_source_paths(item)
    if source_paths_are_import_safe(paths, codebase_path):
        return "function"
    if not any(str(path or "").strip() for path in paths):
        return "script"
    if codebase_path:
        return "script"
    return saved


def script_missing_target_reason(item, codebase_path: str | None = None) -> str:
    paths = _item_source_paths(item)
    if codebase_path:
        if resolved_python_source_files(paths, codebase_path):
            return ""
        return _NO_SCRIPT_TARGET_REASON
    if any(str(path or "").strip() for path in paths):
        return ""
    return _NO_SCRIPT_TARGET_REASON


def _is_allowed_script_import(node: ast.AST) -> bool:
    if isinstance(node, ast.Import):
        return all(
            (alias.name.split(".")[0] in _SCRIPT_IMPORT_MODULES)
            for alias in node.names
        )
    if isinstance(node, ast.ImportFrom):
        if node.level and node.level > 0:
            return False
        root = (node.module or "").split(".")[0]
        return root in _SCRIPT_IMPORT_MODULES
    return False


def _has_disallowed_script_expr(node: ast.AST | None) -> bool:
    if node is None:
        return False
    for child in ast.walk(node):
        if isinstance(
            child,
            (
                ast.Call,
                ast.ListComp,
                ast.SetComp,
                ast.DictComp,
                ast.GeneratorExp,
                ast.Await,
                ast.Yield,
                ast.YieldFrom,
            ),
        ):
            return True
        if isinstance(child, ast.Name) and child.id in {"__import__", "exec", "eval"}:
            return True
    return False


def _script_mode_reason(imports: list[str], body: str) -> str:
    try:
        tree = ast.parse("\n".join([*imports, body]))
    except SyntaxError as error:
        return f"invalid Python test syntax: {error.msg}"

    test_functions = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if not _is_allowed_script_import(node):
                return "script tests may only import subprocess, sys, os, pathlib, or pytest"
            continue
        if isinstance(node, ast.Assign):
            if any(_has_disallowed_script_expr(value) for value in [node.value]):
                return "script tests cannot evaluate calls or comprehensions at module scope"
            continue
        if isinstance(node, ast.AnnAssign):
            if _has_disallowed_script_expr(node.value):
                return "script tests cannot evaluate calls or comprehensions at module scope"
            continue
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            test_functions.append(node)
            continue
        if isinstance(node, ast.AsyncFunctionDef) and node.name.startswith("test_"):
            return "script tests must use a synchronous test_* function"
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue
        return "script tests cannot execute statements at pytest collection"

    if len(test_functions) != 1:
        return "expected exactly one top-level pytest function named test_*"

    function = test_functions[0]
    if function.decorator_list:
        return "script tests cannot use decorators"
    defaults = [*function.args.defaults, *function.args.kw_defaults]
    if any(_has_disallowed_script_expr(default) for default in defaults if default is not None):
        return "script tests cannot evaluate default argument expressions"
    return ""


def clean_test(
    body: str,
    imports: list[str] | None,
    test_mode: str = "function",
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

    if test_mode == "script":
        script_reason = _script_mode_reason(cleaned_imports, body)
        if script_reason:
            return None, cleaned_imports, script_reason

    return body, cleaned_imports, ""


def bound_python_prompt_documents(
    documents: list[str] | None,
    budget: int,
    omission_marker: str = CONTEXT_OMISSION_MARKER,
) -> str:
    """
    Include complete retrieved documents until a character budget is reached.

    Oversized documents are skipped with a marker instead of being truncated.
    Later smaller documents are still considered. The joined result, including
    markers, never exceeds budget when budget is at least as large as the
    marker. An empty input yields NO CONTEXT PROVIDED (not budgeted).
    """
    if not documents:
        return NO_CONTEXT_PROVIDED

    included: list[str] = []
    used = 0
    for document in documents:
        text = document if isinstance(document, str) else str(document)
        separator = "\n\n" if included else ""
        candidate_size = len(separator) + len(text)
        if used + candidate_size <= budget:
            included.append(text)
            used += candidate_size
            continue

        marker_separator = "\n\n" if included else ""
        marker_size = len(marker_separator) + len(omission_marker)
        if used + marker_size <= budget:
            included.append(omission_marker)
            used += marker_size

    if not included:
        if len(omission_marker) <= budget:
            return omission_marker
        return NO_CONTEXT_PROVIDED
    return "\n\n".join(included)


def generation_context_texts(
    target_language: str,
    code_context: list[str] | None,
    summary_context: list[str] | None,
) -> tuple[str, str]:
    """Join retrieved context. Python applies character budgets; C# does not."""
    code_context = list(code_context or [])
    summary_context = list(summary_context or [])
    if target_language == "python":
        return (
            bound_python_prompt_documents(code_context, PYTHON_CODE_CONTEXT_CHARS),
            bound_python_prompt_documents(summary_context, PYTHON_SUMMARY_CONTEXT_CHARS),
        )
    return (
        "\n\n".join(code_context) if code_context else NO_CONTEXT_PROVIDED,
        "\n\n".join(summary_context) if summary_context else NO_CONTEXT_PROVIDED,
    )


def python_unit_test_size_reason(test) -> str:
    body = (getattr(test, "unit_test", None) or "").strip()
    if len(body) <= MAX_TEST_CHARS:
        return ""
    test_id = getattr(test, "id", "unknown")
    return (
        f"rule {test_id}: unit_test exceeds {MAX_TEST_CHARS} characters "
        f"({len(body)} characters); the test was not truncated"
    )


def split_python_unit_tests_for_persistence(tests) -> tuple[list, list[dict]]:
    """Keep executable Python tests; report oversized records without truncated bodies."""
    persistable = []
    oversized = []
    for test in tests or []:
        reason = python_unit_test_size_reason(test)
        if not reason:
            persistable.append(test)
            continue
        oversized.append(
            {
                "id": getattr(test, "id", None),
                "rule": getattr(test, "rule", ""),
                "source_directory": getattr(test, "source_directory", ""),
                "source_file_paths": list(getattr(test, "source_file_paths", None) or []),
                "test_mode": getattr(test, "test_mode", "function"),
                "character_count": len((getattr(test, "unit_test", None) or "").strip()),
                "reason": reason,
            }
        )
    return persistable, oversized


def persist_python_generated_tests(output_dir: str | Path, tests) -> tuple[list, list[dict]]:
    """
    Write executable Python unit tests and always refresh the dropped sidecar.

    unit_tests.json contains only accepted tests (possibly an empty list).
    dropped_generated_tests.json is overwritten every call, including [].
    """
    persistable, oversized = split_python_unit_tests_for_persistence(tests)
    folder = Path(output_dir)
    folder.mkdir(parents=True, exist_ok=True)

    def dump(value):
        if hasattr(value, "model_dump"):
            return value.model_dump()
        return dict(value)

    (folder / "unit_tests.json").write_text(
        json.dumps([dump(test) for test in persistable], indent=2),
        encoding="utf-8",
    )
    (folder / "dropped_generated_tests.json").write_text(
        json.dumps(oversized, indent=2),
        encoding="utf-8",
    )
    return persistable, oversized


def normalize_python_test_body(body: str) -> str:
    """Stable pytest-body comparison: line endings and trailing line space only."""
    unified = (body or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    return "\n".join(line.rstrip() for line in unified.split("\n"))


def _python_case_source_context(case: PythonTestCase) -> tuple:
    item = case.source if case.source is not None else case
    paths = tuple(
        sorted(
            str(path).strip()
            for path in getattr(item, "source_file_paths", None) or []
            if str(path).strip()
        )
    )
    directory = str(getattr(item, "source_directory", "") or "")
    return (case.test_mode or "function", directory, paths)


def _python_case_id(case: PythonTestCase) -> str:
    item = case.source if case.source is not None else case
    item_id = getattr(item, "id", None)
    if item_id is None:
        return case.name
    return str(item_id)


def _drop_duplicate_python_cases(
    cases: list[PythonTestCase],
) -> tuple[list[PythonTestCase], list[PythonTestCase]]:
    """
    Keep the first accepted pytest body per mode and source context.

    Safety validation must already have passed. Distinct source paths,
    directories, or test_mode values are not merged.
    """
    kept = []
    duplicates = []
    first_id_by_key = {}
    for case in cases:
        key = (normalize_python_test_body(case.body), *_python_case_source_context(case))
        previous_id = first_id_by_key.get(key)
        if previous_id is None:
            first_id_by_key[key] = _python_case_id(case)
            kept.append(case)
            continue
        current_id = _python_case_id(case)
        case.reason = (
            f"rule {current_id}: duplicate of rule {previous_id}; "
            "identical pytest body in the same test_mode and source context"
        )
        case.body = ""
        duplicates.append(case)
    return kept, duplicates


def _validate_python_item(item, body: str, imports: list[str], codebase_path: str | None):
    test_mode = effective_python_test_mode(item, codebase_path)
    cleaned_body, cleaned_imports, reason = clean_test(
        body,
        imports,
        test_mode=test_mode,
    )
    if not reason and test_mode == "script":
        reason = script_missing_target_reason(item, codebase_path)
        if reason:
            cleaned_body = None
    return cleaned_body, cleaned_imports, reason, test_mode


def make_cases(prefix: str, items, body_of, imports_of, codebase_path: str | None = None):
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

        body, imports, reason, test_mode = _validate_python_item(
            item,
            body_of(item),
            imports_of(item),
            codebase_path,
        )

        case = PythonTestCase(
            name=name,
            imports=imports,
            body=body or "",
            source=item,
            reason=reason,
            test_mode=test_mode,
        )

        if reason:
            rejected.append(case)
        else:
            cases.append(case)

    cases, duplicates = _drop_duplicate_python_cases(cases)
    rejected.extend(duplicates)
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
    environment[CHECKPOINT_CODEBASE_PATH] = source_directory
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
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_python_environment(codebase_path),
            start_new_session=(os.name != "nt"),
        )
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name != "nt" and process.pid:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                process.kill()
        else:
            process.kill()
        process.wait()
        return PythonTestRun(
            project_error=f"pytest exceeded the {timeout}-second timeout.",
        )

    output = (stdout or "") + "\n" + (stderr or "")

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

def check_tests(
    kind: str,
    prefix: str,
    cases: list[PythonTestCase],
    rejected: list[PythonTestCase],
    codebase_path: str,
    codebase_name: str,
    results_dir: str,
) -> PythonTestRun:
    """
    Write validated pytest tests, run them, and return a Checkpoint-style
    result object.
    """
    generated = len(cases) + len(rejected)
    safe_cases = []
    dropped = list(rejected)
    for case in cases:
        item = case.source if case.source is not None else case
        body, imports, reason, test_mode = _validate_python_item(
            item,
            case.body,
            case.imports,
            codebase_path,
        )
        case.imports = imports
        case.body = body or ""
        case.reason = reason
        case.test_mode = test_mode
        if reason:
            dropped.append(case)
        else:
            safe_cases.append(case)

    safe_cases, duplicates = _drop_duplicate_python_cases(safe_cases)
    dropped.extend(duplicates)

    run = PythonTestRun(
        kind=kind,
        generated=generated,
        dropped=dropped,
    )

    test_dir = test_dir_for(codebase_path, codebase_name)
    write_tests(test_dir, prefix, safe_cases)

    if not safe_cases:
        return run

    execution = run_tests(
        test_dir=test_dir,
        codebase_path=codebase_path,
        results_dir=results_dir,
    )

    run.kept = list(safe_cases)
    run.passed = execution.passed
    run.failed = execution.failed
    run.skipped = execution.skipped
    run.errors = execution.errors
    run.failed_tests = execution.failed_tests
    run.report_path = execution.report_path
    run.output = execution.output
    run.project_error = execution.project_error

    return run


def write_results(run: PythonTestRun, output_dir: str, to_json) -> None:
    """Write Python test outcomes in the same JSON-style structure as C#."""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    (output_path / "validated_tests.json").write_text(
        json.dumps([to_json(case) for case in run.kept], indent=2),
        encoding="utf-8",
    )

    (output_path / "discarded_tests.json").write_text(
        json.dumps(
            [
                {**to_json(case), "reason": case.reason}
                for case in run.dropped
            ],
            indent=2,
        ),
        encoding="utf-8",
    )

    (output_path / "test_report.json").write_text(
        json.dumps(
            {
                **run.summary(),
                "message": run.message(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )