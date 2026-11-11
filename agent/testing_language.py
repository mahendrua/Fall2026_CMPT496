"""
Language-selection helpers for generated test workflows.
"""

import os
import re
from pathlib import Path

from agent.crawl_config import prune

LANGUAGE_CONFIG = {
    "python": {
        "suffixes": {".py"},
        "framework": "pytest",
        "body_patterns": (
            re.compile(r"\b(?:async\s+)?def\s+test_\w+"),
            re.compile(r"\b(?:import\s+pytest\b|from\s+pytest\b)"),
        ),
    },
    "csharp": {
        "suffixes": {".cs"},
        "framework": "xunit",
        "body_patterns": (
            re.compile(r"\[\s*(?:Fact|Theory)\s*\]"),
            re.compile(r"\busing\s+[\w.]+"),
        ),
    },
    "javascript": {
        "suffixes": {".js", ".jsx", ".mjs", ".cjs"},
        "framework": "vitest",
        "body_patterns": (
            re.compile(r"\b(?:describe|it|test|expect)\s*(?:\.\w+)?\s*\("),
            re.compile(r"\b(?:vi|vitest)\."),
        ),
    },
    "java": {
        "suffixes": {".java"},
        "framework": "junit5",
        "body_patterns": (
            re.compile(r"@\s*Test\b"),
            re.compile(r"\bimport\s+org\.junit\.jupiter\b"),
        ),
    },
}

