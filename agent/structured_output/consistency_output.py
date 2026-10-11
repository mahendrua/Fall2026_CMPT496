"""
@file consistency_output.py
@brief Defines the data contracts for Consistency Review.
@details A consistency check compares generated artifacts (summaries, business
rules, unit tests) with the source code they describe, and records each
disagreement as a Finding for a person to review.

Evidence rules these models enforce:
- Source code is evidence of current behaviour, not of intended behaviour.
- Generated summaries, rules and tests are claims, never confirmed requirements.
- Intended behaviour stays "unknown" unless a human-confirmed requirement is
  referenced and cited as evidence.
- Each piece of evidence says whether it came from reading code, from running
  a test, or from a person.
- A review status records a decision about a finding; "reviewed" or
  "dismissed" never means the implementation was proven correct.
"""

import hashlib
import json
import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agent.structured_output.BR_output import RuleProvenance


SCHEMA_VERSION = 1

ArtifactKind = Literal["source", "summary", "business_rule", "unit_test", "requirement"]

# How reliably a reference points at one current artifact.
#   resolved    exactly one artifact found, and nothing shows it is out of date
#   missing     the artifact, or the part of it asked for, does not exist
#   ambiguous   more than one artifact matches; see candidates
#   stale       found, but it was generated from, or recorded against, older content
#   unverified  found, but it could not be read or checked against its schema
LinkStatus = Literal["resolved", "missing", "ambiguous", "stale", "unverified"]

# Where a piece of evidence came from.
#   code_inspection     behaviour inferred by reading code; nothing was run
#   execution           a result actually observed by running a test
#   human_confirmation  a person confirmed it (e.g. a confirmed requirement)
EvidenceOrigin = Literal["code_inspection", "execution", "human_confirmation"]

# What a finding compares.
#   source_summary / source_rule / source_test
#       a generated artifact describes the current implementation inaccurately
#   artifact_artifact
#       generated artifacts disagree with each other
#   implementation_requirement
#       the implementation disagrees with a human-confirmed requirement
FindingCategory = Literal[
    "source_summary",
    "source_rule",
    "source_test",
    "artifact_artifact",
    "implementation_requirement",
]

# Whether the implementation does what is intended. Only a confirmed
# requirement can move this off "unknown".
IntendedBehaviour = Literal["unknown", "matches_requirement", "conflicts_with_requirement"]

# A person's decision about a finding. Neither "reviewed" nor "dismissed"
# means the implementation, or any artifact, has been proven correct.
ReviewStatus = Literal["unresolved", "reviewed", "dismissed"]

#   not_run    no check has been performed (or none for the requested run)
#   completed  every expected artifact was read and every link resolved
#   partial    the check ran, but some artifacts were missing, some links were
#              unresolved, or some comparisons failed
#   failed     the check could not produce a usable result
CheckStatus = Literal["not_run", "completed", "partial", "failed"]

GENERATED_KINDS = ("summary", "business_rule", "unit_test")

SOURCE_CATEGORY_KIND = {
    "source_summary": "summary",
    "source_rule": "business_rule",
    "source_test": "unit_test",
}


def now_stamp():
    """Local time to the second, in the same form as last_run_log.json."""
    return datetime.now().isoformat(timespec="seconds")


def new_check_id():
    """Same shape as RunLog.run_id: time, then a short random suffix."""
    return f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"


