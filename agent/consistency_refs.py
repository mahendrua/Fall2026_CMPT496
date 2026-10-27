"""
@file consistency_refs.py
@brief Builds Consistency Review artifact references from the existing pipeline outputs.
@details Each builder looks up one artifact the way the pipeline wrote it and
returns an ArtifactRef. Lookups never fall back to a similar-looking artifact:
if nothing matches, the reference is "missing"; if several match, it is
"ambiguous" and lists them; if it was provably generated from other content,
it is "stale".

    source file     the file itself, under the codebase root
    summary         agent/file_summary_agent_output/<codebase>/*.json, matched
                    by the "path" field inside each file (the file names
                    <stem>__<n>.json depend on crawl order and are not stable)
    business rule   agent/BR_agent_output/<codebase>/validated_rules.json, by id
    unit test       agent/UT_agent_output/<codebase>/validated_tests.json, by
                    rule id; the test class is UT_<id> (agent/test_harness.py)

The app has no store of human-confirmed requirements, so there is no builder
for them; generated business rules are never treated as requirements.

A source file modified after an artifact was written only adds a caveat to the
reference: modification times do not prove the content changed. "stale" is kept
for proven changes: a content hash that differs (refresh_ref) or a test generated
for a different rule.
"""

import hashlib
import json
import os
import posixpath
from pathlib import Path

from pydantic import ValidationError

from agent.BR_input_loader import APP_DIR, _DRIVE_RE, _normalize_root, _is_abs, _strip_prefix, normalize_rule_path
from agent.structured_output.BR_output import ValidatedRule
from agent.structured_output.consistency_output import ArtifactRef
from agent.structured_output.UT_output import UnitTest


SUMMARY_DIR = Path("agent") / "file_summary_agent_output"
RULES_DIR = Path("agent") / "BR_agent_output"
TESTS_DIR = Path("agent") / "UT_agent_output"


def content_hash(data):
    """Short SHA-256 of bytes, or of a JSON value in a stable form."""
    if not isinstance(data, bytes):
        data = json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(data).hexdigest()[:16]


def _root(codebase_root):
    return codebase_root if isinstance(codebase_root, dict) else _normalize_root(codebase_root)


def map_codebase_paths(paths, codebase_root):
    """
    @brief Maps artifact file paths to POSIX paths relative to the codebase root.
    @details Uses normalize_rule_path(), so absolute paths under the root,
    absolute paths from another machine or folder that contain the codebase
    folder name, Windows separators and paths prefixed with the codebase name
    all map to the same relative path. A path is not mapped (and nothing is
    guessed) when it is outside the codebase, when the codebase folder name
    occurs more than once in a path from elsewhere, or when no such file
    exists: a bare file name is never matched to a file elsewhere in the tree.
    @return (mapped, unmapped): mapped relative paths without duplicates, and
            (original path, reason) for each path that could not be mapped.
    """
    root = _root(codebase_root)
    mapped, unmapped = [], []

    for raw in paths:
        rel = normalize_rule_path(raw, root)
        if _is_abs(rel) or rel in (".", "..") or rel.startswith("../"):
            unmapped.append((raw, "outside the codebase"))
        elif _repeats_codebase_name(raw, root):
            unmapped.append((raw, f"the folder name {root['name']!r} occurs more than once, so the mapping is ambiguous"))
        elif not (Path(root["abs"]) / rel).is_file():
            unmapped.append((raw, f"no file {rel} exists in the codebase"))
        elif rel not in mapped:
            mapped.append(rel)
    return mapped, unmapped


def map_codebase_directory(directory, codebase_root):
    """A directory as a POSIX path relative to the root ('.' for the root), or None if outside it."""
    if directory is None:
        return None
    root = _root(codebase_root)
    rel = normalize_rule_path(directory, root)
    if _is_abs(rel) or rel == ".." or rel.startswith("../") or _repeats_codebase_name(directory, root):
        return None
    return rel


def portable_hash(data, codebase_root):
    """
    @brief Content hash of a summary, rule or test entry with its paths mapped to the codebase.
    @details The same artifact written with absolute or relative paths, or on
    another machine, gets the same hash; any other change gives a new one.
    """
    root = _root(codebase_root)
    if not isinstance(data, dict):
        return content_hash(data)

    def norm(path):
        return normalize_rule_path(path, root) if isinstance(path, str) else path

    data = dict(data)
    if isinstance(data.get("path"), str):
        data["path"] = norm(data["path"])
    if isinstance(data.get("source_directory"), str):
        data["source_directory"] = norm(data["source_directory"])
    if isinstance(data.get("source_file_paths"), list):
        data["source_file_paths"] = [norm(p) for p in data["source_file_paths"]]
    if isinstance(data.get("provenance"), list):
        data["provenance"] = [
            dict(record, source_file_paths=[norm(p) for p in record.get("source_file_paths") or []])
            if isinstance(record, dict) else record
            for record in data["provenance"]
        ]
    return content_hash(data)


