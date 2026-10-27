"""
Language-selection helpers for generated test workflows.
"""

import os
import re
from pathlib import Path

from agent.crawl_config import prune

_LANGUAGE_SUFFIXES = {".py": "python", ".cs": "csharp"}
# Bounded suffix inspection only. Academic labs are small; exceeding this
# without a unique language is treated as undecidable rather than guessed.
PROJECT_LANGUAGE_SCAN_LIMIT = 2000
_EXTRA_IGNORED_DIRS = frozenset({"venv"})


def detect_test_language(
    source_file_groups: list[list[str]],
    codebase_path: str | None = None,
) -> str:
    """
    Determine the single supported language represented by selected rules.

    Each item in source_file_groups is one rule's source_file_paths list.
    When those paths carry no language evidence, codebase_path may supply a
    trusted project-level .py/.cs scan. Rule-path evidence always wins.
    """
    suffixes = {
        Path(source_path).suffix.lower()
        for source_files in source_file_groups
        for source_path in source_files
        if Path(source_path).suffix
    }

    if suffixes == {".py"}:
        return "python"

    if suffixes == {".cs"}:
        return "csharp"

    if suffixes:
        raise ValueError(
            "Unit-test generation currently supports one language at a time. "
            f"Found source types: {', '.join(sorted(suffixes))}."
        )

    if codebase_path:
        return _project_source_language(codebase_path)

    raise ValueError(
        "Cannot determine a test language because the selected rules "
        "do not identify source files."
    )


def _is_inside_project(path: Path, project_root: Path) -> bool:
    try:
        path.resolve().relative_to(project_root)
    except (ValueError, OSError):
        return False
    return True


def _project_source_language(codebase_path: str) -> str:
    """
    Infer python or csharp from .py/.cs filenames under a trusted lab.

    Does not read file contents. Uses crawl_config.prune so ignored and
    Checkpoint-generated folders are skipped. os.walk does not follow
    directory symlinks. Source-file symlinks that resolve outside the
    project are ignored.
    """
    root = Path(codebase_path)
    if not root.exists() or not root.is_dir():
        raise ValueError(
            "Cannot determine a test language because the project directory "
            f"is missing or invalid: {codebase_path}"
        )

    project_root = root.resolve()
    found: set[str] = set()
    visited = 0

    for dirpath, dirs, filenames in os.walk(project_root, followlinks=False):
        prune(dirpath, dirs)
        dirs[:] = [
            name
            for name in dirs
            if name not in _EXTRA_IGNORED_DIRS
            and not (Path(dirpath) / name).is_symlink()
        ]
        visited += 1
        if visited > PROJECT_LANGUAGE_SCAN_LIMIT:
            raise ValueError(
                "Cannot determine a test language because the project scan "
                "exceeded its file budget before a unique language was proven."
            )

        for name in filenames:
            visited += 1
            if visited > PROJECT_LANGUAGE_SCAN_LIMIT:
                raise ValueError(
                    "Cannot determine a test language because the project scan "
                    "exceeded its file budget before a unique language was proven."
                )
            suffix = Path(name).suffix.lower()
            language = _LANGUAGE_SUFFIXES.get(suffix)
            if language is None:
                continue
            candidate = Path(dirpath) / name
            if candidate.is_symlink() and not _is_inside_project(candidate, project_root):
                continue
            found.add(language)
            if len(found) > 1:
                raise ValueError(
                    "Cannot determine a test language because the project "
                    "contains both Python and C# source files."
                )

    if found == {"python"}:
        return "python"
    if found == {"csharp"}:
        return "csharp"
    raise ValueError(
        "Cannot determine a test language because the project contains no "
        "Python or C# source files."
    )


_PYTHON_TEST_DEF = re.compile(r"\b(?:async\s+)?def\s+test_\w*")
_PYTHON_PYTEST_IMPORT = re.compile(r"\b(?:import\s+pytest\b|from\s+pytest\b)")
_CSHARP_XUNIT_ATTR = re.compile(r"\[\s*(?:Fact|Theory)\s*\]")
_CSHARP_USING = re.compile(r"\busing\s+[\w.]+")

_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT_SLASH = re.compile(r"//[^\n]*")
_HASH_COMMENT = re.compile(r"#[^\n]*")
_TRIPLE_STRING = re.compile(r'("""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\')')
_VERBATIM_STRING = re.compile(r'@"(?:""|[^"])*"')
_DOUBLE_STRING = re.compile(r'"(?:\\.|[^"\\])*"')
_SINGLE_STRING = re.compile(r"'(?:\\.|[^'\\])*'")


def _without_comments_and_strings(text: str) -> str:
    """Drop comments and ordinary string literals before looking for language markers."""
    text = _BLOCK_COMMENT.sub(" ", text)
    text = _LINE_COMMENT_SLASH.sub(" ", text)
    text = _TRIPLE_STRING.sub(" ", text)
    text = _VERBATIM_STRING.sub(" ", text)
    text = _DOUBLE_STRING.sub(" ", text)
    text = _SINGLE_STRING.sub(" ", text)
    text = _HASH_COMMENT.sub(" ", text)
    return text


