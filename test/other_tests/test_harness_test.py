"""!
@file test_harness_test.py
@brief Tests for agent/test_harness.py that need neither dotnet nor the AI.
@details Each case below is a failure seen in the Stateless run on 2026-09-24,
where one broken generated test stopped all 78 from running.
"""

from pathlib import Path

from agent.test_harness import (
    MAX_TEST_CHARS,
    TestCase,
    TestRun,
    artifacts_dir_for,
    clean_test,
    ensure_test_project,
    make_cases,
    normalize_imports,
    parse_build_output,
    parse_test_output,
    project_dir_for,
    render_test,
    namespace_for,
)

GOOD_TEST = "[Fact]\npublic void Adds()\n{\n    Assert.Equal(2, 1 + 1);\n}"


def test_imports_get_using_and_semicolon():
    # The unit agent returned bare "Xunit"; the integration agent strips
    # "using" and ";" on purpose.
    assert normalize_imports(["Xunit", "using System;", "System.Linq", " using Stateless ", ""]) == [
        "using Stateless;",
        "using System.Linq;",
        "using System;",
        "using Xunit;",
    ]


def test_imports_drop_paths_and_duplicates():
    assert normalize_imports(["src/Foo.cs", "// comment", "Xunit", "using Xunit;"]) == ["using Xunit;"]


def test_imports_keep_static_and_alias():
    assert normalize_imports(["using static System.Math;", "using Sm = Stateless.StateMachine<int, int>;"]) == [
        "using Sm = Stateless.StateMachine<int, int>;",
        "using static System.Math;",
    ]


def test_good_test_passes_through():
    body, imports, reason = clean_test(GOOD_TEST, ["Xunit"])
    assert reason == ""
    assert body == GOOD_TEST
    assert imports == ["using Xunit;"]


def test_runaway_answer_is_rejected():
    # Tests 4 and 12 were 173,208 and 281,814 characters: cut off at the cap.
    body, _, reason = clean_test(GOOD_TEST + "x" * MAX_TEST_CHARS, [])
    assert body is None
    assert "kept writing" in reason


def test_repetition_loop_is_rejected():
    looping = GOOD_TEST.replace("Adds", "Adds" + "CompleteDefinitiveResolved" * 30)
    body, _, reason = clean_test(looping, [])
    assert body is None
    assert "repeating" in reason


def test_json_leaked_into_body_is_recovered():
    # Test 31 carried the AI's whole JSON answer after some commentary.
    leaked = (
        '// JSON output below.\n",\n  "imports": ["using Xunit;"],\n'
        '  "unit_test": "[Fact]\\npublic void Adds()\\n{\\n    Assert.True(true);\\n}"\n}'
    )
    body, _, reason = clean_test(leaked, [])
    assert reason == ""
    assert body.startswith("[Fact]\npublic void Adds()")


def test_literal_backslash_n_is_unescaped():
    # 4 of 11 integration tests arrived on one line with "\n" as text.
    one_line = GOOD_TEST.replace("\n", "\\n")
    body, _, reason = clean_test(one_line, [])
    assert reason == ""
    assert body == GOOD_TEST


def test_real_newlines_leave_string_escapes_alone():
    with_escape = GOOD_TEST.replace("Assert.Equal(2, 1 + 1);", 'Assert.Equal("a\\nb", "a\\nb");')
    body, _, reason = clean_test(with_escape, [])
    assert reason == ""
    assert '"a\\nb"' in body


def test_markdown_fence_and_inline_usings_are_moved():
    fenced = "```csharp\nusing Stateless;\n\n" + GOOD_TEST + "\n```"
    body, imports, reason = clean_test(fenced, ["Xunit"])
    assert reason == ""
    assert body == GOOD_TEST
    assert imports == ["using Stateless;", "using Xunit;"]


def test_answer_without_test_attribute_is_rejected():
    body, _, reason = clean_test("public void NotATest() { }", [])
    assert body is None
    assert "[Fact]" in reason


def test_make_cases_names_by_id_and_keeps_rejected_text():
    class Item:
        def __init__(self, body):
            self.body = body

    cases, rejected = make_cases(
        "UT",
        [(12, Item(GOOD_TEST)), (12, Item(GOOD_TEST)), (4, Item(""))],
        body_of=lambda item: item.body,
        imports_of=lambda item: ["Xunit"],
    )
    assert [case.name for case in cases] == ["UT_12", "UT_12_b"]
    assert [case.name for case in rejected] == ["UT_4"]
    assert rejected[0].reason


def test_render_wraps_each_test_in_its_own_class():
    case = TestCase(name="UT_7", imports=["using Xunit;"], body=GOOD_TEST)
    text = render_test(case, "stateless.Tests")
    assert text.startswith("using Xunit;\n\nnamespace stateless.Tests;\n\npublic class UT_7\n{\n[Fact]")
    assert text.rstrip().endswith("}\n}")


