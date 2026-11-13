"""!
@file generated_artifact_redaction_test.py
@brief Generated tests, Consistency Review results and the exports built
from saved outputs are cleaned of secrets (T-078, US-034).
@details Covers the unit and integration test files, the test harness's
.cs files, results and HTML report, Consistency Review results and review
notes, and what reproduces saved outputs elsewhere: the run report, the
mutation report, the UML PDF and the Markdown export. Older outputs saved
before this cleaning existed are planted with secrets to show the exports
do not copy them out, while the old files themselves are left as they were.
Every secret is fake; no model, network, dotnet, Java or Stryker is used.
absent() fails with a count, never the value.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
# Fixtures of the suites that own these outputs.
from consistency_checker_test import (  # noqa: E402
    ADD_LINE, DISCOUNT, PRICING_CS, SUMMARY_CLAIM, Env, FakeLLM, age, answer, check, handles_of, proposed,
)
from run_report_data_test import make_run, write  # noqa: E402

import agent.test_harness as test_harness  # noqa: E402
import backend.mutation_testing as mutation_testing  # noqa: E402
import backend.run_report_diagram as run_report_diagram  # noqa: E402
import utils.json_to_markdown as json_to_markdown  # noqa: E402
import utils.secret_redaction as secret_redaction  # noqa: E402
import utils.uml_json_to_pdf as uml  # noqa: E402
from agent.consistency_store import load_check_result, set_review  # noqa: E402
from agent.IT_agent import ITAgent  # noqa: E402
from agent.request_redaction import redact_model_input  # noqa: E402
from agent.structured_output.IT_output import IntegrationTest  # noqa: E402
from agent.structured_output.UT_output import UnitTest  # noqa: E402
from agent.structured_output.UTV_output import UnitTest as UnitTestToValidate  # noqa: E402
from agent.test_harness import TestCase, TestRun, run_tests, write_results, write_tests  # noqa: E402
from agent.UT_agent import UTAgent  # noqa: E402
from backend.run_report import write_run_report  # noqa: E402
from backend.run_report_data import collect  # noqa: E402
from backend.run_report_html import render  # noqa: E402
from utils.output_redaction import sanitize_text, secret_scope  # noqa: E402

FAKE = "Fake-Pass-111!"
KEY = "Fake-Key-123456789"
# Assembled at run time so the repository never holds a real-format token.
GITHUB = "ghp" + "_" + "A1b2C3d4E5f6G7h8I9j0" * 2
PLANTED = (FAKE, KEY, GITHUB)
P1 = "[REDACTED_PASSWORD_1]"
P2 = "[REDACTED_PASSWORD_2]"
K1 = "[REDACTED_API_KEY_1]"
T1 = "[REDACTED_TOKEN_1]"

MARKER = "<<this text cannot be checked>>"

TEST_BODY = (
    "[Fact]\npublic void Admin_can_log_in()\n{\n"
    f'    var password = "{FAKE}";\n'
    "    Assert.True(new Db().CanLogIn(password));\n"
    f'    Assert.Equal("{FAKE}", Db.AdminPassword);\n'
    "}"
)
CLEAN_BODY = TEST_BODY.replace(FAKE, P1)


def absent(text, *secrets):
    """Fail, without quoting them, if any planted secret is in text."""
    __tracebackhide__ = True  # pytest would print text with the frame's arguments
    leaked = sum(secret in text for secret in secrets or PLANTED)
    if leaked:
        raise AssertionError(f"{leaked} planted secret(s) found in the output")


def kept(text, secret):
    """Fail, without quoting it, unless secret is still in text."""
    __tracebackhide__ = True  # pytest would print text with the frame's arguments
    if secret not in text:
        raise AssertionError("the value was expected to be left in place")


def everything_under(folder):
    return "\n".join(
        p.read_text(encoding="utf-8", errors="replace") for p in sorted(Path(folder).rglob("*")) if p.is_file()
    )


def learn_from_request(text):
    """What T-077 does with a request: clean it, and tell the open scope what it found."""
    redact_model_input([("user", text)])


@pytest.fixture
def unreadable_marker(monkeypatch):
    """The detector fails, quoting its input, but only on text holding MARKER."""
    real = secret_redaction._find

    def find(text):
        if MARKER in text:
            raise RuntimeError(f"could not read {text!r}")
        return real(text)

    monkeypatch.setattr(secret_redaction, "_find", find)


# ---------------------------------------------------------------------------
# Generated unit and integration tests
# ---------------------------------------------------------------------------

def test_generated_unit_tests_are_saved_clean_and_still_load(tmp_path):
    test = UnitTest(id=3, rule="Only the admin may log in.", imports=["using Xunit;"],
                    source_directory="src", source_file_paths=["src/Db.cs"], unit_test=TEST_BODY)

    UTAgent(model=object()).writer_node({
        "codebase_name": "Shop", "output_directory": str(tmp_path), "unit_tests": [test],
    })

    folder = tmp_path / "Shop"
    absent(everything_under(folder))
    assert (folder / "unit_tests.txt").read_text(encoding="utf-8") == f"using Xunit;\n\n{CLEAN_BODY}\n\n"

    # What the unit test validation step loads.
    [loaded] = [UnitTestToValidate.model_validate(t) for t in json.loads((folder / "unit_tests.json").read_text(encoding="utf-8"))]
    assert (loaded.id, loaded.imports, loaded.source_file_paths) == (3, ["using Xunit;"], ["src/Db.cs"])
    assert loaded.unit_test == CLEAN_BODY


def test_generated_integration_tests_are_saved_clean_and_still_load(tmp_path):
    test = IntegrationTest(
        workflow_name="Login", workflow_description=f"Logs in with the token {GITHUB}.",
        rule_ids=[3, 4], imports=["using Xunit;"], integration_test=TEST_BODY,
    )

    ITAgent(model=object()).writer_node({
        "codebase_name": "Shop", "output_directory": str(tmp_path), "integration_tests": [test],
    })

    folder = tmp_path / "Shop"
    absent(everything_under(folder))
    assert (folder / "integration_tests.txt").read_text(encoding="utf-8") == (
        f"// Workflow: Login\n// Description: Logs in with the token {T1}.\n"
        f"// Covered Business Rules: [3, 4]\n\nusing Xunit;\n\n{CLEAN_BODY}\n\n"
    )
    [loaded] = [IntegrationTest.model_validate(t) for t in json.loads((folder / "integration_tests.json").read_text(encoding="utf-8"))]
    assert (loaded.workflow_name, loaded.rule_ids, loaded.integration_test) == ("Login", [3, 4], CLEAN_BODY)


# ---------------------------------------------------------------------------
# The test harness: .cs files, results, and the HTML test report
# ---------------------------------------------------------------------------

def test_test_files_and_results_are_saved_clean(tmp_path):
    kept_case = TestCase(name="UT_3", imports=["using Xunit;"], body=TEST_BODY)
    dropped = TestCase(name="UT_4", imports=["using Xunit;"], body="[Fact]\npublic void Broken() {",
                       reason=f'does not compile and the AI could not fix it (line 5: CS1002: ; expected near password = "{FAKE}")')

    write_tests(tmp_path / "Shop.Tests", "Shop.Tests", "UT", [kept_case, dropped])
    run = TestRun(kind="Unit", generated=2, kept=[kept_case], dropped=[dropped], passed=0, failed=1,
                  failed_tests=["Shop.Tests.UT_3.Admin_can_log_in"])
    write_results(run, tmp_path / "out", lambda case: {"name": case.name, "imports": case.imports, "unit_test": case.body})

    absent(everything_under(tmp_path))
    cs = (tmp_path / "Shop.Tests" / "UT_3.cs").read_text(encoding="utf-8")
    assert cs == f"using Xunit;\n\nnamespace Shop.Tests;\n\npublic class UT_3\n{{\n{CLEAN_BODY}\n}}\n"
    # In memory the case is unchanged; only what is written is cleaned.
    kept(kept_case.body, FAKE)

    report = json.loads((tmp_path / "out" / "test_report.json").read_text(encoding="utf-8"))
    assert (report["generated"], report["kept"], report["failed"]) == (2, 1, 1)
    assert report["failed_tests"] == ["Shop.Tests.UT_3.Admin_can_log_in"]
    assert report["dropped"] == [{"name": "UT_4", "reason": dropped.reason.replace(FAKE, P1)}]
    [validated] = json.loads((tmp_path / "out" / "validated_tests.json").read_text(encoding="utf-8"))
    assert validated == {"name": "UT_3", "imports": ["using Xunit;"], "unit_test": CLEAN_BODY}


CONSOLE = (
    "  Failed Shop.Tests.UT_3.Admin_can_log_in [3 ms]\n"
    "Failed!  - Failed:     1, Passed:     2, Skipped:     0, Total:     3\n"
)


def fake_dotnet_test(html):
    """Stands in for `dotnet test`: writes its HTML report where it was asked to."""
    def run(args, **kwargs):
        scratch = args[args.index("--results-directory") + 1]
        name = next(a for a in args if a.startswith("html;LogFileName=")).split("=", 1)[1]
        Path(scratch, name).write_text(html, encoding="utf-8")
        return subprocess.CompletedProcess(args, 1, stdout=CONSOLE, stderr="")
    return run


def test_the_html_test_report_keeps_no_actual_secret_value(tmp_path, monkeypatch):
    # A failed assertion shows the value the running code produced: here
    # the real password, where the generated test expected its placeholder.
    html = f"<html><body><pre>Assert.Equal() Failure\nExpected: {P1}\nActual:   {FAKE}</pre></body></html>"
    monkeypatch.setattr(test_harness.subprocess, "run", fake_dotnet_test(html))

    with secret_scope():
        learn_from_request(f'private const string AdminPassword = "{FAKE}";')
        result = run_tests(str(tmp_path / "Shop.Tests"), "Shop.Tests", "UT", str(tmp_path / "results"), "unit_test_report.html")

    passed, failed, skipped, failed_tests, report_path = result
    assert (passed, failed, skipped, failed_tests) == (2, 1, 0, ["Shop.Tests.UT_3.Admin_can_log_in"])
    page = Path(report_path).read_text(encoding="utf-8")
    absent(page)
    assert page == html.replace(FAKE, P2)


def test_an_html_test_report_that_cannot_be_checked_is_not_kept(tmp_path, monkeypatch, capsys, unreadable_marker):
    monkeypatch.setattr(test_harness.subprocess, "run", fake_dotnet_test(f"<pre>{FAKE} {MARKER}</pre>"))

    *_, report_path = run_tests(str(tmp_path / "Shop.Tests"), "Shop.Tests", "UT", str(tmp_path / "results"), "unit_test_report.html")

    assert report_path == ""
    assert list((tmp_path / "results").iterdir()) == []
    out = capsys.readouterr().out
    absent(out)
    assert "The HTML test report could not be checked for secrets, so it was not kept." in out


# ---------------------------------------------------------------------------
# Consistency Review
# ---------------------------------------------------------------------------

class RedactingFakeLLM(FakeLLM):
    """FakeLLM behind the request redaction production applies (agent/llm.py's _RedactedInput)."""

    def invoke(self, messages):
        safe, _ = redact_model_input(messages)
        return super().invoke(safe)


SECRET_LINE = f'var password = "{FAKE}";'
# The same line as the model is sent it, so as it quotes it.
SEEN_LINE = f'var password = "{P1}";'


@pytest.fixture
def review_env(tmp_path):
    env = Env(tmp_path)
    source = env.root / "src" / "Pricing.cs"
    source.write_text(PRICING_CS.replace(ADD_LINE, f"{SECRET_LINE}\n            {ADD_LINE}"), encoding="utf-8")
    age(source)
    env.write()
    return env


def inconsistent_summary(title="Artifact disagrees with Discount", excerpt=f"{SEEN_LINE}\n{ADD_LINE}", lines=(7, 8)):
    """The model reports a finding citing the line that holds the secret, then repeats the secret."""
    finding = proposed("source_summary", [SUMMARY_CLAIM], excerpt=excerpt, lines=lines, title=title)
    finding.explanation = f"Unless the input is {FAKE}, the code adds 10%; the summary describes a reduction."
    return RedactingFakeLLM(lambda m: answer(handles_of(m), {"SUMMARY": "inconsistent"}, [finding]))


# Known gap B (T-079): the model sees placeholders, so it quotes them, and the
# finding used to be rejected because its excerpt was compared with the raw
# source. It is now compared with what the model was sent, just as strictly.

def test_a_finding_quoting_a_line_that_held_a_secret_is_accepted_at_its_lines(review_env):
    with secret_scope():
        result = check(review_env, inconsistent_summary(), save=True)

    [finding] = result.findings
    source = finding.claims[0].artifact
    assert (source.path, source.line_start, source.line_end) == ("src/Pricing.cs", 7, 8)
    assert finding.claims[0].excerpt == f"{SEEN_LINE}\n{ADD_LINE}"
    # The evidence is the lines as the model saw them, never the raw source.
    assert finding.evidence[0].excerpt.split("\n")[0].strip() == SEEN_LINE
    absent(everything_under(review_env.app / "agent" / "consistency_output"))


@pytest.mark.parametrize("excerpt, lines", [
    # The raw value: the model was never sent it, so it cannot be evidence.
    (f"{SECRET_LINE}\n{ADD_LINE}", (7, 8)),
    # A placeholder the request did not hold there.
    (f'var password = "[REDACTED_PASSWORD_2]";\n{ADD_LINE}', (7, 8)),
    # A line that is in no version of the file.
    ('var password = "letmein";', (7, 7)),
    # Placeholders are not wildcards.
    ("var password = [REDACTED_PASSWORD_1] + 1;", (7, 7)),
])
def test_made_up_or_raw_evidence_is_still_rejected(review_env, excerpt, lines):
    with secret_scope():
        result = check(review_env, inconsistent_summary(excerpt=excerpt, lines=lines))

    assert result.findings == []
    [assessment] = [a for a in result.assessments if a.artifact.kind == "summary"]
    assert not assessment.accepted
    assert "source excerpt does not appear in the code in scope" in assessment.problem
    absent(json.dumps(result.model_dump(mode="json")))


def test_review_results_keep_no_source_secret_and_still_link_and_load(review_env):
    env = review_env
    before = (env.root / "src" / "Pricing.cs").read_bytes()

    with secret_scope():
        result = check(env, inconsistent_summary(), save=True)

    [finding] = result.findings
    saved_file = env.app / "agent" / "consistency_output" / "Shop" / "consistency_result_selected.json"
    absent(saved_file.read_text(encoding="utf-8"))
    assert (env.root / "src" / "Pricing.cs").read_bytes() == before

    [loaded] = load_check_result("Shop", env.app, scope="selected").findings
    assert loaded.id == finding.id
    source = loaded.claims[0].artifact
    assert (source.path, source.symbol, source.line_start, source.line_end) == ("src/Pricing.cs", "Pricing.Discount", 7, 8)
    assert source.content_hash == finding.claims[0].artifact.content_hash
    excerpt = loaded.evidence[0].excerpt
    assert f'var password = "{P1}";' in excerpt and ADD_LINE in excerpt
    # The excerpt keeps the request's placeholder, so the explanation's
    # repetition is numbered after it: numbers are local to each file.
    assert loaded.explanation == f"Unless the input is [REDACTED_PASSWORD_2], the code adds 10%; the summary describes a reduction."

    set_review("Shop", finding.id, "reviewed", note=f"The token {GITHUB} was rotated.", app_dir=env.app)

    absent(everything_under(env.app / "agent" / "consistency_output"))
    review = load_check_result("Shop", env.app, scope="selected").findings[0].review
    assert (review.status, review.note) == ("reviewed", f"The token {T1} was rotated.")


def test_a_title_lengthened_by_its_placeholder_still_loads(review_env):
    title = "T" * 180 + ' password "Pw-12345"'
    assert len(title) == 200

    check(review_env, inconsistent_summary(title=title), save=True)

    [loaded] = load_check_result("Shop", review_env.app, scope="selected").findings
    absent(loaded.title, "Pw-12345")
    assert len(loaded.title) == 200 and loaded.title == "T" * 180 + ' password "[REDACTED'


# ---------------------------------------------------------------------------
# Exports built from saved outputs
# ---------------------------------------------------------------------------

def plant_old_outputs(app, root):
    """Outputs saved before T-078, still holding secrets as the model or the code gave them."""
    files = app / "agent" / "file_summary_agent_output" / "shop"
    summary = json.loads((files / "cart-py__1.json").read_text(encoding="utf-8"))
    summary["summary"] = f"Shopping cart. Logs in with the admin password '{FAKE}'."
    summary["types"][0]["plantuml"] = f'class Cart {{\n  -password : string = "{FAKE}"\n}}'
    write(files / "cart-py__1.json", summary)

    rules = app / "agent" / "BR_agent_output" / "shop" / "validated_rules.json"
    saved = json.loads(rules.read_text(encoding="utf-8"))
    saved[0]["explanation"]["evidence"] = {"cart.py": [f'api_key = "{KEY}"']}
    write(rules, saved)

    report = app / "agent" / "UT_agent_output" / "shop" / "test_report.json"
    data = json.loads(report.read_text(encoding="utf-8"))
    data["message"] = f"Unit tests: 1 of 2 kept. The login failed with token {GITHUB}."
    write(report, data)
    return [files / "cart-py__1.json", rules, report]


def test_the_run_report_does_not_copy_out_old_secrets(tmp_path, monkeypatch):
    app, root = make_run(tmp_path)
    old = plant_old_outputs(app, root)
    stored = {path: path.read_bytes() for path in old}
    monkeypatch.setattr(run_report_diagram, "render_svg", lambda puml, jar: f"<svg><desc>{puml}</desc></svg>")

    result = write_run_report(root, app)

    folder = app / "run_reports" / "shop"
    assert result["error"] is None and result["path"] == str(folder / "run_report.html")
    assert sorted(p.name for p in folder.iterdir()) == [
        "project_diagram.puml", "project_diagram.svg", "run_report.html", "run_report_data.json",
    ]
    absent(everything_under(folder))

    data = json.loads((folder / "run_report_data.json").read_text(encoding="utf-8"))
    cart = next(f for f in data["files"] if f["path"] == "shop/cart.py")
    assert cart["summary"] == f"Shopping cart. Logs in with the admin password '{P1}'."
    assert next(r for r in data["rules"] if r["id"] == 1)["evidence"] == {"cart.py": [f'api_key = "{K1}"']}
    page = (folder / "run_report.html").read_text(encoding="utf-8")
    assert f"admin password '{P1}'" in page

    # Not bulk-edited: the old outputs themselves are byte for byte as they were.
    for path in old:
        assert path.read_bytes() == stored[path], "an old output was rewritten"


def test_the_report_page_is_not_changed_when_nothing_is_secret(tmp_path):
    # The page's own scripts and styles must not look like secrets.
    app, root = make_run(tmp_path)
    page = render(collect("shop", app, codebase_path=root), {"svg": '<svg viewBox="0 0 10 10"></svg>', "types": [], "edges": []})

    out, report = sanitize_text(page)

    assert report.total == 0 and out == page


def test_the_mutation_report_is_saved_clean(tmp_path, monkeypatch):
    def measure(report, codebase, app_dir, out_dir, test_dir, on_progress):
        report["status"] = "failed"
        report["message"] = f"Stryker stopped: the initial test run failed with password = \"{FAKE}\"."
        report["rules"] = [{"id": 1, "rule": f"The API key {KEY!r} is required.", "files": ["src/Db.cs"]}]

    monkeypatch.setattr(mutation_testing, "_measure", measure)
    (tmp_path / "Shop").mkdir()

    report = mutation_testing.run(tmp_path / "Shop", tmp_path, on_progress=lambda message: None)

    saved = Path(report["report_path"]).read_text(encoding="utf-8")
    absent(saved)
    data = json.loads(saved)
    assert data["status"] == "failed" and data["rules"][0]["id"] == 1
    assert data["message"] == f'Stryker stopped: the initial test run failed with password = "{P1}".'


def old_summary(tmp_path):
    path = tmp_path / "summaries" / "Db-cs__1.json"
    write(path, {
        "path": "src/Db.cs",
        "summary": f"Checks the admin password '{FAKE}'.",
        "dependencies": ["System"],
        "types": [{"name": "Db", "kind": "class", "description": f"Sends the API key {KEY!r}.",
                   "plantuml": f'class Db {{\n  -password : string = "{FAKE}"\n}}'}],
        "relationship_plantuml": "",
        "business_rules": [],
    })
    return path


def test_the_uml_pdf_and_its_plantuml_are_built_from_a_cleaned_summary(tmp_path, monkeypatch):
    import reportlab.rl_config as rl_config

    path = old_summary(tmp_path)
    snippets = []
    monkeypatch.setattr(uml, "render_plantuml", lambda snippet, stem, temp_dir, args: snippets.append(snippet))
    # Uncompressed, so the PDF's text can be searched.
    monkeypatch.setattr(rl_config, "pageCompression", 0)
    monkeypatch.setattr(sys, "argv", ["uml_json_to_pdf", str(path), "-o", str(tmp_path / "Db.pdf")])

    assert uml.build_report(uml.parse_args()) == []

    absent("\n".join(snippets))
    assert any(f'-password : string = "{P1}"' in s for s in snippets)
    pdf = (tmp_path / "Db.pdf").read_bytes().decode("latin-1")
    absent(pdf)
    assert "REDACTED_PASSWORD_1" in pdf
    kept(path.read_text(encoding="utf-8"), FAKE)


def test_the_markdown_export_is_cleaned(tmp_path, capsys):
    path = old_summary(tmp_path)

    written = json_to_markdown.process_file(path, path.parent, tmp_path / "markdown")
    json_to_markdown.process_file(path, path.parent, tmp_path / "markdown", to_stdout=True, overwrite=True)

    markdown = written.read_text(encoding="utf-8")
    printed = capsys.readouterr().out
    absent(markdown)
    absent(printed)
    assert f"'{P1}'" in markdown and f"'{P1}'" in printed


# ---------------------------------------------------------------------------
# Outputs saved before T-078, read by today's exports (T-079, item D)
# ---------------------------------------------------------------------------

def test_older_outputs_are_cleaned_by_the_exports_and_themselves_left_unchanged(tmp_path, monkeypatch):
    # Outside any backend run, so only contextual detection applies. The only
    # trace of this secret is a PlantUML private field (known gap A).
    import hashlib
    import reportlab.rl_config as rl_config

    path = tmp_path / "summaries" / "Vault-cs__1.json"
    write(path, {
        "path": "src/Vault.cs", "summary": "Opens the vault.", "dependencies": [],
        "types": [{"name": "Vault", "kind": "class", "description": "Holds credentials.",
                   "plantuml": f'class Vault {{\n  -password : string = "{FAKE}"\n  +Open() : bool\n}}'}],
        "relationship_plantuml": "", "business_rules": [],
    })
    before = hashlib.sha256(path.read_bytes()).hexdigest()

    snippets = []
    monkeypatch.setattr(uml, "render_plantuml", lambda snippet, stem, temp_dir, args: snippets.append(snippet))
    monkeypatch.setattr(rl_config, "pageCompression", 0)
    monkeypatch.setattr(sys, "argv", ["uml_json_to_pdf", str(path), "-o", str(tmp_path / "Vault.pdf")])
    uml.build_report(uml.parse_args())
    markdown = json_to_markdown.process_file(path, path.parent, tmp_path / "markdown")

    absent("\n".join(snippets), FAKE)
    assert any(f'-password : string = "{P1}"' in s and "+Open() : bool" in s for s in snippets)
    absent((tmp_path / "Vault.pdf").read_bytes().decode("latin-1"), FAKE)
    absent(markdown.read_text(encoding="utf-8"), FAKE)
    # Not cleaned in place: the older file is byte for byte as it was.
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
