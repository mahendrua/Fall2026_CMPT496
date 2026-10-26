"""!
@file agent_redaction_test.py
@brief Every agent's AI request path, checked for secrets (T-077, US-034).
@details Each test runs an agent's real prompt-building code -- file
summaries, directory analysis, business-rule condensing and validation,
unit and integration test generation, test repair, and Consistency Review --
with fake secrets planted in source files, retrieved code, summaries,
business rules, generated tests and compiler errors. The model is
FakeProvider wrapped in agent.llm.redacting(), the same protection
build_chat_model() gives every real provider, so `sent` holds exactly what
would have left the machine. No real model, network or dotnet is used.
"""

import asyncio
import re
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_chat_provider import FakeProvider  # noqa: E402
# The Consistency Review fixture: a small codebase with its pipeline outputs.
from consistency_checker_test import PRICING_CS, Env, age  # noqa: E402

import agent.llm as llm_module  # noqa: E402
from agent.BR_agent import BRAgent, _condense_group, _validate_single_rule  # noqa: E402
from agent.consistency_checker import run_consistency_check  # noqa: E402
from agent.directory_agent import DirectoryAgent  # noqa: E402
from agent.file_summary_agent import FileSummaryAgent, _summarize_one  # noqa: E402
from agent.IT_agent import ITAgent, _generate_single_test as generate_integration_test  # noqa: E402
from agent.llm import redacting  # noqa: E402
from agent.structured_output.BR_output import CondensedRule, CondenserOutput, RuleCandidate, ValidatorOutput  # noqa: E402
from agent.structured_output.IT_output import IntegrationTest, WorkflowGroup  # noqa: E402
from agent.structured_output.UT_output import UnitTest, ValidatedRule  # noqa: E402
from agent.test_harness import TestCase, make_fixer  # noqa: E402
from agent.UT_agent import _generate_single_test as generate_unit_test  # noqa: E402
from utils.secret_redaction import SecretRedactionError  # noqa: E402

FAKE = "Fake-Pass-111!"
KEY = "Fake-Key-123456789"
SECRETS = (FAKE, KEY)
PASSWORD_1 = "[REDACTED_PASSWORD_1]"
API_KEY_1 = "[REDACTED_API_KEY_1]"

SOURCE = f"""namespace Shop
{{
    public class Db
    {{
        private const string AdminPassword = "{FAKE}";
        private const string ConnectionString = "Server=db;Database=Shop;User Id=sa;Password={FAKE};";
        public static string ApiKey = "{KEY}";

        public bool CanLogIn(string password) => password == AdminPassword;
    }}
}}
"""

# A summary written before T-077, quoting the secrets in prose.
SUMMARY = (
    f"The Db class checks logins against the admin password '{FAKE}' and calls "
    f"the payment service with the API key '{KEY}'."
)

# A business rule whose evidence was saved as JSON-escaped code.
RULE_TEXT = f"Login succeeds only when the password equals {FAKE}."
EVIDENCE = {"src/Db.cs": [f'private const string AdminPassword = "{FAKE}";']}

ANSWERS = {
    "FileSummaryOutput": {"path": "src/Db.cs", "summary": "Database helpers."},
    "DirectoryOutput": {"directory_name": "src", "directory_path": "src", "purpose": "Data access."},
    "ContextAnalysisOutput": {"sufficient_code_context": True, "sufficient_summary_context": True},
    "CondenserOutput": {"condensed_rules": [{"rule": "Logins are checked.", "source_rule_numbers": [1]}]},
    "ValidatorOutput": {"decision": "discard", "discard_reason": "Not supported by the code."},
    "UnitTest": {"id": 1, "rule": "r", "source_directory": "src", "unit_test": "[Fact]\npublic void T() { }"},
    "WorkflowGroups": {"workflows": [{"workflow_name": "Login", "workflow_description": "d", "rule_ids": [1]}]},
    "IntegrationTest": {"workflow_name": "Login", "workflow_description": "d", "integration_test": "[Fact]\npublic void T() { }"},
    "TestRepair": {"imports": ["using Xunit;"], "test_method": "[Fact]\npublic void Fixed() { }"},
    "ConsistencyAnalysisOutput": {"assessments": [], "findings": []},
}


def protected_model():
    return redacting(FakeProvider)(answers=ANSWERS)


def assert_protected(model, *placeholders):
    """The provider was called, saw no secret, and saw the placeholders."""
    assert model.sent, "the agent never called the model"
    sent = model.sent_text()
    for secret in SECRETS:
        assert secret not in sent
    for placeholder in placeholders:
        assert placeholder in sent


def retrieved_context(model):
    """Code and summary context formatted the way the agents' retrievers do."""
    formatter = BRAgent(model=model)
    code = formatter._format_code_result(
        SOURCE, {"file": "src/Db.cs", "name": "Db", "type": "class"}, "src"
    )
    summary = formatter._format_summary_result(
        SUMMARY, {"path": "src/Db.cs", "type": "file", "name": "Db.cs"}, "src"
    )
    return [code], [summary]


