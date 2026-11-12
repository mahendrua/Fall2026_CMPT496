
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from xml.etree import ElementTree as ET

from agent.crawl_config import mark_generated
from agent.llm import DailyLimitError, RateLimitTimeoutError
from agent.structured_output.UTV_output import TestRepair
from backend.progress_logging import progress


CppBuildSystem = Literal["cmake", "plain"]

MAX_TEST_CHARS = 20_000
MAX_REPAIR_ROUNDS = 2
MAX_CONCURRENCY = 10
BUILD_TIMEOUT_SECONDS = 600
TEST_TIMEOUT_SECONDS = 900
CPP_STANDARD = "17"

CPP_SOURCE_SUFFIXES = {
    ".cpp", ".cc", ".cxx", ".c++", ".cp", ".C",
}

CPP_HEADER_SUFFIXES = {
    ".hpp", ".hh", ".hxx", ".h++", ".h",
}

EXCLUDED_DIRECTORIES = {
    ".git",
    ".svn",
    ".hg",
    ".idea",
    ".vscode",
    "build",
    "cmake-build-debug",
    "cmake-build-release",
    "out",
    "dist",
    "node_modules",
    "vendor",
    "third_party",
    "external",
}


class CppProjectError(Exception):
    """Raised when a C++ project cannot be identified or prepared."""


@dataclass
class CppProject:
    """Detected C++ project and its build configuration."""

    root: Path
    build_system: CppBuildSystem
    build_command: str | None
    build_file: Path | None


@dataclass
class TestCase:
    """One generated GoogleTest test being processed by check_tests()."""

    __test__ = False

    name: str
    imports: list[str]
    body: str
    source: Any = None
    errors: list[str] = field(default_factory=list)
    repairs: int = 0
    reason: str = ""


@dataclass
class TestRun:
    """Summary of generated C++ test validation and execution."""

    __test__ = False

    kind: str
    generated: int = 0
    kept: list[TestCase] = field(default_factory=list)
    dropped: list[TestCase] = field(default_factory=list)
    unusable: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    failed_tests: list[str] = field(default_factory=list)
    removed_stale: list[str] = field(default_factory=list)
    project_error: str = ""
    report_path: str = ""

    @property
    def repaired(self):
        return [case for case in self.kept if case.repairs]

    def summary(self) -> dict:
        return {
            "generated": self.generated,
            "kept": len(self.kept),
            "repaired": [case.name for case in self.repaired],
            "dropped": [
                {"name": case.name, "reason": case.reason}
                for case in self.dropped
            ],
            "passed": self.passed,
            "failed": self.failed,
            "skipped": self.skipped,
            "failed_tests": self.failed_tests,
            "removed_stale_files": self.removed_stale,
            "project_error": self.project_error,
            "junit_report_dir": self.report_path,
            "harness": "googletest",
        }

    def message(self) -> str:
        if self.project_error:
            return (
                f"{self.kind} tests were written but the C++ test project "
                f"could not complete: {self.project_error}"
            )

        if not self.generated:
            return f"No {self.kind.lower()} tests were generated."

        parts = [
            f"{self.kind} tests: {len(self.kept)} of "
            f"{self.generated} kept"
        ]

        details = []

        if self.repaired:
            details.append(f"{len(self.repaired)} repaired by the AI")

        if self.dropped:
            details.append(
                f"{len(self.dropped)} dropped during validation or compilation"
            )

        if details:
            parts[0] += f" ({', '.join(details)})"

        if self.kept:
            parts.append(f"{self.passed} passed, {self.failed} failed")

        return ". ".join(parts) + "."

    def needs_attention(self) -> bool:
        return bool(
            self.project_error
            or self.dropped
            or self.failed
            or not self.kept
        )


# ---------------------------------------------------------------------------
# Project detection
# ---------------------------------------------------------------------------

def find_cpp_project_root(codebase_path: str | Path) -> Path:
    """Find the nearest directory containing a CMakeLists.txt file."""
    path = Path(codebase_path).resolve()

    if not path.exists():
        raise CppProjectError(f"Codebase path does not exist: {path}")

    if path.is_file():
        path = path.parent

    for directory in (path, *path.parents):
        if (directory / "CMakeLists.txt").is_file():
            return directory

    return path


def detect_cpp_project(codebase_path: str | Path) -> CppProject:
    """Detect a CMake-based or plain C++ project."""
    root = find_cpp_project_root(codebase_path)

    cmake_file = root / "CMakeLists.txt"

    if cmake_file.is_file():
        cmake = shutil.which("cmake")

        if not cmake:
            raise CppProjectError(
                "CMake project detected, but cmake is not available."
            )

        return CppProject(
            root=root,
            build_system="cmake",
            build_command=cmake,
            build_file=cmake_file,
        )

    source_files = find_cpp_sources(root)

    if not source_files:
        raise CppProjectError(
            f"No CMakeLists.txt or C++ source files found in {root}."
        )

    compiler = shutil.which("clang++") or shutil.which("g++")

    if not compiler:
        raise CppProjectError(
            "A C++ compiler is required. Install Clang or GCC."
        )

    return CppProject(
        root=root,
        build_system="plain",
        build_command=compiler,
        build_file=None,
    )


