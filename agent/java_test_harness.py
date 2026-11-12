
import re
import shutil

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Any
import subprocess
import xml.etree.ElementTree as ET
import tempfile
import os

import asyncio
import json

from datetime import datetime, timezone
from agent.crawl_config import mark_generated
from agent.llm import DailyLimitError, RateLimitTimeoutError
from agent.structured_output.UTV_output import TestRepair
from backend.progress_logging import progress


JavaBuildSystem = Literal["maven", "gradle", "plain"]

MAX_TEST_CHARS = 20_000
MAX_REPAIR_ROUNDS = 2
MAX_CONCURRENCY = 10
BUILD_TIMEOUT_SECONDS = 600
TEST_TIMEOUT_SECONDS = 900


class JavaProjectError(Exception):
    """Raised when a Java project cannot be identified or prepared."""


@dataclass
class JavaProject:
    root: Path
    build_system: JavaBuildSystem
    build_command: str | None
    build_file: Path | None


@dataclass
class TestCase:
    """One generated Java test being processed by check_tests()."""

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
    """What the Java test harness found."""

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
            "harness": "junit5",
        }

    def message(self) -> str:
        if self.project_error:
            return (
                f"{self.kind} tests were written but the test project "
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
    

def find_java_project_root(codebase_path: str | Path) -> Path:
    """Find the root directory of the Java codebase."""
    path = Path(codebase_path).resolve()

    if not path.exists():
        raise JavaProjectError(f"Codebase path does not exist: {path}")

    if path.is_file():
        path = path.parent

    # Prefer the nearest directory containing a recognized build file.
    for directory in (path, *path.parents):
        if (
            (directory / "pom.xml").is_file()
            or (directory / "build.gradle").is_file()
            or (directory / "build.gradle.kts").is_file()
        ):
            return directory

    # No build file was found; use the supplied codebase directory.
    return path


def detect_java_project(codebase_path: str | Path) -> JavaProject:
    """Detect whether a Java codebase uses Maven, Gradle, or plain Java."""
    root = find_java_project_root(codebase_path)

    maven_file = root / "pom.xml"
    gradle_files = [
        root / "build.gradle",
        root / "build.gradle.kts",
    ]

    has_maven = maven_file.is_file()
    gradle_file = next(
        (file for file in gradle_files if file.is_file()),
        None,
    )
    has_gradle = gradle_file is not None

    if has_maven and has_gradle:
        raise JavaProjectError(
            f"Both Maven and Gradle build files were found in {root}. "
            "Please select the intended build system."
        )

    if has_maven:
        wrapper = root / "mvnw"

        if wrapper.is_file():
            command = str(wrapper)
        else:
            command = shutil.which("mvn")

        if not command:
            raise JavaProjectError(
                "Maven project detected, but neither mvnw nor Maven is available."
            )

        return JavaProject(
            root=root,
            build_system="maven",
            build_command=command,
            build_file=maven_file,
        )

    if has_gradle and gradle_file is not None:
        wrapper = root / "gradlew"

        if wrapper.is_file():
            command = str(wrapper)
        else:
            command = shutil.which("gradle")

        if not command:
            raise JavaProjectError(
                "Gradle project detected, but neither gradlew nor Gradle is available."
            )

        return JavaProject(
            root=root,
            build_system="gradle",
            build_command=command,
            build_file=gradle_file,
        )

    if not list(root.rglob("*.java")):
        raise JavaProjectError(
            f"No Maven, Gradle, or Java source files found in {root}."
        )

    return JavaProject(
        root=root,
        build_system="plain",
        build_command=None,
        build_file=None,
    )


def normalize_imports(
    imports: str | list[str] | None,
) -> list[str]:
    """Normalize and deduplicate Java import statements."""
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

        statement = f"import {item};"

        if statement not in seen:
            normalized.append(statement)
            seen.add(statement)

    return normalized


def clean_test(test: TestCase) -> bool:
    """Validate and clean one generated JUnit 5 test."""
    source = (test.body or "").strip()

    if not source:
        test.reason = "Generated test is empty."
        return False

    source = re.sub(
        r"^```(?:java)?\s*\n?|\n?```\s*$",
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

    if not re.search(r"@Test\b", source):
        test.reason = "Generated test does not contain a JUnit 5 @Test annotation."
        return False

    if not re.search(
        r"\bvoid\s+\w+\s*\([^)]*\)\s*\{",
        source,
    ):
        test.reason = "Generated test does not contain a recognizable test method."
        return False

    assertion_pattern = (
        r"\bassert(?:Equals|NotEquals|True|False|Null|NotNull|"
        r"Same|NotSame|ArrayEquals|IterableEquals|Throws|"
        r"DoesNotThrow|All|InstanceOf|Timeout)\s*\("
        r"|\bfail\s*\("
    )

    if not re.search(assertion_pattern, source):
        test.reason = "Generated test does not contain a recognizable assertion."
        return False

    test.body = source
    test.imports = normalize_imports(test.imports)
    test.reason = ""

    return True



def get_test_package(test: TestCase) -> str:
    """Determine and validate the Java package for a generated test."""
    source = test.body or ""

    package_matches = re.findall(
        r"^\s*package\s+"
        r"([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*;",
        source,
        flags=re.MULTILINE,
    )

    if len(set(package_matches)) > 1:
        raise ValueError(
            f"Test {test.name} contains conflicting package declarations."
        )

    declared_package = package_matches[0] if package_matches else ""
    package_name = test.package_name.strip() or declared_package

    if (
        test.package_name.strip()
        and declared_package
        and test.package_name.strip() != declared_package
    ):
        raise ValueError(
            f"Test {test.name} has conflicting package metadata "
            f"and source declarations."
        )

    if package_name and not re.fullmatch(
        r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*",
        package_name,
    ):
        raise ValueError(
            f"Invalid Java package name: {package_name}"
        )

    return package_name




def render_test(test: TestCase, class_name: str) -> str:
    """Render a generated test method as a complete JUnit 5 class."""
    
    source = test.body or ""

    if not source:
        raise ValueError(f"Test {test.name} has no source.")

    # Determine the package before removing declarations from the body.
    package_name = get_test_package(test)

    imports = normalize_imports(test.imports)

    # Extract package declarations from the generated source.
    source = re.sub(
        r"(?m)^[ \t]*package[ \t]+"
        r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*"
        r"[ \t]*;[ \t]*(?:\r?\n)?",
        "",
        source,
    )

    # Extract imports accidentally included in the generated source.
    source_imports = re.findall(
        r"(?m)^[ \t]*import[ \t]+(?:static[ \t]+)?"
        r"[\w.*]+[ \t]*;",
        source,
    )

    for statement in source_imports:
        imports.extend(normalize_imports(statement))

    # Remove import declarations from the test body.
    source = re.sub(
        r"(?m)^[ \t]*import[ \t]+(?:static[ \t]+)?"
        r"[\w.*]+[ \t]*;[ \t]*(?:\r?\n)?",
        "",
        source,
    ).strip()

    # Ensure the generated class has the JUnit 5 imports it needs.
    if "import org.junit.jupiter.api.Test;" not in imports:
        imports.append("import org.junit.jupiter.api.Test;")

    has_assertion_import = any(
        item.startswith(
            "import static org.junit.jupiter.api.Assertions."
        )
        for item in imports
    )

    if not has_assertion_import:
        imports.append(
            "import static org.junit.jupiter.api.Assertions.*;"
        )

    imports = normalize_imports(imports)

    import_block = "\n".join(imports)

    indented_source = "\n".join(
        f"    {line}" if line.strip() else ""
        for line in source.splitlines()
    )

    package_block = (
        f"package {package_name};\n\n"
        if package_name
        else ""
    )

    return (
        f"{package_block}"
        f"{import_block}\n\n"
        f"class {class_name} {{\n"
        f"{indented_source}\n"
        f"}}\n"
    )


def write_tests(
    cases: list[TestCase],
    output_dir: str | Path,
    prefix: Literal["UT", "IT"],
) -> list[Path]:
    """Write one generated Java test per file in its matching package."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Remove only previous generated tests belonging to this agent.
    for old_file in output_dir.rglob(f"{prefix}_*.java"):
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

        file_path = package_dir / f"{test.name}.java"
        file_path.write_text(
            render_test(test, test.name),
            encoding="utf-8",
        )

        written_files.append(file_path)

    return written_files



def write_test_case(
    test: TestCase,
    output_dir: str | Path,
) -> Path:
    """Write or replace one generated Java test without touching other tests."""
    output_dir = Path(output_dir)
    
    package_name = get_test_package(test)

    package_dir = (
        output_dir.joinpath(*package_name.split("."))
        if package_name
        else output_dir
    )

    package_dir.mkdir(parents=True, exist_ok=True)

    file_path = package_dir / f"{test.name}.java"
    file_path.write_text(
        render_test(test, test.name),
        encoding="utf-8",
    )

    return file_path


def prepare_maven_project(
    project: JavaProject,
    generated_test_dir: Path,
    workspace_dir: Path,
    report_dir: Path,
    test_prefix: str,
) -> Path:
    """
    Creates a temporary Maven POM that compiles and executes only the
    generated tests while preserving the original project's configuration.

    The temporary POM is placed in the project root so Maven retains the
    original project directory as its basedir. Build output and test reports
    are redirected to the Checkpoint workspace.
    """

    generated_test_dir = Path(generated_test_dir).resolve()
    workspace_dir = Path(workspace_dir).resolve()
    report_dir = Path(report_dir).resolve()

    if project.build_file is None:
        raise JavaProjectError("Maven project does not have a pom.xml.")

    pom_path = project.build_file

    try:
        tree = ET.parse(pom_path)
        root = tree.getroot()
    except (ET.ParseError, OSError) as error:
        raise JavaProjectError(
            f"Could not read Maven project file: {error}"
        ) from error

    namespace = ""
    if root.tag.startswith("{"):
        namespace = root.tag.split("}", 1)[0] + "}"

    def tag(name: str) -> str:
        return f"{namespace}{name}"

    def find_or_create(parent: ET.Element, name: str) -> ET.Element:
        element = parent.find(tag(name))
        if element is None:
            element = ET.SubElement(parent, tag(name))
        return element

    # Redirect Maven source and build directories to the Checkpoint workspace.
    build = find_or_create(root, "build")


    source_directory = find_or_create(build, "sourceDirectory")

    if not (source_directory.text or "").strip():
        source_directory.text = str(
            project.root / "src" / "main" / "java"
        )

    find_or_create(build, "testSourceDirectory").text = str(
        generated_test_dir
    )
    find_or_create(build, "directory").text = str(
        workspace_dir / "build"
    )

    # Ensure JUnit 5 is available.
    dependencies = find_or_create(root, "dependencies")

    junit_dependency = None
    for dependency in dependencies.findall(tag("dependency")):
        artifact_id = dependency.find(tag("artifactId"))
        group_id = dependency.find(tag("groupId"))

        if (
            artifact_id is not None
            and (artifact_id.text or "").strip() == "junit-jupiter"
            and group_id is not None
            and (group_id.text or "").strip() == "org.junit.jupiter"
        ):
            junit_dependency = dependency
            break

    if junit_dependency is None:
        junit_dependency = ET.SubElement(
            dependencies, tag("dependency")
        )
        ET.SubElement(
            junit_dependency, tag("groupId")
        ).text = "org.junit.jupiter"
        ET.SubElement(
            junit_dependency, tag("artifactId")
        ).text = "junit-jupiter"
        ET.SubElement(
            junit_dependency, tag("version")
        ).text = "5.11.4"
        ET.SubElement(
            junit_dependency, tag("scope")
        ).text = "test"

    # Find or create the Surefire plugin.
    plugins = find_or_create(build, "plugins")
    surefire = None

    for plugin in plugins.findall(tag("plugin")):
        artifact_id = plugin.find(tag("artifactId"))
        if (
            artifact_id is not None
            and (artifact_id.text or "").strip()
            == "maven-surefire-plugin"
        ):
            surefire = plugin
            break

    if surefire is None:
        surefire = ET.SubElement(plugins, tag("plugin"))
        ET.SubElement(
            surefire, tag("groupId")
        ).text = "org.apache.maven.plugins"
        ET.SubElement(
            surefire, tag("artifactId")
        ).text = "maven-surefire-plugin"

    # Preserve an existing Surefire version.
    version = surefire.find(tag("version"))
    if version is None or not (version.text or "").strip():
        find_or_create(surefire, "version").text = "3.2.5"

    # Always configure the generated-test include pattern and report path.
    configuration = find_or_create(surefire, "configuration")

    includes = find_or_create(configuration, "includes")

    for include in list(includes):
        if include.tag == tag("include"):
            includes.remove(include)

    ET.SubElement(
        includes, tag("include")
    ).text = f"**/{test_prefix}_*.java"

    find_or_create(
        configuration, "reportsDirectory"
    ).text = str(report_dir)

    # Preserve the original POM and write a temporary Checkpoint-specific POM.
    temporary_pom = project.root / (
        f".checkpoint-{test_prefix.lower()}-pom.xml"
    )


    ET.register_namespace(
        "",
        "http://maven.apache.org/POM/4.0.0",
    )
    

    tree.write(
        temporary_pom,
        encoding="utf-8",
        xml_declaration=True,
    )

    return temporary_pom

def cleanup_maven_project(pom_path: str | Path) -> None:
    """Remove a temporary Checkpoint Maven POM."""
    pom_path = Path(pom_path)

    if (
        pom_path.name.startswith(".checkpoint-")
        and pom_path.name.endswith("-pom.xml")
    ):
        pom_path.unlink(missing_ok=True)


def _run_maven_goal(
    project: JavaProject,
    pom_path: str | Path,
    generated_test_dir: str | Path,
    test_prefix: Literal["UT", "IT"],
    goal: Literal["test-compile", "test"],
    timeout: int,
) -> subprocess.CompletedProcess:
    """Run a Maven lifecycle goal for Checkpoint-generated tests."""
    if project.build_system != "maven":
        raise JavaProjectError(
            "Maven execution requires a Maven Java project."
        )

    pom_path = Path(pom_path).resolve()
    generated_test_dir = Path(generated_test_dir).resolve()

    if not pom_path.is_file():
        raise JavaProjectError(
            f"Temporary Maven POM does not exist: {pom_path}"
        )

    if not generated_test_dir.is_dir():
        raise JavaProjectError(
            f"Generated test directory does not exist: "
            f"{generated_test_dir}"
        )

    generated_tests = list(
        generated_test_dir.rglob(f"{test_prefix}_*.java")
    )

    if not generated_tests:
        raise JavaProjectError(
            f"No {test_prefix} generated Java tests found in "
            f"{generated_test_dir}."
        )

    if not project.build_command:
        raise JavaProjectError(
            "No Maven executable is configured for this project."
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
            cwd=pom_path.parent,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise JavaProjectError(
            f"Maven {goal} exceeded the {timeout}-second timeout."
        ) from exc
    except FileNotFoundError as exc:
        raise JavaProjectError(
            "The configured Maven executable could not be found."
        ) from exc


def compile_maven_tests(
    project: JavaProject,
    pom_path: str | Path,
    generated_test_dir: str | Path,
    test_prefix: Literal["UT", "IT"],
    timeout: int = BUILD_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess:
    """Compile generated Java tests using Maven without executing them."""
    return _run_maven_goal(
        project=project,
        pom_path=pom_path,
        generated_test_dir=generated_test_dir,
        test_prefix=test_prefix,
        goal="test-compile",
        timeout=timeout,
    )


def execute_maven_tests(
    project: JavaProject,
    pom_path: str | Path,
    generated_test_dir: str | Path,
    test_prefix: Literal["UT", "IT"],
    timeout: int = TEST_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess:
    """Execute generated Java tests using Maven after compilation."""
    return _run_maven_goal(
        project=project,
        pom_path=pom_path,
        generated_test_dir=generated_test_dir,
        test_prefix=test_prefix,
        goal="test",
        timeout=timeout,
    )



def prepare_gradle_project(
    project: JavaProject,
    generated_test_dir: str | Path,
    workspace_dir: str | Path,
    report_dir: str | Path,
    test_prefix: Literal["UT", "IT"],
) -> Path:
    """Create a Gradle init script without replacing the original project configuration."""
    if project.build_system != "gradle":
        raise JavaProjectError(
            "Gradle preparation requires a Gradle Java project."
        )

    if project.build_file is None or not project.build_file.is_file():
        raise JavaProjectError("Gradle build file does not exist.")

    if not project.build_command:
        raise JavaProjectError(
            "No Gradle executable is configured for this project."
        )

    generated_test_dir = Path(generated_test_dir).resolve()
    workspace_dir = Path(workspace_dir).resolve()
    report_dir = Path(report_dir).resolve()

    if not generated_test_dir.is_dir():
        raise JavaProjectError(
            f"Generated test directory does not exist: {generated_test_dir}"
        )

    workspace_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    build_directory = workspace_dir / "build"

    # JSON string escaping is compatible with Groovy double-quoted strings.
    generated_path = json.dumps(str(generated_test_dir))
    report_path = json.dumps(str(report_dir))
    build_path = json.dumps(str(build_directory))

    test_include = json.dumps(f"**/{test_prefix}_*.class")

    init_script = f"""
gradle.projectsEvaluated {{
    def checkpointProject = gradle.rootProject

    def sourceSets = checkpointProject.extensions.findByName("sourceSets")

    if (sourceSets == null) {{
        throw new GradleException(
            "Checkpoint requires the root Gradle project to apply the Java plugin."
        )
    }}

    def testSourceSet = sourceSets.findByName("test")

    if (testSourceSet == null) {{
        throw new GradleException(
            "Checkpoint could not find the Java test source set."
        )
    }}

    // Keep the original main source configuration unchanged.
    testSourceSet.java.setSrcDirs([
        new File({generated_path})
    ])


    checkpointProject.repositories {{
        mavenCentral()
    }}

    checkpointProject.dependencies.add(
        "testImplementation",
        "org.junit.jupiter:junit-jupiter:5.11.4"
    )

    checkpointProject.dependencies.add(
        "testRuntimeOnly",
        "org.junit.platform:junit-platform-launcher:1.11.4"
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
    init_script_path = Path(init_script_path)

    if init_script_path.name == "checkpoint.init.gradle":
        init_script_path.unlink(missing_ok=True)



def _run_gradle_task(
    project: JavaProject,
    workspace_dir: str | Path,
    generated_test_dir: str | Path,
    test_prefix: Literal["UT", "IT"],
    task: Literal["compileTestJava", "test"],
    timeout: int,
) -> subprocess.CompletedProcess:
    """Run a Gradle task in the original project with Checkpoint configuration."""
    if project.build_system != "gradle":
        raise JavaProjectError(
            "Gradle execution requires a Gradle Java project."
        )

    if not project.build_command:
        raise JavaProjectError(
            "No Gradle executable is configured for this project."
        )

    if project.build_file is None:
        raise JavaProjectError(
            "Gradle build file is not configured."
        )

    workspace_dir = Path(workspace_dir).resolve()
    generated_test_dir = Path(generated_test_dir).resolve()

    init_script = workspace_dir / "checkpoint.init.gradle"

    if not init_script.is_file():
        raise JavaProjectError(
            f"Checkpoint Gradle init script does not exist: {init_script}"
        )

    if not generated_test_dir.is_dir():
        raise JavaProjectError(
            f"Generated test directory does not exist: {generated_test_dir}"
        )

    generated_tests = list(
        generated_test_dir.rglob(f"{test_prefix}_*.java")
    )

    if not generated_tests:
        raise JavaProjectError(
            f"No {test_prefix} generated Java tests found in "
            f"{generated_test_dir}."
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
        raise JavaProjectError(
            f"Gradle task '{task}' exceeded the {timeout}-second timeout."
        ) from exc
    except FileNotFoundError as exc:
        raise JavaProjectError(
            "The configured Gradle executable could not be found."
        ) from exc


def compile_gradle_tests(
    project: JavaProject,
    workspace_dir: str | Path,
    generated_test_dir: str | Path,
    test_prefix: Literal["UT", "IT"],
    timeout: int = BUILD_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess:
    """Compile generated Java tests using Gradle without executing them."""
    return _run_gradle_task(
        project=project,
        workspace_dir=workspace_dir,
        generated_test_dir=generated_test_dir,
        test_prefix=test_prefix,
        task="compileTestJava",
        timeout=timeout,
    )


def execute_gradle_tests(
    project: JavaProject,
    workspace_dir: str | Path,
    generated_test_dir: str | Path,
    test_prefix: Literal["UT", "IT"],
    timeout: int = TEST_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess:
    """Execute generated Java tests using Gradle after compilation."""
    return _run_gradle_task(
        project=project,
        workspace_dir=workspace_dir,
        generated_test_dir=generated_test_dir,
        test_prefix=test_prefix,
        task="test",
        timeout=timeout,
    )


JUNIT_CONSOLE_VERSION = "1.11.4"

JUNIT_CONSOLE_URL = (
    "https://repo.maven.apache.org/maven2/org/junit/platform/"
    "junit-platform-console-standalone/"
    f"{JUNIT_CONSOLE_VERSION}/"
    f"junit-platform-console-standalone-{JUNIT_CONSOLE_VERSION}.jar"
)


def get_junit_console() -> Path:
    """Download or reuse the standalone JUnit 5 console launcher."""
    cache_dir = (
        Path.home()
        / ".cache"
        / "checkpoint"
        / "java"
    )

    cache_dir.mkdir(parents=True, exist_ok=True)

    jar_path = cache_dir / (
        f"junit-platform-console-standalone-"
        f"{JUNIT_CONSOLE_VERSION}.jar"
    )

    if jar_path.is_file() and jar_path.stat().st_size > 0:
        return jar_path

    curl = shutil.which("curl")

    if not curl:
        raise JavaProjectError(
            "curl is required to download the JUnit console launcher."
        )

    temporary_path = jar_path.with_suffix(".download")

    command = [
        curl,
        "--fail",
        "--location",
        "--silent",
        "--show-error",
        "--output",
        str(temporary_path),
        JUNIT_CONSOLE_URL,
    ]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )

        if result.returncode != 0:
            raise JavaProjectError(
                "Could not download the JUnit console launcher: "
                f"{result.stderr.strip()}"
            )

        if not temporary_path.is_file() or temporary_path.stat().st_size == 0:
            raise JavaProjectError(
                "Downloaded JUnit console launcher is empty."
            )

        temporary_path.replace(jar_path)

    except subprocess.TimeoutExpired as exc:
        raise JavaProjectError(
            "Downloading the JUnit console launcher timed out."
        ) from exc
    finally:
        temporary_path.unlink(missing_ok=True)

    return jar_path



def compile_plain_tests(
    project: JavaProject,
    generated_test_dir: str | Path,
    workspace_dir: str | Path,
    test_prefix: Literal["UT", "IT"],
    timeout: int = BUILD_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess:
    """Compile original Java sources and generated JUnit 5 tests."""
    if project.build_system != "plain":
        raise JavaProjectError(
            "Plain Java compilation requires a plain Java project."
        )

    generated_test_dir = Path(generated_test_dir).resolve()
    workspace_dir = Path(workspace_dir).resolve()

    if not generated_test_dir.is_dir():
        raise JavaProjectError(
            f"Generated test directory does not exist: "
            f"{generated_test_dir}"
        )

    generated_tests = list(
        generated_test_dir.rglob(f"{test_prefix}_*.java")
    )

    if not generated_tests:
        raise JavaProjectError(
            f"No {test_prefix} generated Java tests found in "
            f"{generated_test_dir}."
        )

    javac = shutil.which("javac")

    if not javac:
        raise JavaProjectError(
            "javac must be available to compile plain Java tests."
        )

    junit_jar = get_junit_console()

    classes_dir = workspace_dir / "classes"

    # Remove previously compiled classes so deleted or repaired tests
    # cannot remain in the next execution.
    if classes_dir.exists():
        shutil.rmtree(classes_dir)

    classes_dir.mkdir(parents=True, exist_ok=True)

    excluded_directories = {
        ".git",
        ".gradle",
        "build",
        "target",
        "out",
        "node_modules",
    }

    source_files = []

    for path in project.root.rglob("*.java"):
        relative_path = path.relative_to(project.root)

        if any(
            part in excluded_directories
            for part in relative_path.parts
        ):
            continue

        # Generated tests are added explicitly below.
        try:
            path.resolve().relative_to(generated_test_dir)
            continue
        except ValueError:
            pass

        source_files.append(path)

    if not source_files:
        raise JavaProjectError(
            f"No original Java source files found in {project.root}."
        )

    compile_command = [
        javac,
        "-encoding",
        "UTF-8",
        "-cp",
        str(junit_jar),
        "-d",
        str(classes_dir),
        *[str(path) for path in source_files],
        *[str(path) for path in generated_tests],
    ]

    try:
        return subprocess.run(
            compile_command,
            cwd=project.root,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise JavaProjectError(
            f"Plain Java compilation exceeded {timeout} seconds."
        ) from exc
    except FileNotFoundError as exc:
        raise JavaProjectError(
            "The Java compiler executable could not be found."
        ) from exc


def execute_plain_tests(
    project: JavaProject,
    generated_test_dir: str | Path,
    workspace_dir: str | Path,
    test_prefix: Literal["UT", "IT"],
    timeout: int = TEST_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess:
    """Execute previously compiled generated tests using JUnit 5."""
    if project.build_system != "plain":
        raise JavaProjectError(
            "Plain Java execution requires a plain Java project."
        )

    generated_test_dir = Path(generated_test_dir).resolve()
    workspace_dir = Path(workspace_dir).resolve()

    if not generated_test_dir.is_dir():
        raise JavaProjectError(
            f"Generated test directory does not exist: "
            f"{generated_test_dir}"
        )

    generated_tests = list(
        generated_test_dir.rglob(f"{test_prefix}_*.java")
    )

    if not generated_tests:
        raise JavaProjectError(
            f"No {test_prefix} generated Java tests found in "
            f"{generated_test_dir}."
        )

    java = shutil.which("java")

    if not java:
        raise JavaProjectError(
            "java must be available to execute plain Java tests."
        )

    junit_jar = get_junit_console()

    classes_dir = workspace_dir / "classes"
    reports_dir = workspace_dir / "reports"

    if not classes_dir.is_dir():
        raise JavaProjectError(
            f"Compiled Java classes directory does not exist: "
            f"{classes_dir}. Compile the tests before executing them."
        )

    reports_dir.mkdir(parents=True, exist_ok=True)

    execution_command = [
        java,
        "-jar",
        str(junit_jar),
        "execute",
        "--class-path",
        str(classes_dir),
        "--scan-class-path",
        f"--include-classname=.*(?:^|\\.){test_prefix}_.*",
        "--reports-dir",
        str(reports_dir),
        "--details=tree",
    ]

    try:
        return subprocess.run(
            execution_command,
            cwd=project.root,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise JavaProjectError(
            f"Plain Java test execution exceeded {timeout} seconds."
        ) from exc
    except FileNotFoundError as exc:
        raise JavaProjectError(
            "The Java runtime executable could not be found."
        ) from exc



def parse_junit_console_summary(output: str) -> dict | None:
    """Extract test counts from JUnit Console Launcher output."""
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
    """Parse JUnit XML reports into the shared TestRun structure."""
    report_dir = Path(report_dir).resolve()
    test_run.report_path = str(report_dir)

    if not report_dir.is_dir():
        test_run.project_error = (
            f"JUnit report directory does not exist: {report_dir}"
        )
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
                console_results["skipped"]
                + console_results["aborted"]
            )

            if (
                result.returncode != 0
                and test_run.failed == 0
            ):
                test_run.project_error = (
                    output[-4000:]
                    if output
                    else "JUnit execution returned a non-zero exit code."
                )

            return test_run

        test_run.project_error = (
            output[-4000:]
            if output
            else "Test execution produced no JUnit XML reports."
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
            parse_errors.append(
                f"{report_file.name}: {exc}"
            )
            continue

        # Read testcase elements rather than suite totals to avoid
        # counting JUnit containers as individual tests.
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

            elif any(
                element.tag == "skipped"
                for element in testcase
            ):
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

        test_run.project_error = (
            output[-4000:]
            if output
            else (
                f"No {test_prefix} tests were found in the "
                "JUnit XML reports."
            )
        )

    elif result.returncode != 0 and test_run.failed == 0:
        output = "\n".join(
            part for part in (result.stdout, result.stderr) if part
        ).strip()

        test_run.project_error = (
            output[-4000:]
            if output
            else "The test command returned a non-zero exit code."
        )

    return test_run


def parse_java_build_output(
    result: subprocess.CompletedProcess,
    generated_test_dir: str | Path,
) -> tuple[dict[str, list[str]], list[str]]:
    """
    Associate Java compiler errors with generated test files.

    Supports Maven compiler output and Gradle/javac-style diagnostics.
    Errors belonging to the original project or build configuration are
    returned separately so they do not cause generated tests to be dropped.
    """
    generated_test_dir = Path(generated_test_dir).resolve()

    generated_names = {
        path.stem
        for path in generated_test_dir.rglob("*.java")
    }

    errors_by_file: dict[str, list[str]] = {}
    other_errors = []

    output = "\n".join(
        part for part in (result.stdout, result.stderr) if part
    )

    # Maven:
    # [ERROR] /path/UT_1.java:[12,8] cannot find symbol
    maven_error = re.compile(
        r"^\s*\[ERROR\]\s+(.+?\.java):\[(\d+),(\d+)\]\s*(.*)$"
    )

    # Gradle/javac:
    # /path/UT_1.java:12: error: cannot find symbol
    javac_error = re.compile(
        r"^\s*(.+?\.java):\s*(\d+):\s*error:\s*(.*)$"
    )

    for line in output.splitlines():
        match = maven_error.match(line) or javac_error.match(line)

        if match:
            file_path = match.group(1).strip().strip('"')
            line_number = match.group(2)

            # Maven has four groups; javac/Gradle has three.
            message = match.group(4 if match.lastindex >= 4 else 3).strip()

            file_name = Path(file_path.replace("\\", "/")).stem

            diagnostic = f"line {line_number}: {message or 'Java compilation error'}"

            if file_name in generated_names:
                errors = errors_by_file.setdefault(file_name, [])

                if diagnostic not in errors:
                    errors.append(diagnostic)
            else:
                if diagnostic not in other_errors:
                    other_errors.append(
                        f"{file_path}: {diagnostic}"
                    )

            continue

        # Maven often prints additional compiler details on separate lines.
        # Preserve useful symbol/location details for the preceding error.
        if line.strip().startswith((
            "[ERROR]   symbol:",
            "[ERROR]   location:",
            "[ERROR]   reason:",
        )):
            detail = line.strip().removeprefix("[ERROR]").strip()

            if errors_by_file:
                last_errors = next(reversed(errors_by_file.values()))
                if detail not in last_errors:
                    last_errors.append(detail)
            elif detail not in other_errors:
                other_errors.append(detail)

        elif ": error " in line:
            detail = line.strip()

            if detail not in other_errors:
                other_errors.append(detail)

    # A failed build without a recognizable generated-file diagnostic
    # should be treated as a project/build failure.
    if result.returncode != 0 and not errors_by_file and not other_errors:
        tail = output.strip().splitlines()[-5:]

        other_errors.append(
            "\n".join(tail)
            if tail
            else "Java compilation failed without an error message."
        )

    return errors_by_file, other_errors



def get_source_package(
    source_file_paths: list[str],
    codebase_path: str | Path,
) -> str:
    """Determine the Java package from the original source files."""
    codebase_path = Path(codebase_path).resolve()
    packages = set()

    for source_path in source_file_paths:
        path = Path(source_path)

        if not path.is_absolute():
            path = codebase_path / path

        path = path.resolve()

        if not path.is_file() or path.suffix.lower() != ".java":
            continue

        source = path.read_text(encoding="utf-8", errors="replace")

        match = re.search(
            r"(?m)^\s*package\s+"
            r"([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*;",
            source,
        )

        packages.add(match.group(1) if match else "")

    if len(packages) > 1:
        raise ValueError(
            "The source files for this business rule belong to "
            "different Java packages. A single test package cannot "
            "be selected automatically."
        )

    return next(iter(packages), "")


def make_cases(prefix, items, body_of, imports_of, codebase_path):
    """Clean generated tests and retain their original UnitTest objects."""
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



def make_fixer(llm, context_for=None):
    """
    Build the async function that asks the AI to repair one Java test.

    The AI receives the generated test, its compiler errors, and optional
    source/LSP context. It returns corrected imports and test method code.

    Returns None when the AI cannot provide a usable repair. Daily quota
    and rate-limit errors are allowed to propagate to the caller.
    """
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
            "You are a senior Java engineer fixing a generated JUnit 5 "
            "test that does not compile. Fix only what the compiler errors "
            "point at."
        )

        prompt = f"""
#### TEST FILE ({case.name}.java, with line numbers)
{numbered}

#### JAVA PACKAGE
{case.package_name or "No package declaration"}

#### COMPILER ERRORS FOR THIS FILE
{chr(10).join(case.errors)}

#### CODE FROM THE CODEBASE UNDER TEST AND LSP CONTEXT IF PROVIDED
{context or "NO CONTEXT PROVIDED"}

#### TASK
Return a corrected version of the test method that compiles.

#### RULES
- `test_method`: only the method inside the generated class, including
  its @Test annotation. Do not return a class, package, or import lines.
- `imports`: every import statement the method needs, one per entry.
  Use normal Java import syntax, such as `org.junit.jupiter.api.Test`.
- Use JUnit 5 (`org.junit.jupiter.api.Test` and
  `org.junit.jupiter.api.Assertions`).
- Use only public types and members accessible to a test in this package.
  Do not assume private or package-private members are accessible from
  another package.
- Use only constructors, methods, and properties that exist in the
  supplied source code or standard Java/JUnit 5 APIs.
- Do not invent methods, constructors, fields, or helper classes.
- Do not introduce additional testing libraries or mocking frameworks.
- Keep the original test's purpose and assertions. Do not weaken an
  assertion merely to make the test compile.
- If the test cannot be fixed using the available public API, return an
  empty `test_method`.
- Use LSP context as supplementary structural information about symbols,
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
    """
    Run repair calls on the event loop already used by the agent's LLM.

    A temporary loop is used only when the caller does not provide one.
    Repairs are concurrency-limited to avoid overwhelming the model.
    """
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


def _first_error(case):
    """Return the first compiler error for a test."""
    return case.errors[0] if case.errors else "unknown compiler error"


def _drop(run, live, generated_test_dir, case):
    """Remove a failed generated test from the live set and record its reason."""
    live.remove(case)
    run.dropped.append(case)

    for path in Path(generated_test_dir).rglob(f"{case.name}.java"):
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
    """
    Writes, compiles, repairs, and executes generated Java tests.

    Compiler errors are repaired individually, with a maximum of
    MAX_REPAIR_ROUNDS attempts per test. Tests that compile successfully
    are retained even if their assertions fail during execution.
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
    temporary_pom = None
    init_script = None

    generated_test_dir = (
        output_dir / f"{prefix.lower()}_generated_tests"
    )
    workspace_dir = (
        output_dir / f"{prefix.lower()}_java_workspace"
    )
    report_dir = workspace_dir / "reports"

    generated_test_dir.mkdir(parents=True, exist_ok=True)
    workspace_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    if not cases:
        return run

    try:
        project = detect_java_project(codebase_path)

        # Write the generated tests and mark the directory as generated.
        write_tests(cases, generated_test_dir, prefix)
        mark_generated(generated_test_dir)
        
        live = list(cases)

        # Prepare the selected build system once.
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

        def compile_tests():
            """Compile generated tests using the detected build system."""
            if project.build_system == "maven":
                return compile_maven_tests(
                    project,
                    temporary_pom,
                    generated_test_dir,
                    prefix,
                )

            if project.build_system == "gradle":
                return compile_gradle_tests(
                    project,
                    workspace_dir,
                    generated_test_dir,
                    prefix,
                )

            return compile_plain_tests(
                project,
                generated_test_dir,
                workspace_dir,
                prefix,
            )

        def execute_tests():
            """Execute generated tests using the detected build system."""
            if project.build_system == "maven":
                return execute_maven_tests(
                    project,
                    temporary_pom,
                    generated_test_dir,
                    prefix,
                )

            if project.build_system == "gradle":
                return execute_gradle_tests(
                    project,
                    workspace_dir,
                    generated_test_dir,
                    prefix,
                )

            return execute_plain_tests(
                project,
                generated_test_dir,
                workspace_dir,
                prefix,
            )

        # Compile and repair until all remaining tests compile or are dropped.
        while live:
            compile_result = compile_tests()

            errors_by_test, project_errors = parse_java_build_output(
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
                        "\n".join(project_errors)
                        or compile_result.stderr
                        or compile_result.stdout
                        or "Java compilation failed with an unrecognized error."
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
                # There are no remaining tests eligible for repair.
                break

            if fix is None:
                for case in repair_candidates:
                    case.reason = "No Java test repair function was provided."
                    _drop(run, live, generated_test_dir, case)
                continue

            # Repair tests concurrently, respecting the configured limit.
            repairs = _repair(
                fix,
                repair_candidates,
                loop,
            )

            for case, repaired in zip(repair_candidates, repairs):
                if case not in live:
                    continue

                if not repaired:
                    case.reason = (
                        "The Java test could not be repaired."
                    )
                    _drop(run, live, generated_test_dir, case)
                    continue

                imports, body = repaired

                # Validate the repaired test without modifying the original
                # until the replacement has passed normalization.
                candidate = TestCase(
                    name=case.name,
                    imports=imports,
                    body=body,
                    source=case.source,
                    package_name=case.package_name,
                )

                if not clean_test(candidate):
                    case.reason = (
                        "The repaired Java test failed validation."
                    )
                    _drop(run, live, generated_test_dir, case)
                    continue


                case.imports = candidate.imports
                case.body = candidate.body
                case.repairs += 1
                case.errors = []

                # Replace only this test. Do not call write_tests() here,
                # because it deletes every generated test with this prefix.
                write_test_case(case, generated_test_dir)

        # Run only after compilation has completed without generated-test
        # compiler errors. Any tests remaining in live are compilable.
        if live:
            # Remove old reports so previous executions cannot affect results.
            for report in report_dir.glob("**/*"):
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

    except JavaProjectError as error:
        run.project_error = str(error)
        run.kept = list(live)

    except Exception as error:
        run.project_error = (
            f"Unexpected Java test execution error: {error}"
        )
        run.kept = list(live)

    finally:
        if project is not None:
            if temporary_pom is not None:
                cleanup_maven_project(temporary_pom)

            if init_script is not None:
                cleanup_gradle_project(init_script)

    return run