_LANGUAGE_SUFFIXES = {
    suffix: language
    for language, config in LANGUAGE_CONFIG.items()
    for suffix in config["suffixes"]
}
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
        # Only code files say which language the tests need; a rule can
        # also cite documentation such as README.md.
        if Path(source_path).suffix.lower() in _LANGUAGE_SUFFIXES
    }

    languages = {
        _LANGUAGE_SUFFIXES[suffix]
        for suffix in suffixes
    }

    if len(languages) == 1:
        return next(iter(languages))

    if len(languages) > 1:
        raise ValueError(
            "Unit-test generation currently supports one language at a time. "
            f"Found source types: {', '.join(sorted(suffixes))}."
        )

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
                detected = ", ".join(sorted(found))
                raise ValueError(
                    "Cannot determine a test language because the project "
                    f"contains multiple source languages: {detected}"
                )

    if found == {"python"}:
        return "python"
    if found == {"csharp"}:
        return "csharp"
    

    if len(found) == 1:
        return next(iter(found))
    
    supported = ", ".join(sorted(LANGUAGE_CONFIG))

    raise ValueError(
        "Cannot determine a test language because the project contains no "
        f"supported source files. Supported languages: {supported}"
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


def _body_signals(text: str) -> set[str]:
    visible = _without_comments_and_strings(text)
    detected = set()

    for language, config in LANGUAGE_CONFIG.items():
        if any(
            pattern.search(visible)
            for pattern in config["body_patterns"]
        ):
            detected.add(language)

    # The Vitest import contains a string literal, so inspect the original
    # text separately rather than the comment/string-stripped text.
    if re.search(
        r"\bfrom\s*['\"]vitest['\"]"
        r"|\brequire\s*\(\s*['\"]vitest['\"]\s*\)",
        text,
    ):
        detected.add("javascript")

    return detected

def _body_language_from_tests(tests) -> str | None:
    detected_languages = set()

    for test in tests:
        signals = _body_signals(_test_source_text(test))

        if len(signals) > 1:
            detected = ", ".join(sorted(signals))
            raise ValueError(
                "Cannot determine a test language because a generated test "
                f"contains conflicting language markers: {detected}."
            )

        detected_languages.update(signals)

    if len(detected_languages) > 1:
        detected = ", ".join(sorted(detected_languages))
        raise ValueError(
            "Cannot determine a test language because the generated tests "
            f"mix multiple languages: {detected}."
        )

    if detected_languages:
        return next(iter(detected_languages))

    return None

def detect_unit_test_language(tests) -> str:
    """
    Determine the harness language for already generated unit tests.

    Combines source-file suffixes with language-specific test-framework
    evidence. Contradictory or missing evidence is rejected.
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

    supported = ", ".join(sorted(LANGUAGE_CONFIG))

    raise ValueError(
        "Cannot determine a test language because the generated tests "
        f"do not identify a supported language: {supported}."
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

    if target_language == "javascript":
        return """
JAVASCRIPT / VITEST REQUIREMENTS:
- Generate exactly one complete Vitest test block in `unit_test`.
- Use `it(...)` or `test(...)` to define the test.
- Use `expect(...)` for assertions.
- Put every required JavaScript import statement in the `imports` array,
  one complete import statement per entry.
- Import Vitest functions explicitly:
  `import { describe, it, expect } from "vitest";`
- Import only real functions, classes, and modules shown in the supplied
  source context. Do not invent exports, paths, or APIs.
- Source files are located relative to the codebase root.
- Generated test files will be placed in a temporary directory directly
  inside the codebase root.
- When importing a source file, use a relative path beginning with `../`
  followed by its codebase-relative path.
  For example, if the source file is `src/calculator.js`, use
  `import { add } from "../src/calculator.js";`.
- Do not use machine-specific absolute paths.
- Use valid JavaScript syntax. Do not use TypeScript-only syntax.
- Use asynchronous tests only when the source behavior requires them.
- Do not use Jest-specific APIs or assume Jest is installed.
- Do not modify package configuration or install dependencies.
- Do not use C# syntax, xUnit attributes, Python syntax, or pytest.
"""

    if target_language == "java":
        return """
JAVA / JUNIT 5 REQUIREMENTS:

- Generate exactly one complete JUnit 5 test method.
- Return the test method only in the unit_test field.
- Do not generate a surrounding class declaration.
- Use the @Test annotation.
- Use JUnit 5 assertions such as assertEquals, assertTrue,
  assertFalse, assertNull, and assertThrows.
- Put every required Java import statement in the imports array.
- Each imports entry must be a complete Java import statement,
  including its semicolon.
- Import only real classes, methods, and packages supported by
  the supplied source context.
- Preserve the source code's actual package and class names.
- Use package-private access when the generated test belongs
  to the same package as the source class.
- Do not invent constructors, methods, classes, or APIs.
- Do not use JUnit 4, TestNG, Mockito, or additional test dependencies.
- Do not generate a main method or standalone Java application.
- Do not use Python, C#, or JavaScript test syntax.
"""

    raise ValueError(f"Unsupported unit-test language: {target_language}")





def integration_test_generation_instructions(target_language: str) -> str:
    """Return language-specific instructions for integration-test generation."""

    if target_language == "python":
        return """
PYTHON / PYTEST INTEGRATION TEST REQUIREMENTS:

- Generate exactly one complete, top-level pytest test function.
- Name the function using the test_* naming convention.
- Return the complete test function in the integration_test field.
- Use ordinary Python assert statements.
- Put every required Python import in the imports array.
- Each imports entry must be a complete Python import statement.
- Import only real modules, functions, and classes supported by the
  supplied source context.
- Test interactions between multiple application components where possible.
- Exercise the actual workflow rather than testing an isolated function.
- Use temporary data or isolated test data where appropriate.
- Do not invent APIs, classes, methods, or module paths.
- Do not use unittest unless the existing project architecture requires it.
- Do not use C# syntax, xUnit, JavaScript syntax, or Vitest.
"""

    if target_language == "csharp":
        return """
C# / XUNIT INTEGRATION TEST REQUIREMENTS:

- Generate exactly one complete xUnit integration test method.
- Include the appropriate [Fact] or [Theory] attribute.
- Return the complete test method in the integration_test field.
- Put every required C# using directive in the imports array.
- Each imports entry must be a complete using directive, including its
  semicolon.
- Use only xUnit assertions and public APIs supported by the supplied
  source context.
- Test interactions between multiple application components where possible.
- Exercise the actual workflow rather than testing an isolated method.
- Use the application's existing dependency injection and service
  architecture when supported by the retrieved context.
- Do not invent classes, constructors, methods, or namespaces.
- Do not use Moq, FluentAssertions, or other additional test packages.
"""

    if target_language == "javascript":
        return """
JAVASCRIPT / VITEST INTEGRATION TEST REQUIREMENTS:

- Generate exactly one complete Vitest test block.
- Use it(...) or test(...) to define the integration test.
- Use expect(...) for assertions.
- Return the complete test block in the integration_test field.
- Put every required JavaScript import statement in the imports array.
- Each imports entry must be a complete JavaScript import statement.
- Import Vitest functions explicitly when required:
  import { describe, it, expect } from "vitest";
- Import only real modules, functions, and classes supported by the
  supplied source context.
- Use valid JavaScript syntax, not TypeScript-only syntax.
- Test interactions between multiple application components where possible.
- Exercise the actual workflow rather than testing an isolated function.
- Do not invent exports, module paths, or APIs.
- Do not use Jest-specific APIs or assume Jest is installed.
- Do not modify package configuration or install dependencies.
- Do not use C# syntax, xUnit, Python syntax, or pytest.
"""


    if target_language == "java":
        return """
JAVA / JUNIT 5 INTEGRATION TEST REQUIREMENTS:

- Generate exactly one complete JUnit 5 integration test method.
- Return the test method only in the integration_test field.
- Do not generate a surrounding class declaration.
- Include the appropriate @Test annotation.
- Use JUnit 5 assertions.
- Put every required Java import statement in the imports array.
- Each imports entry must be a complete Java import statement,
  including its semicolon.
- Import only real classes, methods, and packages supported by
  the supplied source context.
- Test interactions between multiple application components.
- Exercise an actual application workflow rather than an isolated method.
- Preserve the source code's actual package and class names.
- Use existing constructors, dependencies, and application
  architecture supported by the retrieved context.
- Do not invent APIs, classes, constructors, or methods.
- Do not use JUnit 4, TestNG, Mockito, or additional test dependencies.
- Do not use Python, C#, or JavaScript test syntax.
"""

    raise ValueError(
        f"Unsupported integration-test language: {target_language}"
    )
