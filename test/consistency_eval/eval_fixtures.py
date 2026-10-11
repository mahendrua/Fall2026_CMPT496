"""
@file eval_fixtures.py
@brief Writes an evaluation case to disk the way the pipeline would.
@details Every artifact goes through the pipeline's own models, so a fixture
cannot drift from the real formats:

    summaries   FileSummaryOutput    agent/file_summary_agent_output/<case>/<stem>__<n>.json
    rules       BR ValidatedRule     agent/BR_agent_output/<case>/validated_rules.json
                BR DiscardedRule     .../discarded_rules.json
    tests       UTV UnitTest         agent/UT_agent_output/<case>/validated_tests.json

The codebase folder is named after the case. Paths are written as the
current pipeline writes them (root-relative), except where a case gives an
absolute path on purpose. The source is made older than its outputs, as
after a normal run. Nothing is written outside the given base directory.
"""

import os
from pathlib import Path

from agent.structured_output.BR_output import DiscardedRule, ValidatedRule
from agent.structured_output.file_summary_output import FileSummaryOutput
from agent.structured_output.UTV_output import UnitTest

TEST_TEMPLATE = "[Fact]\npublic void Test_{id}()\n{{\n    {body}\n}}"


def build_case(case, base):
    """
    @brief Writes one case under base and returns (codebase_root, app_dir).
    """
    base = Path(base)
    root = base / "codebase" / case.name
    app = base / "app"
    out = app / "agent"

    for rel, text in case.files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        stamp = os.path.getmtime(path) - 120
        os.utime(path, (stamp, stamp))

    summary_dir = out / "file_summary_agent_output" / case.name
    summary_dir.mkdir(parents=True, exist_ok=True)
    for rel, spec in case.summaries.items():
        copies = 2 if rel in case.duplicate_summaries else 1
        for n in range(1, copies + 1):
            stem = Path(rel).name.replace(".", "-")
            (summary_dir / f"{stem}__{n}.json").write_text(
                _summary(root / rel, spec).model_dump_json(indent=2), encoding="utf-8",
            )

    rule_texts = {r["id"]: r["rule"] for r in case.rules}
    rule_files = {r["id"]: r["files"] for r in case.rules}

    if case.write_rules:
        rules_dir = out / "BR_agent_output" / case.name
        rules_dir.mkdir(parents=True, exist_ok=True)
        _write(rules_dir / "validated_rules.json", [_rule(r) for r in case.rules])
        _write(rules_dir / "discarded_rules.json", [
            DiscardedRule(source_directory="src", **d).model_dump() for d in case.discarded_rules
        ])

    if case.write_tests:
        tests_dir = out / "UT_agent_output" / case.name
        tests_dir.mkdir(parents=True, exist_ok=True)
        _write(tests_dir / "validated_tests.json", [
            UnitTest(
                id=t["id"],
                rule=t["rule"] or rule_texts[t["id"]],
                imports=["using System;", "using System.Collections.Generic;", "using Xunit;"],
                source_directory="src",
                source_file_paths=rule_files.get(t["id"], []),
                unit_test=TEST_TEMPLATE.format(id=t["id"], body=t["body"]),
            ).model_dump()
            for t in case.tests
        ])
    return root, app


def _summary(path, spec):
    def params(items):
        return [dict(p, direction="in") for p in items]

    return FileSummaryOutput.model_validate({
        "path": str(path),
        "summary": spec["summary"],
        "functions": [
            {"name": f["name"], "description": f["description"], "visibility": "public",
             "parameters": params(f["parameters"])}
            for f in spec.get("functions", [])
        ],
        "types": [
            {
                "name": t["name"], "kind": t["kind"], "description": t["description"], "plantuml": "",
                "methods": [
                    # visibility is always set: the schema's None default does not load back.
                    {"name": m["name"], "description": m["description"], "visibility": "public",
                     "parameters": params(m["parameters"])}
                    for m in t["methods"]
                ],
            }
            for t in spec.get("types", [])
        ],
    })


def _rule(spec):
    return ValidatedRule(
        id=spec["id"],
        rule=spec["rule"],
        source_directory=spec["directory"],
        source_file_paths=spec["files"],
        explanation={"evidence": {}, "reasoning": "Fixture rule."},
        provenance=[{"origin": spec["origin"], "source_file_paths": spec["files"]}],
    ).model_dump()


def _write(path, data):
    import json
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
