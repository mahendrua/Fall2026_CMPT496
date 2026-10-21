"""!
@file mutation_testing_test.py
@brief Tests for backend/mutation_testing.py that need neither dotnet nor Stryker.
@details A small made-up C# codebase ("Shop") with the same awkward parts as
ConsoleTables: the repo's own test project, a sample app, a README the rules
point at, and rule files in both the old (full Windows paths) and new
(relative paths + provenance) formats. Stryker itself is replaced by a fake
that writes a report in the shape Stryker.NET 5.0.0 writes.
"""

import json
from pathlib import Path

import backend.mutation_testing as mt
from backend.mutation_testing import (
    _Progress,
    _failure_message,
    dotnet_test_filter,
    mutate_patterns,
    rule_code_files,
    run,
    select_tests,
)


def write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if isinstance(text, str) else json.dumps(text), encoding="utf-8")


TEST_SDK = '<Project Sdk="Microsoft.NET.Sdk"><ItemGroup><PackageReference Include="Microsoft.NET.Test.Sdk" Version="17.14.1" /></ItemGroup></Project>'
LIBRARY = '<Project Sdk="Microsoft.NET.Sdk"></Project>'


def make_project(tmp_path):
    """Returns (app_dir, codebase, test_dir)."""
    app = tmp_path / "app"
    codebase = tmp_path / "my code" / "Shop"
    test_dir = tmp_path / "generated" / "Shop.Tests"

    write(codebase / "src" / "Shop" / "Shop.csproj", LIBRARY)
    write(codebase / "src" / "Shop" / "Cart.cs", "class Cart {}\n")
    write(codebase / "src" / "Shop" / "Pricing" / "Tax.cs", "class Tax {}\n")
    write(codebase / "src" / "Shop.Sample" / "Shop.Sample.csproj", LIBRARY)
    write(codebase / "src" / "Shop.Sample" / "Program.cs", "class Program {}\n")
    write(codebase / "src" / "Shop.Tests" / "Shop.Tests.csproj", TEST_SDK)
    write(codebase / "src" / "Shop.Tests" / "CartTests.cs", "class CartTests {}\n")
    write(codebase / "Readme.md", "# Shop\n")

    write(test_dir / "Shop.Tests.csproj", TEST_SDK)
    for name in ("UT_1", "UT_2", "UT_3", "UT_10", "UT_9", "IT_1", "Helper"):
        write(test_dir / f"{name}.cs", f"public class {name} {{}}\n")

    rules = [
        # Old format: a full Windows path, a path starting at the codebase's own
        # folder name, and bare file names in the evidence.
        {"id": 1, "rule": "Carts add up.", "source_directory": "Shop",
         "source_file_paths": [str(codebase / "Readme.md"), "Shop/Cart.cs"],
         "explanation": {"evidence": {"Cart.cs": ["a + b"], "CartTests.cs": ["x"], "Program.cs": ["y"]},
                         "reasoning": "."}},
        # New format: paths relative to the codebase, plus provenance.
        {"id": 2, "rule": "Tax is added.", "source_directory": "src/Shop",
         "source_file_paths": ["src/Shop/Cart.cs"],
         "provenance": [{"origin": "file", "source_file_paths": ["src/Shop/Pricing/Tax.cs"]}],
         "explanation": {"evidence": {"src/Shop/Cart.cs": ["tax"]}, "reasoning": "."}},
        {"id": 3, "rule": "Its test fails.", "source_directory": ".", "source_file_paths": [],
         "explanation": {"evidence": {"Cart.cs": ["z"], "Nope.cs": ["?"]}, "reasoning": "."}},
        {"id": 4, "rule": "Its test was dropped.", "source_directory": ".", "source_file_paths": [],
         "explanation": {"evidence": {"Cart.cs": ["z"]}, "reasoning": "."}},
        {"id": 10, "rule": "Tested by UT_10.", "source_directory": ".", "source_file_paths": [],
         "explanation": {"evidence": {"Cart.cs": ["z"]}, "reasoning": "."}},
        {"id": 11, "rule": "No test at all.", "source_directory": ".", "source_file_paths": [],
         "explanation": {"evidence": {"Cart.cs": ["z"]}, "reasoning": "."}},
    ]
    write(app / "agent" / "BR_agent_output" / "Shop" / "validated_rules.json", rules)

    ut = app / "agent" / "UT_agent_output" / "Shop"
    write(ut / "unit_tests.json", [{"id": i, "rule": "r", "unit_test": "", "imports": []} for i in (1, 2, 3, 4, 10)])
    write(ut / "test_report.json", {
        "generated": 5, "kept": 4, "repaired": [],
        "dropped": [{"name": "UT_4", "reason": "does not compile (CS0103)"}],
        "passed": 3, "failed": 1, "skipped": 0,
        "failed_tests": ["Shop.Tests.UT_3.Breaks"], "project_error": "",
    })

    return app, codebase, test_dir


