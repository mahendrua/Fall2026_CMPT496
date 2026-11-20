"""
@file rag_lsp_integration_test.py
@brief Regression tests for merging Wyatt's RAG pipelines and Ian's LSP and
       language work onto cmpt-496.

No AI or network: the model is a fake that records what it was sent.
"""

import asyncio
import inspect
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import backend.commands as commands_module  # noqa: E402
from agent import IT_agent, UT_agent  # noqa: E402
from agent.structured_output.UT_output import ValidatedRule  # noqa: E402

SECRET = "sk-live-9f8A7b6C5d4E3f2G1h0J"
SECRET_LINE = f'api_key = "{SECRET}"'


class FakeLLM:
    """Records every prompt; answers structured-output calls with `answer`."""

    def __init__(self, answer=None):
        self.answer = answer
        self.prompts = []

    def with_structured_output(self, *args, **kwargs):
        return self

    def invoke(self, messages):
        self.prompts.append(messages)
        return self.answer

    async def ainvoke(self, messages):
        self.prompts.append(messages)
        return self.answer


def rule(**overrides):
    data = dict(
        id=1,
        rule="Orders of 100.00 or more get 10% off.",
        source_directory=".",
        source_file_paths=["pricing.py"],
        explanation={"evidence": {"pricing.py": ["if subtotal >= 100"]}, "reasoning": "r"},
    )
    data.update(overrides)
    return ValidatedRule.model_validate(data)


def prompt_text(llm):
    return "\n".join(str(content) for _role, content in llm.prompts[-1])


# ---------------------------------------------------------------------------
# Pipeline choice: Simple stays the default, unknown names are refused
# ---------------------------------------------------------------------------

def test_rag_pipeline_defaults_to_simple_and_rejects_unknown_names():
    assert commands_module.rag_pipeline_name(None) == "simple"
    assert commands_module.rag_pipeline_name("") == "simple"
    assert commands_module.rag_pipeline_name(" Corrective ") == "corrective"
    with pytest.raises(ValueError, match="Unknown RAG pipeline"):
        commands_module.rag_pipeline_name("fancy")


def test_commands_and_agents_keep_old_call_shapes():
    for name in ("generate_unit_tests", "generate_integration_tests"):
        params = inspect.signature(getattr(commands_module.Commands, name)).parameters
        assert list(params)[:5] == ["self", "codebase", "selected_rules", "validated_rules_path", "individualStep"]
        assert params["rag_pipeline"].default == "simple"
    assert inspect.signature(commands_module.Commands.full_pipeline).parameters["rag_pipeline"].default == "simple"
    for agent in (UT_agent.UTAgent, IT_agent.ITAgent):
        params = inspect.signature(agent.run).parameters
        assert params["rag_pipeline"].default == "simple"
        assert params["lsp_context"].default is None


@pytest.mark.parametrize("agent_cls", [UT_agent.UTAgent, IT_agent.ITAgent])
def test_every_pipeline_builds_and_simple_graph_is_ready_at_startup(agent_cls):
    agent = agent_cls(model=FakeLLM())
    assert agent.graph is not None          # callers may invoke agent.graph directly
    for pipeline in ("simple", "adaptive", "corrective"):
        assert agent.build_graph(pipeline) is not None


def test_web_search_packages_load_only_when_corrective_needs_them():
    code = (
        "import sys; import agent.UT_agent, agent.IT_agent; "
        "print('langchain_community' in sys.modules)"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True, timeout=300)
    assert out.stdout.strip().splitlines()[-1] == "False", out.stderr[-2000:]


# ---------------------------------------------------------------------------
# Corrective web search never sends a secret to the search engine
# ---------------------------------------------------------------------------

def test_web_queries_with_a_secret_are_dropped():
    queries = ["pytest approx floats", SECRET_LINE, "   ", "decimal ROUND_HALF_UP python", "extra one"]
    assert UT_agent.safe_web_queries(queries) == ["pytest approx floats", "decimal ROUND_HALF_UP python", "extra one"]
    assert UT_agent.safe_web_queries(None) == []


