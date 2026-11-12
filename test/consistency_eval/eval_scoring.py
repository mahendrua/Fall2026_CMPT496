"""
@file eval_scoring.py
@brief Scores a ConsistencyCheckResult against a case's written expectations.
@details Outcomes per expected artifact:

    expected inconsistent   detected (right category, evidence on a supporting
                            line), detected_weak (inconsistent, but another
                            category or no supporting line cited), missed
                            (judged consistent), abstained, rejected
    expected consistent     correct, false_alarm, abstained, rejected
    expected insufficient   correct, overconfident (judged consistent),
                            unsupported_finding (judged inconsistent), abstained
    ambiguous               ambiguous (the verdict is recorded, not scored)
    expected not_assessed   correct, unexpected_assessment

Anything the model did not assess is not_assessed: an incomplete run is never
counted as a clean negative. "acceptable" marks a verdict listed in also_ok.
"""

from collections import Counter, defaultdict


def handle_of(ref):
    if ref.kind == "summary":
        return "SUMMARY"
    if ref.kind == "business_rule":
        return f"RULE_{ref.rule_id}"
    if ref.kind == "unit_test":
        return f"TEST_{ref.rule_id}"
    return ref.kind.upper()


def evidence_lines(source, snippets):
    """Line numbers of the source holding any of the snippets."""
    found = set()
    for number, line in enumerate(source.splitlines(), start=1):
        text = " ".join(line.split())
        if any(" ".join(s.split()) in text for s in snippets):
            found.add(number)
    return found


def score_run(case, result):
    outcomes = []
    for target in case.targets:
        mine = [a for a in result.assessments if a.scope.path == target.path and a.scope.symbol == target.symbol]
        by_handle = defaultdict(list)
        for assessment in mine:
            by_handle[handle_of(assessment.artifact)].append(assessment)
        lines_of = {e.handle: evidence_lines(case.files[target.path], e.evidence) for e in target.expects}

        for expect in target.expects:
            got = by_handle.pop(expect.handle, [])
            assessment = got[0] if got else None
            outcomes.append(_outcome(case, target, expect, assessment, result, lines_of[expect.handle]))

        for handle, extra in by_handle.items():
            outcomes.append(dict(
                case=case.name, target=target.label, handle=handle, expected=None,
                verdict=extra[0].verdict, outcome="unexpected_handle", reason=extra[0].reason,
            ))

    return dict(
        case=case.name,
        tuning=case.tuning,
        status=result.status,
        result_outcome=result.outcome,
        status_as_expected=result.status in case.expected_status,
        errors=list(result.errors),
        undetermined=[dict(handle=handle_of(u.artifact), reason=u.reason) for u in result.undetermined],
        skipped=[dict(handle=handle_of(s.artifact), reason=s.reason) for s in result.skipped],
        finding_ids=sorted(f.id for f in result.findings),
        findings=[dict(
            id=f.id, category=f.category, title=f.title,
            handles=[handle_of(c.artifact) for c in f.claims[1:]],
            source_lines=[f.claims[0].artifact.line_start, f.claims[0].artifact.line_end],
            source_excerpt=f.claims[0].excerpt,
            intended_behaviour=f.intended_behaviour,
            evidence_origins=[e.origin for e in f.evidence],
        ) for f in result.findings],
        checked=[dict(kind=r.kind, handle=handle_of(r) if r.kind != "source" else None, path=r.path, symbol=r.symbol)
                 for r in result.artifacts_checked],
        outcomes=outcomes,
    )


def _outcome(case, target, expect, assessment, result, supporting):
    row = dict(
        case=case.name, target=target.label, handle=expect.handle, expected=expect.expected,
        category=expect.category, verdict=None, accepted=None, reason=None,
    )
    if expect.expected == "not_assessed":
        row["outcome"] = "correct" if assessment is None else "unexpected_assessment"
        if assessment:
            row.update(verdict=assessment.verdict, reason=assessment.reason)
        return row
    if assessment is None:
        row["outcome"] = "not_assessed"
        return row

    verdict = assessment.verdict
    row.update(verdict=verdict, accepted=assessment.accepted, reason=assessment.reason)

    if expect.expected == "ambiguous":
        row["outcome"] = "ambiguous"
    elif not assessment.accepted:
        row["outcome"] = "rejected"
    elif verdict in expect.also_ok:
        row["outcome"] = "acceptable"
    elif expect.expected == "inconsistent":
        if verdict == "inconsistent":
            backing = [f for f in result.findings if f.id in assessment.finding_ids]
            category_ok = any(f.category == expect.category for f in backing)
            evidence_ok = any(
                _overlaps(f.claims[0].artifact.line_start, f.claims[0].artifact.line_end, supporting) for f in backing
            )
            row.update(category_ok=category_ok, evidence_ok=evidence_ok)
            row["outcome"] = "detected" if category_ok and evidence_ok else "detected_weak"
        elif verdict == "consistent":
            row["outcome"] = "missed"
        else:
            row["outcome"] = "abstained"
    elif expect.expected == "consistent":
        row["outcome"] = {"consistent": "correct", "inconsistent": "false_alarm"}.get(verdict, "abstained")
    elif expect.expected == "insufficient_evidence":
        row["outcome"] = {
            "insufficient_evidence": "correct", "consistent": "overconfident", "inconsistent": "unsupported_finding",
        }.get(verdict, "abstained")
    return row


