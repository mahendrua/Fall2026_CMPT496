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

The screen never sends a path. It names a result (codebase, scope, check ID),
a finding ID and the index of one of that finding's artifacts; the reference
itself comes from the stored result, and files are only read inside the
selected codebase or that codebase's pipeline output folders.
"""

import json
from collections import Counter
from pathlib import Path

from pydantic import ValidationError

from agent.BR_input_loader import APP_DIR
from agent.consistency_refs import (
    RULES_DIR, SUMMARY_DIR, TESTS_DIR, _find_symbol, _read_list, refresh_ref,
)
from agent.consistency_store import (
    RESULT_NAMES, ConsistencyStoreError, load_check_result, output_dir, set_review,
)
from agent.structured_output.consistency_output import ReviewStatus
from backend.token_usage import RUN_LOG_NAME

# Longest review note kept.
MAX_NOTE_CHARS = 4000

# Most source lines returned around a cited range, and the context added.
MAX_SOURCE_LINES = 400
SOURCE_CONTEXT_LINES = 3

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
        run_relation    same_run, earlier_run, no_run or unknown
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
    else:
        relation = "same_run" if latest["run_id"] == result.run_id else "earlier_run"

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
