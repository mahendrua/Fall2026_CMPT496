
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from agent.crawl_config import mark_generated
from agent.llm import DailyLimitError, RateLimitTimeoutError
from agent.structured_output.UTV_output import TestRepair
from backend.progress_logging import progress


KotlinBuildSystem = Literal["maven", "gradle", "plain"]

KOTLIN_PLUGIN_VERSION = "2.0.21"
JUNIT_VERSION = "5.11.4"
JUNIT_PLATFORM_VERSION = "1.11.4"

MAX_TEST_CHARS = 20_000
MAX_REPAIR_ROUNDS = 2
MAX_CONCURRENCY = 10
BUILD_TIMEOUT_SECONDS = 600
TEST_TIMEOUT_SECONDS = 900


class KotlinProjectError(Exception):
    """Raised when a Kotlin project cannot be identified or executed."""


@dataclass
class KotlinProject:
    root: Path
    build_system: KotlinBuildSystem
    build_command: str | None
    build_file: Path | None


@dataclass
class TestCase:
    """One generated Kotlin test being processed by check_tests()."""

    __test__ = False

    name: str
    imports: list[str]
    body: str
    source: Any = None
    errors: list[str] = field(default_factory=list)
    repairs: int = 0
    reason: str = ""
    package_name: str = ""


