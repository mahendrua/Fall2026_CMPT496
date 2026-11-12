"""
backend/mutation_mapping.py

Turns Stryker.NET's JSON report into the numbers Checkpoint shows for
mutation testing: the overall score, per file, per generated test and per
business rule.

Stryker makes small changes to the code ("mutants": `a > b` becomes
`a >= b`) and runs the tests against each one. A mutant is killed when a
test fails, i.e. the tests noticed the change. Run with per-test coverage and
bail disabled, every mutant lists the tests that ran its code (coveredBy) and
every test that failed on it (killedBy). So a rule is scored by its own test,
UT_<rule id>, rather than by every test that touches the same file: in a
codebase with one real file, every rule points at the same file.

No files and no subprocesses here; backend/mutation_testing.py reads the
inputs and runs Stryker.
"""

import re

from backend.run_report_data import TEST_NAME


# Stryker's score is detected / (detected + undetected). Compile errors,
# runtime errors and ignored mutants are left out of both.
DETECTED = ("Killed", "Timeout")
UNDETECTED = ("Survived", "NoCoverage")

# The mutants Stryker ran tests against.
TESTED = ("Killed", "Survived", "Timeout")

STATUS_KEYS = {
    "Killed": "killed",
    "Survived": "survived",
    "NoCoverage": "no_coverage",
    "Timeout": "timeout",
    "CompileError": "compile_error",
    "RuntimeError": "runtime_error",
    "Ignored": "ignored",
    "Pending": "pending",
}

MAX_EXAMPLES = 5
MAX_CODE_CHARS = 120


def build(reports, codebase_root, rules, tests_used):
    """
    Map one or more Stryker JSON reports back to rules and tests.

    @param reports Stryker JSON reports, already loaded (one per Stryker run).
    @param codebase_root The codebase folder; file paths are made relative to it.
    @param rules [{"id": 4, "files": ["src/App/Cart.cs"]}]: each rule's code
           files, inside the codebase.
    @param tests_used [{"name": "UT_4", "rule_id": 4}]: the tests Stryker ran.
    @return {"overall", "by_file", "tests", "rules"}
    """
    used_names = [test["name"] for test in tests_used]

    mutants = []
    for report in reports:
        report = _as_dict(report)
        mutants += flatten(report, codebase_root, names_by_test_id(report), used_names)

    by_file = {}
    for mutant in mutants:
        by_file.setdefault(mutant["file"], []).append(mutant)

    tests = [
        dict(
            test,
            covered=sum(test["name"] in m["covered_by"] for m in mutants if m["status"] in TESTED),
            killed=sum(test["name"] in m["killed_by"] for m in mutants),
        )
        for test in tests_used
    ]

    return {
        "overall": counts(mutants) if mutants else None,
        "by_file": {path: counts(items) for path, items in sorted(by_file.items())},
        "tests": tests,
        "rules": [_rule(rule, mutants, tests_used) for rule in rules],
    }


def names_by_test_id(report):
    """
    {Stryker test id: Checkpoint test name}, e.g. {"8fd3...": "UT_4"}.

    Stryker files tests under the file they are in, and the test harness
    names each file after its test (UT_4.cs holds class UT_4), so the file
    name is the test name. A test in any other file falls back to the name
    inside its full method name, and is left out when neither has one.
    """
    names = {}

    for path, test_file in _as_dict(report.get("testFiles")).items():
        stem = re.split(r"[\\/]", str(path))[-1].rsplit(".", 1)[0]

        for test in _as_list(_as_dict(test_file).get("tests")):
            test = _as_dict(test)
            match = TEST_NAME.fullmatch(stem) or TEST_NAME.search(str(test.get("name", "")))
            if match and test.get("id") is not None:
                names[str(test["id"])] = match.group(1)

    return names


def flatten(report, codebase_root, names_by_id, used_names):
    """One dict per mutant, with its file inside the codebase and test names instead of ids."""
    mutants = []
    project_root = report.get("projectRoot") or ""

    for path, file in _as_dict(report.get("files")).items():
        file = _as_dict(file)
        rel = _relative(path, codebase_root, project_root)
        lines = str(file.get("source") or "").splitlines()

        for mutant in _as_list(file.get("mutants")):
            mutant = _as_dict(mutant)
            location = _as_dict(mutant.get("location"))

            killed = {names_by_id[i] for i in _as_list(mutant.get("killedBy")) if i in names_by_id}
            covered = {names_by_id[i] for i in _as_list(mutant.get("coveredBy")) if i in names_by_id}

            # A static mutant (a static field's starting value, say) runs once
            # when the class loads, so Stryker cannot tell which test reached
            # it and runs every test against it; its coveredBy comes back empty.
            if mutant.get("static"):
                covered |= set(used_names)

            line = _as_dict(location.get("start")).get("line")

            mutants.append({
                "id": str(mutant.get("id", "")),
                "file": rel,
                "line": line,
                # The whole line, so "true -> false" can be shown in context.
                "line_text": _short(lines[line - 1]) if isinstance(line, int) and 1 <= line <= len(lines) else "",
                "mutator": mutant.get("mutatorName", ""),
                "original": _code(lines, location),
                "mutated": _short(mutant.get("replacement", "")),
                "status": mutant.get("status", ""),
                "static": bool(mutant.get("static")),
                "covered_by": sorted(covered | killed),
                "killed_by": sorted(killed),
            })

    return mutants