def test_namespace_is_a_valid_identifier():
    assert namespace_for("stateless") == "stateless.Tests"
    assert namespace_for("CliWrap-main") == "CliWrap_main.Tests"
    assert namespace_for("2026 project") == "_2026_project.Tests"


def test_generated_project_is_external_and_references_the_primary_project(monkeypatch, tmp_path):
    import xml.etree.ElementTree as ET
    import agent.test_harness as harness

    codebase = tmp_path / "ConsoleTables source"
    target_project = codebase / "src" / "ConsoleTables" / "ConsoleTables.csproj"
    sample_project = codebase / "src" / "ConsoleTables.Sample" / "ConsoleTables.Sample.csproj"
    test_project = codebase / "src" / "ConsoleTables.Tests" / "ConsoleTables.Tests.csproj"
    target_project.parent.mkdir(parents=True)
    sample_project.parent.mkdir(parents=True)
    test_project.parent.mkdir(parents=True)
    target_project.write_text("<Project />", encoding="utf-8")
    sample_project.write_text("<Project />", encoding="utf-8")
    test_project.write_text("<Project />", encoding="utf-8")
    original_files = {path.relative_to(codebase) for path in codebase.rglob("*") if path.is_file()}

    temp_output = tmp_path / "system temp"
    monkeypatch.setattr(harness.tempfile, "gettempdir", lambda: str(temp_output))

    def create_xunit_project(command, **kwargs):
        generated_dir = Path(command[command.index("-o") + 1])
        generated_dir.mkdir(parents=True, exist_ok=True)
        (generated_dir / "ConsoleTables.Tests.csproj").write_text(
            '<Project Sdk="Microsoft.NET.Sdk">\n'
            '  <ItemGroup>\n'
            '    <PackageReference Include="xunit" Version="2.9.3" />\n'
            '  </ItemGroup>\n'
            '  <ItemGroup>\n'
            '    <ProjectReference Include="..\\**\\*.csproj" Exclude="..\\**\\*.Tests.csproj" />\n'
            '  </ItemGroup>\n'
            '</Project>',
            encoding="utf-8",
        )

    monkeypatch.setattr(harness.subprocess, "run", create_xunit_project)
    output_dir = Path(project_dir_for(codebase, "ConsoleTables"))
    ensure_test_project(str(output_dir), str(codebase), "ConsoleTables")

    assert not output_dir.is_relative_to(codebase)
    generated_project = ET.parse(output_dir / "ConsoleTables.Tests.csproj").getroot()
    references = generated_project.findall(".//ProjectReference")
    assert [reference.get("Include") for reference in references] == [str(target_project.resolve())]
    assert generated_project.find(".//PackageReference[@Include='xunit']") is not None
    isolation_props = ET.parse(output_dir / "Directory.Build.props").getroot()
    excludes = isolation_props.findtext(".//DefaultItemExcludes")
    assert "**/artifacts/**" in excludes
    assert {path.relative_to(codebase) for path in codebase.rglob("*") if path.is_file()} == original_files
    assert "system temp" in str(output_dir)


def test_build_and_test_keep_artifacts_outside_the_project(monkeypatch, tmp_path):
    import agent.test_harness as harness

    test_dir = tmp_path / "ConsoleTables.Tests"
    test_dir.mkdir()
    artifacts_dir = artifacts_dir_for(test_dir)
    assert not Path(artifacts_dir).is_relative_to(test_dir)

    commands = []

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def run(command, **kwargs):
        commands.append(command)
        return Result()

    monkeypatch.setattr(harness.subprocess, "run", run)

    assert harness.build(str(test_dir)) == ({}, [])
    harness.run_tests(
        str(test_dir), "ConsoleTables.Tests", "UT", str(tmp_path / "reports"), "unit.html"
    )

    assert [command[command.index("--artifacts-path") + 1] for command in commands] == [
        artifacts_dir,
        artifacts_dir,
    ]


def test_build_errors_are_split_by_test_file():
    output = "\n".join([
        r"C:\x\stateless.Tests\UT_19.cs(20,5): error CS0200: Property 'State.StateName' cannot be assigned to -- it is read only [C:\x\stateless.Tests\stateless.Tests.csproj]",
        r"C:\x\stateless.Tests\UT_19.cs(20,5): error CS0200: Property 'State.StateName' cannot be assigned to -- it is read only [C:\x\stateless.Tests\stateless.Tests.csproj]",
        r"C:\x\stateless.Tests\IT_3.cs(1,1): error CS1002: ; expected [C:\x\stateless.Tests\stateless.Tests.csproj]",
        r"C:\x\stateless.Tests\stateless.Tests.csproj : error NU1008: Projects that use central package version management should not define the version [C:\x\stateless.Tests\stateless.Tests.csproj]",
        "Build FAILED.",
    ])
    errors_by_file, other_errors = parse_build_output(output)

    assert errors_by_file == {
        "UT_19": ["line 20: CS0200: Property 'State.StateName' cannot be assigned to -- it is read only"],
        "IT_3": ["line 1: CS1002: ; expected"],
    }
    assert len(other_errors) == 1
    assert "NU1008" in other_errors[0]


