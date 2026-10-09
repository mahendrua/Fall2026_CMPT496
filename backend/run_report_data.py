"""
backend/run_report_data.py

Gathers everything one run produced into a single dictionary (US-049, T-164):
the project and folder summaries, the file summaries, the business rules with
their evidence, the tests and how they did, and the run's token numbers.

Read only: it never changes the outputs it reads and makes no AI calls. The
HTML report and the whole-project diagram are built from what it returns.
Saved as run_reports/<codebase>/run_report_data.json.

A missing or unreadable output never stops it; it is listed in "notes" and
that part of the report is left empty.
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path

from agent.crawl_config import prune


REPORT_DIR = "run_reports"
DATA_NAME = "run_report_data.json"

# Same names the pipeline writes (backend/token_usage.py).
RUN_LOG_NAME = "last_run_log.json"
RUN_HISTORY_DIR = "run_logs"

# BR_agent files a rule whose check crashed under discarded rules with this
# reason, so the report can tell "error" apart from "rejected".
RULE_ERROR_PREFIX = "Validation failed with error"

# A failing test as the test run reports it: <namespace>.UT_7.<method>
# (test files are named UT_<rule id> / IT_<n> by agent/test_harness.py).
TEST_NAME = re.compile(r"\b((?:UT|IT)_\d+(?:_b)*)\b")


def report_folder(app_dir, codebase_name):
    return Path(app_dir) / REPORT_DIR / codebase_name


def save(data, app_dir):
    """Write the data file next to the report and return its path."""
    folder = report_folder(app_dir, data["codebase"])
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / DATA_NAME
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def collect(codebase_name, app_dir, codebase_path=None, run_log_path=None):
    """
    Read every output of the latest run on one codebase.

    @param codebase_name Folder name of the codebase, as the outputs are filed.
    @param codebase_path Where the codebase lives; used to shorten file paths.
           Worked out from the outputs when not given.
    @param run_log_path A specific run log to use instead of the newest one.
    """
    app_dir = Path(app_dir)
    agent_dir = app_dir / "agent"
    notes = []

    run = _read_run(app_dir, codebase_name, run_log_path, notes)
    since = _run_start(run)

    folder_rules = _load(
        agent_dir / "directory_agent_output" / codebase_name / "business_rules" / "business_rules.json",
        notes, "folder business rules", since,
    )
    root = Path(codebase_path) if codebase_path else _find_root(folder_rules, codebase_name)
    paths = _PathShortener(root, codebase_name)

    files = _read_files(agent_dir / "file_summary_agent_output" / codebase_name, paths, notes, since)
    folders, project = _read_folders(
        agent_dir / "directory_agent_output" / codebase_name, folder_rules, codebase_name, notes, since,
    )
    rules = _read_rules(agent_dir / "BR_agent_output" / codebase_name, paths, notes, since)
    unit = _read_unit_tests(agent_dir / "UT_agent_output" / codebase_name, paths, notes, since)
    integration = _read_integration_tests(agent_dir / "IT_agent_output" / codebase_name, notes, since)

    _link_files_to_folders(files, folders)
    _link_tests_to_rules(rules, unit["tests"] + integration["tests"])

    old_files = sum(item["old"] for item in files + folders)
    if old_files:
        notes.append(
            f"{old_files} file or folder summaries are older than this run. Nothing clears "
            "the outputs between runs, so they may be left over from an earlier run; "
            "they are marked 'older than this run'."
        )

    return {
        "schema": 1,
        "codebase": codebase_name,
        "codebase_path": str(root) if root else None,
        "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "run": run,
        "overview": {
            "summary": project.get("purpose", ""),
            "responsibilities": project.get("responsibilities") or [],
            "counts": _counts(files, folders, rules, unit, integration),
        },
        "folders": folders,
        "files": files,
        "rules": rules,
        "tests": {"unit": unit, "integration": integration},
        "notes": notes,
    }


# ---------------------------------------------------------
# Reading files
# ---------------------------------------------------------

def _load(path, notes, label, since=None, missing_ok=False):
    """JSON from one output file, or None (with a note) when it can't be read."""
    path = Path(path)

    if not path.exists():
        if not missing_ok:
            notes.append(f"No {label} found ({_tail(path)}).")
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        notes.append(f"Could not read the {label} ({_tail(path)}): {exc}")
        return None

    if since and _is_old(path, since):
        notes.append(f"The {label} ({_tail(path)}) is older than this run.")

    return data


