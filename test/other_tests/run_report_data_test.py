"""!
@file run_report_data_test.py
@brief Tests for backend/run_report_data.py, the reader behind the run report (US-049, T-164).
@details Uses a small made-up Python project ("shop") and hand-written outputs in
the pipeline's formats, so nothing here depends on a real run, an API key, or
on C# -- the reader has to work for any codebase.
"""

import json
import os
import time

from backend.run_report_data import collect, find_run_log, save


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def make_run(tmp_path):
    """A finished run on a small Python project. Returns (app_dir, codebase_root)."""
    app = tmp_path / "app"
    root = tmp_path / "projects" / "shop"
    for rel in ("shop/cart.py", "shop/models/item.py", "README.md"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("# code\n", encoding="utf-8")

    agent = app / "agent"
    files = agent / "file_summary_agent_output" / "shop"
    write(files / "cart-py__1.json", {
        "path": str(root / "shop" / "cart.py"),
        "summary": "Shopping cart.",
        "dependencies": ["decimal"],
        "types": [{"name": "Cart", "kind": "class", "description": "Holds items.",
                   "properties": [], "methods": [{"name": "total", "parameters": []}],
                   "inherits_from": [], "plantuml": "class Cart"}],
        "relationships": [],
        "external_relationships": [{"source": "Cart", "target": "Item", "relationship_type": "association"}],
        "business_rules": [{"rule": "A cart total is never negative.", "source_file": None}],
    })
    # The AI left a folder out of this path; the real file is shop/models/item.py.
    write(files / "item-py__1.json", {
        "path": str(root / "models" / "item.py"),
        "summary": "An item for sale.",
        "types": [{"name": "Item", "kind": "class", "description": "A product.", "plantuml": ""}],
    })

    folders = agent / "directory_agent_output" / "shop"
    write(folders / "root_output" / "shop.json", {
        "directory_name": "shop", "directory_path": ".",
        "purpose": "A small web shop.", "responsibilities": ["Checkout"],
    })
    write(folders / "shop.json", {"directory_name": "shop", "directory_path": "shop", "purpose": "The app."})
    # The AI wrote this folder's path with the folders above the codebase in front.
    write(folders / "shop_models.json", {
        "directory_name": "models", "directory_path": "projects/shop/shop/models", "purpose": "Data classes.",
    })
    write(folders / "business_rules" / "business_rules.json", {
        str(root): {"directory_name": "shop", "directory_path": ".", "observed_rules": ["Orders need a cart."], "inferred_rules": []},
        str(root / "shop"): {"directory_name": "shop", "directory_path": "shop", "observed_rules": [], "inferred_rules": []},
        str(root / "shop" / "models"): {"directory_name": "models", "directory_path": "shop/models",
                                        "observed_rules": [], "inferred_rules": ["Inference: prices are in dollars."]},
    })

    rules = agent / "BR_agent_output" / "shop"
    write(rules / "validated_rules.json", [
        {"id": 1, "rule": "A cart total is never negative.", "source_directory": "shop",
         "source_file_paths": [str(root / "shop" / "cart.py")],
         "explanation": {"evidence": {"cart.py": ["return max(total, 0)"]}, "reasoning": "It clamps at zero."}},
        {"id": 2, "rule": "Items have a price.", "source_directory": "shop/models",
         "source_file_paths": [str(root / "shop" / "models" / "item.py")],
         "explanation": {"evidence": {}, "reasoning": "Field is required."}},
    ])
    write(rules / "discarded_rules.json", [
        {"id": 3, "rule": "Uses a cache.", "source_directory": ".", "source_file_paths": [],
         "reason": "Implementation detail, not a business rule."},
        {"id": 4, "rule": "Refunds take 3 days.", "source_directory": ".", "source_file_paths": [],
         "reason": "Validation failed with error: 503 Service Unavailable"},
    ])

    unit = agent / "UT_agent_output" / "shop"
    generated = [
        {"id": 1, "rule": "A cart total is never negative.", "imports": ["import pytest"], "source_directory": "shop",
         "source_file_paths": [str(root / "shop" / "cart.py")], "unit_test": "def test_total(): ..."},
        {"id": 2, "rule": "Items have a price.", "imports": [], "source_directory": "shop/models",
         "source_file_paths": [], "unit_test": "{\"oops\": true}"},
    ]
    write(unit / "unit_tests.json", generated)
    write(unit / "validated_tests.json", [dict(generated[0], unit_test="def test_total():\n    assert True")])
    write(unit / "discarded_tests.json", [dict(generated[1], reason="the AI mixed its JSON answer into the test code")])
    write(unit / "test_report.json", {
        "generated": 2, "kept": 1, "repaired": ["UT_1"], "passed": 1, "failed": 1, "skipped": 0,
        "dropped": [{"name": "UT_2", "reason": "the AI mixed its JSON answer into the test code"}],
        "failed_tests": ["shop.tests.UT_1.test_total_rounding"], "project_error": "",
        "message": "Unit tests: 1 of 2 kept. 1 passed, 1 failed.",
    })

    integration = agent / "IT_agent_output" / "shop"
    workflow = {"workflow_name": "Checkout", "workflow_description": "Pay for a cart.", "rule_ids": [1, 2],
                "imports": ["pytest"], "integration_test": "def test_checkout():\\n    assert True"}
    write(integration / "integration_tests.json", [workflow])
    write(integration / "validated_tests.json", [workflow])
    write(integration / "discarded_tests.json", [])
    write(integration / "test_report.json", {
        "generated": 1, "kept": 1, "repaired": [], "dropped": [], "passed": 1, "failed": 0, "skipped": 0,
        "failed_tests": [], "project_error": "", "message": "Integration tests: 1 of 1 kept. 1 passed, 0 failed.",
    })

    write(app / "run_logs" / "shop_20200101-100000-aaaaaa.json", {
        "run_id": "20200101-100000-aaaaaa", "codebase": "shop", "command": "full_pipeline", "status": "success",
        "started_at": "2020-01-01T10:00:00", "finished_at": "2020-01-01T10:05:00", "elapsed_seconds": 300,
        "totals": {"total_tokens": 1000, "input_tokens": 700, "output_tokens": 300, "thinking_tokens": 100, "calls": 9},
        "stages": [{"stage": "generate_file_summaries", "status": "success", "calls": 2, "total_tokens": 400}],
    })
    return app, root


def by_id(items, key, value):
    return next(item for item in items if item[key] == value)


def test_rules_tests_and_links(tmp_path):
    app, root = make_run(tmp_path)
    data = collect("shop", app, codebase_path=root)

    assert [r["status"] for r in data["rules"]] == ["proven", "proven", "rejected", "error"]
    assert by_id(data["rules"], "id", 1)["evidence"] == {"cart.py": ["return max(total, 0)"]}
    assert by_id(data["rules"], "id", 1)["source_files"] == ["shop/cart.py"]

    tests = data["tests"]["unit"]["tests"] + data["tests"]["integration"]["tests"]
    ut1, ut2, it1 = (by_id(tests, "name", n) for n in ("UT_1", "UT_2", "IT_1"))
    assert (ut1["status"], ut1["failed_methods"], ut1["repaired"]) == ("failed", ["test_total_rounding"], True)
    assert ut1["code"] == "def test_total():\n    assert True"  # the kept version, not the first draft
    assert (ut2["status"], ut2["reason"]) == ("dropped", "the AI mixed its JSON answer into the test code")
    assert (it1["status"], it1["rule_ids"]) == ("passed", [1, 2])
    assert it1["code"] == "def test_checkout():\n    assert True"  # written-out line breaks made real

    assert by_id(data["rules"], "id", 1)["tests"] == ["UT_1", "IT_1"]
    assert by_id(data["rules"], "id", 3)["tests"] == []

    counts = data["overview"]["counts"]
    assert (counts["rules_proven"], counts["rules_rejected"], counts["rules_error"]) == (2, 1, 1)
    assert (counts["unit_failed"], counts["unit_dropped"], counts["integration_passed"]) == (1, 1, 1)
    assert data["run"]["tokens"]["total_tokens"] == 1000
    assert data["overview"]["summary"] == "A small web shop."


def test_wrong_paths_from_the_ai_are_replaced(tmp_path):
    app, root = make_run(tmp_path)
    data = collect("shop", app, codebase_path=root)

    assert sorted(f["path"] for f in data["files"]) == ["shop/cart.py", "shop/models/item.py"]
    assert sorted(f["path"] for f in data["folders"]) == [".", "shop", "shop/models"]
    assert by_id(data["folders"], "path", "shop/models")["files"] == ["shop/models/item.py"]
    assert by_id(data["folders"], "path", "shop/models")["inferred_rules"] == ["Inference: prices are in dollars."]
    assert any("wrong file path" in note for note in data["notes"])


def test_codebase_location_is_worked_out_when_not_given(tmp_path):
    app, root = make_run(tmp_path)
    data = collect("shop", app)

    assert data["codebase_path"] == str(root)
    assert by_id(data["rules"], "id", 1)["source_files"] == ["shop/cart.py"]


def test_missing_outputs_become_notes_not_errors(tmp_path):
    data = collect("nothing_here", tmp_path)

    assert data["files"] == [] and data["rules"] == [] and data["run"] is None
    assert data["tests"]["unit"]["tests"] == [] and not data["tests"]["unit"]["checked"]
    assert any("No full-pipeline run log" in note for note in data["notes"])
    assert any("No file summaries" in note for note in data["notes"])


def test_files_older_than_the_run_are_marked(tmp_path):
    app, root = make_run(tmp_path)
    log = app / "run_logs" / "shop_20200101-100000-aaaaaa.json"
    entry = json.loads(log.read_text(encoding="utf-8"))
    entry["started_at"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 60))
    log.write_text(json.dumps(entry), encoding="utf-8")

    leftover = app / "agent" / "file_summary_agent_output" / "shop" / "item-py__1.json"
    an_hour_ago = time.time() - 3600
    os.utime(leftover, (an_hour_ago, an_hour_ago))

    data = collect("shop", app, codebase_path=root)

    assert by_id(data["files"], "path", "shop/models/item.py")["old"] is True
    assert by_id(data["files"], "path", "shop/cart.py")["old"] is False
    assert any("older than this run" in note for note in data["notes"])


def test_newest_full_run_log_is_used(tmp_path):
    app, _ = make_run(tmp_path)
    logs = app / "run_logs"
    # Newer, but a single step: its log holds one step, not the run.
    write(logs / "shop_20200102-100000-bbbbbb.json", {"codebase": "shop", "command": "generate_unit_tests"})
    # Newer full run, but of another codebase whose name starts the same way.
    write(logs / "shop_v2_20200103-100000-cccccc.json", {"codebase": "shop_v2", "command": "full_pipeline"})

    assert find_run_log(app, "shop").name == "shop_20200101-100000-aaaaaa.json"


def test_saved_next_to_the_report(tmp_path):
    app, root = make_run(tmp_path)
    path = save(collect("shop", app, codebase_path=root), app)

    assert path == app / "run_reports" / "shop" / "run_report_data.json"
    assert json.loads(path.read_text(encoding="utf-8"))["codebase"] == "shop"
