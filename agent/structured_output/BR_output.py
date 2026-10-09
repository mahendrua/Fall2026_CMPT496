"""
@file BR_output.py
@brief Defines structured output models for the Business Rule validation agent (G3).
@details Includes models for condensed rules, validated rules with evidence,
discarded rules with reasons, and structured LLM outputs for the condenser
and validator nodes.
"""

from typing import Optional, Literal
from pydantic import BaseModel, Field, ConfigDict, model_validator


RuleOrigin = Literal["file", "directory_observed", "directory_inferred"]


class RuleProvenance(BaseModel):
    """
    @brief One origin of a rule candidate together with the source files known for that origin.
    """
    model_config = ConfigDict(extra="forbid")
    origin: RuleOrigin = Field(..., description="Where this occurrence of the rule came from.")
    source_file_paths: list[str] = Field(default_factory=list, description="Source file paths known for this origin, relative to the codebase root. Empty for directory-level rules.")


class RuleCandidate(BaseModel):
    """
    @brief Represents an unvalidated business rule loaded from G1/G2 output.
    @details Common input format for file-level and directory-level rules. A
    loaded candidate has exactly one provenance record; a candidate produced by
    duplicate merging has one record per distinct origin/source combination.
    """
    model_config = ConfigDict(extra="forbid")
    rule: str = Field(..., description="The business rule statement as produced by G1/G2.")
    source_directory: str = Field(..., description="POSIX path of the directory this rule pertains to, relative to the codebase root ('.' for the root).")
    source_file_paths: list[str] = Field(default_factory=list, description="Known source file paths, relative to the codebase root. Empty when the source files are unknown. For merged candidates, the union across all provenance records.")
    origin: RuleOrigin = Field(..., description="Where the rule came from: file-level output, or directory-level observed/inferred rules. For merged candidates, the origin of the first occurrence.")
    provenance: list[RuleProvenance] = Field(default_factory=list, description="Every origin of this rule with its associated source files. Defaults to a single record built from origin and source_file_paths.")

    @model_validator(mode="after")
    def _default_provenance(self):
        if not self.provenance:
            self.provenance = [RuleProvenance(origin=self.origin, source_file_paths=list(self.source_file_paths))]
        return self


class CondensedRule(BaseModel):
    """
    @brief Represents a business rule after the condensation step.
    @details Carries the deduplicated rule text along with provenance metadata
    (source directory and file paths) needed by the retriever and validator.
    """
    model_config = ConfigDict(extra="forbid")
    id: int = Field(..., description="Stable unique identifier for the rule, assigned sequentially by the condenser node.")
    rule: str = Field(..., description="The combined business rule statement.")
    source_directory: str = Field(..., description="The directory this rule pertains to.")
    source_file_paths: list[str] = Field(default_factory=list, description="File paths from which this rule was originally derived. May span multiple files if the condenser merged related rules.")
    provenance: list[RuleProvenance] = Field(default_factory=list, description="Origins of the input rules this rule was condensed from, with their source files.")


class Explanation(BaseModel):
    """
    @brief Contains evidence and reasoning that support a validated business rule.
    """
    model_config = ConfigDict(extra="forbid")
    evidence: dict[str, list[str]] = Field(..., description="Dictionary mapping filenames to lists of code snippets that support the rule.")
    reasoning: str = Field(..., description="Explanation of how the retrieved code snippets imply or support the business rule.")


class ValidatedRule(BaseModel):
    """
    @brief Represents a business rule that has been validated with supporting evidence.
    """
    model_config = ConfigDict(extra="forbid")
    id: int = Field(..., description="Stable unique identifier matching the CondensedRule ID.")
    rule: str = Field(..., description="The business rule statement.")
    source_directory: str = Field(..., description="The directory this rule pertains to.")
    source_file_paths: list[str] = Field(default_factory=list, description="File paths from which this rule was originally derived.")
    explanation: Explanation = Field(..., description="Evidence and reasoning supporting the rule's validity.")
    provenance: list[RuleProvenance] = Field(default_factory=list, description="Origins of the input rules this rule was condensed from, with their source files.")


class DiscardedRule(BaseModel):
    """
    @brief Represents a business rule that was rejected during validation.
    """
    model_config = ConfigDict(extra="forbid")
    id: int = Field(..., description="Stable unique identifier matching the CondensedRule ID.")
    rule: str = Field(..., description="The business rule statement.")
    source_directory: str = Field(..., description="The directory this rule pertains to.")
    source_file_paths: list[str] = Field(default_factory=list, description="File paths from which this rule was originally derived.")
    reason: str = Field(..., description="Explanation of why the rule was discarded.")
    provenance: list[RuleProvenance] = Field(default_factory=list, description="Origins of the input rules this rule was condensed from, with their source files.")


class CondenserRuleOutput(BaseModel):
    """
    @brief One condensed rule returned by the condenser LLM, with the input rules it covers.
    """
    model_config = ConfigDict(extra="forbid")
    rule: str = Field(..., description="The condensed business rule statement.")
    source_rule_numbers: list[int] = Field(..., description="Numbers of the input rules (from the numbered list) that this condensed rule covers. A rule kept as-is lists only its own number.")


class CondenserOutput(BaseModel):
    """
    @brief Structured LLM output from the condenser node.
    @details Returns condensed rules for a single directory group, each with the
    numbers of the input rules it covers. The condenser node uses these numbers
    to carry each input's provenance into the CondensedRule objects it builds.
    """
    model_config = ConfigDict(extra="forbid")
    condensed_rules: list[CondenserRuleOutput] = Field(..., description="List of condensed/deduplicated business rules for a single directory group. Every input rule number must appear in at least one entry.")


class ValidatorOutput(BaseModel):
    """
    @brief Structured LLM output from the combined validator node.
    @details The validator assesses context sufficiency and rule validity in a
    single LLM call. The decision field forces the model to choose one of three
    outcomes, and the conditional fields are populated accordingly.
    """
    model_config = ConfigDict(extra="forbid")
    decision: Literal["need_more_context", "valid", "discard"] = Field(
        ...,
        description="The validator's decision: 'need_more_context' if retrieval should be retried with increased depth, 'valid' if the rule is supported by evidence, 'discard' if the rule cannot be substantiated.")
    explanation: Optional[Explanation] = Field(
        None,
        description="Evidence and reasoning supporting the rule. Populated when decision is 'valid', otherwise None.")
    discard_reason: Optional[str] = Field(
        None,
        description="Explanation of why the rule was discarded. Populated when decision is 'discard', otherwise None.")
