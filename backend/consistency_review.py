"""
backend/consistency_review.py

What the Consistency Review screen reads and writes. No model is created or
called here: saved results are read, review decisions are saved, and an
artifact's current content can be looked up for comparison with the
evidence stored in the result.

    load_for_review()   a saved full or selected check, with review counts and
                        how it relates to the most recent logged run
    save_review()       a review decision for one finding of the result being
                        reviewed (agent/consistency_store.set_review)
    read_artifact()     the current state and content of one artifact a finding
                        refers to, compared with the stored reference
    list_targets()      source files that have generated artifacts to check
    list_methods()      methods of one file that can be checked on their own
    prepare_check()     validates a request to run a check now (no model yet)
    check_summary()     what the screen needs to know about a check just run
    snapshot_outputs()  the pipeline's record of the outputs that existed before it ran
    review_pipeline_outputs()
                        the full pipeline's Consistency Review stage: checks only
                        the outputs that run wrote

The screen never sends a path. It names a result (codebase, scope, check ID),
a finding ID and the index of one of that finding's artifacts; the reference
itself comes from the stored result, and files are only read inside the
selected codebase or that codebase's pipeline output folders.
"""

import json
import re
import shutil
import tempfile
from collections import Counter
from pathlib import Path

from pydantic import ValidationError

from agent.BR_input_loader import APP_DIR, _is_abs, _normalize_root, normalize_rule_path
from agent.consistency_refs import (
    RULES_DIR, SUMMARY_DIR, TESTS_DIR, _find_symbol, _read_list, refresh_ref,
)
from agent.consistency_store import (
    RESULT_NAMES, ConsistencyStoreError, load_check_result, output_dir, save_check_result, set_review,
)
from agent.structured_output.consistency_output import ReviewStatus
from backend.token_usage import RUN_LOG_NAME

# Longest review note kept.
MAX_NOTE_CHARS = 4000

# Most source lines returned around a cited range, and the context added.
MAX_SOURCE_LINES = 400
SOURCE_CONTEXT_LINES = 3

# Most targets one selected check may name.
MAX_TARGETS = 100

# Largest source file whose methods are listed for selection.
MAX_METHOD_FILE_BYTES = 2_000_000

# A method or type name as the checker takes it: "Type.Method" or "Method".
SYMBOL = re.compile(r"^[A-Za-z_$][\w$]*(\.[A-Za-z_$][\w$]*)*$")

# Fields older saved results may lack; their absence is reported, not hidden.
LATER_FIELDS = ("selection", "undetermined", "assessments", "skipped")

REVIEW_STATUSES = ReviewStatus.__args__


class ReviewRequestError(ValueError):
    """A review request that cannot be carried out; the message says why."""


def codebase_name(codebase):
    """The codebase folder name, validated as the store does."""
    name = Path(str(codebase or "")).name
    output_dir(name)                     # raises ConsistencyStoreError for unusable names
    return name


def load_for_review(codebase, scope="full", app_dir=APP_DIR):
    """
    @brief The saved check of one scope, ready for the review screen.
    @return dict with:
        available       False if no result could be read (see problem/error)
        problem         "unreadable" for a corrupt or invalid file
        result          the ConsistencyCheckResult as JSON (reviews applied)
        outcome         no_findings, findings, incomplete, failed or not_run
        review_counts   findings per review status
        available_scopes  scopes that have a saved result
        missing_fields  newer result fields absent from an older saved file
        latest_run      the most recent logged run of this codebase, if known
        run_relation    same_run, earlier_run, later_run (run on its own after
                        the latest logged run, e.g. a check that made no
                        model call), no_run or unknown
    """
    name = codebase_name(codebase)
    if scope not in RESULT_NAMES:
        raise ReviewRequestError(f"Unknown result scope: {scope!r}")

    folder = output_dir(name, app_dir)
    base = dict(
        codebase=name,
        scope=scope,
        available_scopes=[s for s, file in RESULT_NAMES.items() if (folder / file).is_file()],
        result_file=(Path("agent") / "consistency_output" / name / RESULT_NAMES[scope]).as_posix(),
    )

    try:
        result = load_check_result(name, app_dir, scope=scope)
    except ConsistencyStoreError as e:
        return dict(base, available=False, problem="unreadable", error=str(e))

    raw_keys = _raw_keys(folder / RESULT_NAMES[scope])
    latest = _latest_run(name, app_dir)

    if result.status == "not_run":
        relation = "none"
    elif result.run_id is None:
        relation = "no_run"
    elif latest is None:
        relation = "unknown"
    elif latest["run_id"] == result.run_id:
        relation = "same_run"
    elif result.started_at and latest.get("started_at") and result.started_at > latest["started_at"]:
        # Runs that make no model call are not logged, so a check can be newer
        # than the latest logged run without being part of it. Times are to
        # the second, so a tie is read as earlier: it cannot show which came first.
        relation = "later_run"
    else:
        relation = "earlier_run"

    return dict(
        base,
        available=result.status != "not_run",
        result=result.model_dump(mode="json"),
        outcome=result.outcome,
        review_counts=dict(Counter(f.review.status for f in result.findings)),
        missing_fields=[f for f in LATER_FIELDS if raw_keys is not None and f not in raw_keys],
        latest_run=latest,
        run_relation=relation,
    )


