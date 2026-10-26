"""!
@file saved_summary_redaction_test.py
@brief Saved file summaries, directory summaries and business rules are
cleaned of secrets (T-078, US-034).
@details The file summary agent runs end to end on a small codebase with a
fake provider that answers with planted secrets and fails one file with an
error quoting one. The directory and business-rule writers are given state
holding such answers. Each saved file must keep its schema, identifiers,
paths and links, still load where the next stage reads it, and leave the
source files as they were. Every secret is fake; no real model, network or
vector store is used. absent() fails with a count, never the value.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fake_chat_provider import FakeProvider  # noqa: E402

import backend.commands as commands_module  # noqa: E402
import utils.secret_redaction as secret_redaction  # noqa: E402
from agent.BR_agent import BRAgent  # noqa: E402
from agent.BR_input_loader import load_rule_candidates  # noqa: E402
from agent.directory_agent import DirectoryAgent  # noqa: E402
from agent.file_summary_agent import FileSummaryAgent  # noqa: E402
from agent.llm import redacting  # noqa: E402
from agent.structured_output.BR_output import DiscardedRule, ValidatedRule  # noqa: E402
from agent.structured_output.directory_output import BusinessRulesOutput, DirectoryOutput  # noqa: E402
from agent.structured_output.UT_output import ValidatedRule as UnitTestInputRule  # noqa: E402
from backend.commands import Commands  # noqa: E402
from utils.output_redaction import save_json, secret_scope  # noqa: E402

FAKE = "Fake-Pass-111!"
KEY = "Fake-Key-123456789"
# Assembled at run time so the repository never holds a real-format token.
GITHUB = "ghp" + "_" + "A1b2C3d4E5f6G7h8I9j0" * 2
PLANTED = (FAKE, KEY, GITHUB)
P1 = "[REDACTED_PASSWORD_1]"
K1 = "[REDACTED_API_KEY_1]"

DB_CS = f"""namespace Shop
{{
    public class Db
    {{
        private const string AdminPassword = "{FAKE}";
        public static string ApiKey = "{KEY}";

        public bool CanLogIn(string input) => input == AdminPassword;
    }}
}}
"""

BROKEN_CS = "namespace Shop { public class Broken { } }\n"

# What a misbehaving provider answers for Db.cs: one secret with context,
# one only repeated (the key, which only the request gave away).
FILE_SUMMARY = {
    "path": "src/Db.cs",
    "summary": f"Checks logins against the admin password '{FAKE}'.",
    "dependencies": ["System"],
    "types": [{
        "name": "Db", "kind": "class",
        "description": f"Calls the payment service with {KEY}.",
        "plantuml": "class Db {\n  +CanLogIn(in input : string) : bool\n}",
    }],
    "business_rules": [{"rule": f"Login succeeds only when the input equals {FAKE}."}],
}

PROVIDER_ERROR = f"400 Bad Request: could not parse 'password = \"{FAKE}\"'"


def absent(text, *secrets):
    """Fail, without quoting them, if any planted secret is in text."""
    __tracebackhide__ = True  # pytest would print text with the frame's arguments
    leaked = sum(secret in text for secret in secrets or PLANTED)
    if leaked:
        raise AssertionError(f"{leaked} planted secret(s) found in the output")


def everything_under(folder):
    """The text of every file under folder, for "is the secret anywhere" checks."""
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(Path(folder).rglob("*")) if p.is_file())


class EchoingProvider(FakeProvider):
    """Answers with planted secrets; fails any request about Broken.cs with an error quoting one."""

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if any("Broken.cs" in str(message.content) for message in messages):
            self._record(messages, kwargs)
            raise RuntimeError(PROVIDER_ERROR)
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def echoing_model(**answers):
    return redacting(EchoingProvider)(answers={"FileSummaryOutput": FILE_SUMMARY, **answers})


@pytest.fixture
def codebase(tmp_path, monkeypatch):
    """A codebase, and an app folder to run in (the file summary agent writes relative to it)."""
    root = tmp_path / "Shop"
    (root / "src").mkdir(parents=True)
    (root / "src" / "Db.cs").write_text(DB_CS, encoding="utf-8")
    (root / "src" / "Broken.cs").write_text(BROKEN_CS, encoding="utf-8")

    app = tmp_path / "app"
    app.mkdir()
    monkeypatch.chdir(app)
    return root, app


def source_files(root):
    return {p.name: p.read_bytes() for p in (root / "src").iterdir()}


# ---------------------------------------------------------------------------
# File summaries and file-level business rules
# ---------------------------------------------------------------------------

def test_file_summaries_and_their_rules_are_saved_clean(codebase):
    root, app = codebase
    before = source_files(root)
    model = echoing_model()

    with secret_scope():
        FileSummaryAgent(llm=model).run(str(root))

    out = app / "agent" / "file_summary_agent_output" / "Shop"
    absent(everything_under(out))
    absent(model.sent_text())
    assert source_files(root) == before

    db = json.loads((out / "Db-cs__1.json").read_text(encoding="utf-8"))
    assert db["path"] == "src/Db.cs"
    assert db["summary"] == f"Checks logins against the admin password '{P1}'."
    assert db["types"][0]["description"] == f"Calls the payment service with {K1}."
    assert db["types"][0]["plantuml"] == FILE_SUMMARY["types"][0]["plantuml"]
    assert db["business_rules"] == [{
        "rule": f"Login succeeds only when the input equals {P1}.",
        "source_file": str(root / "src" / "Db.cs"),
    }]

    broken = json.loads((out / "Broken-cs__1.json").read_text(encoding="utf-8"))
    assert broken["path"] == str(root / "src" / "Broken.cs")
    assert broken["summary"] == f"Error generating summary: 400 Bad Request: could not parse 'password = \"{P1}\"'"

    # Keyed by the path the model wrote back; each rule's source_file is the real one.
    rules = json.loads((out / "business_rules" / "business_rules.json").read_text(encoding="utf-8"))
    assert rules == {"src/Db.cs": db["business_rules"]}

    # The next stage still reads them.
    inputs = load_rule_candidates(root, out / "business_rules" / "business_rules.json", False, app_dir=app)
    assert [(c.rule, c.source_file_paths) for c in inputs.candidates] == [
        (f"Login succeeds only when the input equals {P1}.", ["src/Db.cs"]),
    ]


MARKER = "<<this text cannot be checked>>"


@pytest.fixture
def unreadable_marker(monkeypatch):
    """The detector fails, quoting its input, but only on text holding MARKER."""
    real = secret_redaction._find

    def find(text):
        if MARKER in text:
            raise RuntimeError(f"could not read {text!r}")
        return real(text)

    monkeypatch.setattr(secret_redaction, "_find", find)


def test_a_summary_that_cannot_be_checked_is_not_saved_and_the_command_says_so(codebase, unreadable_marker, monkeypatch):
    root, app = codebase
    (root / "src" / "Broken.cs").unlink()
    out = app / "agent" / "file_summary_agent_output" / "Shop"
    save_json(out / "Db-cs__1.json", {"path": "src/Db.cs", "summary": "From the previous run."})

    model = echoing_model(FileSummaryOutput={**FILE_SUMMARY, "summary": f"{FILE_SUMMARY['summary']} {MARKER}"})
    monkeypatch.setattr(commands_module, "FileSummaryAgent", lambda: FileSummaryAgent(llm=model))
    commands = Commands()
    commands.app_dir = app

    result = commands.generate_file_summaries(str(root))

    assert result["success"] is False
    assert result["error"] == "Db-cs__1.json was not saved: The text could not be checked for secrets."
    assert json.loads((out / "Db-cs__1.json").read_text(encoding="utf-8"))["summary"] == "From the previous run."
    assert [p.name for p in out.iterdir()] == ["Db-cs__1.json"]
    absent(everything_under(app))


# ---------------------------------------------------------------------------
# Directory summaries and directory-level business rules
# ---------------------------------------------------------------------------

def test_directory_summaries_are_saved_clean_under_their_own_names(tmp_path):
    root = tmp_path / "Shop"
    agent = DirectoryAgent(model=echoing_model())
    out = tmp_path / "directory_agent_output"

    for current, summary in (
        (root / "src", DirectoryOutput(
            directory_name="src", directory_path="src",
            purpose=f"Error generating summary: 401 Unauthorized: bad credentials {GITHUB}",
        )),
        (root, DirectoryOutput(
            directory_name="Shop", directory_path=".",
            purpose="Data access for the shop.",
            responsibilities=[f"Rotating the admin password '{FAKE}'", "Payments"],
        )),
    ):
        agent.writer_node({
            "output_directory": str(out), "codebase_name": "Shop",
            "directory_summary": summary, "current_directory": str(current),
            "directory_path": str(root), "directories": [],
        })

    absent(everything_under(out))
    src = DirectoryOutput.model_validate_json((out / "Shop" / "src.json").read_text(encoding="utf-8"))
    top = DirectoryOutput.model_validate_json((out / "Shop" / "root_output" / "Shop.json").read_text(encoding="utf-8"))
    assert (src.directory_name, src.directory_path) == ("src", "src")
    assert src.purpose == "Error generating summary: 401 Unauthorized: bad credentials [REDACTED_TOKEN_1]"
    assert (top.directory_name, top.directory_path, top.purpose) == ("Shop", ".", "Data access for the shop.")
    assert top.responsibilities == [f"Rotating the admin password '{P1}'", "Payments"]


def test_directory_business_rules_are_saved_clean_and_still_load(tmp_path):
    root = tmp_path / "Shop"
    (root / "src").mkdir(parents=True)
    out = tmp_path / "app" / "agent" / "directory_agent_output"

    with secret_scope():
        # The key was given away by a request earlier in the run.
        redacting(FakeProvider)().invoke([("user", f'public static string ApiKey = "{KEY}";')])
        DirectoryAgent(model=echoing_model()).rollup_writer_node({
            "output_directory": str(out), "codebase_name": "Shop",
            "accumulated_business_rules": {str(root / "src"): BusinessRulesOutput(
                directory_name="src", directory_path="src",
                observed_rules=["Orders need a verified account."],
                inferred_rules=[f"Inference: payments are sent with {KEY}."],
            )},
        })

    path = out / "Shop" / "business_rules" / "business_rules.json"
    absent(path.read_text(encoding="utf-8"))
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert list(saved) == [str(root / "src")]
    assert saved[str(root / "src")] == {
        "directory_name": "src", "directory_path": "src",
        "observed_rules": ["Orders need a verified account."],
        "inferred_rules": [f"Inference: payments are sent with {K1}."],
    }

    inputs = load_rule_candidates(root, False, path, app_dir=tmp_path / "app")
    assert [(c.rule, c.origin) for c in inputs.candidates] == [
        ("Orders need a verified account.", "directory_observed"),
        (f"Inference: payments are sent with {K1}.", "directory_inferred"),
    ]


# ---------------------------------------------------------------------------
# Validated and discarded business rules
# ---------------------------------------------------------------------------

def test_validated_and_discarded_rules_keep_ids_links_and_schema(tmp_path):
    provenance = [{"origin": "file", "source_file_paths": ["src/Db.cs"]}]
    validated = ValidatedRule(
        id=4, rule="Only administrators may log in.", source_directory="src",
        source_file_paths=["src/Db.cs", "src/appsettings.json"],
        explanation={
            "evidence": {
                "src/Db.cs": [f'private const string AdminPassword = "{FAKE}";'],
                "src/appsettings.json": [f'"ApiKey": "{KEY}"'],
            },
            "reasoning": f"CanLogIn compares the input with {FAKE}.",
        },
        provenance=provenance,
    )
    discarded = DiscardedRule(
        id=5, rule="Payments are retried.", source_directory="src", source_file_paths=["src/Db.cs"],
        reason=f"Validation failed with error: 401 bad credentials {GITHUB}", provenance=provenance,
    )
    out = tmp_path / "BR_agent_output"

    BRAgent(model=echoing_model()).writer_node({
        "output_directory": str(out), "codebase_name": "Shop",
        "validated_rules": [validated], "discarded_rules": [discarded],
    })

    absent(everything_under(out))
    saved = json.loads((out / "Shop" / "validated_rules.json").read_text(encoding="utf-8"))
    rejected = json.loads((out / "Shop" / "discarded_rules.json").read_text(encoding="utf-8"))

    # What the unit and integration test steps load.
    rule = UnitTestInputRule.model_validate(saved[0])
    assert (rule.id, rule.source_directory, rule.source_file_paths) == (4, "src", validated.source_file_paths)
    assert [p.model_dump() for p in rule.provenance] == provenance
    assert rule.explanation.evidence == {
        "src/Db.cs": [f'private const string AdminPassword = "{P1}";'],
        "src/appsettings.json": [f'"ApiKey": "{K1}"'],
    }
    assert rule.explanation.reasoning == f"CanLogIn compares the input with {P1}."

    gone = DiscardedRule.model_validate(rejected[0])
    assert gone.id == 5
    # The run report tells errors from rejections by this prefix.
    assert gone.reason == "Validation failed with error: 401 bad credentials [REDACTED_TOKEN_1]"