# ---------- choosing tests ----------

def test_only_passing_unit_tests_are_used(tmp_path):
    app, _, test_dir = make_project(tmp_path)
    used, left_out = select_tests(app, "Shop", test_dir)

    assert used == [{"name": "UT_1", "rule_id": 1}, {"name": "UT_2", "rule_id": 2}, {"name": "UT_10", "rule_id": 10}]

    why = {test["name"]: test["why"] for test in left_out}
    assert why == {"UT_3": "failed", "UT_4": "dropped", "IT_1": "integration", "UT_9": "stale"}  # not Helper

    # Each reason reads on after the test's name and says why it rules the test out.
    reasons = {test["name"]: test["reason"] for test in left_out}
    assert reasons["UT_3"] == ("already fails on the code as it is now, so it cannot show whether a change "
                               "broke anything (failing check: Breaks)")
    assert reasons["UT_4"] == ("was thrown away by the unit test step before it ever ran: "
                               "does not compile (CS0103)")
    assert reasons["IT_1"].startswith("is an integration test: it checks several rules at once")
    assert reasons["UT_9"] == "is left over from an older run and is not in the latest unit test results"


def test_a_passing_test_whose_file_is_gone_is_left_out(tmp_path):
    app, _, test_dir = make_project(tmp_path)
    (test_dir / "UT_2.cs").unlink()
    used, left_out = select_tests(app, "Shop", test_dir)
    assert "UT_2" not in [t["name"] for t in used]
    assert {"name": "UT_2", "rule_id": 2, "why": "missing",
            "reason": "passed, but its file is no longer in the generated test project"} in left_out


def test_filter_names_each_class_exactly():
    # Without the trailing dot, UT_1 would also run UT_10 and UT_11.
    assert dotnet_test_filter("Shop.Tests", ["UT_1", "UT_10"]) == (
        "FullyQualifiedName~Shop.Tests.UT_1.|FullyQualifiedName~Shop.Tests.UT_10."
    )


# ---------- choosing files ----------

def test_rule_files_from_old_and_new_rule_formats(tmp_path):
    app, codebase, _ = make_project(tmp_path)
    rules = json.loads((app / "agent" / "BR_agent_output" / "Shop" / "validated_rules.json").read_text())
    target = codebase / "src" / "Shop" / "Shop.csproj"

    per_rule, skipped, unresolved = rule_code_files(rules, codebase, "Shop", target)

    assert per_rule[1] == ["src/Shop/Cart.cs"]
    assert per_rule[2] == ["src/Shop/Cart.cs", "src/Shop/Pricing/Tax.cs"]  # provenance adds Tax.cs
    reasons = {item["path"]: (item["reason"], item["rules"]) for item in skipped}
    assert reasons["Readme.md"] == ("not C# code", [1])
    assert reasons["src/Shop.Tests/CartTests.cs"] == ("test code", [1])
    assert reasons["src/Shop.Sample/Program.cs"][0].startswith("in Shop.Sample.csproj, not the project")
    assert unresolved == [{"reference": "Nope.cs", "rules": [3]}]


def test_a_full_path_from_another_machine_outside_the_codebase_is_unresolved(tmp_path):
    _, codebase, _ = make_project(tmp_path)
    outside = tmp_path / "elsewhere" / "Cart2.cs"
    write(outside, "class X {}")
    rules = [{"id": 1, "source_file_paths": [str(outside)], "explanation": {"evidence": {}}}]
    per_rule, skipped, unresolved = rule_code_files(rules, codebase, "Shop", codebase / "src" / "Shop" / "Shop.csproj")
    assert per_rule[1] == [] and skipped == [] and unresolved[0]["rules"] == [1]


def test_mutate_patterns_stay_inside_the_project(tmp_path):
    _, codebase, _ = make_project(tmp_path)
    patterns = mutate_patterns(["src/Shop/Cart.cs", "src/Shop/Pricing/Tax.cs"], codebase,
                               codebase / "src" / "Shop" / "Shop.csproj")
    # No full path, so the spaces in "my code" never reach Stryker's patterns.
    assert patterns == ["**/Cart.cs", "**/Pricing/Tax.cs"]