def _tail(path):
    """The last three parts of a path, enough to find the file."""
    return "/".join(Path(path).parts[-3:])


def _is_old(path, since):
    try:
        return datetime.fromtimestamp(Path(path).stat().st_mtime) < since
    except OSError:
        return False


def _as_list(value):
    return value if isinstance(value, list) else []


def _as_dict(value):
    return value if isinstance(value, dict) else {}


# ---------------------------------------------------------
# Run log
# ---------------------------------------------------------

def find_run_log(app_dir, codebase_name):
    """
    The newest full-pipeline run log for this codebase, or None.

    Runs of single steps are skipped: their logs hold one step, not the run.
    """
    app_dir = Path(app_dir)
    candidates = sorted(
        (app_dir / RUN_HISTORY_DIR).glob(f"{codebase_name}_*.json"),
        reverse=True,  # names end in the run id, which starts with the time
    )
    candidates.append(app_dir / RUN_LOG_NAME)

    for path in candidates:
        try:
            log = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue

        if log.get("codebase") == codebase_name and log.get("command") == "full_pipeline":
            return path

    return None


def _read_run(app_dir, codebase_name, run_log_path, notes):
    path = Path(run_log_path) if run_log_path else find_run_log(app_dir, codebase_name)

    if path is None:
        notes.append(
            "No full-pipeline run log found for this project, so token numbers are missing "
            "and leftover files from earlier runs can't be told apart."
        )
        return None

    log = _load(path, notes, "run log")
    if not isinstance(log, dict):
        return None

    totals = _as_dict(log.get("totals"))

    return {
        "run_id": log.get("run_id"),
        "status": log.get("status"),
        "error": log.get("error"),
        "started_at": log.get("started_at"),
        "finished_at": log.get("finished_at"),
        "elapsed_seconds": log.get("elapsed_seconds"),
        "log_file": str(path),
        "tokens": {
            key: totals.get(key, 0)
            for key in (
                "total_tokens", "input_tokens", "output_tokens", "thinking_tokens",
                "calls", "failed_calls", "waits", "waited_seconds",
            )
        },
        "stages": [
            {
                key: stage.get(key)
                for key in (
                    "stage", "status", "error", "elapsed_seconds", "calls",
                    "failed_calls", "total_tokens", "thinking_tokens",
                )
            }
            for stage in _as_list(log.get("stages"))
            if isinstance(stage, dict)
        ],
    }


def _run_start(run):
    try:
        return datetime.fromisoformat(run["started_at"])
    except (TypeError, KeyError, ValueError):
        return None


# ---------------------------------------------------------
# Paths
# ---------------------------------------------------------

def _find_root(folder_rules, codebase_name):
    """
    Where the codebase was on disk during the run.

    The folder business rules are keyed by each folder's full path, and the
    code (not the AI) fills in its path inside the codebase; the root is the
    one whose path is ".".
    """
    for full_path, entry in _as_dict(folder_rules).items():
        if isinstance(entry, dict) and entry.get("directory_path") == ".":
            return Path(full_path)
    return None