def _overlaps(first, last, supporting):
    return first is not None and any(first <= n <= (last or first) for n in supporting)


def summarize(scored, calls=None):
    """
    @brief Totals across runs. Tuning cases are counted separately from the rest.
    @param scored score_run() results, one per run.
    @param calls Per-run instrumentation dicts (calls, proposed/accepted findings,
           tokens, waits, seconds), in the same order, or None.
    """
    def counts(rows, expected):
        return dict(Counter(r["outcome"] for r in rows if r["expected"] == expected))

    split = {}
    for label, wanted in (("held_out", False), ("tuning", True)):
        rows = [row for run in scored if run["tuning"] == wanted for row in run["outcomes"]]
        split[label] = dict(
            explicit_contradictions=counts(rows, "inconsistent"),
            matching_artifacts=counts(rows, "consistent"),
            insufficient_evidence_expected=counts(rows, "insufficient_evidence"),
            should_not_be_assessed=counts(rows, "not_assessed"),
            unexpected_handles=sum(r["outcome"] == "unexpected_handle" for r in rows),
        )

    ambiguous = defaultdict(Counter)
    for run in scored:
        for row in run["outcomes"]:
            if row["expected"] == "ambiguous":
                ambiguous[f"{row['case']} {row['target']} {row['handle']}"][row["verdict"] or "not_assessed"] += 1

    verdicts, ids = defaultdict(list), defaultdict(list)
    for run in scored:
        ids[run["case"]].append(tuple(run["finding_ids"]))
        for row in run["outcomes"]:
            verdicts[(run["case"], row["target"], row["handle"])].append(row["verdict"])
    repeated = {k: v for k, v in verdicts.items() if len(v) > 1}
    repeated_ids = {k: v for k, v in ids.items() if len(v) > 1}

    status = Counter(run["status"] for run in scored)
    totals = dict(
        runs=len(scored),
        status=dict(status),
        runs_with_errors=[dict(case=r["case"], status=r["status"], errors=r["errors"]) for r in scored if r["errors"]],
        unexpected_status=[dict(case=r["case"], status=r["status"]) for r in scored if not r["status_as_expected"]],
        scores=split,
        ambiguous_verdicts={k: dict(v) for k, v in ambiguous.items()},
        repeatability=dict(
            verdict_groups=len(repeated),
            verdicts_identical=sum(len(set(v)) == 1 and None not in v for v in repeated.values()),
            finding_id_sets_identical=sum(len(set(v)) == 1 for v in repeated_ids.values()),
            cases_repeated=len(repeated_ids),
            unstable=[dict(case=k[0], target=k[1], handle=k[2], verdicts=v) for k, v in repeated.items() if len(set(v)) > 1],
        ),
    )

    if calls is not None:
        proposed = sum(c["proposed_findings"] for c in calls)
        accepted = sum(len(run["findings"]) for run in scored)
        rejected_verdicts = sum(r["outcome"] == "rejected" for run in scored for r in run["outcomes"])
        totals["model"] = dict(
            calls=sum(c["calls"] for c in calls),
            refused_by_budget=sum(c.get("refused", 0) for c in calls),
            unreadable_answers=sum(c["unreadable"] for c in calls),
            provider_waits=sum(c["waits"] for c in calls),
            proposed_findings=proposed,
            accepted_findings=accepted,
            finding_rejection_rate=round((proposed - accepted) / proposed, 3) if proposed else None,
            rejected_verdicts=rejected_verdicts,
            input_tokens=sum(c["input_tokens"] for c in calls),
            output_tokens=sum(c["output_tokens"] for c in calls),
            thinking_tokens=sum(c["thinking_tokens"] for c in calls),
            total_tokens=sum(c["total_tokens"] for c in calls),
            seconds=round(sum(c["seconds"] for c in calls), 1),
        )
    return totals
