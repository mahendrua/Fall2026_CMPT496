"""
backend/mutation_testing.py

Mutation testing for the generated unit tests: how much would they notice if
someone changed the code later?

Checkpoint writes its tests from the code they test, so they pass by
construction. Stryker.NET makes small changes to the code the business rules
point at (`a > b` becomes `a >= b`) and reruns the unit tests on each one.
The share of changes the tests catch is the "test strength" in the run report;
backend/mutation_mapping.py turns Stryker's results into numbers per rule.

From a terminal:

    python -m backend.mutation_testing targetCodebases/<project>

Writes agent/MT_output/<codebase>/mutation_report.json, and keeps Stryker's
own config, console output and reports in agent/MT_output/<codebase>/stryker/,
outside the codebase so no later analysis run reads them.

What goes in:
  - tests: only UT_ tests that passed in the last unit test run. Stryker stops
    if a test fails before any code is changed, and IT_ tests cover several
    rules at once, so they would blur the per-rule numbers.
  - code: only .cs files a rule points at (its evidence and source files),
    inside the project the unit tests are built against.

Read only on every other output. Like the run report it never raises: a
problem ends up in the report's status and message, in plain words.
"""

import argparse
import contextvars
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from agent.test_harness import namespace_for, project_dir_for, target_project_for
from backend.mutation_mapping import build
from backend.progress_logging import progress
from backend.run_report_data import _PathShortener, _read_unit_tests


logger = logging.getLogger(__name__)

OUTPUT_DIR = "MT_output"
REPORT_NAME = "mutation_report.json"

# The whole Stryker run is stopped after this long. ConsoleTables takes a
# few minutes.
TIME_LIMIT_MINUTES = 30

# Stryker's own default. Each mutant may run for
# (test time) * 1.5 + this many milliseconds before it counts as hung.
ADDITIONAL_TIMEOUT_MS = 3000

# Stryker.NET mutates C# only.
CODE_EXTENSIONS = (".cs",)

# The test framework the unit test step records in test_report.json, and the
# language it means. Only xUnit (C#) tests can be measured.
HARNESS_LANGUAGES = {"xunit": "C#", "pytest": "Python"}

INSTALL_HINT = "Install it with: dotnet tool install -g dotnet-stryker"

# Stryker's console output (version 5.0.0), as the progress is read from it:
#   [11:55:08 INF] 11    total mutants will be tested
#   S....S.....          the dots reporter: one mark per mutant tested
TOTAL_LINE = re.compile(r"(\d+)\s+total mutants will be tested")
MARKS_LINE = re.compile(r"[.A-Z]+")
INITIAL_FAILURE = "Initial testrun has failing tests."

# What the dots reporter prints for each mutant.
MARKS = {".": "killed", "S": "survived", "T": "timeout"}