class _PathShortener:
    """Turns the full Windows paths in the outputs into paths inside the codebase."""

    def __init__(self, root, codebase_name):
        self.root_path = Path(root) if root else None
        self.root = os.path.normcase(os.path.normpath(str(root))) if root else None
        self.codebase_name = codebase_name
        self._files = None

    def file(self, path):
        """
        A source file's path inside the codebase, checked against the disk.

        The path in a file summary is copied out by the AI and sometimes comes
        back wrong, e.g. with a folder left out ('Lib/Parser.cs' for
        'src/Lib/Parser.cs'). When it names no real file, the real file with
        the same name and the longest matching end is used; when several
        files match equally well, the AI's path is kept.

        @return (path, True if it had to be corrected)
        """
        short = self(path)
        real, by_name = self._real_files()

        if not real or short.lower() in real:
            return short, False

        # "." and "/" count alike, since the AI sometimes writes a nested
        # folder "App.Core/Models" as "App.Core.Models".
        wanted = re.split(r"[/.]", short.lower())

        def matching_end(candidate):
            parts = re.split(r"[/.]", candidate.lower())
            count = 0
            while count < min(len(parts), len(wanted)) and parts[-1 - count] == wanted[-1 - count]:
                count += 1
            return count

        candidates = by_name.get(short.lower().rsplit("/", 1)[-1], [])
        if not candidates:
            return short, False

        best = max(matching_end(c) for c in candidates)
        winners = [c for c in candidates if matching_end(c) == best]

        if len(winners) != 1:
            return short, False  # same name in several folders: can't tell which

        return winners[0], True

    def _real_files(self):
        """Every file in the codebase ({lowercase path}, {lowercase name: [paths]}), read once."""
        if self._files is None:
            real, by_name = set(), {}

            if self.root_path is not None and self.root_path.is_dir():
                for folder, subfolders, names in os.walk(self.root_path):
                    prune(folder, subfolders)  # the same folders the pipeline skips
                    for name in names:
                        rel = os.path.relpath(os.path.join(folder, name), self.root_path).replace("\\", "/")
                        real.add(rel.lower())
                        by_name.setdefault(name.lower(), []).append(rel)

            self._files = (real, by_name)

        return self._files

    def __call__(self, path):
        if not path:
            return ""

        text = os.path.normpath(str(path))

        if self.root:
            folded = os.path.normcase(text)
            if folded == self.root:
                return "."
            if folded.startswith(self.root + os.sep):
                return text[len(self.root) + 1:].replace("\\", "/")

        # Fall back to whatever follows the codebase's own folder name.
        parts = Path(text).parts
        if self.codebase_name in parts:
            rest = parts[parts.index(self.codebase_name) + 1:]
            if rest:
                return "/".join(rest)

        return text.replace("\\", "/")


def _folder_of(path):
    parent = path.rsplit("/", 1)[0] if "/" in path else "."
    return parent or "."


# ---------------------------------------------------------
# File and folder summaries
# ---------------------------------------------------------

def _read_files(folder, paths, notes, since):
    if not folder.is_dir():
        notes.append(f"No file summaries found ({_tail(folder)}).")
        return []

    files, corrected = [], 0

    for json_path in sorted(folder.glob("*.json")):
        data = _load(json_path, notes, "file summary")
        if not isinstance(data, dict):
            continue

        path, fixed = paths.file(data.get("path"))
        path = path or json_path.stem
        corrected += fixed

        files.append({
            "path": path,
            "folder": _folder_of(path),
            "summary": data.get("summary", ""),
            "dependencies": _as_list(data.get("dependencies")),
            "functions": [
                {key: fn.get(key) for key in ("name", "description", "return_type", "visibility")}
                for fn in _as_list(data.get("functions")) if isinstance(fn, dict)
            ],
            "types": [_type(t) for t in _as_list(data.get("types")) if isinstance(t, dict)],
            "relationships": _as_list(data.get("relationships")),
            "external_relationships": _as_list(data.get("external_relationships")),
            "business_rules": [
                rule.get("rule", "") if isinstance(rule, dict) else str(rule)
                for rule in _as_list(data.get("business_rules"))
            ],
            "old": bool(since and _is_old(json_path, since)),
        })

    if corrected:
        notes.append(
            f"{corrected} file summaries had a wrong file path (the AI copies it out); "
            "they were matched to the real files by name."
        )

    return files


def _type(entry):
    """A type from a file summary, without its per-type PlantUML text."""
    return {
        "name": entry.get("name", ""),
        "kind": entry.get("kind", "class"),
        "description": entry.get("description", ""),
        "inherits_from": _as_list(entry.get("inherits_from")),
        "enum_values": _as_list(entry.get("enum_values")),
        "properties": [
            {key: prop.get(key) for key in ("name", "type", "visibility", "is_static", "description")}
            for prop in _as_list(entry.get("properties")) if isinstance(prop, dict)
        ],
        "methods": [
            {
                "name": method.get("name"),
                "visibility": method.get("visibility"),
                "return_type": method.get("return_type"),
                "is_static": method.get("is_static", False),
                "is_constructor": method.get("is_constructor", False),
                "parameters": [
                    f"{param.get('type') or ''} {param.get('name') or ''}".strip()
                    for param in _as_list(method.get("parameters")) if isinstance(param, dict)
                ],
                "description": method.get("description", ""),
            }
            for method in _as_list(entry.get("methods")) if isinstance(method, dict)
        ],
    }


