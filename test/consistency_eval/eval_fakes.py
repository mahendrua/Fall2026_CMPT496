"""
@file eval_fakes.py
@brief Stand-in models for offline evaluation, and a recorder for real ones.
@details OracleLLM answers from a case's expectations, citing the verbatim
excerpts and evidence lines written in eval_cases.py. Running it end to end checks
that the fixtures, the checker's validation and the scorer agree; it says
nothing about how a real model would answer. AgreeingLLM calls everything
consistent, so the scorer's handling of misses can be tested.
"""

import re
import time

from agent.structured_output.consistency_checker_output import (
    ArtifactClaimOutput, AssessmentOutput, ConsistencyAnalysisOutput, FindingOutput, SourceClaimOutput,
)


def prompt_parts(messages):
    user = messages[-1][1]
    path = re.search(r"^File: (.+)$", user, re.M).group(1)
    scope = re.search(r"^Code in scope: (.+)$", user, re.M).group(1)
    symbol = re.match(r"lines \d+-\d+ \(([^)]+)\)", scope)
    handles = re.search(r"^Generated artifacts to assess: (.*)$", user, re.M).group(1).split(", ")
    return path, symbol.group(1) if symbol else None, handles


class _Structured:
    def __init__(self, respond):
        self.respond = respond

    def with_structured_output(self, schema):
        assert schema is ConsistencyAnalysisOutput
        return self

    def invoke(self, messages):
        return self.respond(messages)


class OracleLLM(_Structured):
    """Answers exactly what the case expects, with valid citations."""

    def __init__(self, case):
        super().__init__(self._answer)
        self.case = case

    def _answer(self, messages):
        path, symbol, handles = prompt_parts(messages)
        target = next(t for t in self.case.targets if t.path == path and t.symbol == symbol)
        expects = {e.handle: e for e in target.expects}
        source = self.case.files[path].splitlines()

        assessments, findings = [], []
        for handle in handles:
            expect = expects.get(handle)
            if expect is None:
                verdict = "consistent"
            elif expect.expected in ("inconsistent", "consistent", "insufficient_evidence"):
                verdict = expect.expected
            else:                                   # ambiguous: any defensible verdict will do
                verdict = expect.also_ok[0] if expect.also_ok else "consistent"
            assessments.append(AssessmentOutput(artifact=handle, verdict=verdict, reason=f"Oracle: {expect.claim if expect else 'no expectation'}"))
            if verdict == "inconsistent":
                line = next(n for n, text in enumerate(source, 1) if expect.evidence[0] in text)
                findings.append(FindingOutput(
                    category=expect.category,
                    title=f"{handle} disagrees with the code",
                    explanation=expect.note or expect.claim,
                    source=SourceClaimOutput(statement="What the code does.", excerpt=expect.evidence[0],
                                             line_start=line, line_end=line),
                    claims=[ArtifactClaimOutput(artifact=handle, statement=expect.claim, excerpt=expect.excerpt)],
                ))
        return ConsistencyAnalysisOutput(assessments=assessments, findings=findings)


class AgreeingLLM(_Structured):
    """Calls every artifact consistent."""

    def __init__(self):
        super().__init__(lambda messages: ConsistencyAnalysisOutput(assessments=[
            AssessmentOutput(artifact=h, verdict="consistent", reason="Agrees.") for h in prompt_parts(messages)[2]
        ]))


class CallBudgetExhausted(RuntimeError):
    """Raised instead of calling the provider once the call budget is used up."""


class CallBudget:
    """
    The most provider requests one evaluation may make, shared by every run.
    Each attempt counts, including the checker's re-ask of an unreadable answer.
    """

    def __init__(self, limit):
        self.limit = limit
        self.used = 0
        self.refused = 0

    @property
    def exhausted(self):
        return self.used >= self.limit


class RecordingLLM:
    """
    Wraps a chat model: counts calls, times them, keeps each structured
    answer (no model reasoning is requested or kept), and counts answers that
    could not be read. With a budget, a request beyond it is refused before
    anything is sent.
    """

    def __init__(self, llm, budget=None):
        self.llm = llm
        self.budget = budget
        self.calls = []
        self.refused = 0

    def with_structured_output(self, schema):
        self.structured = self.llm.with_structured_output(schema)
        return self

    def invoke(self, messages):
        if self.budget is not None:
            if self.budget.exhausted:
                self.refused += 1
                self.budget.refused += 1
                raise CallBudgetExhausted(f"call budget of {self.budget.limit} used up; nothing was sent")
            self.budget.used += 1
        start = time.perf_counter()
        try:
            answer = self.structured.invoke(messages)
        except Exception as e:
            self.calls.append(dict(seconds=time.perf_counter() - start, error=f"{type(e).__name__}: {e}", answer=None))
            raise
        dumped = answer.model_dump() if hasattr(answer, "model_dump") else answer
        self.calls.append(dict(seconds=time.perf_counter() - start, error=None, answer=dumped))
        return answer

    def stats(self):
        return dict(
            calls=len(self.calls),
            refused=self.refused,
            unreadable=sum(c["error"] is not None or not isinstance(c["answer"], dict) for c in self.calls),
            proposed_findings=sum(len((c["answer"] or {}).get("findings") or []) for c in self.calls if isinstance(c["answer"], dict)),
            seconds=sum(c["seconds"] for c in self.calls),
        )