# ---------- reading Stryker's console ----------

# Stryker.NET 5.0.0's console output on a small project, as it arrived.
CONSOLE = [
    "Version: 5.0.0\r\n\r\n[11:55:07 INF] Analysis starting.\r\n",
    "[11:55:07 INF] 16 mutants created\r\n",
    "[11:55:08 INF] 2     mutants got status NoCoverage.   Reason: Not covered by any test.\r\n",
    "[11:55:08 INF] 11    total mutants will be tested\r\n",
    "S", ".", ".", ".", ".", ".", ".", "T", ".", ".", ".",
    "\r\n\r\nYour json report has been generated at:\r\nfile://C:/x/reports/mutation-report.json\r\n",
    "[11:55:25 INF] Time Elapsed 00:00:30.5942567\r\n",
    "[11:55:25 INF] The final mutation score is 69.23 %\r\n",
]


def test_progress_counts_mutants_as_they_finish():
    messages = []
    watcher = _Progress(messages.append, every_seconds=0)
    for chunk in CONSOLE:
        watcher.feed(chunk)

    result = watcher.result(exit_code=0, timed_out=False)
    assert (result["total"], result["tested"]) == (11, 11)
    assert result["live_counts"] == {"survived": 1, "killed": 9, "timeout": 1}
    assert messages[0] == "Test strength: 11 planted mistakes to try..."
    assert messages[-1] == "Test strength: 11 of 11 planted mistakes tried"


def test_progress_stopped_part_way():
    watcher = _Progress(lambda message: None)
    for chunk in CONSOLE[:9]:
        watcher.feed(chunk)
    result = watcher.result(exit_code=None, timed_out=True)
    assert (result["total"], result["tested"]) == (11, 5)


def test_initial_test_failure_is_explained():
    output = "\r\n\r\nInitial testrun has failing tests.\r\n\r\nShop.Tests.UT_1.Adds\r\n\r\nAssert.Equal() Failure\r\n"
    message = _failure_message({"output": output, "exit_code": 1})
    assert "failed before any code was changed (Shop.Tests.UT_1.Adds)" in message
    assert "run the unit test step again" in message


# ---------- a whole run, with Stryker faked ----------

def fake_stryker_report(codebase, test_dir):
    cart = str(codebase / "src" / "Shop" / "Cart.cs")
    return {
        "schemaVersion": "2", "thresholds": {"high": 80, "low": 60},
        "projectRoot": str(codebase / "src" / "Shop"),
        "files": {cart: {"language": "cs", "source": "class Cart { int A(int a, int b) => a + b; }", "mutants": [
            {"id": "1", "mutatorName": "Arithmetic mutation", "replacement": "a - b", "status": "Killed",
             "location": {"start": {"line": 1, "column": 37}, "end": {"line": 1, "column": 42}},
             "coveredBy": ["t1", "t2"], "killedBy": ["t1"]},
            {"id": "2", "mutatorName": "Arithmetic mutation", "replacement": "a * b", "status": "Survived",
             "location": {"start": {"line": 1, "column": 37}, "end": {"line": 1, "column": 42}},
             "coveredBy": ["t2"], "killedBy": []},
        ]}},
        "testFiles": {
            str(test_dir / "UT_1.cs"): {"tests": [{"id": "t1", "name": "Shop.Tests.UT_1.Adds"}]},
            str(test_dir / "UT_2.cs"): {"tests": [{"id": "t2", "name": "Shop.Tests.UT_2.Taxes"}]},
        },
    }


def fake_stryker(monkeypatch, codebase, test_dir, *, timed_out=False, exit_code=0, output="", write_report=True):
    calls = []

    def fake_run(args, cwd, time_limit_seconds, on_progress):
        calls.append({"args": args, "cwd": Path(cwd), "limit": time_limit_seconds})
        out = Path(args[args.index("--output") + 1])
        if write_report:
            write(out / "reports" / "mutation-report.json", fake_stryker_report(codebase, test_dir))
        return {"exit_code": exit_code, "timed_out": timed_out, "output": output,
                "total": 2, "tested": 1 if timed_out else 2, "live_counts": {"killed": 1}}

    monkeypatch.setattr(mt, "find_stryker", lambda: (["dotnet-stryker"], "5.0.0", ""))
    monkeypatch.setattr(mt, "run_stryker", fake_run)
    return calls