def _summary_file_name(folder_path):
    """The name directory_agent gives a folder's summary (see directory_agent.py)."""
    return folder_path.strip("./").replace("/", "_") + ".json"


def _read_folders(folder, folder_rules, codebase_name, notes, since):
    """
    Folder summaries, and the whole-project summary (the root folder's).

    The path inside each summary is written by the AI and is sometimes wrong
    (e.g. with the folders above the codebase in front), so the path the code
    recorded with the folder's business rules is used when there is one.
    """
    rules_by_path = {
        entry.get("directory_path"): entry
        for entry in _as_dict(folder_rules).values()
        if isinstance(entry, dict) and entry.get("directory_path")
    }
    path_by_file = {_summary_file_name(path): path for path in rules_by_path if path != "."}

    if not folder.is_dir():
        notes.append(f"No folder summaries found ({_tail(folder)}).")
        return [], {}

    folders, project = [], {}
    summaries = [(p, False) for p in sorted(folder.glob("*.json"))]
    summaries += [(p, True) for p in sorted((folder / "root_output").glob("*.json"))]

    for json_path, is_root in summaries:
        data = _load(json_path, notes, "folder summary")
        if not isinstance(data, dict):
            continue

        if is_root:
            path = "."
            project = data
        else:
            path = path_by_file.get(json_path.name) or _clean_folder_path(
                data.get("directory_path") or json_path.stem, codebase_name
            )

        rules = _as_dict(rules_by_path.get(path))

        folders.append({
            "path": path,
            "name": codebase_name if path == "." else path.rsplit("/", 1)[-1],
            "purpose": data.get("purpose", ""),
            "responsibilities": _as_list(data.get("responsibilities")),
            "observed_rules": _as_list(rules.get("observed_rules")),
            "inferred_rules": _as_list(rules.get("inferred_rules")),
            "files": [],
            "old": bool(since and _is_old(json_path, since)),
        })

    if not project:
        notes.append("No whole-project summary found (directory_agent_output/<codebase>/root_output).")

    folders.sort(key=lambda item: (item["path"] != ".", item["path"].lower()))
    return folders, project


def _clean_folder_path(path, codebase_name):
    """Drop anything up to the codebase's own folder name: 'x/<codebase>/docs' -> 'docs'."""
    parts = [part for part in str(path).replace("\\", "/").split("/") if part and part != "."]
    if codebase_name in parts:
        parts = parts[parts.index(codebase_name) + 1:]
    return "/".join(parts) or "."


def _link_files_to_folders(files, folders):
    by_path = {folder["path"]: folder for folder in folders}
    for item in files:
        folder = by_path.get(item["folder"])
        if folder is not None:
            folder["files"].append(item["path"])


# ---------------------------------------------------------
# Business rules
# ---------------------------------------------------------

def _read_rules(folder, paths, notes, since):
    proven = _load(folder / "validated_rules.json", notes, "proven business rules", since)
    discarded = _load(folder / "discarded_rules.json", notes, "rejected business rules", since)

    rules = []

    for entry in _as_list(proven):
        if not isinstance(entry, dict):
            continue
        explanation = _as_dict(entry.get("explanation"))
        rules.append(_rule(entry, paths, "proven",
                           evidence=_as_dict(explanation.get("evidence")),
                           reasoning=explanation.get("reasoning", "")))

    for entry in _as_list(discarded):
        if not isinstance(entry, dict):
            continue
        reason = entry.get("reason", "")
        status = "error" if reason.startswith(RULE_ERROR_PREFIX) else "rejected"
        rules.append(_rule(entry, paths, status, reason=reason))

    rules.sort(key=lambda rule: _number(rule["id"]))
    return rules