def save_review(codebase, scope, check_id, finding_id, status, note=None, app_dir=APP_DIR):
    """
    @brief Saves a review decision for a finding of the result being reviewed.
    @details The finding must belong to the given scope's current saved check
    (check_id guards against a result replaced since the screen loaded it).
    Only the review is written: findings, verdicts and artifacts are untouched.
    """
    result = _result(codebase, scope, check_id, app_dir)
    finding = _finding(result, finding_id)
    if status not in REVIEW_STATUSES:
        raise ReviewRequestError(f"Unknown review status: {status!r}")
    if note is not None and not isinstance(note, str):
        raise ReviewRequestError("A review note must be text.")
    if note and len(note) > MAX_NOTE_CHARS:
        raise ReviewRequestError(f"The note is {len(note):,} characters; the limit is {MAX_NOTE_CHARS:,}.")

    try:
        review = set_review(result.codebase, finding.id, status, note, app_dir=app_dir)
    except ConsistencyStoreError as e:
        raise ReviewRequestError(str(e)) from e

    saved = load_check_result(result.codebase, app_dir, scope=scope)
    return dict(
        finding_id=finding.id,
        review=review.model_dump(mode="json"),
        review_counts=dict(Counter(f.review.status for f in saved.findings)),
    )


def read_artifact(codebase, scope, check_id, finding_id, artifact_index, app_dir=APP_DIR):
    """
    @brief The current state of one artifact a finding refers to.
    @return dict with state ("unchanged", "changed", "missing", "unavailable"),
            the stored and current reference, and the current content when it
            can be read. The finding's stored excerpts remain the evidence the
            check used; this is only for comparison.
    """
    result = _result(codebase, scope, check_id, app_dir)
    finding = _finding(result, finding_id)
    if not isinstance(artifact_index, int) or not 0 <= artifact_index < len(finding.artifacts):
        raise ReviewRequestError(f"Finding {finding.id} has no artifact {artifact_index!r}.")

    root = Path(str(codebase))
    if not root.is_dir():
        raise ReviewRequestError(f"The codebase folder {root} is not available, so current content cannot be read.")

    stored = finding.artifacts[artifact_index]
    current = refresh_ref(stored, root, app_dir)

    if current.status == "missing":
        state = "missing"
    elif current.status == "stale" and current.content_hash and current.content_hash != stored.content_hash:
        state = "changed"
    elif current.status == "resolved":
        state = "unchanged"
    else:
        state = "unavailable"

    content, lines = None, None
    if state in ("unchanged", "changed"):
        content, lines = _content(stored, current, root, result.codebase, app_dir)

    return dict(
        finding_id=finding.id,
        artifact_index=artifact_index,
        state=state,
        stored=stored.model_dump(mode="json"),
        current=current.model_dump(mode="json"),
        content=content,
        lines=lines,
    )