def _is_excluded(path: Path, root: Path) -> bool:
    """Return whether a source path is inside an excluded directory."""
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True

    return any(part in EXCLUDED_DIRECTORIES for part in relative.parts)


def find_cpp_sources(
    root: str | Path,
    exclude_dir: str | Path | None = None,
) -> list[Path]:
    """Find C++ implementation files while excluding generated/build files."""
    root = Path(root).resolve()
    excluded = (
        Path(exclude_dir).resolve()
        if exclude_dir is not None
        else None
    )

    results = []

    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in CPP_SOURCE_SUFFIXES:
            continue

        if _is_excluded(path, root):
            continue

        if excluded is not None:
            try:
                path.resolve().relative_to(excluded)
                continue
            except ValueError:
                pass

        results.append(path)

    return sorted(results)


# ---------------------------------------------------------------------------
# Test normalization and validation
# ---------------------------------------------------------------------------

def normalize_imports(
    imports: str | list[str] | None,
) -> list[str]:
    """Normalize and deduplicate C++ include directives."""
    if not imports:
        return []

    if isinstance(imports, str):
        imports = imports.splitlines()

    normalized = []
    seen = set()

    for item in imports:
        item = item.strip()

        if not item:
            continue

        if item.startswith("#include"):
            statement = item
        else:
            item = item.rstrip(";").strip()

            if item.startswith("<") or item.startswith('"'):
                statement = f"#include {item}"
            else:
                statement = f"#include <{item}>"

        if statement not in seen:
            normalized.append(statement)
            seen.add(statement)

    return normalized


def clean_test(test: TestCase) -> bool:
    """Validate and clean one generated GoogleTest test file."""
    source = (test.body or "").strip()

    if not source:
        test.reason = "Generated test is empty."
        return False

    source = re.sub(
        r"^```(?:cpp|c\+\+|cxx)?\s*\n?|\n?```\s*$",
        "",
        source,
        flags=re.IGNORECASE,
    ).strip()

    if not source:
        test.reason = "Generated test is empty after removing code fences."
        return False

    if len(source) > MAX_TEST_CHARS:
        test.reason = (
            f"Generated test exceeded the {MAX_TEST_CHARS:,}-character limit."
        )
        return False

    test_pattern = r"\bTEST(?:_F|_P)?\s*\("

    if not re.search(test_pattern, source):
        test.reason = (
            "Generated test does not contain a GoogleTest TEST, TEST_F, "
            "or TEST_P declaration."
        )
        return False

    assertion_pattern = (
        r"\b(?:EXPECT|ASSERT)_"
        r"(?:TRUE|FALSE|EQ|NE|LT|LE|GT|GE|STREQ|STRNE|"
        r"FLOAT_EQ|DOUBLE_EQ|NEAR|THROW|NO_THROW|ANY_THROW|"
        r"NO_FATAL_FAILURE|PRED|PRED_FORMAT)\b"
        r"|\b(?:FAIL|SUCCEED|ADD_FAILURE)\s*\("
    )

    if not re.search(assertion_pattern, source):
        test.reason = (
            "Generated test does not contain a recognizable "
            "GoogleTest assertion."
        )
        return False

    test.body = source
    test.imports = normalize_imports(test.imports)
    test.reason = ""

    return True


def render_test(test: TestCase) -> str:
    """Render a generated GoogleTest test as a complete C++ source file."""
    source = (test.body or "").strip()

    if not source:
        raise ValueError(f"Test {test.name} has no source.")

    imports = normalize_imports(test.imports)

    source_imports = re.findall(
        r"(?m)^[ \t]*#include[ \t]+[<\"][^>\"]+[>\"][ \t]*$",
        source,
    )

    imports.extend(source_imports)

    source = re.sub(
        r"(?m)^[ \t]*#include[ \t]+[<\"][^>\"]+[>\"][ \t]*\r?\n?",
        "",
        source,
    ).strip()

    if not any(
        re.search(r"#include\s*[<\"]gtest/gtest\.h[>\"]", item)
        for item in imports
    ):
        imports.append("#include <gtest/gtest.h>")

    imports = normalize_imports(imports)

    return "\n".join(imports) + "\n\n" + source + "\n"


def write_tests(
    cases: list[TestCase],
    output_dir: str | Path,
    prefix: Literal["UT", "IT"],
) -> list[Path]:
    """Write one generated C++ test per source file."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    for old_file in output_dir.glob(f"{prefix}_*.cpp"):
        old_file.unlink()

    written_files = []

    for test in cases:
        file_path = output_dir / f"{test.name}.cpp"
        file_path.write_text(
            render_test(test),
            encoding="utf-8",
        )
        written_files.append(file_path)

    return written_files


def write_test_case(
    test: TestCase,
    output_dir: str | Path,
) -> Path:
    """Write or replace one generated C++ test without touching other tests."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    file_path = output_dir / f"{test.name}.cpp"
    file_path.write_text(
        render_test(test),
        encoding="utf-8",
    )

    return file_path