def counts(mutants):
    """Mutants by status, plus Stryker's score (None when nothing could be scored)."""
    result = {key: 0 for key in STATUS_KEYS.values()}

    for mutant in mutants:
        key = STATUS_KEYS.get(mutant["status"])
        if key:
            result[key] += 1

    detected = sum(result[STATUS_KEYS[s]] for s in DETECTED)
    scored = detected + sum(result[STATUS_KEYS[s]] for s in UNDETECTED)

    result.update(
        total=len(mutants),
        scored=scored,
        detected=detected,
        score=round(100 * detected / scored, 1) if scored else None,
    )
    return result


def own_test_counts(mutants, own):
    """
    What a rule's own tests did with the mutants they ran.

    covered: mutants whose code the tests ran; caught: they failed on it;
    caught_by_other_tests_only: another test noticed, these did not;
    survived: no test noticed; timeout: the code hung (Stryker cannot say
    which test was running, so these are counted on their own).
    """
    own = set(own)
    covered = [m for m in mutants if m["status"] in TESTED and own & set(m["covered_by"])]
    caught = [m for m in covered if own & set(m["killed_by"])]

    return {
        "covered": len(covered),
        "caught": len(caught),
        "caught_by_other_tests_only": sum(
            m["status"] == "Killed" and not own & set(m["killed_by"]) for m in covered
        ),
        "survived": sum(m["status"] == "Survived" for m in covered),
        "timeout": sum(m["status"] == "Timeout" for m in covered),
    }


def _rule(rule, mutants, tests_used):
    own = [t["name"] for t in tests_used if str(t.get("rule_id")) == str(rule["id"])]
    files = {path.lower() for path in rule.get("files") or []}
    in_files = [m for m in mutants if m["file"].lower() in files]

    # The most useful examples are changes the rule's own test ran without
    # noticing. With no test of its own, the rule's files are all there is.
    if own:
        candidates = [m for m in mutants if m["status"] == "Survived" and set(own) & set(m["covered_by"])]
    else:
        candidates = [m for m in in_files if m["status"] in UNDETECTED]
    candidates.sort(key=lambda m: (m["file"], m["line"] or 0))

    return {
        "id": rule["id"],
        "tests": own,
        "files": list(rule.get("files") or []),
        "own_tests": own_test_counts(mutants, own) if own else None,
        "in_rule_files": counts(in_files) if in_files else None,
        "surviving": len(candidates),
        "surviving_examples": [_example(m) for m in candidates[:MAX_EXAMPLES]],
    }


def _example(mutant):
    return {key: mutant[key] for key in ("file", "line", "line_text", "mutator", "original", "mutated", "status")}


# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------

def _relative(path, codebase_root, project_root):
    """A file path from Stryker, made relative to the codebase with / separators."""
    path = str(path).replace("\\", "/")

    # Stryker 5 writes full paths; relative ones would be inside its project.
    if not re.match(r"^([A-Za-z]:)?/", path) and project_root:
        path = str(project_root).replace("\\", "/").rstrip("/") + "/" + path

    root = str(codebase_root).replace("\\", "/").rstrip("/")
    if path.lower().startswith(root.lower() + "/"):
        return path[len(root) + 1:]
    return path


def _code(lines, location):
    """The original code a mutant replaced, from the file's source (lines and columns start at 1)."""
    start = _as_dict(location.get("start"))
    end = _as_dict(location.get("end"))

    try:
        first, first_col = int(start["line"]), int(start["column"])
        last, last_col = int(end["line"]), int(end["column"])
    except (KeyError, TypeError, ValueError):
        return ""

    if not 1 <= first <= last <= len(lines):
        return ""

    if first == last:
        text = lines[first - 1][first_col - 1:last_col - 1]
    else:
        text = " ".join(
            [lines[first - 1][first_col - 1:]] + lines[first:last - 1] + [lines[last - 1][:last_col - 1]]
        )

    return _short(text)


def _short(text):
    text = " ".join(str(text or "").split())
    return text if len(text) <= MAX_CODE_CHARS else text[:MAX_CODE_CHARS - 3] + "..."


def _as_list(value):
    return value if isinstance(value, list) else []


def _as_dict(value):
    return value if isinstance(value, dict) else {}