def list_targets(codebase, app_dir=APP_DIR):
    """
    @brief The source files a check would cover, with what was generated for each.
    @details The same files a whole-codebase check covers (the checker's own
    discovery: files named by a summary, rule or test, inside the codebase and
    outside the folders the crawler skips). Counts are plain numbers; no model
    is created and no source file is read.
    @return dict with codebase, files [{path, summary, rules, tests,
            methods_supported}], outputs {summaries, rules, tests} and notes.
    """
    from agent.consistency_checker import ConsistencyChecker, _Collected, _entry_files
    from agent.consistency_refs import map_codebase_paths, summary_ref
    from utils.tree_parse import LANGUAGE_DICT

    root = _existing_root(codebase)
    name = root.name
    app_dir = Path(app_dir)
    found = _Collected()
    files = ConsistencyChecker(app_dir=app_dir)._all_targets(_normalize_root(root), found)

    rules, _ = _read_list(app_dir / RULES_DIR / name / "validated_rules.json")
    tests_dir = app_dir / TESTS_DIR / name
    tests_file = tests_dir / "validated_tests.json"
    if not tests_file.is_file():
        tests_file = tests_dir / "unit_tests.json"
    tests, _ = _read_list(tests_file)
    test_ids = {t.get("id") for t in tests or [] if isinstance(t, dict)}

    rules_by_file = {}
    for rule in rules or []:
        if isinstance(rule, dict):
            for path in map_codebase_paths(_entry_files(rule), root)[0]:
                rules_by_file.setdefault(path, []).append(rule.get("id"))

    listed = []
    for target in files:
        ids = rules_by_file.get(target.path, [])
        listed.append(dict(
            path=target.path,
            summary=summary_ref(root, target.path, app_dir=app_dir).status,
            rules=len(ids),
            tests=sum(1 for i in ids if i in test_ids),
            methods_supported=Path(target.path).suffix in LANGUAGE_DICT,
        ))

    return dict(
        codebase=name,
        files=listed,
        outputs=dict(
            summaries=(app_dir / SUMMARY_DIR / name).is_dir(),
            rules=rules is not None,
            tests=tests is not None,
        ),
        notes=list(found.notes),
    )


def list_methods(codebase, path, app_dir=APP_DIR):
    """
    @brief Methods of one source file that a check can be limited to.
    @details Names come from the same tree-sitter queries the checker uses to
    find a method. A name shared by several methods (overloads) is listed as
    overloaded: the checker would report it as ambiguous, so check the whole
    file instead.
    """
    from agent.consistency_checker import _method_chunks

    root, targets = prepare_check(codebase, "selected", [path])
    rel = targets[0]
    file = (root / rel).resolve()
    if not file.is_relative_to(root.resolve()) or not file.is_file():
        raise ReviewRequestError(f"{rel} does not exist in the codebase.")
    if file.stat().st_size > MAX_METHOD_FILE_BYTES:
        return dict(path=rel, supported=False, methods=[], reason="The file is too large to list its methods.")

    chunks = _method_chunks(file.read_bytes(), rel)
    if chunks is None:
        return dict(path=rel, supported=False, methods=[],
                    reason="Methods cannot be listed for this kind of file; check the whole file instead.")

    def symbol(chunk):
        container = chunk.get("container")
        return f"{container}.{chunk['name']}" if container and container != "Global" else chunk["name"]

    names = Counter(symbol(c) for c in chunks)
    methods = [
        dict(symbol=symbol(c), line_start=c["start_line"], line_end=c["end_line"], overloaded=names[symbol(c)] > 1)
        for c in chunks
    ]
    return dict(path=rel, supported=True, methods=methods)


def _existing_root(codebase):
    root = Path(str(codebase or ""))
    codebase_name(root)
    if not root.is_dir():
        raise ReviewRequestError(f"The codebase folder {root} does not exist.")
    return root