def _rule(entry, paths, status, evidence=None, reasoning="", reason=""):
    return {
        "id": entry.get("id"),
        "rule": entry.get("rule", ""),
        "status": status,
        "folder": entry.get("source_directory", ""),
        "source_files": [paths.file(p)[0] for p in _as_list(entry.get("source_file_paths"))],
        "evidence": evidence or {},
        "reasoning": reasoning,
        "reason": reason,
        "tests": [],
    }


def _number(value):
    try:
        return (0, int(value))
    except (TypeError, ValueError):
        return (1, str(value))


# ---------------------------------------------------------
# Tests
# ---------------------------------------------------------

def _test_names(prefix, ids):
    """
    The name each generated test was given, in order.

    Mirrors make_cases in agent/test_harness.py: <prefix>_<id>, with "_b"
    added when an id repeats.
    """
    names, used = [], set()
    for item_id in ids:
        name = f"{prefix}_{item_id}"
        while name in used:
            name += "_b"
        used.add(name)
        names.append(name)
    return names


def _report_section(report, generated_count):
    """Counts for one kind of test, from its test_report.json when there is one."""
    report = _as_dict(report)
    return {
        "checked": bool(report),
        "message": report.get("message", ""),
        "project_error": report.get("project_error", ""),
        "generated": report.get("generated", generated_count),
        "kept": report.get("kept"),
        "dropped": len(_as_list(report.get("dropped"))) if report else None,
        "repaired": len(_as_list(report.get("repaired"))) if report else None,
        "passed": report.get("passed"),
        "failed": report.get("failed"),
        "skipped": report.get("skipped"),
    }


def _status(name, report, dropped, failed):
    """
    passed / failed / dropped / not run, for one test file.

    passed and failed in test_report.json count test methods; a file can hold
    more than one, so the file failed if any of its methods did.
    """
    if name in dropped:
        return "dropped"
    if not report or report.get("project_error"):
        return "not run"
    if name in failed:
        return "failed"
    if (report.get("passed") or 0) + (report.get("failed") or 0):
        return "passed"
    return "not run"


def _failures(report):
    """{test name: [failed method names]} from test_report.json."""
    failed = {}
    for full_name in _as_list(_as_dict(report).get("failed_tests")):
        match = TEST_NAME.search(str(full_name))
        if match:
            method = str(full_name)[match.end():].lstrip(".")
            failed.setdefault(match.group(1), []).append(method or str(full_name))
    return failed


def _read_unit_tests(folder, paths, notes, since):
    generated = _as_list(_load(folder / "unit_tests.json", notes, "generated unit tests", since))
    kept = _as_list(_load(folder / "validated_tests.json", notes, "kept unit tests", missing_ok=True))
    discarded = _as_list(_load(folder / "discarded_tests.json", notes, "dropped unit tests", missing_ok=True))
    report = _load(folder / "test_report.json", notes, "unit test results", since, missing_ok=True)

    if generated and report is None:
        notes.append("The unit tests have no test_report.json, so they were not compiled or run in this run.")

    generated = [t for t in generated if isinstance(t, dict)]
    names = _test_names("UT", [t.get("id") for t in generated])

    kept_code = {}
    for entry in kept:
        if isinstance(entry, dict):
            kept_code.setdefault(str(entry.get("id")), []).append(entry)
    dropped_code = {}
    for entry in discarded:
        if isinstance(entry, dict):
            dropped_code.setdefault(str(entry.get("id")), []).append(entry)

    report_dict = _as_dict(report)
    dropped = {d.get("name"): d.get("reason", "") for d in _as_list(report_dict.get("dropped")) if isinstance(d, dict)}
    repaired = set(_as_list(report_dict.get("repaired")))
    failures = _failures(report)

    tests = []
    for name, entry in zip(names, generated):
        key = str(entry.get("id"))
        status = _status(name, report, dropped, failures)
        final = _take(dropped_code if status == "dropped" else kept_code, key, entry)

        tests.append({
            "name": name,
            "kind": "unit",
            "title": entry.get("rule", ""),
            "rule_ids": [entry.get("id")],
            "status": status,
            "repaired": name in repaired,
            "reason": dropped.get(name) or final.get("reason", ""),
            "failed_methods": failures.get(name, []),
            "source_files": [paths.file(p)[0] for p in _as_list(entry.get("source_file_paths"))],
            "imports": _as_list(final.get("imports")),
            "code": final.get("unit_test", ""),
        })

    return dict(_report_section(report, len(generated)), tests=tests)