def _repeats_codebase_name(raw, root):
    """True for a path from outside the root that names the codebase folder more than once."""
    path = posixpath.normpath(str(raw).strip().replace("\\", "/"))
    if not _is_abs(path) or _strip_prefix(path, root["abs"]) is not None:
        return False
    ignore_case = bool(_DRIVE_RE.match(path))
    name = root["name"].casefold() if ignore_case else root["name"]
    parts = [part.casefold() if ignore_case else part for part in path.split("/")[1:]]
    return parts.count(name) > 1


def _located(paths, root):
    """(mapped paths, note or None) for an artifact's source files."""
    mapped, unmapped = map_codebase_paths(paths, root)
    if not unmapped:
        return mapped, None
    return mapped, "Source path(s) not mapped to the codebase: " + "; ".join(
        f"{raw} ({reason})" for raw, reason in unmapped
    ) + "."


def source_ref(codebase_root, path, symbol=None, line_start=None, line_end=None):
    """
    @brief Reference to a source file (and optionally a method or line range in it).
    @param path Any path form the pipeline writes; normalized against the root.
    @return Resolved with the file's hash, or missing if the file is gone, outside
            the codebase, or shorter than the line range.
    """
    root = _normalize_root(codebase_root)
    rel = normalize_rule_path(path, root)
    ref = dict(kind="source", path=rel, symbol=symbol, line_start=line_start, line_end=line_end)

    if _is_abs(rel) or rel == "." or rel.startswith("../"):
        return ArtifactRef(**ref, status="missing", detail=f"{path} is not inside the codebase {root['name']}.")

    file = Path(root["abs"]) / rel
    if not file.is_file():
        return ArtifactRef(**ref, status="missing", detail=f"Source file {rel} does not exist.")

    data = file.read_bytes()
    last = line_end or line_start
    if last and last > len(data.splitlines()):
        return ArtifactRef(
            **ref, status="missing", content_hash=content_hash(data),
            detail=f"{rel} has fewer than {last} lines; the line range no longer exists.",
        )

    return ArtifactRef(**ref, content_hash=content_hash(data))


def summary_ref(codebase_root, source_path, symbol=None, app_dir=APP_DIR):
    """
    @brief Reference to the file summary of one source file, or one method in it.
    @param symbol "Type.Method" or "Method". A bare name matches top-level
           functions and methods of any type in that file only.
    """
    root = _normalize_root(codebase_root)
    rel = normalize_rule_path(source_path, root)
    summary_dir = Path(app_dir) / SUMMARY_DIR / root["name"]
    ref = dict(kind="summary", path=rel, symbol=symbol)

    if not summary_dir.is_dir():
        return ArtifactRef(**ref, status="missing", detail=f"No file summaries exist for {root['name']}.")

    matches, unreadable = [], 0
    for file in sorted(summary_dir.glob("*.json")):
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            unreadable += 1
            continue
        if not isinstance(data, dict) or not isinstance(data.get("path"), str) or "summary" not in data:
            continue
        if normalize_rule_path(data["path"], root) == rel:
            matches.append((file, data))

    if not matches:
        detail = f"No file summary describes {rel}."
        if unreadable:
            detail += f" {unreadable} summary file(s) could not be read."
        return ArtifactRef(**ref, status="missing", detail=detail)

    if len(matches) > 1:
        return ArtifactRef(
            **ref, status="ambiguous",
            candidates=[_app_relative(file, app_dir) for file, _ in matches],
            detail=f"{len(matches)} file summaries describe {rel}.",
        )

    file, data = matches[0]
    ref["artifact_file"] = _app_relative(file, app_dir)

    target = data
    if symbol:
        found = _find_symbol(data, symbol)
        if not found:
            return ArtifactRef(**ref, status="missing", detail=f"The summary of {rel} does not describe {symbol}.")
        if len(found) > 1:
            return ArtifactRef(
                **ref, status="ambiguous", candidates=[label for label, _ in found],
                detail=f"The summary of {rel} describes {len(found)} members named {symbol}.",
            )
        target = found[0][1]

    ref["content_hash"] = portable_hash(target, root)
    return _mark_if_older(ArtifactRef(**ref), file, root, [rel])