def prepare_check(codebase, scope, targets):
    """
    @brief Validates a request to run a check now, before any run is logged or model created.
    @param codebase The codebase folder, as every command receives it.
    @param scope "full" (no targets) or "selected" (one or more targets).
    @param targets Source paths, or {"path", "symbol"} dicts, inside the codebase.
    @return (root Path, targets for run_consistency_check, or None for a full check)
    @raises ReviewRequestError For anything that cannot be checked as asked.
    """
    root = _existing_root(codebase)
    if scope not in RESULT_NAMES:
        raise ReviewRequestError(f"Unknown check scope: {scope!r}")

    targets = targets or []
    if not isinstance(targets, list):
        raise ReviewRequestError("Targets must be a list of files or {path, symbol} entries.")
    if scope == "full":
        if targets:
            raise ReviewRequestError("A whole-codebase check takes no selected files.")
        return root, None
    if not targets:
        raise ReviewRequestError("Select at least one file to check.")
    if len(targets) > MAX_TARGETS:
        raise ReviewRequestError(f"{len(targets)} selections is more than the {MAX_TARGETS} one check can take.")

    norm_root = _normalize_root(root)
    checked = []
    for raw in targets:
        if isinstance(raw, str):
            path, symbol = raw, None
        elif isinstance(raw, dict) and set(raw) <= {"path", "symbol"} and isinstance(raw.get("path"), str):
            path, symbol = raw["path"], raw.get("symbol")
        else:
            raise ReviewRequestError(f"Not a valid selection: {raw!r}")

        if not path.strip():
            raise ReviewRequestError("A selected file has no path.")
        rel = normalize_rule_path(path, norm_root)
        if _is_abs(rel) or rel in (".", "..") or rel.startswith("../"):
            raise ReviewRequestError(f"{path} is not inside the codebase {root.name}.")

        if symbol is not None:
            if not isinstance(symbol, str) or not symbol.strip():
                symbol = None
            elif not SYMBOL.match(symbol.strip()):
                raise ReviewRequestError(f"{symbol!r} is not a method name such as Type.Method.")
            else:
                symbol = symbol.strip()

        target = {"path": rel, "symbol": symbol} if symbol else rel
        if target not in checked:
            checked.append(target)
    return root, checked


# The checker's note for outputs whose run is unknown; a pipeline stage knows.
UNKNOWN_RUN_NOTE = "the pipeline does not record which run wrote them."


def snapshot_outputs(codebase_name, app_dir=APP_DIR):
    """
    @brief Size and modification time of every file the checker could read, before a pipeline run.
    @details The pipeline's outputs carry no run identity, and file summaries
    are not cleared between runs, so this is how the review stage tells the
    files a run wrote from leftovers of earlier runs.
    @return {path relative to app_dir: (mtime_ns, size)}
    """
    app_dir = Path(app_dir)
    snapshot = {}
    for folder in (SUMMARY_DIR, RULES_DIR, TESTS_DIR):
        base = app_dir / folder / codebase_name
        for file in base.rglob("*") if base.is_dir() else []:
            if file.is_file():
                stat = file.stat()
                snapshot[file.relative_to(app_dir).as_posix()] = (stat.st_mtime_ns, stat.st_size)
    return snapshot


def review_pipeline_outputs(codebase, app_dir, before, run_check):
    """
    @brief The full pipeline's Consistency Review stage.
    @details Checks the whole codebase against only the outputs written or
    changed since before was taken: they are copied into a temporary folder
    with the same layout and the checker reads that, so a leftover from an
    earlier run is never compared as if this run produced it. Leftovers are
    listed in the result. The result is saved as the codebase's whole-project
    check (selected-files results and reviews are untouched).
    @param run_check agent.consistency_checker.run_consistency_check, or a stand-in.
    @return check_summary() of the saved result, plus the leftover count.
    """
    root = Path(str(codebase))
    name = root.name
    app_dir = Path(app_dir)
    after = snapshot_outputs(name, app_dir)
    fresh = sorted(path for path, state in after.items() if before.get(path) != state)
    leftovers = sorted(path for path in after if path not in fresh)

    with tempfile.TemporaryDirectory(prefix="checkpoint_review_") as view:
        for rel in fresh:
            target = Path(view) / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(app_dir / rel, target)
        result = run_check(root, targets=None, app_dir=view, save=False)

    result.notes = [n for n in result.notes if not n.endswith(UNKNOWN_RUN_NOTE)]
    result.notes.append(
        f"Compared only the outputs this pipeline run wrote ({len(fresh)} file{'s' if len(fresh) != 1 else ''})."
    )
    if leftovers:
        shown = ", ".join(leftovers[:10]) + (f", and {len(leftovers) - 10} more" if len(leftovers) > 10 else "")
        result.notes.append(
            f"{len(leftovers)} output file{'s' if len(leftovers) != 1 else ''} left over from an earlier run "
            f"{'were' if len(leftovers) != 1 else 'was'} not written by this run and not compared: {shown}."
        )
    save_check_result(result, app_dir)

    summary = check_summary(result)
    summary.update(current_run_outputs=len(fresh), leftover_outputs=len(leftovers))
    return summary


