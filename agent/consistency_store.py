"""
@file consistency_store.py
@brief Saves and loads Consistency Review results and review decisions.
@details Follows the other agents' outputs: one folder per codebase under agent/.

    agent/consistency_output/<codebase>/consistency_result.json
        the latest check, without review decisions
    agent/consistency_output/<codebase>/reviews.json
        review decisions keyed by finding ID

Reviews are kept apart so that running the check again does not erase them: a
finding with the same ID (same category and same referenced content) picks up
its earlier decision. Decisions for findings not in the latest check are kept
in case the finding comes back.

Files are written to a temporary file and then swapped in, as error_log.json
is, so an interrupted write never leaves half a file.
"""

import json
import os
from pathlib import Path

from pydantic import ValidationError

from agent.BR_input_loader import APP_DIR
from agent.structured_output.consistency_output import (
    SCHEMA_VERSION, ConsistencyCheckResult, Review, now_stamp,
)


OUTPUT_DIR = Path("agent") / "consistency_output"
RESULT_NAME = "consistency_result.json"
REVIEWS_NAME = "reviews.json"


class ConsistencyStoreError(ValueError):
    """
    @brief A stored consistency file is unreadable, invalid, or belongs to another codebase.
    """


def output_dir(codebase_name, app_dir=APP_DIR):
    """
    @brief Folder holding one codebase's consistency outputs.
    @param codebase_name The codebase root folder name, as the other agents use it.
    """
    name = str(codebase_name or "").strip()
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        raise ConsistencyStoreError(f"Not a codebase name: {codebase_name!r}")
    return Path(app_dir) / OUTPUT_DIR / name


def save_check_result(result: ConsistencyCheckResult, app_dir=APP_DIR) -> Path:
    """
    @brief Saves a check as the codebase's latest result, replacing the previous one.
    @details Review decisions are not written here; use set_review().
    @return Path of the written file.
    """
    path = output_dir(result.codebase, app_dir) / RESULT_NAME
    data = result.model_dump(mode="json", exclude={"findings": {"__all__": {"review"}}})
    _write_json(path, data)
    return path


def load_check_result(codebase_name, app_dir=APP_DIR, run_id=None) -> ConsistencyCheckResult:
    """
    @brief Loads the latest check with its review decisions applied.
    @param run_id If given, only a check from that pipeline run is returned.
    @return The stored check, or a not_run result (with a note) when there is
            none, or none for the requested run. A check from another run is
            never returned in its place.
    @raises ConsistencyStoreError If a stored file is unreadable or invalid.
    """
    path = output_dir(codebase_name, app_dir) / RESULT_NAME

    if not path.is_file():
        return ConsistencyCheckResult(
            codebase=codebase_name, run_id=run_id, status="not_run",
            notes=["No consistency check has been run for this codebase."],
        )

    data = _read_json(path)
    try:
        result = ConsistencyCheckResult.model_validate(data)
    except ValidationError as e:
        raise ConsistencyStoreError(f"{path} is not a valid consistency result: {e}") from e

    if result.codebase != codebase_name:
        raise ConsistencyStoreError(
            f"{path} holds a check of {result.codebase!r}, not {codebase_name!r}."
        )

    if run_id is not None and result.run_id != run_id:
        return ConsistencyCheckResult(
            codebase=codebase_name, run_id=run_id, status="not_run",
            notes=[
                f"No consistency check exists for run {run_id}. The latest check "
                f"({result.check_id}) belongs to run {result.run_id or 'none (run on its own)'}."
            ],
        )

    reviews = load_reviews(codebase_name, app_dir)
    for finding in result.findings:
        if finding.id in reviews:
            finding.review = reviews[finding.id]

    return result


def load_reviews(codebase_name, app_dir=APP_DIR) -> dict[str, Review]:
    """
    @brief All saved review decisions for a codebase, keyed by finding ID.
    @raises ConsistencyStoreError If reviews.json is unreadable or invalid.
    """
    path = output_dir(codebase_name, app_dir) / REVIEWS_NAME
    if not path.is_file():
        return {}

    data = _read_json(path)
    if not isinstance(data, dict) or not isinstance(data.get("reviews"), dict):
        raise ConsistencyStoreError(f"{path} is not a valid reviews file.")
    if data.get("codebase") != codebase_name:
        raise ConsistencyStoreError(f"{path} holds reviews of {data.get('codebase')!r}, not {codebase_name!r}.")

    try:
        return {fid: Review.model_validate(review) for fid, review in data["reviews"].items()}
    except ValidationError as e:
        raise ConsistencyStoreError(f"{path} has an invalid review: {e}") from e


def set_review(codebase_name, finding_id, status, note=None, app_dir=APP_DIR) -> Review:
    """
    @brief Records a review decision for a finding in the latest check.
    @param status "unresolved", "reviewed" or "dismissed". Neither of the last
           two means the implementation is correct.
    @param note Optional note; blank clears it.
    @raises ConsistencyStoreError If the latest check has no such finding.
    """
    result = load_check_result(codebase_name, app_dir)
    if finding_id not in {finding.id for finding in result.findings}:
        raise ConsistencyStoreError(f"The latest consistency check has no finding {finding_id!r}.")

    try:
        review = Review(status=status, note=note, updated_at=now_stamp())
    except ValidationError as e:
        raise ConsistencyStoreError(f"Invalid review: {e.errors()[0]['msg']}") from e

    reviews = load_reviews(codebase_name, app_dir)
    reviews[finding_id] = review

    _write_json(
        output_dir(codebase_name, app_dir) / REVIEWS_NAME,
        {
            "schema_version": SCHEMA_VERSION,
            "codebase": codebase_name,
            "reviews": {fid: r.model_dump(mode="json") for fid, r in reviews.items()},
        },
    )
    return review


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ConsistencyStoreError(f"Could not read {path}: {e}") from e


def _write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
