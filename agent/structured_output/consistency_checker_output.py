"""
@file consistency_checker_output.py
@brief Structured output the Consistency Review model returns for one comparison.
@details The model refers to artifacts only by the handles the checker gave it
(SOURCE, SUMMARY, RULE_<id>, TEST_<id>) and cites verbatim excerpts. It has no
field for paths, execution results, confirmed requirements or intended
behaviour: the checker builds those (or leaves them unknown) itself, after
verifying every handle, excerpt and line range. See agent/consistency_checker.py.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SourceClaimOutput(BaseModel):
    """
    @brief What the source code does, as read from it.
    """
    model_config = ConfigDict(extra="forbid")
    statement: str = Field(..., description="What the code currently does, in plain language, e.g. 'Returns the price increased by 10% (110 for an input of 100)'. Describe behaviour, not intent.")
    excerpt: str = Field(..., description="Code copied exactly from the SOURCE artifact, without line numbers, that shows this behaviour.")
    line_start: int = Field(..., description="First line number of the excerpt in SOURCE.")
    line_end: int = Field(..., description="Last line number of the excerpt in SOURCE.")


class ArtifactClaimOutput(BaseModel):
    """
    @brief What one generated artifact says about the same behaviour.
    """
    model_config = ConfigDict(extra="forbid")
    artifact: str = Field(..., description="Handle of the generated artifact making the claim, e.g. SUMMARY, RULE_3 or TEST_3.")
    statement: str = Field(..., description="What this artifact claims, in plain language.")
    excerpt: str = Field(..., description="Text copied exactly from that artifact that makes the claim, e.g. a sentence of the summary or an assertion of the test.")


class FindingOutput(BaseModel):
    """
    @brief One concrete disagreement between the source and generated artifacts.
    """
    model_config = ConfigDict(extra="forbid")
    category: Literal["source_summary", "source_rule", "source_test", "artifact_artifact"] = Field(
        ...,
        description=(
            "source_summary / source_rule / source_test: the summary, rule or test describes or expects "
            "behaviour the code does not have. artifact_artifact: generated artifacts contradict each other "
            "(still cite the source)."
        ),
    )
    title: str = Field(..., description="Short title naming the disagreement.")
    explanation: str = Field(..., description="The concrete disagreement: what the code does and what the artifact says instead.")
    source: SourceClaimOutput = Field(..., description="What the code does.")
    claims: list[ArtifactClaimOutput] = Field(..., description="The conflicting claims of the generated artifacts involved.")
    behaviour_uncertainty: list[str] = Field(default_factory=list, description="Anything uncertain about what the code currently does (e.g. depends on code not shown). Not about what it should do.")


class AssessmentOutput(BaseModel):
    """
    @brief The verdict for one generated artifact.
    """
    model_config = ConfigDict(extra="forbid")
    artifact: str = Field(..., description="Handle of the generated artifact.")
    verdict: Literal["consistent", "inconsistent", "not_applicable", "insufficient_evidence"] = Field(
        ...,
        description=(
            "consistent: matches what the code does. inconsistent: contradicts it (report a finding). "
            "not_applicable: is about behaviour outside the code in scope. insufficient_evidence: cannot be "
            "decided from the code supplied."
        ),
    )
    reason: str = Field(..., description="One or two sentences: what the code does and what the artifact says. A conclusion, not step-by-step reasoning.")


class ConsistencyAnalysisOutput(BaseModel):
    """
    @brief The model's full answer for one comparison.
    """
    model_config = ConfigDict(extra="forbid")
    assessments: list[AssessmentOutput] = Field(..., description="Exactly one assessment for every generated artifact handle supplied.")
    findings: list[FindingOutput] = Field(default_factory=list, description="One entry per concrete disagreement. Empty if every artifact is consistent.")