def check_summary(result):
    """
    @brief What the screen needs about a check that has just run.
    @details check_id and scope identify the exact saved result to load.
    A warning is set for anything short of a completed check, as test steps
    do for the Complete screen.
    """
    findings = len(result.findings)
    if result.status == "completed":
        message = (f"Consistency check completed: {findings} mismatch{'es' if findings != 1 else ''} found."
                   if findings else "Consistency check completed: no mismatches found in the assessed artifacts.")
    elif result.status == "partial":
        message = (f"Consistency check incomplete: {findings} mismatch{'es' if findings != 1 else ''} found, "
                   "but some artifacts were not compared.")
    else:
        message = "Consistency check failed: no conclusion can be drawn."

    summary = dict(
        message=message,
        codebase=result.codebase,
        scope=result.scope,
        selection=list(result.selection),
        check_id=result.check_id,
        run_id=result.run_id,
        status=result.status,
        outcome=result.outcome,
        findings=findings,
        missing_artifacts=len(result.missing_artifacts),
        unresolved_links=len(result.unresolved_links),
        skipped=len(result.skipped),
        undetermined=len(result.undetermined),
        errors=list(result.errors),
    )
    if result.status != "completed":
        first = result.errors[0] if result.errors else "Some artifacts were missing, unresolved or undetermined."
        summary["warning"] = f"{message} {first}"
    return summary


# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------

def _result(codebase, scope, check_id, app_dir):
    name = codebase_name(codebase)
    if scope not in RESULT_NAMES:
        raise ReviewRequestError(f"Unknown result scope: {scope!r}")
    try:
        result = load_check_result(name, app_dir, scope=scope)
    except ConsistencyStoreError as e:
        raise ReviewRequestError(f"The saved result cannot be read: {e}") from e
    if result.status == "not_run":
        raise ReviewRequestError("There is no saved check to review.")
    if result.check_id != check_id:
        raise ReviewRequestError(
            "The saved check has changed since this screen loaded it. Reload the result before reviewing."
        )
    return result


def _finding(result, finding_id):
    for finding in result.findings:
        if finding.id == finding_id:
            return finding
    raise ReviewRequestError(f"The check being reviewed has no finding {finding_id!r}.")


def _raw_keys(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return set(data) if isinstance(data, dict) else None


def _latest_run(name, app_dir):
    """The last logged run, if it was a run of this codebase."""
    try:
        data = json.loads((Path(app_dir) / RUN_LOG_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("codebase") != name or not data.get("run_id"):
        return None
    return {key: data.get(key) for key in ("run_id", "command", "status", "started_at", "finished_at")}


def _output_file(app_dir, relative, name):
    """A generated artifact file, only inside this codebase's pipeline output folders."""
    allowed = [(Path(app_dir) / folder / name).resolve() for folder in (SUMMARY_DIR, RULES_DIR, TESTS_DIR)]
    path = (Path(app_dir) / str(relative or "")).resolve()
    if not any(path.is_relative_to(folder) for folder in allowed) or not path.is_file():
        raise ReviewRequestError(f"{relative} is not a pipeline output of {name}.")
    return path


def _content(stored, current, root, name, app_dir):
    from agent.consistency_checker import _render_member, _render_rule, _render_summary, _render_test

    if current.kind == "source":
        path = (root / current.path).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            raise ReviewRequestError(f"{current.path} is not inside the codebase.")
        text = path.read_text(encoding="utf-8", errors="replace").splitlines()
        first = max(1, (stored.line_start or 1) - SOURCE_CONTEXT_LINES)
        last = min(len(text), (stored.line_end or stored.line_start or len(text)) + SOURCE_CONTEXT_LINES)
        last = min(last, first + MAX_SOURCE_LINES - 1)
        return "\n".join(text[first - 1:last]), [first, last]

    path = _output_file(app_dir, current.artifact_file, name)
    if current.kind == "summary":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None, None
        if current.symbol:
            members = _find_symbol(data, current.symbol)
            return (_render_member(current.symbol, members[0][1]) if len(members) == 1 else None), None
        return _render_summary(data), None

    entries, _ = _read_list(path)
    for entry in entries or []:
        if isinstance(entry, dict) and entry.get("id") == current.rule_id:
            return (_render_rule(entry) if current.kind == "business_rule" else _render_test(entry)), None
    return None, None