# ---------------------------------------------------------------------------
# Generation prompts carry Ian's LSP context and Wyatt's web context together
# ---------------------------------------------------------------------------

def test_unit_test_prompt_includes_lsp_and_web_context_in_both_call_styles():
    answer = SimpleNamespace(imports=[], unit_test="def test_x():\n    assert True")
    llm = FakeLLM(answer)
    # Ian's call style: lsp_context as the 8th positional argument.
    asyncio.run(UT_agent._generate_single_test(
        llm, rule(), ["CODE"], ["SUMMARY"], "python", "function", None, ["LSP: apply_discount(subtotal)"]))
    assert "LSP: apply_discount(subtotal)" in prompt_text(llm)
    # Both by keyword, as test_generator_node calls it.
    asyncio.run(UT_agent._generate_single_test(
        llm, rule(), ["CODE"], ["SUMMARY"], "python",
        lsp_context=["LSP: Inventory.reserve"], web_context=["[WEB RESULT] decimal docs"]))
    text = prompt_text(llm)
    assert "LSP: Inventory.reserve" in text and "[WEB RESULT] decimal docs" in text


def test_integration_test_prompt_includes_lsp_web_and_language_instructions():
    from agent.structured_output.IT_output import IntegrationTest

    answer = IntegrationTest(workflow_name="w", workflow_description="d", integration_test="def test_w(): pass")
    llm = FakeLLM(answer)
    workflow = SimpleNamespace(workflow_name="Checkout", workflow_description="Price an order", rules=[rule()])
    asyncio.run(IT_agent._generate_single_test(
        llm, workflow, workflow.rules, ["CODE"], ["SUMMARY"],
        lsp_context=["LSP: checkout(cart, inventory)"], target_language="python",
        web_context=["[WEB RESULT] pytest raises"]))
    text = prompt_text(llm)
    assert "LSP: checkout(cart, inventory)" in text
    assert "[WEB RESULT] pytest raises" in text
    assert "python" in text.lower()


def test_integration_runner_keeps_ai_repair_switched_on():
    # Wyatt's branch had set fix=None while timing runs, which silently turned
    # off AI repair of integration tests that do not compile.
    source = inspect.getsource(IT_agent.ITAgent.runner_node)
    assert "fix=None" not in source
    assert "make_csharp_fixer(self.llm, context_for)" in source


# ---------------------------------------------------------------------------
# Every language harness saves results with secrets removed (T-078)
# ---------------------------------------------------------------------------

HARNESSES = ["test_harness", "python_test_harness", "java_test_harness", "javascript_test_harness",
             "c_test_harness", "cpp_test_harness", "kotlin_test_harness"]


class FakeRun:
    def __init__(self, case):
        self.kept = [case]
        self.dropped = [case]

    def summary(self):
        return {"generated": 1, "kept": 1, "failed_tests": [f"error near {SECRET_LINE}"]}

    def message(self):
        return "done"


@pytest.mark.parametrize("module_name", HARNESSES)
def test_harness_results_are_saved_without_secrets(module_name, tmp_path):
    module = __import__(f"agent.{module_name}", fromlist=["write_results"])
    case = SimpleNamespace(name="UT_1", body=SECRET_LINE, imports=[], reason=f"failed: {SECRET_LINE}", errors=[])
    module.write_results(FakeRun(case), str(tmp_path), lambda c: {"id": 1, "unit_test": c.body})
    written = {p.name: p.read_text(encoding="utf-8") for p in tmp_path.glob("*.json")}
    assert {"validated_tests.json", "discarded_tests.json", "test_report.json"} <= set(written)
    for name, text in written.items():
        json.loads(text)
        assert SECRET not in text, f"{module_name} wrote the secret into {name}"


def test_python_unit_tests_json_is_saved_without_secrets(tmp_path):
    from agent.python_test_harness import persist_python_generated_tests
    from agent.structured_output.UT_output import UnitTest

    test = UnitTest(imports=[], unit_test=f"def test_key():\n    {SECRET_LINE}\n    assert api_key", id=1,
                    rule="r", source_directory=".", source_file_paths=["pricing.py"])
    persist_python_generated_tests(tmp_path, [test])
    text = (tmp_path / "unit_tests.json").read_text(encoding="utf-8")
    assert SECRET not in text and "def test_key" in text