def validated_rule():
    return ValidatedRule(
        id=1, rule=RULE_TEXT, source_directory="src", source_file_paths=["src/Db.cs"],
        explanation={"evidence": EVIDENCE, "reasoning": "AdminPassword is compared directly."},
    )


# ---------------------------------------------------------------------------
# Source files
# ---------------------------------------------------------------------------

def test_file_summary_sends_redacted_source_and_leaves_the_file_alone(tmp_path):
    path = tmp_path / "Db.cs"
    path.write_text(SOURCE, encoding="utf-8")
    model = protected_model()
    agent = FileSummaryAgent(llm=model)

    _, output, error = asyncio.run(_summarize_one(agent.structured_llm, str(path)))

    assert error is None and output.summary == "Database helpers."
    assert_protected(model, PASSWORD_1, API_KEY_1)
    assert path.read_text(encoding="utf-8") == SOURCE


# ---------------------------------------------------------------------------
# Retrieved code and summaries
# ---------------------------------------------------------------------------

def test_directory_agent_sends_redacted_retrieved_context(tmp_path):
    model = protected_model()
    agent = DirectoryAgent(model=model)
    code, summaries = retrieved_context(model)
    directory = str(tmp_path / "src")

    analysis = agent.context_analyser_node({
        "current_directory": directory, "directory_path": str(tmp_path),
        "code_context": code, "summary_context": summaries,
        "codebase_k": 5, "file_summary_k": 5,
    })
    summary = agent.summarizer_node({
        "current_directory": directory, "child_summaries": {},
        "code_context": code, "summary_context": summaries,
    })

    assert analysis["sufficient_code_context"] is True
    assert summary["directory_summary"].purpose == "Data access."
    assert len(model.sent) == 2
    assert_protected(model, PASSWORD_1, API_KEY_1)


def test_business_rule_agent_sends_redacted_rules_and_context():
    model = protected_model()
    code, summaries = retrieved_context(model)
    candidate = RuleCandidate(rule=RULE_TEXT, source_directory="src", origin="file")
    rule = CondensedRule(id=1, rule=RULE_TEXT, source_directory="src")

    _, condensed, condense_error = asyncio.run(
        _condense_group(model.with_structured_output(CondenserOutput), "src", [candidate])
    )
    _, verdict, validate_error = asyncio.run(
        _validate_single_rule(model.with_structured_output(ValidatorOutput), rule, code, summaries, False)
    )

    assert condense_error is None and condensed[0].rule == "Logins are checked."
    assert validate_error is None and verdict.decision == "discard"
    assert_protected(model, PASSWORD_1, API_KEY_1)


# ---------------------------------------------------------------------------
# Test generation and repair
# ---------------------------------------------------------------------------

def test_unit_test_generation_sends_redacted_rule_evidence_and_context():
    model = protected_model()
    code, summaries = retrieved_context(model)

    _, output, error = asyncio.run(
        generate_unit_test(model.with_structured_output(UnitTest), validated_rule(), code, summaries)
    )

    assert error is None and output.unit_test.startswith("[Fact]")
    assert_protected(model, PASSWORD_1, API_KEY_1)


def test_integration_test_grouping_and_generation_send_redacted_rules():
    model = protected_model()
    code, summaries = retrieved_context(model)
    rule = validated_rule()

    groups = ITAgent(model=model).workflow_grouper_node({"validated_rules": [rule]})
    workflow = WorkflowGroup(workflow_name="Login", workflow_description="d", rules=[rule])
    _, output, error = asyncio.run(generate_integration_test(
        model.with_structured_output(IntegrationTest), workflow, [rule], code, summaries,
    ))

    assert [g.workflow_name for g in groups["workflow_groups"]] == ["Login"]
    assert error is None and output.integration_test.startswith("[Fact]")
    assert_protected(model, PASSWORD_1, API_KEY_1)


def test_repair_sends_redacted_test_errors_and_codebase_context():
    model = protected_model()
    body = (
        "[Fact]\npublic void Logs_in()\n{\n"
        f'    var credential = new NetworkCredential("ann", "{FAKE}");\n'
        f'    Assert.True(new Db().CanLogIn("{FAKE}"));\n'
        "}"
    )
    case = TestCase(
        name="UT_1", imports=["using Xunit;"], body=body,
        errors=[f'UT_1.cs(14,21): error CS0246: NetworkCredential not found near "{FAKE}"'],
    )

    fix = make_fixer(model, context_for=lambda _case: SOURCE)
    result = asyncio.run(fix(case, "Shop.Tests"))

    assert result == (["using Xunit;"], "[Fact]\npublic void Fixed() { }")
    assert_protected(model, PASSWORD_1, API_KEY_1)
    # The test case itself is local state, not changed by redaction.
    assert FAKE in case.body