class ArtifactRef(BaseModel):
    """
    @brief Points at one artifact, or at the place where one was expected.
    @details Paths are POSIX paths relative to the codebase root, as produced
    by BR_input_loader.normalize_rule_path(). A reference that is not resolved
    keeps whatever was asked for and says why in detail, so missing or doubtful
    links are visible instead of being guessed.
    """
    model_config = ConfigDict(extra="forbid")

    kind: ArtifactKind = Field(..., description="What kind of artifact this is.")
    status: LinkStatus = Field("resolved", description="How reliably this reference points at one current artifact.")
    path: Optional[str] = Field(None, description="Source file the artifact is about, relative to the codebase root.")
    directory: Optional[str] = Field(None, description="Directory the artifact is about, relative to the codebase root ('.' for the root). Used by folder-level business rules.")
    artifact_file: Optional[str] = Field(None, description="Where the artifact itself is stored, relative to the app directory (e.g. agent/BR_agent_output/Shop/validated_rules.json). Empty for source files.")
    symbol: Optional[str] = Field(None, description="Method or type within the file, as 'Type.Method' or 'Method'.")
    line_start: Optional[int] = Field(None, ge=1, description="First line (1-based) of the relevant code, when known.")
    line_end: Optional[int] = Field(None, ge=1, description="Last line (1-based, inclusive) of the relevant code, when known.")
    rule_id: Optional[int] = Field(None, description="ID of the validated business rule (validated_rules.json 'id').")
    test_id: Optional[str] = Field(None, description="Name of the generated test class, e.g. 'UT_3' (see agent/test_harness.py).")
    provenance: list[RuleProvenance] = Field(default_factory=list, description="Origins of a business rule with their source files, copied unchanged from the BR agent output.")
    content_hash: Optional[str] = Field(None, description="Hash of the referenced content when the reference was made, used to detect later changes.")
    candidates: list[str] = Field(default_factory=list, description="The competing matches when status is 'ambiguous'.")
    detail: Optional[str] = Field(None, description="Why the reference is not resolved, or any limitation of it.")
    confirmed_by: Optional[str] = Field(None, description="Who confirmed a requirement, and how. Only for kind 'requirement'.")

    @model_validator(mode="after")
    def _check(self):
        if self.line_start and self.line_end and self.line_end < self.line_start:
            raise ValueError("line_end must not be before line_start.")
        if self.kind == "source" and not self.path:
            raise ValueError("A source reference needs a path.")
        # A missing output file (e.g. no validated_rules.json) has no ID to name.
        if self.kind == "business_rule" and self.rule_id is None and self.status != "missing":
            raise ValueError("A business rule reference needs a rule_id.")
        if self.kind == "unit_test" and self.test_id is None and self.rule_id is None and self.status != "missing":
            raise ValueError("A unit test reference needs a test_id or rule_id.")
        if self.provenance and self.kind != "business_rule":
            raise ValueError("Only business rule references carry provenance.")
        if self.confirmed_by and self.kind != "requirement":
            raise ValueError("Only requirement references can be confirmed.")
        if self.kind == "requirement" and self.status == "resolved" and not self.confirmed_by:
            raise ValueError("A resolved requirement must say who confirmed it.")
        if self.status == "ambiguous" and len(self.candidates) < 2:
            raise ValueError("An ambiguous reference must list at least two candidates.")
        if self.status != "resolved" and not self.detail:
            raise ValueError(f"A {self.status} reference must explain why in detail.")
        return self

    def key(self):
        """
        Identity of the referenced content, used to build finding IDs.
        Includes content_hash, so a reference to changed content is a
        different reference.
        """
        return "|".join(str(part) for part in (
            self.kind, self.path, self.directory, self.symbol,
            self.rule_id, self.test_id, self.content_hash,
        ))


class Claim(BaseModel):
    """
    @brief What one artifact says about the behaviour in question.
    @details For a source claim this is the behaviour read from the code; it
    describes what the code does now, not what it should do.
    """
    model_config = ConfigDict(extra="forbid")

    artifact: ArtifactRef = Field(..., description="The artifact making the claim.")
    statement: str = Field(..., min_length=1, description="The claim in plain language, e.g. 'Reduces the price by 10%'.")
    excerpt: Optional[str] = Field(None, description="The exact text or code the claim was taken from.")


class Evidence(BaseModel):
    """
    @brief One piece of support for a finding, with where it came from.
    """
    model_config = ConfigDict(extra="forbid")

    origin: EvidenceOrigin = Field(..., description="Whether this was inferred from code, observed by running a test, or confirmed by a person.")
    description: str = Field(..., min_length=1, description="What this evidence shows.")
    artifact: Optional[ArtifactRef] = Field(None, description="The artifact the evidence comes from.")
    excerpt: Optional[str] = Field(None, description="The code, assertion or output the evidence rests on.")

    @model_validator(mode="after")
    def _check(self):
        if self.origin == "execution" and (self.artifact is None or self.artifact.kind != "unit_test"):
            raise ValueError("Execution evidence must reference the test that was run.")
        if self.origin == "human_confirmation" and (self.artifact is None or self.artifact.kind != "requirement"):
            raise ValueError("Human confirmation must reference the confirmed requirement.")
        return self