@dataclass
class TestRun:
    """Results produced by the Kotlin test harness."""

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
            "harness": "kotlin-junit5",
        }

    def message(self) -> str:
        if self.project_error:
            return (
                f"{self.kind} tests were written but the Kotlin test "
                f"project could not complete: {self.project_error}"
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


def find_kotlin_project_root(codebase_path: str | Path) -> Path:
    """Find the nearest Maven or Gradle root for a Kotlin codebase."""
    path = Path(codebase_path).resolve()

    if not path.exists():
        raise KotlinProjectError(f"Codebase path does not exist: {path}")

    if path.is_file():
        path = path.parent

    for directory in (path, *path.parents):
        if (
            (directory / "pom.xml").is_file()
            or (directory / "build.gradle").is_file()
            or (directory / "build.gradle.kts").is_file()
        ):
            return directory

    return path


def detect_kotlin_project(codebase_path: str | Path) -> KotlinProject:
    """Detect Maven, Gradle, or plain Kotlin project structure."""
    root = find_kotlin_project_root(codebase_path)

    maven_file = root / "pom.xml"
    gradle_file = next(
        (
            file
            for file in (
                root / "build.gradle",
                root / "build.gradle.kts",
            )
            if file.is_file()
        ),
        None,
    )

    if maven_file.is_file() and gradle_file is not None:
        raise KotlinProjectError(
            f"Both Maven and Gradle build files were found in {root}. "
            "Please select the intended build system."
        )

    if maven_file.is_file():
        wrapper = root / "mvnw"
        command = str(wrapper) if wrapper.is_file() else shutil.which("mvn")

        if not command:
            raise KotlinProjectError(
                "Maven project detected, but neither mvnw nor Maven is available."
            )

        return KotlinProject(root, "maven", command, maven_file)

    if gradle_file is not None:
        wrapper = root / "gradlew"
        command = str(wrapper) if wrapper.is_file() else shutil.which("gradle")

        if not command:
            raise KotlinProjectError(
                "Gradle project detected, but neither gradlew nor Gradle is available."
            )

        return KotlinProject(root, "gradle", command, gradle_file)

    if not any(root.rglob("*.kt")):
        raise KotlinProjectError(
            f"No Maven, Gradle, or Kotlin source files found in {root}."
        )

    return KotlinProject(root, "plain", None, None)


def normalize_imports(imports: str | list[str] | None) -> list[str]:
    """Normalize and deduplicate Kotlin import statements."""
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

        item = re.sub(r"^import\s+", "", item)
        item = item.rstrip(";").strip()

        if not item:
            continue

        statement = f"import {item}"

        if statement not in seen:
            normalized.append(statement)
            seen.add(statement)

    return normalized


def clean_test(test: TestCase) -> bool:
    """Validate one generated Kotlin JUnit 5 test function."""
    source = (test.body or "").strip()

    if not source:
        test.reason = "Generated test is empty."
        return False

    source = re.sub(
        r"^```(?:kotlin|kt)?\s*\n?|\n?```\s*$",
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

    if not re.search(r"@\s*Test\b", source):
        test.reason = "Generated test does not contain a JUnit 5 @Test annotation."
        return False

    if not re.search(
        r"\bfun\s+[A-Za-z_]\w*\s*\([^)]*\)",
        source,
    ):
        test.reason = "Generated test does not contain a recognizable Kotlin test function."
        return False

    assertion_pattern = (
        r"\bassert(?:Equals|NotEquals|True|False|Null|NotNull|"
        r"Same|NotSame|ArrayEquals|IterableEquals|Throws|"
        r"DoesNotThrow|All|InstanceOf|Timeout)\s*\("
        r"|\bfail\s*\("
    )

    if not re.search(assertion_pattern, source):
        test.reason = "Generated test does not contain a recognizable JUnit assertion."
        return False

    # The generator returns a function, not a class or package declaration.
    if re.search(r"^\s*(?:class|object|interface)\s+", source, re.MULTILINE):
        test.reason = "Generated test must contain a test function, not a class declaration."
        return False

    test.body = source
    test.imports = normalize_imports(test.imports)
    test.reason = ""
    return True


def get_test_package(test: TestCase) -> str:
    """Determine and validate the Kotlin package for a generated test."""
    source = test.body or ""

    package_matches = re.findall(
        r"^\s*package\s+"
        r"([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*$",
        source,
        flags=re.MULTILINE,
    )

    if len(set(package_matches)) > 1:
        raise ValueError(
            f"Test {test.name} contains conflicting package declarations."
        )

    declared_package = package_matches[0] if package_matches else ""
    package_name = (test.package_name or '').strip() or declared_package

    if (
        (test.package_name or '').strip()
        and declared_package
        and (test.package_name or '').strip() != declared_package
    ):
        raise ValueError(
            f"Test {test.name} has conflicting package metadata and source declarations."
        )

    if package_name and not re.fullmatch(
        r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*",
        package_name,
    ):
        raise ValueError(f"Invalid Kotlin package name: {package_name}")

    return package_name


def render_test(test: TestCase, class_name: str) -> str:
    """Render a generated Kotlin function as a complete JUnit 5 test class."""
    source = (test.body or "").strip()

    if not source:
        raise ValueError(f"Test {test.name} has no source.")

    package_name = get_test_package(test)
    imports = normalize_imports(test.imports)

    source = re.sub(
        r"(?m)^[ \t]*package[ \t]+"
        r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*[ \t]*(?:\r?\n)?",
        "",
        source,
    )

    source_imports = re.findall(
        r"(?m)^[ \t]*import[ \t]+[\w.*]+",
        source,
    )

    for statement in source_imports:
        imports.extend(normalize_imports(statement))

    source = re.sub(
        r"(?m)^[ \t]*import[ \t]+[\w.*]+[ \t]*(?:\r?\n)?",
        "",
        source,
    ).strip()

    if "import org.junit.jupiter.api.Test" not in imports:
        imports.append("import org.junit.jupiter.api.Test")

    if not any(
        item.startswith("import org.junit.jupiter.api.Assertions.")
        for item in imports
    ):
        imports.append("import org.junit.jupiter.api.Assertions.*")

    imports = normalize_imports(imports)

    package_block = f"package {package_name}\n\n" if package_name else ""
    import_block = "\n".join(imports)

    return (
        f"{package_block}"
        f"{import_block}\n\n"
        f"class {class_name} {{\n"
        f"{source}\n"
        f"}}\n"
    )


def write_tests(
    cases: list[TestCase],
    output_dir: str | Path,
    prefix: Literal["UT", "IT"],
) -> list[Path]:
    """Write one generated Kotlin test class per file."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    for old_file in output_dir.rglob(f"{prefix}_*.kt"):
        old_file.unlink()

    written_files = []

    for test in cases:
        package_name = get_test_package(test)
        package_dir = (
            output_dir.joinpath(*package_name.split("."))
            if package_name
            else output_dir
        )
        package_dir.mkdir(parents=True, exist_ok=True)

        file_path = package_dir / f"{test.name}.kt"
        file_path.write_text(
            render_test(test, test.name),
            encoding="utf-8",
        )
        written_files.append(file_path)

    return written_files


def write_test_case(test: TestCase, output_dir: str | Path) -> Path:
    """Write or replace one generated Kotlin test without touching other tests."""
    output_dir = Path(output_dir)
    package_name = get_test_package(test)

    package_dir = (
        output_dir.joinpath(*package_name.split("."))
        if package_name
        else output_dir
    )
    package_dir.mkdir(parents=True, exist_ok=True)

    file_path = package_dir / f"{test.name}.kt"
    file_path.write_text(
        render_test(test, test.name),
        encoding="utf-8",
    )
    return file_path


def _find_or_create(
    parent: ET.Element,
    name: str,
    namespace: str,
) -> ET.Element:
    tag = f"{namespace}{name}"
    element = parent.find(tag)

    if element is None:
        element = ET.SubElement(parent, tag)

    return element



def prepare_maven_project(
    project: KotlinProject,
    generated_test_dir: Path,
    workspace_dir: Path,
    report_dir: Path,
    test_prefix: str,
) -> Path:
    """Create a temporary Maven POM configured for generated Kotlin tests."""
    if project.build_file is None:
        raise KotlinProjectError("Maven project does not have a pom.xml.")

    try:
        tree = ET.parse(project.build_file)
        root = tree.getroot()
    except (ET.ParseError, OSError) as error:
        raise KotlinProjectError(
            f"Could not read Maven project file: {error}"
        ) from error

    namespace = ""
    if root.tag.startswith("{"):
        namespace = root.tag.split("}", 1)[0] + "}"

    generated_test_dir = Path(generated_test_dir).resolve()
    workspace_dir = Path(workspace_dir).resolve()
    report_dir = Path(report_dir).resolve()

    production_source_dir = (project.root / "src" / "main" / "kotlin").resolve()

    build = _find_or_create(root, "build", namespace)

    _find_or_create(build, "testSourceDirectory", namespace).text = str(
        generated_test_dir
    )
    _find_or_create(build, "directory", namespace).text = str(
        workspace_dir / "build"
    )

    dependencies = _find_or_create(root, "dependencies", namespace)

    def has_dependency(group: str, artifact: str) -> bool:
        for dependency in dependencies.findall(f"{namespace}dependency"):
            group_element = dependency.find(f"{namespace}groupId")
            artifact_element = dependency.find(f"{namespace}artifactId")

            if (
                group_element is not None
                and artifact_element is not None
                and (group_element.text or "").strip() == group
                and (artifact_element.text or "").strip() == artifact
            ):
                return True

        return False

    if not has_dependency("org.junit.jupiter", "junit-jupiter"):
        dependency = ET.SubElement(dependencies, f"{namespace}dependency")
        ET.SubElement(dependency, f"{namespace}groupId").text = (
            "org.junit.jupiter"
        )
        ET.SubElement(dependency, f"{namespace}artifactId").text = (
            "junit-jupiter"
        )
        ET.SubElement(dependency, f"{namespace}version").text = JUNIT_VERSION
        ET.SubElement(dependency, f"{namespace}scope").text = "test"

    plugins = _find_or_create(build, "plugins", namespace)

    kotlin_plugin = None
    surefire_plugin = None

    for plugin in plugins.findall(f"{namespace}plugin"):
        artifact = plugin.find(f"{namespace}artifactId")

        if artifact is None:
            continue

        artifact_name = (artifact.text or "").strip()

        if artifact_name == "kotlin-maven-plugin":
            kotlin_plugin = plugin
        elif artifact_name == "maven-surefire-plugin":
            surefire_plugin = plugin

    if kotlin_plugin is None:
        kotlin_plugin = ET.SubElement(plugins, f"{namespace}plugin")
        ET.SubElement(kotlin_plugin, f"{namespace}groupId").text = (
            "org.jetbrains.kotlin"
        )
        ET.SubElement(kotlin_plugin, f"{namespace}artifactId").text = (
            "kotlin-maven-plugin"
        )

    version = kotlin_plugin.find(f"{namespace}version")
    if version is None:
        version = ET.SubElement(kotlin_plugin, f"{namespace}version")
    if not (version.text or "").strip():
        version.text = KOTLIN_PLUGIN_VERSION

    executions = _find_or_create(kotlin_plugin, "executions", namespace)

    execution_sources = {
        "checkpoint-kotlin-compile": (
            "compile",
            production_source_dir,
        ),
        "checkpoint-kotlin-test-compile": (
            "test-compile",
            generated_test_dir,
        ),
    }

    for execution_id, (goal, source_dir) in execution_sources.items():
        execution = next(
            (
                item
                for item in executions.findall(f"{namespace}execution")
                if (
                    item.find(f"{namespace}id") is not None
                    and (item.find(f"{namespace}id").text or "").strip()
                    == execution_id
                )
            ),
            None,
        )

        if execution is None:
            execution = ET.SubElement(executions, f"{namespace}execution")
            ET.SubElement(execution, f"{namespace}id").text = execution_id

        goals = _find_or_create(execution, "goals", namespace)

        if not any(
            (item.text or "").strip() == goal
            for item in goals.findall(f"{namespace}goal")
        ):
            ET.SubElement(goals, f"{namespace}goal").text = goal

        execution_config = _find_or_create(
            execution,
            "configuration",
            namespace,
        )
        source_dirs = _find_or_create(
            execution_config,
            "sourceDirs",
            namespace,
        )

        for child in list(source_dirs):
            source_dirs.remove(child)

        ET.SubElement(source_dirs, f"{namespace}sourceDir").text = str(
            source_dir
        )

    # Remove the previous unsupported shared configuration if present.
    shared_config = kotlin_plugin.find(f"{namespace}configuration")
    if shared_config is not None:
        old_test_sources = shared_config.find(
            f"{namespace}testSourceDirs"
        )
        if old_test_sources is not None:
            shared_config.remove(old_test_sources)

    if surefire_plugin is None:
        surefire_plugin = ET.SubElement(plugins, f"{namespace}plugin")
        ET.SubElement(surefire_plugin, f"{namespace}groupId").text = (
            "org.apache.maven.plugins"
        )
        ET.SubElement(surefire_plugin, f"{namespace}artifactId").text = (
            "maven-surefire-plugin"
        )

    surefire_version = surefire_plugin.find(f"{namespace}version")
    if surefire_version is None:
        surefire_version = ET.SubElement(
            surefire_plugin,
            f"{namespace}version",
        )
    if not (surefire_version.text or "").strip():
        surefire_version.text = "3.2.5"

    surefire_config = _find_or_create(
        surefire_plugin,
        "configuration",
        namespace,
    )
    includes = _find_or_create(surefire_config, "includes", namespace)

    for child in list(includes):
        includes.remove(child)

    ET.SubElement(includes, f"{namespace}include").text = (
        f"**/{test_prefix}_*.class"
    )
    _find_or_create(
        surefire_config,
        "reportsDirectory",
        namespace,
    ).text = str(report_dir)

    temporary_pom = project.root / (
        f".checkpoint-{test_prefix.lower()}-pom.xml"
    )

    ET.register_namespace("", "http://maven.apache.org/POM/4.0.0")
    tree.write(
        temporary_pom,
        encoding="utf-8",
        xml_declaration=True,
    )

    return temporary_pom


def cleanup_maven_project(pom_path: str | Path) -> None:
    """Remove a temporary Checkpoint Maven POM."""
    path = Path(pom_path)

    if path.name.startswith(".checkpoint-") and path.name.endswith("-pom.xml"):
        path.unlink(missing_ok=True)


def _run_maven_goal(
    project: KotlinProject,
    pom_path: str | Path,
    generated_test_dir: str | Path,
    test_prefix: Literal["UT", "IT"],
    goal: Literal["test-compile", "test"],
    timeout: int,
) -> subprocess.CompletedProcess:
    """Run a Maven lifecycle goal for generated Kotlin tests."""
    pom_path = Path(pom_path).resolve()
    generated_test_dir = Path(generated_test_dir).resolve()

    if not pom_path.is_file():
        raise KotlinProjectError(f"Temporary Maven POM does not exist: {pom_path}")

    if not list(generated_test_dir.rglob(f"{test_prefix}_*.kt")):
        raise KotlinProjectError(
            f"No {test_prefix} generated Kotlin tests found in {generated_test_dir}."
        )

    command = [
        project.build_command,
        "-B",
        "-Dstyle.color=never",
        "-f",
        str(pom_path),
        goal,
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
        raise KotlinProjectError(
            f"Maven {goal} exceeded the {timeout}-second timeout."
        ) from exc
    except FileNotFoundError as exc:
        raise KotlinProjectError("The configured Maven executable could not be found.") from exc


def compile_maven_tests(project, pom_path, generated_test_dir, test_prefix,
                        timeout=BUILD_TIMEOUT_SECONDS):
    """Compile generated Kotlin tests using Maven."""
    return _run_maven_goal(
        project, pom_path, generated_test_dir, test_prefix,
        "test-compile", timeout,
    )


def execute_maven_tests(project, pom_path, generated_test_dir, test_prefix,
                        timeout=TEST_TIMEOUT_SECONDS):
    """Execute generated Kotlin tests using Maven."""
    return _run_maven_goal(
        project, pom_path, generated_test_dir, test_prefix,
        "test", timeout,
    )


def prepare_gradle_project(
    project: KotlinProject,
    generated_test_dir: str | Path,
    workspace_dir: str | Path,
    report_dir: str | Path,
    test_prefix: Literal["UT", "IT"],
) -> Path:
    """Create a Gradle init script that configures Kotlin test sources."""
    if project.build_system != "gradle":
        raise KotlinProjectError("Gradle preparation requires a Gradle project.")

    generated_test_dir = Path(generated_test_dir).resolve()
    workspace_dir = Path(workspace_dir).resolve()
    report_dir = Path(report_dir).resolve()

    workspace_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    generated_path = json.dumps(str(generated_test_dir))
    report_path = json.dumps(str(report_dir))
    build_path = json.dumps(str(workspace_dir / "build"))
    test_include = json.dumps(f"**/{test_prefix}_*.class")

    init_script = f"""
gradle.projectsEvaluated {{
    def checkpointProject = gradle.rootProject

    def kotlinExtension = checkpointProject.extensions.findByName("kotlin")

    if (kotlinExtension == null) {{
        throw new GradleException(
            "Checkpoint requires the Kotlin Gradle plugin in the project."
        )
    }}

    def sourceSets = kotlinExtension.sourceSets
    def testSourceSet = sourceSets.findByName("test")

    if (testSourceSet == null) {{
        throw new GradleException(
            "Checkpoint could not find the Kotlin test source set."
        )
    }}

    testSourceSet.kotlin.setSrcDirs([
        new File({generated_path})
    ])

    checkpointProject.repositories {{
        mavenCentral()
    }}

    checkpointProject.dependencies.add(
        "testImplementation",
        "org.junit.jupiter:junit-jupiter:{JUNIT_VERSION}"
    )

    checkpointProject.dependencies.add(
        "testRuntimeOnly",
        "org.junit.platform:junit-platform-launcher:{JUNIT_PLATFORM_VERSION}"
    )

    def testTask = checkpointProject.tasks.findByName("test")

    if (testTask == null) {{
        throw new GradleException(
            "Checkpoint could not find the Gradle test task."
        )
    }}

    testTask.useJUnitPlatform()
    testTask.include({test_include})

    testTask.reports.junitXml.outputLocation.set(
        new File({report_path})
    )

    checkpointProject.layout.buildDirectory.set(
        new File({build_path})
    )
}}
"""

    init_path = workspace_dir / "checkpoint.init.gradle"
    init_path.write_text(init_script, encoding="utf-8")
    return init_path


def cleanup_gradle_project(init_script_path: str | Path) -> None:
    """Remove a temporary Checkpoint Gradle init script."""
    path = Path(init_script_path)

    if path.name == "checkpoint.init.gradle":
        path.unlink(missing_ok=True)


def _run_gradle_task(
    project: KotlinProject,
    workspace_dir: str | Path,
    generated_test_dir: str | Path,
    test_prefix: Literal["UT", "IT"],
    task: Literal["compileTestKotlin", "test"],
    timeout: int,
) -> subprocess.CompletedProcess:
    """Run a Gradle Kotlin compilation or test task."""
    workspace_dir = Path(workspace_dir).resolve()
    generated_test_dir = Path(generated_test_dir).resolve()
    init_script = workspace_dir / "checkpoint.init.gradle"

    if not init_script.is_file():
        raise KotlinProjectError(f"Checkpoint Gradle init script does not exist: {init_script}")

    if not list(generated_test_dir.rglob(f"{test_prefix}_*.kt")):
        raise KotlinProjectError(
            f"No {test_prefix} generated Kotlin tests found in {generated_test_dir}."
        )

    command = [
        project.build_command,
        "--no-daemon",
        "--console=plain",
        "-p",
        str(project.root),
        "-I",
        str(init_script),
        task,
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
        raise KotlinProjectError(
            f"Gradle task '{task}' exceeded the {timeout}-second timeout."
        ) from exc
    except FileNotFoundError as exc:
        raise KotlinProjectError("The configured Gradle executable could not be found.") from exc


def compile_gradle_tests(project, workspace_dir, generated_test_dir, test_prefix,
                         timeout=BUILD_TIMEOUT_SECONDS):
    """Compile generated Kotlin tests using Gradle."""
    return _run_gradle_task(
        project, workspace_dir, generated_test_dir, test_prefix,
        "compileTestKotlin", timeout,
    )


def execute_gradle_tests(project, workspace_dir, generated_test_dir, test_prefix,
                         timeout=TEST_TIMEOUT_SECONDS):
    """Execute generated Kotlin tests using Gradle."""
    return _run_gradle_task(
        project, workspace_dir, generated_test_dir, test_prefix,
        "test", timeout,
    )



def prepare_plain_project(
    project: KotlinProject,
    generated_test_dir: str | Path,
    workspace_dir: str | Path,
    report_dir: str | Path,
) -> Path:
    """
    Create an isolated Gradle project for a plain Kotlin codebase.

    Production Kotlin sources are copied into the temporary project's main
    source set. Conventional source-root prefixes are removed while module
    directory prefixes are preserved.

    Generated tests remain in their original directory and are configured
    as the isolated project's test source set.

    Original project files are not modified.
    """
    project_root = Path(project.root).resolve()
    workspace_dir = Path(workspace_dir).resolve()
    generated_test_dir = Path(generated_test_dir).resolve()
    report_dir = Path(report_dir).resolve()

    workspace_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    source_root = workspace_dir / "src" / "main" / "kotlin"

    if source_root.exists():
        shutil.rmtree(source_root)

    source_root.mkdir(parents=True, exist_ok=True)

    excluded_directories = {
        ".git",
        ".gradle",
        ".idea",
        "build",
        "target",
        "out",
        "node_modules",
        ".venv",
        "venv",
    }

    test_source_sets = {
        "test",
        "tests",
        "testfixtures",
        "androidtest",
        "integrationtest",
        "unittest",
        "commontest",
    }

    excluded_paths = (generated_test_dir, workspace_dir)
    conventional_source_roots = {
        ("src", "main", "kotlin"),
        ("src", "main", "java"),
    }

    copied_count = 0
    skipped_count = 0

    for source_file in project_root.rglob("*.kt"):
        try:
            relative = source_file.relative_to(project_root)
        except ValueError:
            continue

        parts = tuple(part.lower() for part in relative.parts)

        # Exclude build artifacts, dependency directories, and IDE metadata.
        if any(part in excluded_directories for part in parts):
            skipped_count += 1
            continue

        # Exclude conventional test source roots.
        is_test_source = (
            parts[0] in test_source_sets
            or any(
                parent == "src" and child in test_source_sets
                for parent, child in zip(parts, parts[1:])
            )
        )

        if is_test_source:
            skipped_count += 1
            continue

        resolved_source = source_file.resolve()

        # Do not copy generated tests or files from the temporary workspace.
        excluded_file = False

        for excluded_path in excluded_paths:
            try:
                resolved_source.relative_to(excluded_path)
                excluded_file = True
                break
            except ValueError:
                pass

        if excluded_file:
            skipped_count += 1
            continue

        # Remove the conventional source-root portion of the path.
        # Preserve any module prefix, such as:
        # module-a/src/main/kotlin/com/example/File.kt
        # -> module-a/com/example/File.kt
        destination_parts = relative.parts

        for index in range(len(parts) - 2):
            candidate = parts[index:index + 3]

            if candidate in conventional_source_roots:
                destination_parts = (
                    relative.parts[:index]
                    + relative.parts[index + 3:]
                )
                break

        if not destination_parts:
            skipped_count += 1
            continue

        destination = source_root.joinpath(*destination_parts)
        destination.parent.mkdir(parents=True, exist_ok=True)

        shutil.copy2(resolved_source, destination)
        copied_count += 1

    if copied_count == 0:
        raise KotlinProjectError(
            f"No production Kotlin source files found in {project_root}."
        )

    main_dir = json.dumps(str(source_root))
    test_dir = json.dumps(str(generated_test_dir))
    report = json.dumps(str(report_dir))
    build_dir = json.dumps(str(workspace_dir / "build"))

    build_script = f"""
plugins {{
    id 'org.jetbrains.kotlin.jvm' version '{KOTLIN_PLUGIN_VERSION}'
}}

repositories {{
    mavenCentral()
}}

dependencies {{
    testImplementation 'org.junit.jupiter:junit-jupiter:{JUNIT_VERSION}'
    testRuntimeOnly 'org.junit.platform:junit-platform-launcher:{JUNIT_PLATFORM_VERSION}'
}}

kotlin {{
    sourceSets {{
        main {{
            kotlin.setSrcDirs([{main_dir}])
        }}
        test {{
            kotlin.setSrcDirs([{test_dir}])
        }}
    }}
}}

test {{
    useJUnitPlatform()
    include '**/*_*.class'
    reports {{
        junitXml {{
            outputLocation = file({report})
        }}
    }}
}}

layout.buildDirectory.set(file({build_dir}))
"""

    build_file = workspace_dir / "build.gradle"
    settings_file = workspace_dir / "settings.gradle"

    settings_file.write_text(
        "rootProject.name = 'checkpoint-plain-kotlin-tests'\n",
        encoding="utf-8",
    )

    build_file.write_text(
        build_script,
        encoding="utf-8",
    )

    progress(
        f"Plain Kotlin workspace prepared: "
        f"{copied_count} production files copied, "
        f"{skipped_count} files skipped."
    )

    return build_file



def _run_plain_gradle(
    project: KotlinProject,
    workspace_dir: str | Path,
    task: Literal["compileTestKotlin", "test"],
    timeout: int,
) -> subprocess.CompletedProcess:
    """Run a task in the isolated Gradle project for plain Kotlin."""
    gradle = shutil.which("gradle")

    if not gradle:
        raise KotlinProjectError(
            "Plain Kotlin execution requires Gradle because kotlinc is not installed."
        )

    workspace_dir = Path(workspace_dir).resolve()

    try:
        return subprocess.run(
            [
                gradle,
                "--no-daemon",
                "--console=plain",
                "-p",
                str(workspace_dir),
                task,
            ],
            cwd=project.root,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise KotlinProjectError(
            f"Plain Kotlin Gradle task '{task}' exceeded {timeout} seconds."
        ) from exc


def compile_plain_tests(project, workspace_dir, timeout=BUILD_TIMEOUT_SECONDS):
    """Compile generated tests in the isolated plain-Kotlin Gradle project."""
    return _run_plain_gradle(
        project, workspace_dir, "compileTestKotlin", timeout,
    )


def execute_plain_tests(project, workspace_dir, timeout=TEST_TIMEOUT_SECONDS):
    """Execute generated tests in the isolated plain-Kotlin Gradle project."""
    return _run_plain_gradle(
        project, workspace_dir, "test", timeout,
    )


def parse_junit_console_summary(output: str) -> dict | None:
    """Extract test counts from JUnit console output."""
    output = re.sub(r"\x1b\[[0-9;]*m", "", output)

    patterns = {
        "found": r"\[\s*(\d+)\s+tests? found\s*\]",
        "skipped": r"\[\s*(\d+)\s+tests? skipped\s*\]",
        "aborted": r"\[\s*(\d+)\s+tests? aborted\s*\]",
        "successful": r"\[\s*(\d+)\s+tests? successful\s*\]",
        "failed": r"\[\s*(\d+)\s+tests? failed\s*\]",
    }

    counts = {}

    for key, pattern in patterns.items():
        match = re.search(pattern, output)

        if match:
            counts[key] = int(match.group(1))
        elif key == "found":
            return None
        else:
            counts[key] = 0

    return counts


def parse_test_results(
    result: subprocess.CompletedProcess,
    report_dir: str | Path,
    test_prefix: Literal["UT", "IT"],
    test_run: TestRun,
    expected_names: set[str] | None = None,
) -> TestRun:
    """Parse JUnit XML reports for generated Kotlin tests."""
    report_dir = Path(report_dir).resolve()
    test_run.report_path = str(report_dir)

    if not report_dir.is_dir():
        test_run.project_error = f"JUnit report directory does not exist: {report_dir}"
        return test_run

    report_files = list(report_dir.rglob("TEST-*.xml"))

    if not report_files:
        output = "\n".join(
            part for part in (result.stdout, result.stderr) if part
        ).strip()

        console_results = parse_junit_console_summary(output)

        if console_results is not None:
            test_run.passed = console_results["successful"]
            test_run.failed = console_results["failed"]
            test_run.skipped = (
                console_results["skipped"] + console_results["aborted"]
            )

            if result.returncode != 0 and test_run.failed == 0:
                test_run.project_error = output[-4000:] or (
                    "JUnit execution returned a non-zero exit code."
                )

            return test_run

        test_run.project_error = (
            output[-4000:] if output else "Test execution produced no JUnit XML reports."
        )
        return test_run

    total = 0
    failures = 0
    skipped = 0
    failed_tests = []
    parse_errors = []

    class_pattern = re.compile(
        rf"(?:^|\.){re.escape(test_prefix)}_\d+(?:_b)*$"
    )

    def is_generated_class(classname: str) -> bool:
        simple_name = classname.rsplit(".", 1)[-1]

        if expected_names is not None:
            return simple_name in expected_names

        return bool(class_pattern.search(classname))

    for report_file in report_files:
        try:
            root = ET.parse(report_file).getroot()
        except ET.ParseError as exc:
            parse_errors.append(f"{report_file.name}: {exc}")
            continue

        for testcase in root.iter("testcase"):
            classname = testcase.get("classname", "")

            if not is_generated_class(classname):
                continue

            total += 1
            test_name = testcase.get("name", "unknown")
            display_name = f"{classname}::{test_name}"

            failure_elements = [
                element
                for element in testcase
                if element.tag in ("failure", "error")
            ]

            if failure_elements:
                failures += 1
                failed_tests.append(display_name)
            elif any(element.tag == "skipped" for element in testcase):
                skipped += 1

    test_run.passed = total - failures - skipped
    test_run.failed = failures
    test_run.skipped = skipped
    test_run.failed_tests = failed_tests

    if parse_errors:
        test_run.project_error = (
            "Some JUnit reports could not be parsed: "
            + "; ".join(parse_errors)
        )
    elif total == 0:
        output = "\n".join(
            part for part in (result.stdout, result.stderr) if part
        ).strip()
        test_run.project_error = output[-4000:] or (
            f"No {test_prefix} tests were found in the JUnit XML reports."
        )
    elif result.returncode != 0 and test_run.failed == 0:
        output = "\n".join(
            part for part in (result.stdout, result.stderr) if part
        ).strip()
        test_run.project_error = output[-4000:] or (
            "The test command returned a non-zero exit code."
        )

    return test_run



def parse_kotlin_build_output(
    result: subprocess.CompletedProcess,
    generated_test_dir: str | Path,
) -> tuple[dict[str, list[str]], list[str]]:
    """Associate Kotlin compiler errors with generated test files."""
    generated_test_dir = Path(generated_test_dir).resolve()

    generated_names = {
        path.stem for path in generated_test_dir.rglob("*.kt")
    }

    errors_by_file: dict[str, list[str]] = {}
    other_errors = []

    output = "\n".join(
        part for part in (result.stdout, result.stderr) if part
    )

    patterns = [
        # Kotlin compiler:
        # e: file:///path/UT_1.kt: (12, 8): Unresolved reference
        re.compile(
            r"^e:\s*(?:file://)?(.+?\.kt):\s*"
            r"\((\d+),\s*(\d+)\):\s*(.*)$"
        ),

        # Gradle/Kotlin:
        # /path/UT_1.kt:12:8: error: Unresolved reference
        re.compile(
            r"^(.+?\.kt):(\d+):(\d+):\s*error:\s*(.*)$"
        ),

        # Maven compiler:
        # [ERROR] /path/UT_1.kt:[12,8] Unresolved reference
        re.compile(
            r"^\s*\[ERROR\]\s+(.+?\.kt):\[(\d+),(\d+)\]\s*(.*)$"
        ),
    ]

    for line in output.splitlines():
        stripped = line.strip()

        match = next(
            (
                pattern.match(stripped)
                for pattern in patterns
                if pattern.match(stripped)
            ),
            None,
        )

        if match:
            file_path = match.group(1).strip().strip('"')
            line_number = match.group(2)
            message = match.group(4).strip()

            file_name = Path(
                file_path.replace("\\", "/")
            ).stem

            diagnostic = (
                f"line {line_number}: "
                f"{message or 'Kotlin compilation error'}"
            )

            if file_name in generated_names:
                errors = errors_by_file.setdefault(file_name, [])

                if diagnostic not in errors:
                    errors.append(diagnostic)

            else:
                project_error = f"{file_path}: {diagnostic}"

                if project_error not in other_errors:
                    other_errors.append(project_error)

    if result.returncode != 0 and not errors_by_file and not other_errors:
        tail = output.strip().splitlines()[-6:]

        other_errors.append(
            "\n".join(tail)
            if tail
            else "Kotlin compilation failed without a diagnostic."
        )

    return errors_by_file, other_errors


def get_source_package(
    source_file_paths: list[str],
    codebase_path: str | Path,
) -> str:
    """Determine the Kotlin package from the original source files."""
    root = Path(codebase_path).resolve()
    packages = set()

    for source_path in source_file_paths:
        path = Path(source_path)

        if not path.is_absolute():
            path = root / path

        path = path.resolve()

        if not path.is_file() or path.suffix.lower() != ".kt":
            continue

        source = path.read_text(encoding="utf-8", errors="replace")

        match = re.search(
            r"(?m)^\s*package\s+"
            r"([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*$",
            source,
        )

        packages.add(match.group(1) if match else "")

    if len(packages) > 1:
        raise ValueError(
            "The source files for this business rule belong to different "
            "Kotlin packages. A single test package cannot be selected automatically."
        )

    return next(iter(packages), "")


def make_cases(prefix, items, body_of, imports_of, codebase_path):
    """Clean generated Kotlin tests and retain their original objects."""
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

        try:
            case.package_name = get_source_package(
                item.source_file_paths,
                codebase_path,
            )
        except (ValueError, OSError) as exc:
            case.reason = str(exc)
            rejected.append(case)
            continue

        if clean_test(case):
            cases.append(case)
        else:
            rejected.append(case)

    return cases, rejected


def write_results(run, output_dir, to_json):
    """Write validated, discarded, and summary Kotlin test results."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    validated = [to_json(case) for case in run.kept]
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


def make_fixer(llm, context_for=None):
    """Build the async structured-output function that repairs Kotlin tests."""
    structured_llm = llm.with_structured_output(TestRepair)

    async def fix(case):
        context = context_for(case) if context_for else ""

        if isinstance(context, (list, tuple)):
            context = "\n\n".join(str(item) for item in context)

        numbered = "\n".join(
            f"{number:4d}  {line}"
            for number, line in enumerate(
                render_test(case, case.name).splitlines(),
                1,
            )
        )

        system_message = (
            "You are a senior Kotlin engineer fixing a generated Kotlin "
            "JUnit 5 test that does not compile. Fix only what the compiler "
            "errors identify."
        )

        prompt = f"""
#### TEST FILE ({case.name}.kt, with line numbers)
{numbered}

#### KOTLIN PACKAGE
{case.package_name or "No package declaration"}

#### COMPILER ERRORS FOR THIS FILE
{chr(10).join(case.errors)}

#### CODE FROM THE CODEBASE UNDER TEST AND LSP CONTEXT IF PROVIDED
{context or "NO CONTEXT PROVIDED"}

#### TASK
Return a corrected Kotlin test function that compiles.

#### RULES
- test_method must contain only the Kotlin test function, including @Test.
- Do not return a class declaration, package declaration, or imports.
- Use Kotlin syntax: fun testName() {{ ... }}.
- Use JUnit 5 and org.junit.jupiter.api.Test.
- Return Kotlin imports in the imports array, without semicolons.
- Use Kotlin JUnit assertions, not Java static-import syntax.
- Use only real project types, constructors, functions, and properties.
- Preserve the original test's intent and assertions.
- Do not weaken assertions merely to make the test compile.
- Do not add dependencies or mocking frameworks.
- If the test cannot be repaired from the available evidence, return
  an empty test_method.
- Treat LSP context as supplementary evidence; verify accessibility
  and signatures against source code where possible.
"""

        try:
            output = await structured_llm.ainvoke([
                ("system", system_message),
                ("user", prompt),
            ])
        except (DailyLimitError, RateLimitTimeoutError):
            raise
        except Exception as error:
            progress(f"AI repair call failed for {case.name}: {error}")
            return None

        if output is None or not (output.test_method or "").strip():
            return None

        return output.imports or [], output.test_method

    return fix


def _repair(fix, cases, loop):
    """Run repair calls on the event loop already used by the agent."""
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
    """Remove a failed generated Kotlin test and record its reason."""
    if case in live:
        live.remove(case)

    run.dropped.append(case)

    for path in Path(generated_test_dir).rglob(f"{case.name}.kt"):
        path.unlink(missing_ok=True)


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
    """Write, compile, repair, and execute generated Kotlin tests."""
    output_dir = Path(output_dir)

    run = TestRun(
        kind=kind,
        generated=len(cases) + len(rejected),
        dropped=list(rejected),
        unusable=len(rejected),
    )

    generated_test_dir = output_dir / f"{prefix.lower()}_generated_tests"
    workspace_dir = output_dir / f"{prefix.lower()}_kotlin_workspace"
    report_dir = workspace_dir / "reports"

    generated_test_dir.mkdir(parents=True, exist_ok=True)
    workspace_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    if not cases:
        return run

    project = None
    temporary_pom = None
    init_script = None
    live: list[TestCase] = []

    try:
        project = detect_kotlin_project(codebase_path)

        write_tests(cases, generated_test_dir, prefix)
        mark_generated(generated_test_dir)
        live = list(cases)

        if project.build_system == "maven":
            temporary_pom = prepare_maven_project(
                project,
                generated_test_dir,
                workspace_dir,
                report_dir,
                prefix,
            )

        elif project.build_system == "gradle":
            init_script = prepare_gradle_project(
                project,
                generated_test_dir,
                workspace_dir,
                report_dir,
                prefix,
            )

        else:
            prepare_plain_project(
                project,
                generated_test_dir,
                workspace_dir,
                report_dir,
            )

        def compile_tests():
            if project.build_system == "maven":
                return compile_maven_tests(
                    project, temporary_pom, generated_test_dir, prefix,
                )

            if project.build_system == "gradle":
                return compile_gradle_tests(
                    project, workspace_dir, generated_test_dir, prefix,
                )

            return compile_plain_tests(project, workspace_dir)

        def execute_tests():
            if project.build_system == "maven":
                return execute_maven_tests(
                    project, temporary_pom, generated_test_dir, prefix,
                )

            if project.build_system == "gradle":
                return execute_gradle_tests(
                    project, workspace_dir, generated_test_dir, prefix,
                )

            return execute_plain_tests(project, workspace_dir)

        while live:
            compile_result = compile_tests()

            errors_by_test, project_errors = parse_kotlin_build_output(
                compile_result,
                generated_test_dir,
            )

            if project_errors:
                run.project_error = "\n".join(project_errors)
                run.kept = list(live)
                return run

            if not errors_by_test:
                if compile_result.returncode != 0:
                    run.project_error = (
                        compile_result.stderr
                        or compile_result.stdout
                        or "Kotlin compilation failed with an unrecognized error."
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
                    case.reason = "No Kotlin test repair function was provided."
                    _drop(run, live, generated_test_dir, case)
                continue

            repairs = _repair(fix, repair_candidates, loop)

            for case, repaired in zip(repair_candidates, repairs):
                if case not in live:
                    continue

                if not repaired:
                    case.reason = "The Kotlin test could not be repaired."
                    _drop(run, live, generated_test_dir, case)
                    continue

                imports, body = repaired

                candidate = TestCase(
                    name=case.name,
                    imports=imports,
                    body=body,
                    source=case.source,
                    package_name=case.package_name,
                )

                if not clean_test(candidate):
                    case.reason = "The repaired Kotlin test failed validation."
                    _drop(run, live, generated_test_dir, case)
                    continue

                case.imports = candidate.imports
                case.body = candidate.body
                case.repairs += 1
                case.errors = []

                write_test_case(case, generated_test_dir)

        if live:
            for report in report_dir.rglob("*"):
                if report.is_file():
                    report.unlink()

            execution_result = execute_tests()
            run.kept = list(live)

            parse_test_results(
                execution_result,
                report_dir,
                prefix,
                run,
                expected_names={case.name for case in live},
            )
        else:
            run.kept = []

    except (DailyLimitError, RateLimitTimeoutError):
        raise

    except KotlinProjectError as error:
        run.project_error = str(error)
        run.kept = list(live)

    except Exception as error:
        run.project_error = f"Unexpected Kotlin test execution error: {error}"
        run.kept = list(live)

    finally:
        if temporary_pom is not None:
            cleanup_maven_project(temporary_pom)

        if init_script is not None:
            cleanup_gradle_project(init_script)

    return run