def test_test_output_counts_and_failed_names():
    output = "\n".join([
        "  Passed stateless.Tests.UT_1.Works [2 ms]",
        "  Failed stateless.Tests.UT_12.GuardBlocks [5 ms]",
        "  Failed stateless.Tests.UT_30.Throws(value: 3) [1 ms]",
        "Failed!  - Failed:     2, Passed:    52, Skipped:     0, Total:    54, Duration: 254 ms - stateless.Tests.dll (net10.0)",
    ])
    assert parse_test_output(output) == (
        52, 2, 0, ["stateless.Tests.UT_12.GuardBlocks", "stateless.Tests.UT_30.Throws"]
    )


def test_test_output_counts_at_normal_verbosity():
    output = "\n".join([
        "  Failed stateless.Tests.UT_72.GraphNames [19 ms]",
        "Test Run Failed.",
        "Total tests: 62",
        "     Passed: 57",
        "     Failed: 5",
        " Total time: 0.7579 Seconds",
    ])
    assert parse_test_output(output) == (57, 5, 0, ["stateless.Tests.UT_72.GraphNames"])


def test_run_message_is_honest():
    kept = [TestCase(name=f"UT_{i}", imports=[], body=GOOD_TEST) for i in range(50)]
    kept[0].repairs = 1
    dropped = [TestCase(name=f"UT_{i}", imports=[], body="", reason="x") for i in range(50, 78)]
    run = TestRun(kind="Unit", generated=78, kept=kept, dropped=dropped, passed=52, failed=5)

    assert run.message() == (
        "Unit tests: 50 of 78 kept (1 repaired by the AI, 28 dropped because they "
        "did not compile). 52 passed, 5 failed."
    )
    assert run.needs_attention()


def test_project_error_message_says_nothing_ran():
    run = TestRun(kind="Integration", generated=11, project_error="NU1008 ...")
    assert "none ran" in run.message()


def _scripted_check(monkeypatch, tmp_path, builds, cases, fix):
    """Run check_tests() with dotnet replaced by a scripted list of builds."""
    import agent.test_harness as harness

    results = iter(builds)
    monkeypatch.setattr(harness, "ensure_test_project", lambda *args: None)
    monkeypatch.setattr(harness, "build", lambda test_dir: (next(results), []))
    monkeypatch.setattr(harness, "run_tests", lambda *args: (len(cases), 0, 0, [], ""))
    Path(harness.project_dir_for(tmp_path, "demo")).mkdir(parents=True)

    return harness.check_tests("Unit", "UT", cases, [], str(tmp_path), "demo", str(tmp_path / "out"), fix=fix)


def test_each_test_gets_its_own_repair_tries(monkeypatch, tmp_path):
    # UT_2's errors only appear after UT_1 is repaired (the compiler reports
    # in layers). With a shared round count it got one try; now it gets two.
    first = TestCase(name="UT_1", imports=[], body=GOOD_TEST)
    layered = TestCase(name="UT_2", imports=[], body=GOOD_TEST)
    builds = [{"UT_1": ["e"]}, {"UT_2": ["e"]}, {"UT_2": ["e"]}, {}]

    async def fix(case, namespace):
        return ["Xunit"], GOOD_TEST

    run = _scripted_check(monkeypatch, tmp_path, builds, [first, layered], fix)

    assert [case.name for case in run.kept] == ["UT_1", "UT_2"]
    assert (first.repairs, layered.repairs) == (1, 2)
    assert run.dropped == []


def test_test_still_broken_after_its_tries_is_dropped(monkeypatch, tmp_path):
    stubborn = TestCase(name="UT_3", imports=[], body=GOOD_TEST)
    builds = [{"UT_3": ["line 5: CS0122: inaccessible"]}] * 3 + [{}]

    async def fix(case, namespace):
        return ["Xunit"], GOOD_TEST

    run = _scripted_check(monkeypatch, tmp_path, builds, [stubborn], fix)

    assert run.kept == []
    assert run.dropped[0].reason.startswith("still does not compile after 2 AI repair(s)")


def test_xunit_extension_attributes_count_as_tests():
    # CliWrap's generated tests use [SkippableFact(Timeout = 15000)]; they
    # should reach the compiler (and the AI repair), not be thrown out here.
    skippable = GOOD_TEST.replace("[Fact]", "[SkippableFact(Timeout = 15000)]")
    body, _, reason = clean_test(skippable, [])
    assert reason == ""
    assert body == skippable