# ---------------------------------------------------------------------------
# Ian's LSP context: saved inside the project, cleaned of secrets, optional
# ---------------------------------------------------------------------------

def test_lsp_context_is_stored_inside_the_project():
    from lsp.context_storage import get_context_storage_dir

    folder = get_context_storage_dir(REPO / "test" / "fixtures" / "python_e2e" / "function_lab")
    assert folder.is_relative_to(REPO / "agent" / "lsp_context_output")


def test_lsp_context_is_saved_without_secrets_and_loads_back(tmp_path):
    from lsp.context import CodebaseContext
    from lsp.context_storage import load_codebase_context, save_codebase_context

    codebase = tmp_path / "shop"
    codebase.mkdir()
    context = CodebaseContext(codebase_path=codebase)
    context.warnings.append(f"pricing.py: {SECRET_LINE}")
    saved = save_codebase_context(context, output_dir=tmp_path / "out")
    assert SECRET not in Path(saved).read_text(encoding="utf-8")
    assert load_codebase_context(codebase, output_dir=tmp_path / "out") is not None


# ---------------------------------------------------------------------------
# Python integration tests follow the lab's test mode (script labs cannot be
# imported, and the harness rejects script tests that import student code)
# ---------------------------------------------------------------------------

FIXTURES = REPO / "test" / "fixtures" / "python_e2e"


def test_python_integration_instructions_have_a_script_mode():
    from agent.testing_language import integration_test_generation_instructions

    script = integration_test_generation_instructions("python", test_mode="script")
    function = integration_test_generation_instructions("python")
    assert "may contain only: import subprocess" in script
    assert "CHECKPOINT_CODEBASE_PATH" in script and "integration_test" in script
    assert "subprocess" not in function
    assert integration_test_generation_instructions("csharp") == \
        integration_test_generation_instructions("csharp", test_mode="script")


@pytest.mark.parametrize("lab,source,expect_script", [
    ("script_lab", "grade_script.py", True),
    ("function_lab", "math_lab.py", False),
])
def test_integration_prompt_uses_the_labs_python_test_mode(lab, source, expect_script):
    from agent.structured_output.IT_output import IntegrationTest

    answer = IntegrationTest(workflow_name="w", workflow_description="d", integration_test="def test_w(): pass")
    llm = FakeLLM(answer)
    workflow = SimpleNamespace(workflow_name="W", workflow_description="d",
                               rules=[rule(source_file_paths=[source])])
    asyncio.run(IT_agent._generate_single_test(
        llm, workflow, workflow.rules, ["CODE"], ["SUMMARY"],
        target_language="python", codebase_path=str(FIXTURES / lab)))
    text = prompt_text(llm)
    assert ("SCRIPT INTEGRATION TEST REQUIREMENTS" in text) is expect_script
    if expect_script:
        assert f"TRUSTED SOURCE PATHS (codebase-relative): {source}" in text


def test_corrective_keeps_codebase_context_when_graded_incorrect():
    from agent.structured_output.UT_output import CorrectorOutput

    class QueryLLM(FakeLLM):
        def invoke(self, messages):
            return SimpleNamespace(queries=["python decimal quantize"])

    class Search:
        def invoke(self, query):
            return [{"link": "https://example.test/decimal", "title": "Decimal", "snippet": "quantize docs"}]

    agent = UT_agent.UTAgent(model=QueryLLM())
    agent._web_search = Search()
    context = {"code_context": ["CODE"], "summary_context": ["SUMMARY"], "lsp_context": ["LSP"]}
    state = {
        "validated_rules": [rule()],
        "rule_contexts": {"1": dict(context)},
        "corrective_decisions": {"1": CorrectorOutput(decision="incorrect", reasoning="wrong file")},
    }
    out = agent.web_searcher_node(state)["rule_contexts"]["1"]
    assert out["code_context"] == ["CODE"] and out["summary_context"] == ["SUMMARY"] and out["lsp_context"] == ["LSP"]
    assert out["web_context"] and "quantize docs" in out["web_context"][0]