def _test_source_text(test) -> str:
    imports = getattr(test, "imports", None) or []
    body = getattr(test, "unit_test", None) or ""
    return "\n".join([*imports, body])


def _path_language_from_tests(tests) -> str | None:
    source_file_groups = [
        list(getattr(test, "source_file_paths", None) or [])
        for test in tests
    ]
    try:
        return detect_test_language(source_file_groups)
    except ValueError as error:
        if "do not identify source files" in str(error):
            return None
        raise


def _body_signals(text: str) -> tuple[bool, bool]:
    visible = _without_comments_and_strings(text)
    python = bool(
        _PYTHON_TEST_DEF.search(visible) or _PYTHON_PYTEST_IMPORT.search(visible)
    )
    csharp = bool(
        _CSHARP_XUNIT_ATTR.search(visible) or _CSHARP_USING.search(visible)
    )
    return python, csharp


def _body_language_from_tests(tests) -> str | None:
    saw_python = False
    saw_csharp = False

    for test in tests:
        python, csharp = _body_signals(_test_source_text(test))
        if python and csharp:
            raise ValueError(
                "Cannot determine a test language because a generated test "
                "mixes Python and C# syntax."
            )
        saw_python = saw_python or python
        saw_csharp = saw_csharp or csharp

    if saw_python and saw_csharp:
        raise ValueError(
            "Cannot determine a test language because the generated tests "
            "mix Python and C# syntax."
        )
    if saw_python:
        return "python"
    if saw_csharp:
        return "csharp"
    return None


def detect_unit_test_language(tests) -> str:
    """
    Determine the harness language for already generated unit tests.

    Combines source-file suffixes with pytest/xUnit body evidence.
    Contradictory or missing evidence is rejected; C# is never a default.
    """
    if not tests:
        raise ValueError(
            "Cannot determine a test language because no unit tests were provided."
        )

    path_language = _path_language_from_tests(tests)
    body_language = _body_language_from_tests(tests)

    if path_language and body_language and path_language != body_language:
        raise ValueError(
            "Cannot determine a test language because the source files and "
            "generated tests disagree."
        )

    if path_language:
        return path_language
    if body_language:
        return body_language

    raise ValueError(
        "Cannot determine a test language because the generated tests "
        "do not identify Python or C#."
    )


def unit_test_generation_instructions(target_language: str, test_mode: str = "function") -> str:
    """Return language-specific instructions for test generation."""

    if target_language == "python" and test_mode == "script":
        return """
PYTHON / PYTEST SCRIPT REQUIREMENTS:
- Generate exactly one complete, undecorated, synchronous top-level pytest
  function named `test_*`. Put it only in `unit_test`.
- Do not wrap it in a class. Do not use async def.
- The `imports` array may contain only: import subprocess, import sys,
  import os, from pathlib import Path. No other imports.
- Do not import student modules, packages, or files from the lab.
  Pytest collection must not execute student code.
- Resolve the script with Path(os.environ["CHECKPOINT_CODEBASE_PATH"]) and a
  codebase-relative path from the supplied source files. Do not hard-code
  machine-specific absolute paths.
- Inside the test function only, run the script with subprocess.run using
  sys.executable and an argv list. Never use shell=True.
- Pass deterministic stdin via input=, capture stdout and stderr, and set
  timeout=5. cwd may be a temporary directory or the script's parent folder
  for sibling data files; that is not a security sandbox.
- Assert on returncode and on stdout/stderr text that the business rule and
  source actually support. Do not invent numeric or printed results.
- If the rule does not specify observable output, assert only that the
  process exits 0.
- Do not use C# syntax, xUnit, or pytest plugins.
"""

    if target_language == "python":
        return """
PYTHON / PYTEST REQUIREMENTS:
- Generate exactly one complete, top-level pytest function named `test_*`.
- Return that complete function only in `unit_test`.
- Do not wrap it in a class.
- Use ordinary Python `assert` statements.
- Put every required Python import in the `imports` array, one complete
  import statement per entry.
- Import only real functions or classes shown in the supplied source context.
- Do not use C# syntax, xUnit attributes, `using` statements, or `dotnet`.
- Do not require external pytest plugins.
"""

    if target_language == "csharp":
        return """
C# / XUNIT REQUIREMENTS:
- Return one xUnit test method in `unit_test`, including `[Fact]` or `[Theory]`.
- Put every required C# `using` directive in the `imports` array.
- Use only xUnit assertions and public APIs shown in the supplied source.
- Do not use Moq, FluentAssertions, or other test packages.
"""

    raise ValueError(f"Unsupported unit-test language: {target_language}")