def business_rule_ref(codebase_root, rule_id, app_dir=APP_DIR):
    """
    @brief Reference to one validated business rule, keeping its provenance.
    @details A rule found only in discarded_rules.json is missing (with the
             discard reason), since nothing downstream was generated from it.
    """
    root = _normalize_root(codebase_root)
    rules_dir = Path(app_dir) / RULES_DIR / root["name"]
    rules_file = rules_dir / "validated_rules.json"
    ref = dict(kind="business_rule", rule_id=rule_id, artifact_file=_app_relative(rules_file, app_dir))

    entries, problem = _read_list(rules_file)
    if problem:
        return ArtifactRef(**ref, status="missing" if entries is None else "unverified", detail=problem)

    matches = [entry for entry in entries if isinstance(entry, dict) and entry.get("id") == rule_id]

    if not matches:
        detail = f"No validated business rule has ID {rule_id}."
        discarded, _ = _read_list(rules_dir / "discarded_rules.json")
        for entry in discarded or []:
            if isinstance(entry, dict) and entry.get("id") == rule_id:
                detail = f"Business rule {rule_id} was discarded during validation: {entry.get('reason', 'no reason given')}"
                break
        return ArtifactRef(**ref, status="missing", detail=detail)

    if len(matches) > 1:
        return ArtifactRef(
            **ref, status="ambiguous",
            candidates=[entry.get("rule", "") or f"entry {i + 1}" for i, entry in enumerate(matches)],
            detail=f"{len(matches)} validated business rules have ID {rule_id}.",
        )

    try:
        rule = ValidatedRule.model_validate(matches[0])
    except ValidationError as e:
        return ArtifactRef(**ref, status="unverified", detail=f"Business rule {rule_id} does not match its schema: {e.errors()[0]['msg']}")

    raw_files = list(dict.fromkeys(
        [*rule.source_file_paths, *(p for record in rule.provenance for p in record.source_file_paths)]
    ))
    files, unmapped = _located(raw_files, root)
    notes = [unmapped] if unmapped else []
    if not raw_files:
        notes.append("Folder-level rule: no source files are recorded for it.")
    unlocated = bool(raw_files) and not files
    built = ArtifactRef(
        **ref,
        status="unverified" if unlocated else "resolved",
        path=files[0] if len(files) == 1 else None,
        directory=map_codebase_directory(rule.source_directory, root),
        provenance=rule.provenance,
        content_hash=portable_hash(matches[0], root),
        detail=" ".join(notes) or None,
    )
    return _mark_if_older(built, rules_file, root, files)


def unit_test_ref(codebase_root, rule_id, app_dir=APP_DIR):
    """
    @brief Reference to the generated unit test for one validated rule.
    @details Uses validated_tests.json (the tests as finally compiled) and
             falls back to unit_tests.json (as generated, never built) only when
             no validated output exists. Rule IDs are renumbered on every
             business rule validation, so a test whose rule text no longer
             matches the current rule with that ID is stale.
    """
    root = _normalize_root(codebase_root)
    tests_dir = Path(app_dir) / TESTS_DIR / root["name"]
    validated = tests_dir / "validated_tests.json"
    tests_file = validated if validated.exists() else tests_dir / "unit_tests.json"
    test_id = f"UT_{rule_id}"
    ref = dict(kind="unit_test", rule_id=rule_id, test_id=test_id, artifact_file=_app_relative(tests_file, app_dir))

    entries, problem = _read_list(tests_file)
    if problem:
        if entries is None:
            problem = f"No unit tests exist for {root['name']}."
        return ArtifactRef(**ref, status="missing" if entries is None else "unverified", detail=problem)

    matches = [entry for entry in entries if isinstance(entry, dict) and entry.get("id") == rule_id]

    if not matches:
        detail = f"No generated unit test exists for business rule {rule_id}."
        discarded, _ = _read_list(tests_dir / "discarded_tests.json")
        for entry in discarded or []:
            if isinstance(entry, dict) and entry.get("id") == rule_id:
                detail = f"The unit test for business rule {rule_id} was discarded: {entry.get('reason', 'no reason given')}"
                break
        return ArtifactRef(**ref, status="missing", detail=detail)

    if len(matches) > 1:
        # make_cases() names repeats UT_<id>_b, UT_<id>_b_b, ...
        return ArtifactRef(
            **ref, status="ambiguous",
            candidates=[test_id + "_b" * i for i in range(len(matches))],
            detail=f"{len(matches)} generated unit tests belong to business rule {rule_id}.",
        )

    try:
        test = UnitTest.model_validate(matches[0])
    except ValidationError as e:
        return ArtifactRef(**ref, status="unverified", detail=f"Unit test {test_id} does not match its schema: {e.errors()[0]['msg']}")

    files, unmapped = _located(test.source_file_paths, root)
    notes = [unmapped] if unmapped else []
    if tests_file != validated:
        notes.append("Only the generated test exists; it has not been compiled or run.")
    built = ArtifactRef(
        **ref,
        path=files[0] if len(files) == 1 else None,
        directory=map_codebase_directory(test.source_directory, root),
        content_hash=portable_hash(matches[0], root),
        detail=" ".join(notes) or None,
    )

    rule = business_rule_ref(codebase_root, rule_id, app_dir)
    if rule.status == "missing" and (Path(app_dir) / rule.artifact_file).is_file():
        return built.model_copy(update=dict(
            status="stale",
            detail=f"{test_id} was generated for business rule {rule_id}, which no longer exists. {rule.detail}",
        ))
    if rule.status == "resolved":
        rules, _ = _read_list(Path(app_dir) / rule.artifact_file)
        current = next(entry for entry in rules if isinstance(entry, dict) and entry.get("id") == rule_id)
        if " ".join(str(current.get("rule", "")).split()) != " ".join(test.rule.split()):
            return built.model_copy(update=dict(
                status="stale",
                detail=f"{test_id} was generated for a different rule than the current business rule {rule_id}.",
            ))

    return _mark_if_older(built, tests_file, root, files)