def saved(app):
    return json.loads((app / "agent" / "MT_output" / "Shop" / "mutation_report.json").read_text(encoding="utf-8"))


def test_full_run_maps_results_to_rules(tmp_path, monkeypatch):
    app, codebase, test_dir = make_project(tmp_path)
    calls = fake_stryker(monkeypatch, codebase, test_dir)

    report = run(codebase, app, test_dir=test_dir, on_progress=lambda m: None)

    assert report["status"] == "finished"
    assert report["message"] == "Test strength: the tests caught 50% of 2 planted mistakes."
    assert saved(app)["overall"]["score"] == 50.0

    config = report["tool"]["config"]
    assert config["project"] == "Shop.csproj"
    assert config["mutate"] == ["**/Cart.cs", "**/Pricing/Tax.cs"]
    assert config["test-case-filter"] == (
        "FullyQualifiedName~Shop.Tests.UT_1.|FullyQualifiedName~Shop.Tests.UT_2.|FullyQualifiedName~Shop.Tests.UT_10."
    )
    assert config["disable-bail"] is True and config["coverage-analysis"] == "perTest"
    assert report["tool"]["version"] == "5.0.0"

    # Stryker runs from the generated test project, and writes outside the codebase.
    assert calls[0]["cwd"] == test_dir
    out = Path(calls[0]["args"][calls[0]["args"].index("--output") + 1])
    assert codebase not in out.parents and app in out.parents

    rules = {rule["id"]: rule for rule in report["rules"]}
    assert rules[1]["own_tests"]["caught"] == 1
    assert rules[2]["own_tests"] == {"covered": 2, "caught": 0, "caught_by_other_tests_only": 1, "survived": 1, "timeout": 0}
    assert rules[2]["surviving_examples"][0]["original"] == "a + b"
    assert rules[3]["note"] == ("Not measured, because its test UT_3 already fails on the code as it is now, so it "
                                "cannot show whether a change broke anything (failing check: Breaks).")
    assert rules[11]["note"] == "Not measured, because no unit test was generated for this rule."


def test_inputs_are_never_changed(tmp_path, monkeypatch):
    app, codebase, test_dir = make_project(tmp_path)
    fake_stryker(monkeypatch, codebase, test_dir)
    inputs = [app / "agent" / "BR_agent_output" / "Shop" / "validated_rules.json",
              app / "agent" / "UT_agent_output" / "Shop" / "test_report.json",
              test_dir / "UT_1.cs"]
    before = [path.read_bytes() for path in inputs]

    run(codebase, app, test_dir=test_dir, on_progress=lambda m: None)

    assert [path.read_bytes() for path in inputs] == before


def test_an_old_stryker_report_is_never_reused(tmp_path, monkeypatch):
    app, codebase, test_dir = make_project(tmp_path)
    stale = app / "agent" / "MT_output" / "Shop" / "stryker" / "reports" / "mutation-report.json"
    write(stale, {"files": {}})
    fake_stryker(monkeypatch, codebase, test_dir, exit_code=1, output="boom", write_report=False)

    report = run(codebase, app, test_dir=test_dir, on_progress=lambda m: None)

    assert report["status"] == "failed" and not stale.exists()
    assert "exit code 1" in report["message"]


def test_time_limit_keeps_what_finished(tmp_path, monkeypatch):
    app, codebase, test_dir = make_project(tmp_path)
    calls = fake_stryker(monkeypatch, codebase, test_dir, timed_out=True, write_report=False)

    report = run(codebase, app, time_limit_minutes=2, test_dir=test_dir, on_progress=lambda m: None)

    assert calls[0]["limit"] == 120
    assert report["status"] == "timed_out"
    assert report["message"].startswith("Stopped after 2 minutes with 1 of 2 planted mistakes tried (1 caught so far).")
    assert report["stryker"]["live_counts"] == {"killed": 1}
    assert report["rules"] == [] and report["overall"] is None


def test_missing_stryker_is_said_in_plain_words(tmp_path, monkeypatch):
    app, codebase, test_dir = make_project(tmp_path)
    monkeypatch.setattr(mt, "find_stryker", lambda: (None, None, "Stryker.NET is not installed. " + mt.INSTALL_HINT))

    report = run(codebase, app, test_dir=test_dir, on_progress=lambda m: None)

    assert report["status"] == "not_run"
    assert "dotnet tool install -g dotnet-stryker" in report["message"]
    assert saved(app)["status"] == "not_run"
    # What would have been measured is still recorded.
    assert [t["name"] for t in report["tests"]["used"]] == ["UT_1", "UT_2", "UT_10"]