# ---------------------------------------------------------------------------
# Consistency Review
# ---------------------------------------------------------------------------

def test_consistency_review_sends_redacted_source_and_artifacts(tmp_path):
    env = Env(tmp_path)
    source = env.root / "src" / "Pricing.cs"
    source.write_text(
        PRICING_CS.replace(
            "    public class Pricing\n    {\n",
            "    public class Pricing\n    {\n"
            f'        private const string AdminPassword = "{FAKE}";\n'
            f'        public static string ApiKey = "{KEY}";\n',
        ),
        encoding="utf-8",
    )
    age(source)
    env.write(
        pricing=f"Applies a discount once the admin password '{FAKE}' is checked.",
        rule_text=RULE_TEXT,
        assertion=f'Assert.True(new Db().CanLogIn("{FAKE}"));',
    )
    model = protected_model()

    run_consistency_check(env.root, targets=["src/Pricing.cs"], llm=model, app_dir=env.app)

    assert_protected(model, PASSWORD_1, API_KEY_1)
    assert FAKE in source.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Batches: one request per call, as the agents' asyncio.gather batches run
# ---------------------------------------------------------------------------

def test_agent_batch_requests_stay_separate():
    model = protected_model()
    structured = model.with_structured_output(ValidatorOutput)

    def rule(i):
        return CondensedRule(id=i, rule=f'Login requires password == "Fake-Pass-{i:03d}!".', source_directory="src")

    async def batch():
        return await asyncio.gather(*(
            _validate_single_rule(structured, rule(i), [], [], False) for i in range(20)
        ))

    results = asyncio.run(batch())

    assert all(error is None for _, _, error in results)
    assert len(model.sent) == 20
    for call in model.sent:
        user = call["messages"][-1].content
        assert PASSWORD_1 in user
        assert "[REDACTED_PASSWORD_2]" not in user
        assert not re.search(r"Fake-Pass-\d{3}!", user)


# ---------------------------------------------------------------------------
# A request that cannot be checked is never sent, and the error stays safe
# ---------------------------------------------------------------------------

@pytest.fixture
def redaction_fails(monkeypatch):
    def fail(*_args, **_kwargs):
        raise SecretRedactionError("Cannot check a Thing in an AI request for secrets.")

    monkeypatch.setattr(llm_module, "redact_model_input", fail)
    monkeypatch.setattr(llm_module, "redact_prompts", fail)


def test_failed_redaction_stops_every_agent_path(redaction_fails, tmp_path, capsys):
    model = protected_model()
    code, summaries = retrieved_context(model)
    path = tmp_path / "Db.cs"
    path.write_text(SOURCE, encoding="utf-8")
    rule = CondensedRule(id=1, rule=RULE_TEXT, source_directory="src")
    case = TestCase(name="UT_1", imports=[], body=f'var p = "{FAKE}";', errors=["error"])

    _, _, summary_error = asyncio.run(_summarize_one(FileSummaryAgent(llm=model).structured_llm, str(path)))
    _, _, validate_error = asyncio.run(
        _validate_single_rule(model.with_structured_output(ValidatorOutput), rule, code, summaries, False)
    )
    _, _, generate_error = asyncio.run(
        generate_unit_test(model.with_structured_output(UnitTest), validated_rule(), code, summaries)
    )
    repaired = asyncio.run(make_fixer(model, lambda _case: SOURCE)(case, "Shop.Tests"))

    for error in (summary_error, validate_error, generate_error):
        assert isinstance(error, SecretRedactionError)
    assert repaired is None

    assert model.sent == []
    assert FAKE not in capsys.readouterr().out


def test_failed_redaction_stops_consistency_review(redaction_fails, tmp_path):
    env = Env(tmp_path).write()
    model = protected_model()

    result = run_consistency_check(env.root, targets=["src/Pricing.cs"], llm=model, app_dir=env.app)

    # Reported as not checked, never quietly completed.
    assert model.sent == []
    assert result.status in ("partial", "failed")


# ---------------------------------------------------------------------------
# No production code can reach a provider except through agent/llm.py
# ---------------------------------------------------------------------------

PROVIDER_IMPORT = re.compile(
    r"^\s*(?:from|import)\s+(?:langchain_(?:openai|anthropic|google_genai|mistralai|groq|deepseek|xai)"
    r"|openai|anthropic|google\.genai|google\.generativeai|mistralai|groq)\b",
    re.MULTILINE,
)


def test_only_agent_llm_builds_provider_models():
    production = [
        path
        for folder in ("agent", "backend", "utils", "src")
        for path in (PROJECT_ROOT / folder).rglob("*.py")
    ] + [PROJECT_ROOT / "main.py"]

    offenders = [
        str(path.relative_to(PROJECT_ROOT))
        for path in production
        if path != PROJECT_ROOT / "agent" / "llm.py"
        and PROVIDER_IMPORT.search(path.read_text(encoding="utf-8", errors="replace"))
    ]

    assert offenders == []