def refresh_ref(ref, codebase_root, app_dir=APP_DIR):
    """
    @brief Re-resolves a saved reference and compares it with what it recorded.
    @return The current reference. If it still resolves but its content hash
            differs from the saved one, it is returned as stale.
    """
    if ref.kind == "source":
        current = source_ref(codebase_root, ref.path, ref.symbol, ref.line_start, ref.line_end)
    elif ref.kind == "summary":
        current = summary_ref(codebase_root, ref.path, ref.symbol, app_dir)
    elif ref.kind == "business_rule":
        current = business_rule_ref(codebase_root, ref.rule_id, app_dir)
    elif ref.kind == "unit_test":
        current = unit_test_ref(codebase_root, ref.rule_id, app_dir)
    else:
        return ref.model_copy(update=dict(
            status="unverified", detail=f"{ref.kind} references cannot be re-checked.",
        ))

    if current.status == "resolved" and ref.content_hash and current.content_hash != ref.content_hash:
        return current.model_copy(update=dict(
            status="stale", detail="The artifact has changed since this reference was recorded.",
        ))
    return current


# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------

def _app_relative(file, app_dir):
    try:
        return Path(file).resolve().relative_to(Path(app_dir).resolve()).as_posix()
    except ValueError:
        return Path(file).as_posix()


def _read_list(file):
    """
    Returns (entries, problem). entries is None when the file does not exist,
    and [] with a problem when it cannot be read as a JSON list.
    """
    if not Path(file).is_file():
        return None, f"{Path(file).name} does not exist."
    try:
        data = json.loads(Path(file).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return [], f"{Path(file).name} could not be read: {e}"
    if not isinstance(data, list):
        return [], f"{Path(file).name} is not a list."
    return data, None


def _find_symbol(summary, symbol):
    """(label, entry) for each member of the summary named by symbol."""
    type_name, _, name = symbol.rpartition(".")
    found = []

    for kind in summary.get("types") or []:
        if type_name and kind.get("name") != type_name:
            continue
        for method in kind.get("methods") or []:
            if method.get("name") == name:
                found.append((f"{kind.get('name')}.{_signature(method)}", method))

    if not type_name:
        for function in summary.get("functions") or []:
            if function.get("name") == name:
                found.append((_signature(function), function))

    return found


def _signature(member):
    params = ", ".join(
        str(p.get("type") or p.get("name") or "?") for p in member.get("parameters") or []
    )
    return f"{member.get('name')}({params})"


def _mark_if_older(ref, artifact_file, root, source_paths):
    """
    Adds a caveat if any source file was modified after the artifact was
    written. The reference stays resolved: the pipeline records no hash of the
    source an artifact was generated from, so a later modification time does
    not prove the content changed (a checkout or copy also updates it).
    """
    try:
        written = os.path.getmtime(artifact_file)
    except OSError:
        return ref

    newer = []
    for rel in source_paths:
        source = Path(root["abs"]) / rel
        if source.is_file() and os.path.getmtime(source) > written:
            newer.append(rel)

    if not newer:
        return ref
    caveat = (
        f"Possibly out of date: {', '.join(newer)} was modified after this artifact "
        "was generated (by modification time only; the content may be unchanged)."
    )
    return ref.model_copy(update=dict(detail=" ".join(filter(None, [ref.detail, caveat]))))