def run(
    codebase,
    app_dir,
    time_limit_minutes=TIME_LIMIT_MINUTES,
    additional_timeout_ms=ADDITIONAL_TIMEOUT_MS,
    test_dir=None,
    on_progress=progress,
):
    """
    Measure one codebase and save mutation_report.json.

    @param codebase Path to the codebase.
    @param app_dir The app folder, where agent/ is.
    @param test_dir The generated test project; found like the unit test step does when not given.
    @param on_progress Called with a short message as the run goes.
    @return The report (also saved). Never raises.
    """
    app_dir = Path(app_dir)
    codebase = Path(codebase).resolve()
    out_dir = app_dir / "agent" / OUTPUT_DIR / codebase.name
    clock = time.perf_counter()

    report = {
        "schema": 1,
        "codebase": codebase.name,
        "status": "",
        "message": "",
        "started_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "finished_at": None,
        "elapsed_seconds": None,
        "limits": {"time_limit_minutes": time_limit_minutes, "additional_timeout_ms": additional_timeout_ms},
        "tool": None,
        "test_project": None,
        "project_under_test": None,
        "inputs": {},
        "stryker": None,
        "tests": {"used": [], "left_out": []},
        "files": {"mutated": [], "skipped": [], "unresolved": []},
        "overall": None,
        "by_file": {},
        "rules": [],
    }

    try:
        _measure(report, codebase, app_dir, out_dir, test_dir, on_progress)
    except Exception as exc:
        logger.exception("Mutation testing failed")
        _stop(report, "failed", f"Mutation testing failed: {exc}")

    report["finished_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    report["elapsed_seconds"] = round(time.perf_counter() - clock, 1)

    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / REPORT_NAME
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        report["report_path"] = str(path)
    except OSError as exc:
        logger.exception("Could not save the mutation report")
        report["message"] += f" The report could not be saved: {exc}"
        report["report_path"] = None

    return report


def _measure(report, codebase, app_dir, out_dir, test_dir, on_progress):
    limits = report["limits"]
    name = codebase.name

    if not codebase.is_dir():
        return _stop(report, "not_run", f"Codebase not found: {codebase}")

    # Otherwise a Python project is told to run the unit test step it already ran,
    # because its tests are not where the C# test project would be.
    harness = _unit_test_harness(app_dir, name)
    if harness and harness != "xunit":
        return _stop(report, "not_run", (
            "Test Strength can only measure C# projects for now, and this project's unit tests "
            f"are written in {HARNESS_LANGUAGES.get(harness, harness)}."
        ))

    test_dir = Path(test_dir or project_dir_for(codebase, name))
    report["test_project"] = str(test_dir)
    if not test_dir.is_dir():
        return _stop(report, "not_run", (
            "No generated unit tests were found for this project, so there is nothing to "
            "measure. Run the unit test step first."
        ))

    rules_path = app_dir / "agent" / "BR_agent_output" / name / "validated_rules.json"
    tests_path = app_dir / "agent" / "UT_agent_output" / name / "test_report.json"
    report["inputs"] = {"validated_rules": str(rules_path), "unit_test_results": str(tests_path)}

    try:
        rules = json.loads(rules_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _stop(report, "not_run", "No business rules were found. Run the business rule step first.")
    rules = [rule for rule in rules if isinstance(rule, dict)] if isinstance(rules, list) else []

    used, left_out = select_tests(app_dir, name, test_dir)
    report["tests"] = {"used": used, "left_out": left_out}
    if not used:
        return _stop(report, "not_run", (
            "No unit test passed in the last test run, and mutation testing needs passing "
            "tests to compare against. Run the unit test step first."
        ))

    try:
        target = Path(target_project_for(codebase, name))
    except ValueError as exc:
        return _stop(report, "not_run", f"Could not tell which project the unit tests are built against: {exc}")
    report["project_under_test"] = _inside(target, codebase)

    rule_files, skipped, unresolved = rule_code_files(rules, codebase, name, target)
    mutated = sorted({path for files in rule_files.values() for path in files})
    report["files"] = {
        "mutated": [
            {"path": path, "rules": [rid for rid, files in rule_files.items() if path in files]}
            for path in mutated
        ],
        "skipped": skipped,
        "unresolved": unresolved,
    }
    if not mutated:
        return _stop(report, "not_run", f"None of the rules point at C# code in {target.name}, so there is nothing to change.")

    command, version, problem = find_stryker()
    if problem:
        return _stop(report, "not_run", problem)

    stryker_dir = out_dir / "stryker"
    shutil.rmtree(stryker_dir, ignore_errors=True)  # never read an older run's report
    stryker_dir.mkdir(parents=True, exist_ok=True)

    config = {
        "project": target.name,
        "mutate": mutate_patterns(mutated, codebase, target),
        "test-case-filter": dotnet_test_filter(namespace_for(name), [test["name"] for test in used]),
        "reporters": ["json", "html", "dots"],
        "coverage-analysis": "perTest",
        "disable-bail": True,
        "additional-timeout": limits["additional_timeout_ms"],
        "break-on-initial-test-failure": True,
    }
    config_path = stryker_dir / "stryker-config.json"
    config_path.write_text(json.dumps({"stryker-config": config}, indent=2), encoding="utf-8")

    args = command + ["--config-file", str(config_path), "--output", str(stryker_dir), "--skip-version-check"]
    report["tool"] = {"name": "Stryker.NET", "version": version, "command": args, "config": config}

    on_progress(
        f"Test strength: running the {len(used)} passing unit tests once before planting "
        f"mistakes in {len(mutated)} file{'s' if len(mutated) != 1 else ''}..."
    )
    result = run_stryker(args, test_dir, limits["time_limit_minutes"] * 60, on_progress)

    output_path = stryker_dir / "stryker_output.txt"
    output_path.write_text(result["output"], encoding="utf-8", errors="replace")
    json_path = stryker_dir / "reports" / "mutation-report.json"
    html_path = stryker_dir / "reports" / "mutation-report.html"

    report["stryker"] = {
        "exit_code": result["exit_code"],
        "timed_out": result["timed_out"],
        "total": result["total"],
        "tested": result["tested"],
        "live_counts": result["live_counts"],
        "console_output": str(output_path),
        "json_report": str(json_path) if json_path.exists() else None,
        "html_report": str(html_path) if html_path.exists() else None,
    }

    if result["timed_out"]:
        caught = result["live_counts"].get("killed", 0)
        return _stop(report, "timed_out", (
            f"Stopped after {_minutes(limits['time_limit_minutes'])} with {result['tested']} of "
            f"{result['total'] if result['total'] is not None else '?'} planted mistakes tried "
            f"({caught} caught so far). Stryker saves its detailed results only when it "
            "finishes, so there are no numbers per rule. Raise the time limit to finish."
        ))

    if result["exit_code"] != 0 or not json_path.exists():
        return _stop(report, "failed", _failure_message(result))

    try:
        stryker_json = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return _stop(report, "failed", f"Stryker finished but its report could not be read: {exc}")

    mapped = build(
        [stryker_json],
        codebase,
        [{"id": rule.get("id"), "files": rule_files.get(rule.get("id"), [])} for rule in rules],
        used,
    )

    left_out_by_rule = {str(test.get("rule_id")): test for test in left_out if test.get("rule_id") is not None}
    for rule in mapped["rules"]:
        if rule["own_tests"] is None:
            test = left_out_by_rule.get(str(rule["id"]))
            rule["note"] = (
                f"Not measured, because its test {test['name']} {test['reason']}." if test
                else "Not measured, because no unit test was generated for this rule."
            )

    report["overall"] = mapped["overall"]
    report["by_file"] = mapped["by_file"]
    report["tests"]["used"] = mapped["tests"]
    report["rules"] = mapped["rules"]

    overall = mapped["overall"]
    if overall and overall["score"] is not None:
        message = f"Test strength: the tests caught {overall['score']:.0f}% of {overall['scored']} planted mistakes."
    else:
        message = "Stryker finished but planted no mistakes it could test."
    _stop(report, "finished", message)


def _stop(report, status, message):
    report["status"] = status
    report["message"] = message


# ---------------------------------------------------------
# Inputs
# ---------------------------------------------------------

def select_tests(app_dir, codebase_name, test_dir):
    """
    Split the generated tests into the ones Stryker runs and the ones it leaves out.

    Read the same way the run report reads them, so both agree on which
    tests passed: test_report.json lists only counts, failures and dropped
    tests, not the names of the ones that passed.

    Each left-out test gets a short code (`why`) and a reason that reads on
    after its name ("UT_3 already fails on the code as it is now, ...").

    @return (used, left_out): [{"name", "rule_id"}], [{"name", "rule_id", "why", "reason"}]
    """
    unit = _read_unit_tests(
        Path(app_dir) / "agent" / "UT_agent_output" / codebase_name,
        _PathShortener(None, codebase_name),
        [],
        None,
    )
    on_disk = {path.stem for path in Path(test_dir).glob("*.cs")}

    used, left_out = [], []
    for test in unit["tests"]:
        entry = {"name": test["name"], "rule_id": (test["rule_ids"] or [None])[0]}

        if test["status"] != "passed":
            left_out.append(dict(entry, **_why_left_out(test)))
        elif test["name"] not in on_disk:
            left_out.append(dict(entry, why="missing",
                                 reason="passed, but its file is no longer in the generated test project"))
        else:
            used.append(entry)

    listed = {test["name"] for test in unit["tests"]}
    for stem in sorted(on_disk - listed, key=_natural):
        if stem.startswith("IT_"):
            why, reason = "integration", (
                "is an integration test: it checks several rules at once, so it cannot score a single rule"
            )
        elif stem.startswith("UT_"):
            why, reason = "stale", "is left over from an older run and is not in the latest unit test results"
        else:
            continue
        left_out.append({"name": stem, "rule_id": None, "why": why, "reason": reason})

    return used, left_out


def _unit_test_harness(app_dir, codebase_name):
    """The test framework of the last unit test run ("xunit", "pytest"), or None when not known."""
    path = Path(app_dir) / "agent" / "UT_agent_output" / codebase_name / "test_report.json"
    try:
        harness = json.loads(path.read_text(encoding="utf-8")).get("harness")
    except (OSError, ValueError, AttributeError):
        return None
    return harness if isinstance(harness, str) and harness else None


def _why_left_out(test):
    """Why a test that did not pass cannot be used, in words a reader can follow."""
    if test["status"] == "dropped":
        reason = "was thrown away by the unit test step before it ever ran"
        return {"why": "dropped", "reason": f"{reason}: {test['reason']}" if test["reason"] else reason}

    if test["status"] == "failed":
        # Mutation testing asks "does a test fail when the code changes?"; a
        # test that already fails on the unchanged code cannot answer that.
        reason = "already fails on the code as it is now, so it cannot show whether a change broke anything"
        methods = ", ".join(test["failed_methods"])
        return {"why": "failed", "reason": f"{reason} (failing check: {methods})" if methods else reason}

    return {"why": "not_run", "reason": "was not run in the last unit test run, so nobody knows whether it passes"}


def _natural(name):
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", name)]


def rule_code_files(rules, codebase_root, codebase_name, target_project):
    """
    The C# files each rule points at, inside the project the tests are built against.

    A rule's references are its evidence file names, its source_file_paths
    and, in newer rule files, the paths in its provenance. Older rule files
    hold full Windows paths, paths starting at the codebase's own folder
    name, or a bare file name; they are matched to real files the same way
    the run report does it.

    @return (per_rule, skipped, unresolved)
            per_rule    {rule id: [paths inside the codebase]}
            skipped     [{"path", "reason", "rules"}] files a rule names that are not changed
            unresolved  [{"reference", "rules"}] names that match no single file
    """
    root = Path(codebase_root).resolve()
    target_dir = Path(target_project).resolve().parent
    paths = _PathShortener(root, codebase_name)

    per_rule, skipped, unresolved = {}, {}, {}

    for rule in rules:
        rule_id = rule.get("id")
        files = per_rule.setdefault(rule_id, [])

        for reference in _references(rule):
            rel, _ = paths.file(reference)
            full = (root / rel).resolve() if rel else root

            # A full path from another machine can name a file outside this codebase.
            if not rel or root not in full.parents or not full.is_file():
                entry = unresolved.setdefault(reference, {"reference": reference, "rules": []})
                _add(entry["rules"], rule_id)
                continue

            rel = full.relative_to(root).as_posix()
            reason = _skip_reason(full, root, target_dir)
            if reason:
                entry = skipped.setdefault(rel, {"path": rel, "reason": reason, "rules": []})
                _add(entry["rules"], rule_id)
            else:
                _add(files, rel)

    return per_rule, list(skipped.values()), list(unresolved.values())


def _references(rule):
    explanation = rule.get("explanation") if isinstance(rule.get("explanation"), dict) else {}
    evidence = explanation.get("evidence") if isinstance(explanation.get("evidence"), dict) else {}

    references = list(evidence) + list(rule.get("source_file_paths") or [])
    for origin in rule.get("provenance") or []:
        if isinstance(origin, dict):
            references += origin.get("source_file_paths") or []

    unique = []
    for reference in references:
        if isinstance(reference, str) and reference.strip():
            _add(unique, reference.strip())
    return unique


def _skip_reason(path, root, target_dir):
    if path.suffix.lower() not in CODE_EXTENSIONS:
        return "not C# code"

    project = _project_of(path, root)
    if project is None:
        return "not part of a C# project"
    if _is_test_project(project):
        return "test code"
    if project.parent.resolve() != target_dir:
        return f"in {project.name}, not the project the unit tests are built against"
    return ""


def _project_of(path, root):
    """The nearest .csproj in the file's folder or above it, inside the codebase."""
    folder = path.parent
    while True:
        projects = sorted(folder.glob("*.csproj"))
        if projects:
            return projects[0]
        if folder == root or root not in folder.parents:
            return None
        folder = folder.parent


def _is_test_project(project):
    try:
        text = project.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "Microsoft.NET.Test.Sdk" in text or bool(re.search(r"<IsTestProject>\s*true", text, re.I))


def _add(items, item):
    if item not in items:
        items.append(item)


def _inside(path, root):
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return str(path)


# ---------------------------------------------------------
# Stryker
# ---------------------------------------------------------

def dotnet_test_filter(namespace, names):
    """
    dotnet test filter for exactly these test classes.

    The trailing dot ends the class name, so UT_1 does not also match UT_10.
    """
    return "|".join(f"FullyQualifiedName~{_escape(namespace)}.{_escape(name)}." for name in names)


def _escape(text):
    return re.sub(r"([\\()&|=!~])", r"\\\1", text)


def mutate_patterns(files, codebase_root, target_project):
    """
    Stryker `mutate` patterns for files inside the codebase.

    Stryker matches a pattern against each file's full path and its path
    inside the project, so `**/` plus the path inside the project fits either,
    and keeps the full path (with its spaces) out of the pattern.
    """
    project_dir = Path(target_project).resolve().parent
    patterns = []
    for rel in files:
        inside = os.path.relpath(Path(codebase_root).resolve() / rel, project_dir).replace("\\", "/")
        patterns.append(f"**/{inside}")
    return patterns


def find_stryker():
    """
    How to start Stryker.NET and its version.

    @return (command, version, "") or (None, None, what is missing in plain words)
    """
    try:
        listed = subprocess.run(
            ["dotnet", "tool", "list", "--global"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        )
    except FileNotFoundError:
        return None, None, "The .NET SDK (dotnet) was not found, so mutation testing could not run."
    except subprocess.TimeoutExpired:
        return None, None, "Checking for Stryker.NET took too long, so mutation testing did not run."

    match = re.search(r"^dotnet-stryker\s+(\S+)", listed.stdout or "", re.M | re.I)
    if not match:
        return None, None, f"Stryker.NET is not installed, so mutation testing did not run. {INSTALL_HINT}"

    # Global tools live in ~/.dotnet/tools, which the app's PATH may not include.
    exe = shutil.which("dotnet-stryker")
    if not exe:
        tool = Path.home() / ".dotnet" / "tools" / ("dotnet-stryker.exe" if os.name == "nt" else "dotnet-stryker")
        exe = str(tool) if tool.exists() else None

    return ([exe] if exe else ["dotnet", "stryker"]), match.group(1), ""


def run_stryker(args, test_dir, time_limit_seconds, on_progress):
    """
    Run Stryker from the test project's folder, reporting progress as it goes.

    Stopped (with every process it started) once the time limit is reached.

    @return {"exit_code", "timed_out", "output", "total", "tested", "live_counts"}
    """
    watcher = _Progress(on_progress)

    try:
        process = subprocess.Popen(args, cwd=str(test_dir), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except FileNotFoundError:
        return watcher.result(exit_code=None, timed_out=False, extra=f"Could not start Stryker: {args[0]} was not found.")

    # Progress is reported from the reader thread. A new thread starts with no
    # context, so without this its messages lose the app's request ID and
    # main.js rejects them ("Backend response did not include a request ID").
    context = contextvars.copy_context()
    reader = threading.Thread(target=context.run, args=(_read, process.stdout, watcher), daemon=True)
    reader.start()

    timed_out = False
    try:
        process.wait(timeout=time_limit_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(process)

    reader.join(timeout=30)
    return watcher.result(exit_code=process.returncode, timed_out=timed_out)


def _read(stream, watcher):
    while True:
        chunk = stream.read1(4096) if hasattr(stream, "read1") else stream.read(4096)
        if not chunk:
            break
        watcher.feed(chunk.decode("utf-8", errors="replace"))


def _kill_tree(process):
    """Stryker starts build and test processes of its own; stop them all."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=60)
        else:
            process.kill()
        process.wait(timeout=60)
    except Exception:
        logger.warning("Could not stop Stryker cleanly", exc_info=True)


class _Progress:
    """Reads Stryker's console output: how many mutants there are, and how many are done."""

    def __init__(self, on_progress, every_seconds=5):
        self.on_progress = on_progress
        self.every_seconds = every_seconds
        self.text = ""
        self.total = None
        self.marks = ""
        self._last_message = 0.0
        self._last_tested = -1

    def feed(self, chunk):
        self.text += chunk

        if self.total is None:
            match = TOTAL_LINE.search(self.text)
            if not match:
                return
            self.total = int(match.group(1))
            self._start = match.end()
            self.on_progress(f"Test strength: {self.total} planted mistakes to try...")

        # After the total, the dots reporter writes one mark per mutant on a
        # line of its own; log lines always have "[", spaces or small letters.
        self.marks = "".join(
            line.strip() for line in self.text[self._start:].splitlines()
            if MARKS_LINE.fullmatch(line.strip())
        )

        tested = len(self.marks)
        now = time.monotonic()
        if tested and tested != self._last_tested and (tested == self.total or now - self._last_message >= self.every_seconds):
            self._last_message, self._last_tested = now, tested
            self.on_progress(f"Test strength: {tested} of {self.total} planted mistakes tried")

    @property
    def live_counts(self):
        counted = Counter(MARKS.get(mark, "other") for mark in self.marks)
        return dict(counted)

    def result(self, exit_code, timed_out, extra=""):
        return {
            "exit_code": exit_code,
            "timed_out": timed_out,
            "output": self.text + (f"\n{extra}\n" if extra else ""),
            "total": self.total,
            "tested": len(self.marks),
            "live_counts": self.live_counts,
        }


def _failure_message(result):
    output = result["output"]

    if INITIAL_FAILURE in output:
        failing = [
            line.strip() for line in output.split(INITIAL_FAILURE, 1)[1].splitlines()
            if re.fullmatch(r"[\w.]+", line.strip())
        ]
        names = f" ({', '.join(failing[:5])})" if failing else ""
        return (
            f"Stryker stopped because a unit test failed before any code was changed{names}. "
            "The last test results may be out of date: run the unit test step again."
        )

    tail = " ".join(line.strip() for line in output.strip().splitlines()[-3:])
    return (
        f"Stryker did not finish (exit code {result['exit_code']}). Last output: {tail or 'none'}. "
        "The full output is in stryker_output.txt."
    )


def _minutes(minutes):
    return f"{minutes:g} minute{'s' if minutes != 1 else ''}"


# ---------------------------------------------------------
# Terminal
# ---------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Measure how many planted mistakes the generated unit tests catch (Stryker.NET)."
    )
    parser.add_argument("codebase", help="Path to the codebase, e.g. targetCodebases/<project>")
    parser.add_argument("--time-limit", type=float, default=TIME_LIMIT_MINUTES,
                        help=f"Stop the whole run after this many minutes (default {TIME_LIMIT_MINUTES}).")
    parser.add_argument("--mutant-timeout", type=int, default=ADDITIONAL_TIMEOUT_MS,
                        help=f"Extra milliseconds each change may run before it counts as hung (default {ADDITIONAL_TIMEOUT_MS}).")
    args = parser.parse_args(argv)

    app_dir = Path(__file__).resolve().parent.parent
    codebase = Path(args.codebase)
    if not codebase.is_absolute() and not codebase.exists():
        codebase = app_dir / codebase

    # Flushed, so progress shows while Stryker runs even when output goes to a file.
    report = run(codebase, app_dir, args.time_limit, args.mutant_timeout,
                 on_progress=lambda message: print(message, flush=True))

    print(report["message"])
    if report.get("report_path"):
        print(f"Report: {report['report_path']}")
    return 0 if report["status"] == "finished" else 1


if __name__ == "__main__":
    raise SystemExit(main())