class Review(BaseModel):
    """
    @brief A person's decision about a finding.
    @details Records only that the finding was looked at. "reviewed" and
    "dismissed" do not mean the implementation or any artifact is correct.
    """
    model_config = ConfigDict(extra="forbid")

    status: ReviewStatus = Field("unresolved", description="unresolved, reviewed or dismissed.")
    note: Optional[str] = Field(None, description="Optional note from the reviewer.")
    updated_at: Optional[str] = Field(None, description="When the decision was last changed.")

    @field_validator("note")
    @classmethod
    def _blank_note_is_none(cls, note):
        if note is None:
            return None
        note = note.strip()
        return note or None


class Finding(BaseModel):
    """
    @brief One disagreement for a person to review.
    @details The ID is derived from the category and the referenced content,
    so the same disagreement about the same content keeps its ID (and its
    review) across checks, while a disagreement about changed content gets a
    new one. Pass a discriminator when one check reports more than one finding
    for the same category and artifacts.
    """
    model_config = ConfigDict(extra="forbid")

    id: str = Field("", description="Stable ID; computed from category, artifacts and discriminator when left empty.")
    codebase: str = Field(..., min_length=1, description="Codebase (root folder name) the finding belongs to.")
    run_id: Optional[str] = Field(None, description="Pipeline run the check belonged to, as in last_run_log.json.")
    category: FindingCategory = Field(..., description="Which two things disagree.")
    title: str = Field(..., min_length=1, max_length=200, description="Short title.")
    explanation: str = Field(..., min_length=1, description="The concrete disagreement, in plain language.")
    claims: list[Claim] = Field(..., min_length=2, description="The conflicting claims, one per artifact.")
    artifacts: list[ArtifactRef] = Field(default_factory=list, description="Every artifact the finding concerns. Claim artifacts are added automatically.")
    evidence: list[Evidence] = Field(default_factory=list, description="Support for the finding.")
    uncertainty: list[str] = Field(default_factory=list, description="What is unknown, assumed or could not be checked.")
    intended_behaviour: IntendedBehaviour = Field("unknown", description="Whether the implementation matches a confirmed requirement. 'unknown' unless one is referenced.")
    discriminator: str = Field("", description="Distinguishes findings with the same category and artifacts.")
    review: Review = Field(default_factory=Review, description="The reviewer's decision.")

    @model_validator(mode="after")
    def _check(self):
        keys = {ref.key() for ref in self.artifacts}
        for claim in self.claims:
            if claim.artifact.key() not in keys:
                self.artifacts.append(claim.artifact)
                keys.add(claim.artifact.key())

        kinds = [claim.artifact.kind for claim in self.claims]

        if self.category in SOURCE_CATEGORY_KIND:
            other = SOURCE_CATEGORY_KIND[self.category]
            if "source" not in kinds or other not in kinds:
                raise ValueError(f"A {self.category} finding needs a source claim and a {other} claim.")

        if self.category == "artifact_artifact":
            if sum(kind in GENERATED_KINDS for kind in kinds) < 2:
                raise ValueError("An artifact_artifact finding needs claims from at least two generated artifacts.")

        confirmed = [
            claim.artifact for claim in self.claims
            if claim.artifact.kind == "requirement" and claim.artifact.status == "resolved"
        ]
        human = any(item.origin == "human_confirmation" for item in self.evidence)

        if self.category == "implementation_requirement":
            if "source" not in kinds or not confirmed or not human:
                raise ValueError(
                    "An implementation_requirement finding needs a source claim, a "
                    "confirmed requirement claim, and human_confirmation evidence."
                )

        if self.intended_behaviour != "unknown" and not (confirmed and human):
            raise ValueError(
                "Intended behaviour stays 'unknown' unless a confirmed requirement "
                "is claimed and cited as human_confirmation evidence."
            )

        if not self.id:
            self.id = make_finding_id(self.category, self.artifacts, self.discriminator)
        return self


class Undetermined(BaseModel):
    """
    @brief An artifact that was compared but whose consistency could not be decided.
    @details Not a finding and not agreement: the evidence supplied was not
    enough (e.g. the behaviour depends on code in another file).
    """
    model_config = ConfigDict(extra="forbid")

    artifact: ArtifactRef = Field(..., description="The artifact that could not be judged.")
    reason: str = Field(..., min_length=1, description="Why consistency could not be determined.")