def _read_integration_tests(folder, notes, since):
    generated = _as_list(_load(folder / "integration_tests.json", notes, "generated integration tests", since))
    kept = _as_list(_load(folder / "validated_tests.json", notes, "kept integration tests", missing_ok=True))
    discarded = _as_list(_load(folder / "discarded_tests.json", notes, "dropped integration tests", missing_ok=True))
    report = _load(folder / "test_report.json", notes, "integration test results", since, missing_ok=True)

    if generated and report is None:
        notes.append("The integration tests have no test_report.json, so they were not compiled or run in this run.")

    generated = [t for t in generated if isinstance(t, dict)]
    # Integration tests are numbered by their place in the list (IT_1, IT_2...).
    names = _test_names("IT", range(1, len(generated) + 1))

    # Kept and dropped tests carry no number, only their workflow name.
    by_workflow = {}
    for entry in kept + discarded:
        if isinstance(entry, dict):
            by_workflow.setdefault(entry.get("workflow_name"), []).append(entry)

    report_dict = _as_dict(report)
    dropped = {d.get("name"): d.get("reason", "") for d in _as_list(report_dict.get("dropped")) if isinstance(d, dict)}
    repaired = set(_as_list(report_dict.get("repaired")))
    failures = _failures(report)

    tests = []
    for name, entry in zip(names, generated):
        final = _take(by_workflow, entry.get("workflow_name"), entry)

        tests.append({
            "name": name,
            "kind": "integration",
            "title": entry.get("workflow_name", ""),
            "description": entry.get("workflow_description", ""),
            "rule_ids": _as_list(entry.get("rule_ids")),
            "status": _status(name, report, dropped, failures),
            "repaired": name in repaired,
            "reason": dropped.get(name) or final.get("reason", ""),
            "failed_methods": failures.get(name, []),
            "source_files": [],
            "imports": _as_list(final.get("imports")),
            "code": _readable_code(final.get("integration_test", "")),
        })

    return dict(_report_section(report, len(generated)), tests=tests)


def _take(groups, key, default):
    """The next saved copy of a test under this key; a repeated id or workflow uses them in order."""
    found = groups.get(key)
    if not found:
        return default
    return found.pop(0) if len(found) > 1 else found[0]


def _readable_code(code):
    """
    Older integration_tests.json files hold the code with its line breaks
    written out as a backslash and an "n"; turn those back into real ones.
    """
    code = str(code or "")
    if "\n" not in code and "\\n" in code:
        code = (code.replace("\\r\\n", "\n").replace("\\n", "\n")
                    .replace("\\t", "    ").replace('\\"', '"'))
    return code


def _link_tests_to_rules(rules, tests):
    by_id = {str(rule["id"]): rule for rule in rules}
    for test in tests:
        for rule_id in test["rule_ids"]:
            rule = by_id.get(str(rule_id))
            if rule is not None:
                rule["tests"].append(test["name"])


# ---------------------------------------------------------
# Key numbers
# ---------------------------------------------------------

def _counts(files, folders, rules, unit, integration):
    def by_status(items, status):
        return sum(item["status"] == status for item in items)

    return {
        "files": len(files),
        "folders": len(folders),
        "types": sum(len(item["types"]) for item in files),
        "rules": len(rules),
        "rules_proven": by_status(rules, "proven"),
        "rules_rejected": by_status(rules, "rejected"),
        "rules_error": by_status(rules, "error"),
        "rules_with_tests": sum(bool(rule["tests"]) for rule in rules),
        "unit_tests": len(unit["tests"]),
        "unit_passed": by_status(unit["tests"], "passed"),
        "unit_failed": by_status(unit["tests"], "failed"),
        "unit_dropped": by_status(unit["tests"], "dropped"),
        "integration_tests": len(integration["tests"]),
        "integration_passed": by_status(integration["tests"], "passed"),
        "integration_failed": by_status(integration["tests"], "failed"),
        "integration_dropped": by_status(integration["tests"], "dropped"),
    }