def make_cases(
    prefix: str,
    items,
    body_of,
    imports_of,
    codebase_path=None,
):
    """Clean generated tests and retain their original test objects."""
    cases = []
    rejected = []
    used = set()

    for item_id, item in items:
        name = f"{prefix}_{item_id}"

        while name in used:
            name += "_b"

        used.add(name)

        case = TestCase(
            name=name,
            imports=normalize_imports(imports_of(item)),
            body=body_of(item) or "",
            source=item,
        )

        if clean_test(case):
            cases.append(case)
        else:
            rejected.append(case)

    return cases, rejected


# ---------------------------------------------------------------------------
# CMake integration
# ---------------------------------------------------------------------------

def _cmake_quote(value: str | Path) -> str:
    """Quote a path for use as a CMake string."""
    value = str(value).replace("\\", "/")
    value = value.replace('"', '\\"')
    return f'"{value}"'


def _googletest_prefix() -> str | None:
    """Find a likely GoogleTest installation prefix."""
    environment_prefix = os.environ.get("CMAKE_PREFIX_PATH")

    if environment_prefix:
        for entry in environment_prefix.split(os.pathsep):
            if (Path(entry) / "lib" / "cmake" / "GTest").is_dir():
                return entry

    brew = shutil.which("brew")

    if brew:
        try:
            result = subprocess.run(
                [brew, "--prefix", "googletest"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )

            if result.returncode == 0:
                prefix = result.stdout.strip()

                if prefix and Path(prefix).exists():
                    return prefix
        except (subprocess.TimeoutExpired, OSError):
            pass

    for prefix in (
        Path("/opt/homebrew/opt/googletest"),
        Path("/usr/local/opt/googletest"),
        Path("/usr"),
        Path("/usr/local"),
    ):
        if (prefix / "lib" / "cmake" / "GTest").is_dir():
            return str(prefix)

    return None


def prepare_cmake_project(
    project: CppProject,
    generated_test_dir: Path,
    workspace_dir: Path,
    test_prefix: Literal["UT", "IT"],
) -> Path:
    """
    Create a temporary CMake wrapper around the original project.

    The original CMakeLists.txt is not modified. The wrapper configures
    C and C++, disables common project test/example options, adds the
    original project as a subdirectory, and links generated tests against
    production library targets.

    Library targets with names indicating test, development, mock,
    benchmark, example, or demonstration code are excluded.
    """
    if project.build_system != "cmake":
        raise CppProjectError(
            "CMake preparation requires a CMake C++ project."
        )

    if project.build_file is None:
        raise CppProjectError("CMakeLists.txt is not configured.")

    generated_test_dir = Path(generated_test_dir).resolve()
    workspace_dir = Path(workspace_dir).resolve()

    if not generated_test_dir.is_dir():
        raise CppProjectError(
            f"Generated test directory does not exist: {generated_test_dir}"
        )

    workspace_dir.mkdir(parents=True, exist_ok=True)

    generated_sources = sorted(
        generated_test_dir.glob(f"{test_prefix}_*.cpp")
    )

    if not generated_sources:
        raise CppProjectError(
            f"No {test_prefix} generated C++ tests found in "
            f"{generated_test_dir}."
        )

    source_list = "\n".join(
        f"    {_cmake_quote(path)}"
        for path in generated_sources
    )

    original_root = _cmake_quote(project.root)

    wrapper = f"""
cmake_minimum_required(VERSION 3.20)

project(CheckpointGeneratedTests LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD {CPP_STANDARD})
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# Disable common project test and example options in this temporary build.
# These settings do not modify the original project files.
set(FORT_ENABLE_TESTING OFF CACHE BOOL "" FORCE)
set(BUILD_TESTING OFF CACHE BOOL "" FORCE)
set(BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)

find_package(GTest CONFIG REQUIRED)

set(CHECKPOINT_ORIGINAL_ROOT {original_root})

add_subdirectory(
    "${{CHECKPOINT_ORIGINAL_ROOT}}"
    checkpoint_original
)

# Collect library targets from the original project and its subdirectories.
function(checkpoint_collect_targets directory output)
    get_property(
        local_targets
        DIRECTORY "${{directory}}"
        PROPERTY BUILDSYSTEM_TARGETS
    )

    set(collected "")

    foreach(target IN LISTS local_targets)
        get_target_property(target_type ${{target}} TYPE)

        if(
            target_type STREQUAL "STATIC_LIBRARY"
            OR target_type STREQUAL "SHARED_LIBRARY"
            OR target_type STREQUAL "OBJECT_LIBRARY"
            OR target_type STREQUAL "INTERFACE_LIBRARY"
        )
            list(APPEND collected ${{target}})
        endif()
    endforeach()

    get_property(
        subdirectories
        DIRECTORY "${{directory}}"
        PROPERTY SUBDIRECTORIES
    )

    foreach(subdirectory IN LISTS subdirectories)
        checkpoint_collect_targets(
            "${{subdirectory}}"
            child_targets
        )
        list(APPEND collected ${{child_targets}})
    endforeach()

    set(${{output}} "${{collected}}" PARENT_SCOPE)
endfunction()

checkpoint_collect_targets(
    "${{CHECKPOINT_ORIGINAL_ROOT}}"
    checkpoint_all_library_targets
)

# Filter targets that are likely to be internal test/development artifacts.
set(checkpoint_library_targets "")

foreach(target IN LISTS checkpoint_all_library_targets)
    string(TOLOWER "${{target}}" target_lower)

    if(
        target_lower MATCHES
        "(^|[-_])(test|tests|dev|mock|benchmark|benchmarks|example|examples|demo|sample|fixture|unit|integration)([-_]|$)"
    )
        message(STATUS "Checkpoint: excluding non-production target ${{target}}")
    else()
        list(APPEND checkpoint_library_targets ${{target}})
    endif()
endforeach()

add_executable(
    checkpoint_generated_tests
{source_list}
)

target_link_libraries(
    checkpoint_generated_tests
    PRIVATE
    GTest::gtest_main
)

if(checkpoint_library_targets)
    message(STATUS "Checkpoint: linking production library targets: ${{checkpoint_library_targets}}")

    target_link_libraries(
        checkpoint_generated_tests
        PRIVATE
        ${{checkpoint_library_targets}}
    )

elseif(checkpoint_all_library_targets)
    message(FATAL_ERROR
        "The original project defines library targets, but all were "
        "excluded as test/development targets. Check the project build "
        "options or target names before generating tests."
    )

else()
    # Projects without library targets need their implementation files
    # compiled directly into the generated test executable.
    message(STATUS
        "Checkpoint: no library targets found; compiling project sources directly."
    )

    file(
        GLOB_RECURSE checkpoint_original_sources
        CONFIGURE_DEPENDS
        "${{CHECKPOINT_ORIGINAL_ROOT}}/*.cpp"
        "${{CHECKPOINT_ORIGINAL_ROOT}}/*.cc"
        "${{CHECKPOINT_ORIGINAL_ROOT}}/*.cxx"
        "${{CHECKPOINT_ORIGINAL_ROOT}}/*.c"
    )

    foreach(source_file IN LISTS checkpoint_original_sources)
        if(
            source_file MATCHES
            "/(build|cmake-build-debug|cmake-build-release|[.]git|node_modules|tests|test|examples)/"
        )
            list(REMOVE_ITEM checkpoint_original_sources "${{source_file}}")
        endif()

        get_filename_component(source_name "${{source_file}}" NAME)

        if(source_name MATCHES "^(main|Main|app_main)[.](cpp|cc|cxx|c)$")
            list(REMOVE_ITEM checkpoint_original_sources "${{source_file}}")
        endif()
    endforeach()

    if(NOT checkpoint_original_sources)
        message(FATAL_ERROR
            "No project implementation sources were found for the generated tests."
        )
    endif()

    target_sources(
        checkpoint_generated_tests
        PRIVATE
        ${{checkpoint_original_sources}}
    )

    target_include_directories(
        checkpoint_generated_tests
        PRIVATE
        "${{CHECKPOINT_ORIGINAL_ROOT}}"
        "${{CHECKPOINT_ORIGINAL_ROOT}}/include"
        "${{CHECKPOINT_ORIGINAL_ROOT}}/src"
    )

    # Include source directories so project-local headers can be resolved.
    foreach(source_file IN LISTS checkpoint_original_sources)
        get_filename_component(source_directory "${{source_file}}" DIRECTORY)

        target_include_directories(
            checkpoint_generated_tests
            PRIVATE
            "${{source_directory}}"
        )
    endforeach()
endif()

enable_testing()
include(GoogleTest)
gtest_discover_tests(checkpoint_generated_tests)
"""

    wrapper_path = workspace_dir / "CMakeLists.txt"
    wrapper_path.write_text(wrapper, encoding="utf-8")

    return wrapper_path


def configure_cmake_project(
    project: CppProject,
    workspace_dir: str | Path,
    timeout: int = BUILD_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess:
    """Configure the temporary CMake wrapper project."""
    if not project.build_command:
        raise CppProjectError("CMake executable is not configured.")

    workspace_dir = Path(workspace_dir).resolve()
    build_dir = workspace_dir / "build"

    command = [
        project.build_command,
        "-S",
        str(workspace_dir),
        "-B",
        str(build_dir),
        "-G",
        "Ninja",
        f"-DCMAKE_CXX_STANDARD={CPP_STANDARD}",
    ]

    prefix = _googletest_prefix()

    if prefix:
        command.append(f"-DCMAKE_PREFIX_PATH={prefix}")

    try:
        return subprocess.run(
            command,
            cwd=workspace_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CppProjectError(
            f"CMake configuration exceeded {timeout} seconds."
        ) from exc
    except FileNotFoundError as exc:
        raise CppProjectError(
            "The configured CMake executable could not be found."
        ) from exc


def compile_cmake_tests(
    project: CppProject,
    workspace_dir: str | Path,
    timeout: int = BUILD_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess:
    """Build the generated GoogleTest executable using CMake and Ninja."""
    workspace_dir = Path(workspace_dir).resolve()
    build_dir = workspace_dir / "build"

    if not (build_dir / "build.ninja").is_file():
        raise CppProjectError(
            "CMake has not generated a Ninja build. Configure the project first."
        )

    command = [
        project.build_command,
        "--build",
        str(build_dir),
        "--target",
        "checkpoint_generated_tests",
        "--parallel",
    ]

    try:
        return subprocess.run(
            command,
            cwd=workspace_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CppProjectError(
            f"CMake compilation exceeded {timeout} seconds."
        ) from exc


def execute_cmake_tests(
    workspace_dir: str | Path,
    report_dir: str | Path,
    timeout: int = TEST_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess:
    """Execute the CMake-built GoogleTest executable and write XML results."""
    workspace_dir = Path(workspace_dir).resolve()
    report_dir = Path(report_dir).resolve()

    executable = (
        workspace_dir
        / "build"
        / "checkpoint_generated_tests"
    )

    if os.name == "nt":
        executable = executable.with_suffix(".exe")

    if not executable.is_file():
        raise CppProjectError(
            f"Generated test executable does not exist: {executable}"
        )

    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "gtest.xml"

    command = [
        str(executable),
        f"--gtest_output=xml:{report_path}",
    ]

    try:
        return subprocess.run(
            command,
            cwd=workspace_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CppProjectError(
            f"GoogleTest execution exceeded {timeout} seconds."
        ) from exc


# ---------------------------------------------------------------------------
# Plain C++ integration
# ---------------------------------------------------------------------------

def _find_gtest_installation() -> tuple[Path, Path]:
    """Find GoogleTest headers and libraries for direct compiler invocation."""
    prefix = _googletest_prefix()

    if prefix:
        prefix_path = Path(prefix)
        include_dir = prefix_path / "include"
        library_dir = prefix_path / "lib"

        if (
            (include_dir / "gtest" / "gtest.h").is_file()
            and (
                (library_dir / "libgtest_main.a").is_file()
                or (library_dir / "libgtest_main.dylib").is_file()
                or (library_dir / "libgtest_main.so").is_file()
            )
        ):
            return include_dir, library_dir

    pkg_config = shutil.which("pkg-config")

    if pkg_config:
        try:
            result = subprocess.run(
                [pkg_config, "--variable=includedir", "gtest"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )

            if result.returncode == 0:
                include_dir = Path(result.stdout.strip())

                result = subprocess.run(
                    [pkg_config, "--variable=libdir", "gtest"],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                )

                if result.returncode == 0:
                    library_dir = Path(result.stdout.strip())

                    if (include_dir / "gtest" / "gtest.h").is_file():
                        return include_dir, library_dir
        except (subprocess.TimeoutExpired, OSError):
            pass

    raise CppProjectError(
        "GoogleTest headers and libraries could not be located. "
        "Install GoogleTest or configure CMAKE_PREFIX_PATH."
    )


def _contains_main_function(source_file: Path) -> bool:
    """Check whether a source file appears to define a main function."""
    try:
        source = source_file.read_text(
            encoding="utf-8",
            errors="replace",
        )
    except OSError:
        return False

    return bool(
        re.search(
            r"\bmain\s*\(\s*(?:int\s+\w+|void)?\s*(?:,\s*"
            r"(?:char\s*\*+\s*\w+|char\s+\*\s*\w+))?\s*\)",
            source,
        )
    )


def compile_plain_tests(
    project: CppProject,
    generated_test_dir: str | Path,
    workspace_dir: str | Path,
    test_prefix: Literal["UT", "IT"],
    timeout: int = BUILD_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess:
    """Compile original C++ sources and generated GoogleTest tests."""
    if project.build_system != "plain":
        raise CppProjectError(
            "Plain compilation requires a plain C++ project."
        )

    generated_test_dir = Path(generated_test_dir).resolve()
    workspace_dir = Path(workspace_dir).resolve()

    generated_tests = sorted(
        generated_test_dir.glob(f"{test_prefix}_*.cpp")
    )

    if not generated_tests:
        raise CppProjectError(
            f"No {test_prefix} generated C++ tests found in "
            f"{generated_test_dir}."
        )

    compiler = project.build_command

    if not compiler or not Path(compiler).exists():
        raise CppProjectError("The configured C++ compiler is unavailable.")

    include_dir, library_dir = _find_gtest_installation()

    classes_dir = workspace_dir / "classes"

    if classes_dir.exists():
        shutil.rmtree(classes_dir)

    classes_dir.mkdir(parents=True, exist_ok=True)

    source_files = [
        path
        for path in find_cpp_sources(project.root, generated_test_dir)
        if not _contains_main_function(path)
    ]

    if not source_files:
        raise CppProjectError(
            f"No original C++ implementation files found in {project.root}."
        )

    include_dirs = {
        project.root,
        project.root / "include",
        project.root / "src",
        include_dir,
    }

    include_dirs.update(path.parent for path in source_files)

    command = [
        compiler,
        f"-std=c++{CPP_STANDARD}",
        "-pthread",
        *[
            argument
            for directory in sorted(include_dirs)
            if directory.is_dir()
            for argument in ("-I", str(directory))
        ],
        *[str(path) for path in source_files],
        *[str(path) for path in generated_tests],
        "-L",
        str(library_dir),
        "-lgtest_main",
        "-lgtest",
        "-pthread",
        "-o",
        str(classes_dir / "checkpoint_generated_tests"),
    ]

    try:
        return subprocess.run(
            command,
            cwd=project.root,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CppProjectError(
            f"Plain C++ compilation exceeded {timeout} seconds."
        ) from exc
    except FileNotFoundError as exc:
        raise CppProjectError(
            "The configured C++ compiler could not be found."
        ) from exc


def execute_plain_tests(
    workspace_dir: str | Path,
    report_dir: str | Path,
    timeout: int = TEST_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess:
    """Execute previously compiled plain C++ GoogleTests."""
    workspace_dir = Path(workspace_dir).resolve()
    report_dir = Path(report_dir).resolve()

    executable = (
        workspace_dir
        / "classes"
        / "checkpoint_generated_tests"
    )

    if os.name == "nt":
        executable = executable.with_suffix(".exe")

    if not executable.is_file():
        raise CppProjectError(
            f"Compiled C++ test executable does not exist: {executable}"
        )

    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "gtest.xml"

    command = [
        str(executable),
        f"--gtest_output=xml:{report_path}",
    ]

    try:
        return subprocess.run(
            command,
            cwd=workspace_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CppProjectError(
            f"GoogleTest execution exceeded {timeout} seconds."
        ) from exc


# ---------------------------------------------------------------------------
# Build diagnostics and test-result parsing
# ---------------------------------------------------------------------------

def parse_cpp_build_output(
    result: subprocess.CompletedProcess,
    generated_test_dir: str | Path,
) -> tuple[dict[str, list[str]], list[str]]:
    """Associate Clang/GCC diagnostics with generated test source files."""
    generated_test_dir = Path(generated_test_dir).resolve()

    generated_names = {
        path.stem
        for path in generated_test_dir.glob("*.cpp")
    }

    errors_by_file: dict[str, list[str]] = {}
    other_errors = []

    output = "\n".join(
        part for part in (result.stdout, result.stderr) if part
    )

    diagnostic_pattern = re.compile(
        r"^\s*(.+?\.(?:cpp|cc|cxx)):(\d+):(?:(\d+):)?\s*"
        r"(?:fatal\s+)?error:\s*(.*)$",
        flags=re.IGNORECASE,
    )

    for line in output.splitlines():
        match = diagnostic_pattern.match(line)

        if not match:
            continue

        file_path = match.group(1).strip().strip('"')
        line_number = match.group(2)
        message = match.group(4).strip()

        file_name = Path(file_path.replace("\\", "/")).stem
        diagnostic = (
            f"line {line_number}: "
            f"{message or 'C++ compilation error'}"
        )

        if file_name in generated_names:
            errors = errors_by_file.setdefault(file_name, [])

            if diagnostic not in errors:
                errors.append(diagnostic)
        else:
            detail = f"{file_path}: {diagnostic}"

            if detail not in other_errors:
                other_errors.append(detail)

    if result.returncode != 0 and not errors_by_file and not other_errors:
        tail = output.strip().splitlines()[-8:]

        other_errors.append(
            "\n".join(tail)
            if tail
            else "C++ compilation failed without a recognizable diagnostic."
        )

    return errors_by_file, other_errors


def parse_gtest_results(
    result: subprocess.CompletedProcess,
    report_dir: str | Path,
    test_run: TestRun,
) -> TestRun:
    """Parse GoogleTest XML output into the shared TestRun structure."""
    report_dir = Path(report_dir).resolve()
    test_run.report_path = str(report_dir)

    report_file = report_dir / "gtest.xml"

    if not report_file.is_file():
        output = "\n".join(
            part for part in (result.stdout, result.stderr) if part
        ).strip()

        test_run.project_error = (
            output[-4000:]
            if output
            else "GoogleTest did not produce an XML report."
        )
        return test_run

    try:
        root = ET.parse(report_file).getroot()
    except ET.ParseError as exc:
        test_run.project_error = (
            f"Could not parse GoogleTest XML report: {exc}"
        )
        return test_run

    total = 0
    failures = 0
    skipped = 0
    failed_tests = []

    for testcase in root.iter("testcase"):
        total += 1

        suite_name = testcase.get("classname", "unknown")
        test_name = testcase.get("name", "unknown")
        display_name = f"{suite_name}::{test_name}"

        failure_elements = [
            element
            for element in testcase
            if element.tag in ("failure", "error")
        ]

        if failure_elements:
            failures += 1
            failed_tests.append(display_name)

        elif any(
            element.tag == "skipped"
            for element in testcase
        ):
            skipped += 1

    test_run.passed = total - failures - skipped
    test_run.failed = failures
    test_run.skipped = skipped
    test_run.failed_tests = failed_tests

    if total == 0:
        output = "\n".join(
            part for part in (result.stdout, result.stderr) if part
        ).strip()

        test_run.project_error = (
            output[-4000:]
            if output
            else "GoogleTest did not report any test cases."
        )

    elif result.returncode != 0 and failures == 0:
        output = "\n".join(
            part for part in (result.stdout, result.stderr) if part
        ).strip()

        test_run.project_error = (
            output[-4000:]
            if output
            else "GoogleTest returned a non-zero exit code."
        )

    return test_run


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def write_results(run, output_dir, to_json):
    """Write validated, discarded, and summary test results."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    validated = [
        to_json(case)
        for case in run.kept
    ]

    discarded = [
        dict(to_json(case), reason=case.reason)
        for case in run.dropped
    ]

    (output_dir / "validated_tests.json").write_text(
        json.dumps(validated, indent=2),
        encoding="utf-8",
    )

    (output_dir / "discarded_tests.json").write_text(
        json.dumps(discarded, indent=2),
        encoding="utf-8",
    )

    report = dict(run.summary(), message=run.message())

    (output_dir / "test_report.json").write_text(
        json.dumps(report, indent=2),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# AI compiler-error repair
# ---------------------------------------------------------------------------

def make_fixer(llm, context_for=None):
    """Build an async function that repairs one generated GoogleTest file."""
    structured_llm = llm.with_structured_output(TestRepair)

    async def fix(case):
        context = context_for(case) if context_for else ""

        if isinstance(context, (list, tuple)):
            context = "\n\n".join(str(item) for item in context)

        numbered = "\n".join(
            f"{number:4d}  {line}"
            for number, line in enumerate(
                render_test(case).splitlines(),
                1,
            )
        )

        system_message = (
            "You are a senior C++ engineer fixing a generated GoogleTest "
            "test that does not compile. Fix only what the compiler errors "
            "identify."
        )

        prompt = f"""
#### TEST FILE ({case.name}.cpp, with line numbers)
{numbered}

#### COMPILER ERRORS FOR THIS FILE
{chr(10).join(case.errors)}

#### CODE FROM THE CODEBASE UNDER TEST AND LSP CONTEXT IF PROVIDED
{context or "NO CONTEXT PROVIDED"}

#### TASK
Return a corrected GoogleTest test declaration that compiles.

#### RULES
- test_method: only the GoogleTest declaration and its test body.
  Do not return a main function or a complete source file.
- imports: every required C++ include directive, one per entry.
  Example: #include "Calculator.h"
- Use GoogleTest macros such as TEST, TEST_F, or TEST_P.
- Use only public types and members accessible to the generated test.
- Use only functions, constructors, fields, and types supported by the
  supplied source code or standard C++/GoogleTest APIs.
- Do not invent methods, constructors, fields, or helper classes.
- Do not introduce additional testing frameworks or mocking libraries.
- Keep the original test's purpose and assertions.
- Do not weaken an assertion merely to make the test compile.
- If the test cannot be fixed using the available public API,
  return an empty test_method.
- Use LSP context as supplementary information about symbols,
  definitions, references, and relationships between source files.
- Do not assume a symbol is publicly accessible merely because LSP
  identifies it. Verify accessibility using source-code evidence.
- Prefer concrete source-code evidence over LSP metadata.
"""

        messages = [
            ("system", system_message),
            ("user", prompt),
        ]

        try:
            output = await structured_llm.ainvoke(messages)

        except (DailyLimitError, RateLimitTimeoutError):
            raise

        except Exception as error:
            progress(
                f"AI repair call failed for {case.name}: {error}"
            )
            return None

        if output is None or not (output.test_method or "").strip():
            return None

        return output.imports or [], output.test_method

    return fix


def _repair(fix, cases, loop):
    """Run repair calls on the event loop already used by the agent's LLM."""

    async def run_batch():
        semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

        async def guarded(case):
            async with semaphore:
                return await fix(case)

        return await asyncio.gather(
            *(guarded(case) for case in cases)
        )

    if loop is not None:
        return loop.run_until_complete(run_batch())

    temporary = asyncio.new_event_loop()

    try:
        return temporary.run_until_complete(run_batch())
    finally:
        temporary.close()


def _drop(run, live, generated_test_dir, case):
    """Remove a failed generated test and record its rejection reason."""
    live.remove(case)
    run.dropped.append(case)

    for path in Path(generated_test_dir).glob(f"{case.name}.cpp"):
        path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Main harness entry point
# ---------------------------------------------------------------------------

def check_tests(
    kind: str,
    prefix: str,
    cases: list[TestCase],
    rejected: list[TestCase],
    codebase_path: str,
    codebase_name: str,
    output_dir: str | Path,
    fix=None,
    loop=None,
) -> TestRun:
    """
    Write, compile, repair, and execute generated C++ GoogleTests.

    Compiler errors are repaired individually, with at most
    MAX_REPAIR_ROUNDS attempts per test. Tests that compile successfully
    remain valid even if their assertions fail during execution.
    """
    output_dir = Path(output_dir)

    run = TestRun(
        kind=kind,
        generated=len(cases) + len(rejected),
        dropped=list(rejected),
        unusable=len(rejected),
    )

    live: list[TestCase] = []
    project = None

    generated_test_dir = (
        output_dir / f"{prefix.lower()}_generated_tests"
    )
    workspace_dir = (
        output_dir / f"{prefix.lower()}_cpp_workspace"
    )
    report_dir = workspace_dir / "reports"

    generated_test_dir.mkdir(parents=True, exist_ok=True)
    workspace_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    if not cases:
        return run

    try:
        project = detect_cpp_project(codebase_path)

        write_tests(cases, generated_test_dir, prefix)
        mark_generated(generated_test_dir)

        live = list(cases)

        if project.build_system == "cmake":
            prepare_cmake_project(
                project,
                generated_test_dir,
                workspace_dir,
                prefix,
            )

            configure_result = configure_cmake_project(
                project,
                workspace_dir,
            )

            if configure_result.returncode != 0:
                output = "\n".join(
                    part
                    for part in (
                        configure_result.stdout,
                        configure_result.stderr,
                    )
                    if part
                ).strip()

                run.project_error = (
                    output[-4000:]
                    if output
                    else "CMake configuration failed."
                )
                run.kept = list(live)
                return run

        def compile_tests():
            """Compile generated tests using the detected build system."""
            if project.build_system == "cmake":
                return compile_cmake_tests(
                    project,
                    workspace_dir,
                )

            return compile_plain_tests(
                project,
                generated_test_dir,
                workspace_dir,
                prefix,
            )

        def execute_tests():
            """Execute generated tests using the detected build system."""
            if project.build_system == "cmake":
                return execute_cmake_tests(
                    workspace_dir,
                    report_dir,
                )

            return execute_plain_tests(
                workspace_dir,
                report_dir,
            )

        while live:
            compile_result = compile_tests()

            errors_by_test, project_errors = parse_cpp_build_output(
                compile_result,
                generated_test_dir,
            )

            if project_errors:
                run.project_error = "\n".join(project_errors)
                run.kept = list(live)
                return run

            if not errors_by_test:
                if compile_result.returncode != 0:
                    output = "\n".join(
                        part
                        for part in (
                            compile_result.stdout,
                            compile_result.stderr,
                        )
                        if part
                    ).strip()

                    run.project_error = (
                        output[-4000:]
                        if output
                        else "C++ compilation failed with an unrecognized error."
                    )
                    run.kept = list(live)
                    return run

                break

            repair_candidates = []

            for case in list(live):
                errors = errors_by_test.get(case.name, [])

                if not errors:
                    continue

                case.errors = errors

                if case.repairs >= MAX_REPAIR_ROUNDS:
                    case.reason = (
                        "Exceeded the maximum number of compiler repair rounds."
                    )
                    _drop(run, live, generated_test_dir, case)
                    continue

                repair_candidates.append(case)

            if not repair_candidates:
                break

            if fix is None:
                for case in repair_candidates:
                    case.reason = (
                        "No C++ test repair function was provided."
                    )
                    _drop(run, live, generated_test_dir, case)
                continue

            repairs = _repair(
                fix,
                repair_candidates,
                loop,
            )

            for case, repaired in zip(repair_candidates, repairs):
                if case not in live:
                    continue

                if not repaired:
                    case.reason = "The C++ test could not be repaired."
                    _drop(run, live, generated_test_dir, case)
                    continue

                imports, body = repaired

                candidate = TestCase(
                    name=case.name,
                    imports=imports,
                    body=body,
                    source=case.source,
                )

                if not clean_test(candidate):
                    case.reason = (
                        "The repaired C++ test failed validation."
                    )
                    _drop(run, live, generated_test_dir, case)
                    continue

                case.imports = candidate.imports
                case.body = candidate.body
                case.repairs += 1
                case.errors = []

                write_test_case(case, generated_test_dir)

            if project.build_system == "cmake":
                prepare_cmake_project(
                    project,
                    generated_test_dir,
                    workspace_dir,
                    prefix,
                )

                configure_result = configure_cmake_project(
                    project,
                    workspace_dir,
                )

                if configure_result.returncode != 0:
                    output = "\n".join(
                        part
                        for part in (
                            configure_result.stdout,
                            configure_result.stderr,
                        )
                        if part
                    ).strip()

                    run.project_error = (
                        output[-4000:]
                        if output
                        else "CMake configuration failed after test repair."
                    )
                    run.kept = list(live)
                    return run

        if live:
            for report_file in report_dir.rglob("*"):
                if report_file.is_file():
                    report_file.unlink()

            execution_result = execute_tests()
            run.kept = list(live)

            parse_gtest_results(
                execution_result,
                report_dir,
                run,
            )

        else:
            run.kept = []

    except (DailyLimitError, RateLimitTimeoutError):
        raise

    except CppProjectError as error:
        run.project_error = str(error)
        run.kept = list(live)

    except Exception as error:
        run.project_error = (
            f"Unexpected C++ test execution error: {error}"
        )
        run.kept = list(live)

    return run