def make_finding_id(category, artifacts, discriminator=""):
    """Short hash of the category, the referenced content and the discriminator."""
    payload = json.dumps(
        [category, sorted(ref.key() for ref in artifacts), discriminator],
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class ConsistencyCheckResult(BaseModel):
    """
    @brief Everything one consistency check looked at and found.
    @details status separates "no findings" from "not checked":
    - completed with no findings: every expected artifact was compared and
      nothing disagreed (this does not prove any artifact is correct);
    - partial: findings may be incomplete; see missing_artifacts,
      unresolved_links and errors;
    - failed: nothing usable was produced; see errors;
    - not_run: no check exists for this codebase or run.

    missing_artifacts lists artifacts the check needed but could not find
    (e.g. no validated_rules.json). A method that simply has no generated
    test is not missing; it is just not compared. undetermined lists
    artifacts that were compared without a conclusion; they make the check
    partial, since their consistency is unknown.
    """
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = SCHEMA_VERSION
    check_id: str = Field(default_factory=new_check_id, description="ID of this check.")
    codebase: str = Field(..., min_length=1, description="Codebase (root folder name) that was checked.")
    run_id: Optional[str] = Field(None, description="Pipeline run the check belonged to, if any.")
    scope: Literal["full", "selected"] = Field("full", description="Whether every artifact or only selected ones were checked.")
    selection: list[str] = Field(default_factory=list, description="What a selected check covered: source paths, or 'path#Type.Method'. Empty for a full check.")
    status: CheckStatus = Field(..., description="Whether the check finished, and how completely.")
    started_at: Optional[str] = Field(None, description="When the check started.")
    finished_at: Optional[str] = Field(None, description="When the check finished.")
    artifacts_checked: list[ArtifactRef] = Field(default_factory=list, description="Artifacts that were compared.")
    missing_artifacts: list[ArtifactRef] = Field(default_factory=list, description="Artifacts the check needed but could not find.")
    unresolved_links: list[ArtifactRef] = Field(default_factory=list, description="References that were ambiguous, stale or unverifiable, so were not compared.")
    findings: list[Finding] = Field(default_factory=list, description="Disagreements found.")
    undetermined: list[Undetermined] = Field(default_factory=list, description="Artifacts compared without a conclusion.")
    errors: list[str] = Field(default_factory=list, description="Errors that stopped part or all of the check.")
    notes: list[str] = Field(default_factory=list, description="Anything else the reader should know, e.g. why no check is shown.")

    @model_validator(mode="after")
    def _check(self):
        for ref in self.artifacts_checked:
            if ref.status != "resolved":
                raise ValueError("artifacts_checked may only hold resolved references.")
        for ref in self.missing_artifacts:
            if ref.status != "missing":
                raise ValueError("missing_artifacts may only hold missing references.")
        for ref in self.unresolved_links:
            if ref.status in ("resolved", "missing"):
                raise ValueError("unresolved_links holds ambiguous, stale or unverified references.")

        ids = set()
        for finding in self.findings:
            if finding.codebase != self.codebase:
                raise ValueError(f"Finding {finding.id} belongs to codebase {finding.codebase!r}, not {self.codebase!r}.")
            if self.run_id and finding.run_id and finding.run_id != self.run_id:
                raise ValueError(f"Finding {finding.id} belongs to run {finding.run_id}, not {self.run_id}.")
            if finding.id in ids:
                raise ValueError(f"Duplicate finding ID {finding.id}; pass a discriminator to tell them apart.")
            ids.add(finding.id)

        if self.scope == "selected" and not self.selection:
            raise ValueError("A selected check must record its selection.")
        if self.scope == "full" and self.selection:
            raise ValueError("A full check has no selection.")

        incomplete = bool(self.errors or self.missing_artifacts or self.unresolved_links or self.undetermined)

        if self.status == "completed" and incomplete:
            raise ValueError("A check with errors, missing artifacts, unresolved links or undetermined comparisons is partial, not completed.")
        if self.status == "partial" and not incomplete:
            raise ValueError("A partial check must say what is missing, unresolved, undetermined or failed.")
        if self.status == "failed" and not self.errors:
            raise ValueError("A failed check must list its errors.")
        if self.status == "not_run" and (self.findings or self.artifacts_checked or self.errors or self.undetermined):
            raise ValueError("A check that did not run has no findings, artifacts or errors.")
        return self

    @property
    def outcome(self):
        """
        One word for the UI: no_findings, findings, incomplete, failed or not_run.
        "no_findings" is only possible for a completed check.
        """
        if self.status in ("not_run", "failed"):
            return self.status
        if self.status == "partial":
            return "incomplete"
        return "findings" if self.findings else "no_findings"
