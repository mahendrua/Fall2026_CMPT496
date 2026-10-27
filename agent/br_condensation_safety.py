"""
Deterministic safety checks for BR condenser output.

Does not call an LLM, Chroma, or embeddings. Invalid index coverage falls back
to the original directory group. Unsafe multi-rule merges fall back per output.
"""

from __future__ import annotations

import re

from agent.BR_input_loader import merge_rule_candidates
from agent.structured_output.BR_output import RuleCandidate

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_COMPARISON_RE = re.compile(r"<=|>=|==|!=|<|>")
_DIGIT_MATH_RE = re.compile(r"(?<=\d)\s*([+\-*/])\s*(?=\d)")
_EXCEPTION_RE = re.compile(r"\b[A-Z][A-Za-z0-9_]*(?:Error|Exception)\b")


def numeric_tokens(text: str) -> tuple[str, ...]:
    return tuple(_NUMBER_RE.findall(text or ""))


def comparison_tokens(text: str) -> tuple[str, ...]:
    return tuple(_COMPARISON_RE.findall(text or ""))


def formula_operator_tokens(text: str) -> tuple[str, ...]:
    return tuple(_DIGIT_MATH_RE.findall(text or ""))


def exception_tokens(text: str) -> tuple[str, ...]:
    return tuple(_EXCEPTION_RE.findall(text or ""))


def requirement_signature(text: str) -> tuple:
    return (
        frozenset(numeric_tokens(text)),
        frozenset(comparison_tokens(text)),
        frozenset(formula_operator_tokens(text)),
        frozenset(exception_tokens(text)),
    )


def merge_is_unsafe(source_texts: list[str], condensed_text: str = "") -> bool:
    """
    True when originals disagree on high-confidence independently testable literals.

    condensed_text is ignored for numeric/operator/exception conflicts so that
    restating every original number does not make an unsafe merge valid.
    """
    del condensed_text
    signatures = [requirement_signature(text) for text in source_texts or []]
    if len(signatures) < 2:
        return False
    first = signatures[0]
    return any(signature != first for signature in signatures[1:])


def index_assignments_are_valid(group: list, outputs: list) -> bool:
    """Every original 1-based index must appear exactly once across outputs."""
    if not group or not outputs:
        return False

    size = len(group)
    seen: list[int] = []
    for output in outputs:
        rule = (getattr(output, "rule", None) or "").strip()
        numbers = list(getattr(output, "source_rule_numbers", None) or [])
        if not rule or not numbers:
            return False
        if len(numbers) != len(set(numbers)):
            return False
        for number in numbers:
            if not isinstance(number, int) or isinstance(number, bool):
                return False
            if number < 1 or number > size:
                return False
            seen.append(number)

    return len(seen) == size and set(seen) == set(range(1, size + 1))


def apply_condenser_safety(group: list[RuleCandidate], outputs: list) -> list[RuleCandidate] | None:
    """
    Apply index and semantic-merge checks to one directory group.

    Returns None when index coverage cannot be trusted (caller keeps originals).
    Otherwise returns candidates: merged paraphrases and unmerged unsafe groups.
    """
    if not index_assignments_are_valid(group, outputs):
        return None

    result: list[RuleCandidate] = []
    for output in outputs:
        numbers = list(output.source_rule_numbers)
        sources = [group[number - 1] for number in numbers]
        directories = {candidate.source_directory for candidate in sources}
        unsafe = len(sources) > 1 and (
            len(directories) > 1
            or merge_is_unsafe([candidate.rule for candidate in sources], output.rule)
        )
        if not unsafe:
            result.append(merge_rule_candidates(sources, rule=output.rule))
            continue
        for number in sorted(numbers):
            result.append(group[number - 1])
    return result