def test_no_test_project(tmp_path):
    app, codebase, _ = make_project(tmp_path)
    report = run(codebase, app, test_dir=tmp_path / "nowhere", on_progress=lambda m: None)
    assert report["status"] == "not_run" and "Run the unit test step first" in report["message"]


def test_no_passing_tests(tmp_path):
    app, codebase, test_dir = make_project(tmp_path)
    write(app / "agent" / "UT_agent_output" / "Shop" / "test_report.json",
          {"generated": 5, "kept": 0, "dropped": [], "passed": 0, "failed": 0,
           "failed_tests": [], "project_error": "the test project did not build"})
    report = run(codebase, app, test_dir=test_dir, on_progress=lambda m: None)
    assert report["status"] == "not_run" and "No unit test passed" in report["message"]


def test_no_rules(tmp_path):
    app, codebase, test_dir = make_project(tmp_path)
    (app / "agent" / "BR_agent_output" / "Shop" / "validated_rules.json").unlink()
    report = run(codebase, app, test_dir=test_dir, on_progress=lambda m: None)
    assert report["status"] == "not_run" and "business rule step" in report["message"]


def test_an_unexpected_error_never_raises(tmp_path, monkeypatch):
    app, codebase, test_dir = make_project(tmp_path)

    def broken(*args):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(mt, "rule_code_files", broken)
    report = run(codebase, app, test_dir=test_dir, on_progress=lambda m: None)

    assert report["status"] == "failed" and "disk on fire" in report["message"]
    assert saved(app)["status"] == "failed"


# ---------- the backend command ----------

def mutation_command(tmp_path, monkeypatch, report):
    import backend.commands as commands

    cmd = commands.Commands()
    cmd.app_dir = tmp_path
    seen = {}

    def fake_run(codebase, app_dir, time_limit_minutes):
        seen["time_limit_minutes"] = time_limit_minutes
        return report

    monkeypatch.setattr(commands, "run_mutation_testing", fake_run)
    monkeypatch.setattr(commands, "write_run_report",
                        lambda *a, **k: {"path": str(tmp_path / "run_report.html"), "error": None, "diagram_error": ""})
    return cmd, seen


def test_command_returns_the_score_and_refreshes_the_run_report(tmp_path, monkeypatch):
    cmd, seen = mutation_command(tmp_path, monkeypatch, {
        "status": "finished", "message": "Test strength: 78% of 212 code changes caught.", "report_path": "x.json"})

    result = cmd.run_mutation_tests(str(tmp_path / "Shop"))

    assert result["success"] and result["command"] == "run_mutation_tests"
    assert result["result"]["message"] == "Test strength: 78% of 212 code changes caught."
    assert "warning" not in result["result"]
    assert result["result"]["run_report"] == str(tmp_path / "run_report.html")
    assert seen["time_limit_minutes"] == mt.TIME_LIMIT_MINUTES


def test_command_that_could_not_measure_still_succeeds_with_a_warning(tmp_path, monkeypatch):
    cmd, seen = mutation_command(tmp_path, monkeypatch, {
        "status": "not_run", "message": "Stryker.NET is not installed.", "report_path": "x.json"})

    result = cmd.run_mutation_tests(str(tmp_path / "Shop"), time_limit_minutes=5)

    assert result["success"] is True
    assert result["result"]["warning"] == "Stryker.NET is not installed."
    assert seen["time_limit_minutes"] == 5


def test_progress_from_the_reader_thread_keeps_the_app_request_id():
    # The app rejects any backend message without its request ID. Progress is
    # sent from the thread that reads Stryker's output, so the ID must reach it.
    import sys
    from backend.progress_logging import current_request_id, request_scope
    from backend.mutation_testing import run_stryker

    fake_stryker = [sys.executable, "-c",
                    "print('[10:00:00 INF] 3    total mutants will be tested'); print('S..')"]
    ids = []
    with request_scope("request-42"):
        result = run_stryker(fake_stryker, ".", 60, lambda message: ids.append(current_request_id()))

    assert result["tested"] == 3
    assert ids and set(ids) == {"request-42"}
