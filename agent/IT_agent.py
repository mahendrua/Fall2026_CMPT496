"""
@file IT_agent.py
@brief Defines the ITAgent, a LangGraph-based agent for generating Integration Tests based of business rules.
@details Implements a retriever-generator-writer-runner workflow that takes validated business rules from BR_agent output,
generates integration tests, writes the results to JSON, then builds, repairs and runs them (agent/test_harness.py).
"""
import logging
logger = logging.getLogger(__name__)
from agent.states.IT_agent_state import ITGraphState
from agent.structured_output.IT_output import (
    ValidatedRule, IntegrationTest, WorkflowGroup, WorkflowGroups
)
from langgraph.graph import StateGraph, START, END
from agent.llm import make_llm
import os
import sys
import json
import asyncio
import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
from pathlib import Path
from collections import defaultdict
from backend.progress_logging import progress
from utils.chroma_utils import collection_name


from lsp.context import CodebaseContext
from lsp.retriever import retrieve_lsp_context
from agent.testing_language import (
    detect_test_language,
    integration_test_generation_instructions,
)
from agent.test_harness import (
    MAX_TEST_OUTPUT_TOKENS,
    check_tests as check_csharp_tests,
    make_cases as make_csharp_cases,
    make_fixer as make_csharp_fixer,
    write_results as write_csharp_results,
)

from agent.javascript_test_harness import (
    check_tests as check_javascript_tests,
    make_cases as make_javascript_cases,
    make_fixer as make_javascript_fixer,
    write_results as write_javascript_results,
)

from agent.python_test_harness import (
    check_tests as check_python_tests,
    make_cases as make_python_cases,
    write_results as write_python_results,
)

from agent.java_test_harness import (
    check_tests as check_java_tests,
    make_cases as make_java_cases,
    make_fixer as make_java_fixer,
    write_results as write_java_results,
)

from agent.cpp_test_harness import (
    check_tests as check_cpp_tests,
    make_cases as make_cpp_cases,
    make_fixer as make_cpp_fixer,
    write_results as write_cpp_results,
)

from agent.c_test_harness import (
    check_tests as check_c_tests,
    make_cases as make_c_cases,
    make_fixer as make_c_fixer,
    write_results as write_c_results,
)

from agent.kotlin_test_harness import (
    check_tests as check_kotlin_tests,
    make_cases as make_kotlin_cases,
    make_fixer as make_kotlin_fixer,
    write_results as write_kotlin_results,
)

MAX_CONCURRENCY = 10
DEFAULT_CODEBASE_K = 30
DEFAULT_FILE_SUMMARY_K = 15
MAX_CODEBASE_K = 30
MAX_FILE_SUMMARY_K = 10

class ITAgent:
    """
    @brief LangGraph-based agent for generating integration tests based on business rules.

    @details
    The ITAgent constructs and executes a LangGraph workflow that:
    - Retrieves file and summary context for validated business rules from BR_agent output.
    - Generates integration tests for each validated rule.
    - Writes the generated integration tests to JSON output files.
    - Writes each test to its own file, has the AI repair the ones that do not compile,
      and runs the rest.
    """

    def __init__(self, model=None):
        """
        @brief Initializes the ITAgent with a specified language model.
        @param model An optional language model to use. If not provided, defaults to gemini-3-flash-preview.
        """
        progress("Intializing integration test agent...", 5)
        if model is None:
            self.llm = make_llm(max_output_tokens=MAX_TEST_OUTPUT_TOKENS)
        else:
            self.llm = model
        self.graph = self.build_graph()

    def build_graph(self) -> StateGraph:
        """
        @brief Constructs the StateGraph that defines the ITAgent workflow.
        @return A compiled StateGraph object.

        @details
        Graph structure:
            retriever → test_generator → writer → END

        Conditional routing from condenser and validator:
            - If current_rules is non-empty → retriever
            - If current_rules is empty (all rules processed) → writer
        """

        builder = StateGraph(ITGraphState)

        builder.add_node("workflow_grouper", self.workflow_grouper_node)
        builder.add_node("retriever", self.retriever_node)
        builder.add_node("test_generator", self.test_generator_node)
        builder.add_node("writer", self.writer_node)
        builder.add_node("runner", self.runner_node)


        builder.set_entry_point("workflow_grouper")

        builder.add_edge("workflow_grouper", "retriever")
        builder.add_edge("retriever", "test_generator")
        builder.add_edge("test_generator", "writer")
        builder.add_edge("writer", "runner")
        builder.add_edge("runner", END)

        return builder.compile()

    def run(self, validated_rules: list[ValidatedRule], codebase_name: str, codebase_path: str, lsp_context: CodebaseContext | None = None) -> ITGraphState:
        """
        @brief Executes the ITAgent workflow.
        @param input_rules Dictionary of validated business rules from BR_agent output. Keys are file or directory paths,
               values are lists of ValidatedRule objects.
        @param codebase_name Name of the target codebase, used to look up the correct ChromaDB collections.
        @return Final state of the graph after execution.
        """

        target_language = detect_test_language(
            [rule.source_file_paths for rule in validated_rules],
            codebase_path=codebase_path,
        )

        progress(
            "Running integration test generation pipeline...",
            10
        )
        if getattr(sys, 'frozen', False):
            base_dir = Path(sys.executable).parent
        else:
            base_dir = Path(__file__).parent.parent

        db_dir = (base_dir / "vectorStores").resolve()

        embedding_fn = SentenceTransformerEmbeddingFunction(model_name="all-MiniLM-L6-v2")
        client = chromadb.PersistentClient(path=str(db_dir)) 

        code_collection = client.get_collection(
            name=collection_name(codebase_name, "code"),
            embedding_function=embedding_fn
        )

        summary_collection = client.get_collection(
            name=collection_name(codebase_name, "summary"),
            embedding_function=embedding_fn
        )

        progress(
            "Loaded code and summary databases.",
            20
        )

        initial_state = {
            "target_language": target_language,
            "validated_rules": validated_rules,
            "integration_tests": [],
            "workflow_contexts": {},
            "test_imports": set(),
            "codebase_k": DEFAULT_CODEBASE_K,
            "file_summary_k": DEFAULT_FILE_SUMMARY_K,
            "code_collection": code_collection,
            "summary_collection": summary_collection,
            "codebase_name": codebase_name,
            "codebase_path": codebase_path,
            "output_directory": "./agent/IT_agent_output",
            "lsp_context": lsp_context,
        }

        self._loop = asyncio.new_event_loop()
        try:
            return self.graph.invoke(initial_state)
        finally:
            self._loop.close()
            self._loop = None

    def workflow_grouper_node(self, state: ITGraphState) -> ITGraphState:
        """
            @brief Groups validated business rules into business workflows.

            @details
            Uses the language model to analyze all validated business rules and identify
            groups of rules that participate in the same end-to-end business workflow.

            Each workflow represents a collection of related business rules that should
            be exercised together in a single integration test.

            @param state Current workflow state containing validated business rules.
            @return Updated state containing workflow_groups.
        """

        progress(
            "Grouping validated business rules into workflows...",
         25
        )

        validated_rules = state.get("validated_rules", [])
        if not validated_rules:
            raise ValueError("No validated business rules provided.")

        structured_llm = self.llm.with_structured_output(WorkflowGroups)
        rules_text = ""

        for rule in validated_rules:
            rules_text += f"""
            ----------------------------------------

            Rule ID:
            {rule.id}

            Business Rule:
            {rule.rule}

            Validation Explanation:
            {rule.explanation}

            Source Directory:
            {rule.source_directory}

            Source Files:
            {", ".join(rule.source_file_paths)}

            """

        system_message = (
                    "You are a Senior Software Architect specializing in software "
                    "architecture analysis and integration testing."
            )

        prompt = f"""
            You are given a collection of validated business rules extracted from a software system.

            Your task is to identify which business rules belong to the same end-to-end business workflow.

            A workflow is a sequence of related business operations that together accomplish a user or
            system goal.

            Examples of workflows include:

            • User Registration
                - Validate Email
                - Create User
                - Save User
                - Send Welcome

            • Order Checkout
                - Validate Shopping Cart
                - Reserve Inventory
                - Process Payment
                - Generate Receipt

            • Password Reset
                - Validate Account
                - Generate Reset Token
                - Send Reset Email
                - Update Password

            Guidelines:

            - Every workflow should contain business rules that interact with one another.
            - A business rule may belong to ONLY ONE workflow.
            - Do NOT invent new business rules.
            - Do NOT modify existing business rules.
            - Use the validation explanations, directories, and source files to determine
            which rules are likely part of the same workflow.
            - Prefer grouping rules that span multiple files or components.

            Your response will be parsed into the following schema:

            WorkflowGroups
            └── workflows[]
                ├── workflow_name
                ├── workflow_description
                └── rule_ids

            Do not output markdown, explanations, or JSON examples.
            Only populate the schema fields.

            For each workflow:

            - workflow_name:
            A concise business workflow name.

            - workflow_description:
            One or two sentences describing the overall purpose of the workflow.

            - rule_ids:
            A list of the integer IDs of the validated business rules that belong to this workflow.

            Important constraints:

            - Every validated business rule must appear in exactly one workflow.
            - Do not omit any rule IDs.
            - Do not duplicate rule IDs across workflows.
            - Do not invent new rule IDs.
            - Do not return business rule objects.
            - Return only the integer IDs corresponding to the validated rules provided below.
            - Use the exact field names:
                • workflow_name
                • workflow_description
                • rule_ids

            BUSINESS RULES

            {rules_text}
        """

        messages = [
            ("system", system_message),
            ("user", prompt),
        ]



        progress("LLM returned workflow grouping.")
        output = structured_llm.invoke(messages)
        progress("Structured output parsed successfully.")

        progress(f"Output type: {type(output)}")

        # Build lookup table from rule ID -> ValidatedRule
        rule_lookup = {
            rule.id: rule
            for rule in validated_rules
        }

        progress("Structured output parsed.", 26)

        workflow_groups = []

        progress(f"Found {len(output.workflows)} workflows.", 27)

        for i, workflow in enumerate(output.workflows):
            progress(f"Processing workflow {i+1}/{len(output.workflows)}", 27)

            grouped_rules = [
                rule
                for rule in validated_rules
                if rule.id in workflow.rule_ids
            ]

            progress(f"Matched {len(grouped_rules)} rules.", 28)

            workflow_groups.append(
                WorkflowGroup(
                    workflow_name=workflow.workflow_name,
                    workflow_description=workflow.workflow_description,
                    rules=grouped_rules
                )
            )

        progress("Finished workflow grouping.", 29)

        progress(
            f"Grouped {len(validated_rules)} business rules into "
            f"{len(workflow_groups)} workflows.",
            30
        )

        return {
            "workflow_groups": workflow_groups,
        }
            
    
    def retriever_node(self, state: ITGraphState) -> ITGraphState:
        """
        @brief Retrieves relevant code snippets and file summaries for each business workflow.

        @details
        Iterates over workflow groups and queries ChromaDB code and summary collections.
        Each workflow combines the context of all business rules it contains.

        Retrieval priority:
            1. Files directly referenced by workflow rules
            2. Files inside workflow directories
            3. Other relevant retrieved files

        Workflow context is stored in workflow_contexts[workflow_name].

        @param state Current workflow state containing workflow groups and retrieval parameters.
        @return Updated state with workflow_contexts populated.
        """

        progress(
            "Retrieving context for business workflows...",
            30
        )

        workflow_groups = state.get("workflow_groups", [])
        if not workflow_groups:
            raise ValueError("No workflow groups to retrieve context for.")


        validated_rules = state.get("validated_rules", [])
        if not validated_rules:
            raise ValueError("No validated rules available.")

        # Build lookup table:
        # rule_id -> ValidatedRule
        rule_lookup = {
            rule.id: rule
            for rule in validated_rules
        }


        code_collection = state["code_collection"]
        summary_collection = state["summary_collection"]
        code_k = state["codebase_k"]
        summary_k = state["file_summary_k"]
        existing_contexts = state.get("workflow_contexts", {})
        updated_contexts = dict(existing_contexts)


        for workflow in workflow_groups:

            # Resolve workflow rule IDs back into ValidatedRule objects
            workflow_rules = workflow.rules

            if not workflow_rules:
                continue

            source_directories = set()
            source_file_paths = []
            rule_descriptions = []

            for rule in workflow_rules:
                source_directories.add(rule.source_directory)
                source_file_paths.extend(rule.source_file_paths)
                rule_descriptions.append(rule.rule)


            source_directory = ", ".join(source_directories)


            query_text = f"""
            Workflow:
            {workflow.workflow_name}

            Workflow Description:
            {workflow.workflow_description}

            Business Rules:
            {chr(10).join(rule_descriptions)}

            Source Directories:
            {source_directory}
            """


            safe_code_k = min(
                code_k,
                code_collection.count()
            ) or 1

            safe_summary_k = min(
                summary_k,
                summary_collection.count()
            ) or 1


            code_results = code_collection.query(query_texts=[query_text],n_results=safe_code_k)
            summary_results = summary_collection.query(query_texts=[query_text],n_results=safe_summary_k)


            # Workflow-level context storage
            workflow_key = workflow.workflow_name


            workflow_ctx = updated_contexts.get(
                workflow_key,
                {
                    "code_context": [],
                    "summary_context": [],
                    "lsp_context": []
                }
            )

            existing_code = set(workflow_ctx["code_context"])
            existing_summary = set(workflow_ctx["summary_context"])
            existing_lsp = set(workflow_ctx["lsp_context"])

            # -------------------------
            # Process code results
            # -------------------------

            code_docs = code_results.get("documents",[[]])[0]

            code_metas = code_results.get("metadatas",[[]])[0]

            source_file_code = []
            directory_code = []
            fallback_code = []


            for doc, meta in zip(code_docs, code_metas):

                file_path = self._normalize_path(
                    meta.get("file", "")
                )

                formatted = self._format_code_result(
                    doc,
                    meta,
                    source_directory
                )


                if self._is_from_source_file(
                    file_path,
                    source_file_paths
                ):
                    source_file_code.append(formatted)


                elif self._is_in_directory(
                    file_path,
                    source_directory
                ):
                    directory_code.append(formatted)

                else:
                    fallback_code.append(formatted)

            # -------------------------
            # Process summary results
            # -------------------------
            summary_docs = summary_results.get(
                "documents",
                [[]]
            )[0]

            summary_metas = summary_results.get(
                "metadatas",
                [[]]
            )[0]

            source_file_summary = []
            directory_summary = []
            fallback_summary = []


            for doc, meta in zip(summary_docs, summary_metas):

                summary_path = self._normalize_path(
                    meta.get("path", "")
                )


                formatted = self._format_summary_result(
                    doc,
                    meta,
                    source_directory
                )


                if self._is_from_source_file(
                    summary_path,
                    source_file_paths
                ):
                    source_file_summary.append(formatted)
                elif self._is_in_directory(
                    summary_path,
                    source_directory
                ):
                    directory_summary.append(formatted)
                else:
                    fallback_summary.append(formatted)

            # -------------------------
            # Merge contexts
            # -------------------------

            new_code = list(
                workflow_ctx["code_context"]
            )

            for item in (
                source_file_code
                + directory_code
                + fallback_code
            ):
                if item not in existing_code:
                    new_code.append(item)

            new_summary = list(workflow_ctx["summary_context"])

            for item in (
                source_file_summary
                + directory_summary
                + fallback_summary
            ):
                if item not in existing_summary:
                    new_summary.append(item)


            lsp_results = retrieve_lsp_context(state.get("lsp_context"), source_file_paths)
            
            new_lsp = list(workflow_ctx["lsp_context"])
            for item in lsp_results:
                if item not in existing_lsp:
                    new_lsp.append(item)
                    existing_lsp.add(item)

            updated_contexts[workflow_key] = {"code_context": new_code,"summary_context": new_summary,"lsp_context": new_lsp}


        progress(
            "Workflow context retrieval complete.",
            50
        )


        return {
            "workflow_contexts": updated_contexts
        }
    
    
    def test_generator_node(self, state: ITGraphState) -> ITGraphState:
        """
        @brief Generates integration tests for validated business rules.

        @details
        Uses the same language model but structured output to produce a integration test string
        for each rule that passed validation. The results are written to `integration_tests`
        in the workflow state for the final writer node.
        """

        progress(
            "Generating integration tests from workflows...",
            60
        )

        target_language = state["target_language"]

        workflow_groups = state.get("workflow_groups", [])
        if not workflow_groups:
            return {
                "integration_tests": [],
                "test_imports": set()
            }
        
        workflow_contexts = state.get("workflow_contexts", {})  
        
        structured_llm = self.llm.with_structured_output(IntegrationTest)

        async def run_batch():
            sem = asyncio.Semaphore(MAX_CONCURRENCY)
            async def guarded(workflow):
                async with sem:
                    ctx = workflow_contexts.get(workflow.workflow_name, {"code_context": [], "summary_context": [], "lsp_context": []})
                    return await _generate_single_test(structured_llm,workflow,workflow.rules,ctx["code_context"],ctx["summary_context"],ctx["lsp_context"],target_language,)
            return await asyncio.gather(*(guarded(workflow) for workflow in workflow_groups))

        results = self._loop.run_until_complete(run_batch())

        progress(
            f"Generated {len(results)} integration test candidates.",
            80
        )

        test_imports = set()
        integration_tests = []
        for workflow, output, err in results:
            if err is not None:
                progress(f"Integration test generation error for workflow {workflow.workflow_name}: {err}")
                logger.error(f"Integration test generation error for workflow {workflow.workflow_name}: {err}")
                continue
            test_imports.update(output.imports)
            integration_tests.append(
                IntegrationTest(
                    workflow_name=workflow.workflow_name,
                    workflow_description=workflow.workflow_description,
                    rule_ids=[rule.id for rule in workflow.rules],
                    source_file_paths=list(dict.fromkeys(
                        path
                        for rule in workflow.rules
                        for path in rule.source_file_paths
                    )),
                    imports=output.imports,
                    integration_test=output.integration_test,
                )
            )
        progress(
            f"Generated integration tests for {len(integration_tests)} workflows.",
            85
        )
        return {"integration_tests": integration_tests, "test_imports": test_imports}

    def writer_node(self, state: ITGraphState) -> ITGraphState:
        """
        @brief Writes generated integration tests to JSON and text output files.

        @details
        Serializes structured integration tests to JSON and writes a combined
        language-appropriate text representation of the generated tests.

        @param state Current workflow state containing integration tests.
        @return Empty dict (terminal node).
        """
        progress(
            "Writing generated integration tests...",
            90
        )

        codebase_name = state["codebase_name"]
        target_language = state["target_language"]

        base_output_dir = state.get(
            "output_directory",
            "./agent/IT_agent_output"
        )

        codebase_subdir = os.path.join(base_output_dir, codebase_name)
        os.makedirs(codebase_subdir, exist_ok=True)

        integration_tests = state.get("integration_tests", [])

        if not integration_tests:
            progress("No integration tests were generated.", 98)
            return {}

        integration_tests_path_json = os.path.join(
            codebase_subdir,
            "integration_tests.json"
        )

        integration_tests_path_txt = os.path.join(
            codebase_subdir,
            "integration_tests.txt"
        )

        # Preserve the structured JSON output.
        with open(integration_tests_path_json, "w", encoding="utf-8") as f:
            json.dump(
                [test.model_dump() for test in integration_tests],
                f,
                indent=2
            )

        # Select the appropriate comment syntax.
        comment_prefix = "#" if target_language == "python" else "//"

        with open(integration_tests_path_txt, "w", encoding="utf-8") as file:
            for test in integration_tests:

                file.write(
                    f"{comment_prefix} Workflow: {test.workflow_name}\n"
                )
                file.write(
                    f"{comment_prefix} Description: {test.workflow_description}\n"
                )
                file.write(
                    f"{comment_prefix} Covered Business Rules: {test.rule_ids}\n\n"
                )

                # Write language-appropriate imports.
                if getattr(test, "imports", None):
                    for imp in test.imports:
                        imp = imp.strip()

                        if not imp:
                            continue

                        if target_language == "csharp":
                            # The structured-output validator may return
                            # namespaces without the using keyword or semicolon.
                            if not imp.startswith("using "):
                                imp = f"using {imp}"

                            if not imp.endswith(";"):
                                imp += ";"

                        # Python and JavaScript imports are already expected
                        # to be complete import statements.
                        file.write(imp + "\n")

                    file.write("\n")

                # Write the generated integration test.
                file.write(test.integration_test.strip() + "\n\n")

        progress(
            f"Wrote {len(integration_tests)} workflow integration tests to "
            f"{integration_tests_path_json} and {integration_tests_path_txt}",
            98
        )

        return {}

   
    def runner_node(self, state: ITGraphState) -> ITGraphState:
        """
        @brief Validates and executes generated integration tests using the
            appropriate language-specific test harness.

        @details
        Each supported language uses its own test harness. Tests that fail
        compilation can be repaired using retrieved code and LSP context.

        @param state Current workflow state containing integration_tests,
                    workflow_contexts, and target language information.
        @return Updated state containing the test_run result.
        """
        codebase_path = state["codebase_path"]
        codebase_name = state["codebase_name"]
        target_language = state["target_language"]

        base_output_dir = state.get(
            "output_directory",
            "./agent/IT_agent_output"
        )

        codebase_dir = os.path.join(
            base_output_dir,
            codebase_name
        )

        workflow_contexts = state.get("workflow_contexts", {})
        integration_tests = state.get("integration_tests", [])

        if not integration_tests:
            progress("No integration tests available for execution.", 100, True)
            return {"test_run": None}

        # Select the appropriate language-specific case builder.
        if target_language == "python":
            cases, rejected = make_python_cases(
                "IT",
                list(enumerate(integration_tests, 1)),
                body_of=lambda test: test.integration_test,
                imports_of=lambda test: test.imports,
                codebase_path=codebase_path,
            )

        elif target_language == "csharp":
            cases, rejected = make_csharp_cases(
                "IT",
                list(enumerate(integration_tests, 1)),
                body_of=lambda test: test.integration_test,
                imports_of=lambda test: test.imports,
            )

        elif target_language == "javascript":
            cases, rejected = make_javascript_cases(
                "IT",
                list(enumerate(integration_tests, 1)),
                body_of=lambda test: test.integration_test,
                imports_of=lambda test: test.imports,
            )

        elif target_language == "java":
            cases, rejected = make_java_cases(
                "IT",
                list(enumerate(integration_tests, 1)),
                body_of=lambda test: test.integration_test,
                imports_of=lambda test: test.imports,
                codebase_path=codebase_path,
            )

        elif target_language == "kotlin":
            cases, rejected = make_kotlin_cases(
                "IT",
                list(enumerate(integration_tests, 1)),
                body_of=lambda test: test.integration_test,
                imports_of=lambda test: test.imports,
                codebase_path=codebase_path,
            )

        elif target_language == "c":
            cases, rejected = make_c_cases(
                "IT",
                list(enumerate(integration_tests, 1)),
                body_of=lambda test: test.integration_test,
                imports_of=lambda test: test.imports,
                codebase_path=codebase_path,
            )

        elif target_language == "cpp":
            cases, rejected = make_cpp_cases(
                "IT",
                list(enumerate(integration_tests, 1)),
                body_of=lambda test: test.integration_test,
                imports_of=lambda test: test.imports,
                codebase_path=codebase_path,
            )

        else:
            raise ValueError(
                f"Unsupported integration-test language: {target_language}"
            )

        # Retrieve workflow-specific context for AI repair.
        def context_for(case):
            ctx = workflow_contexts.get(
                case.source.workflow_name,
                {}
            )

            context_parts = []

            code_context = ctx.get("code_context", [])
            if code_context:
                context_parts.append(
                    "### RETRIEVED CODE CONTEXT\n\n"
                    + "\n\n".join(code_context)
                )

            lsp_context = ctx.get("lsp_context", [])
            if lsp_context:
                context_parts.append(
                    "### RETRIEVED LSP CONTEXT\n\n"
                    + "\n\n".join(lsp_context)
                )

            return "\n\n".join(context_parts)

        # Execute using the selected harness.
        if target_language == "python":

            test_run = check_python_tests(
                "Integration",
                "IT",
                cases,
                rejected,
                codebase_path,
                codebase_name,
                codebase_dir,
            )

            write_python_results(
                test_run,
                codebase_dir,
                lambda case: dict(
                    case.source.model_dump(),
                    imports=case.imports,
                    integration_test=case.body,
                ),
            )

        elif target_language == "csharp":

            test_run = check_csharp_tests(
                "Integration",
                "IT",
                cases,
                rejected,
                codebase_path,
                codebase_name,
                codebase_dir,
                fix=make_csharp_fixer(self.llm, context_for),
                loop=self._loop,
            )

            write_csharp_results(
                test_run,
                codebase_dir,
                lambda case: dict(
                    case.source.model_dump(),
                    imports=case.imports,
                    integration_test=case.body,
                ),
            )

        elif target_language == "javascript":

            test_run = check_javascript_tests(
                "Integration",
                "IT",
                cases,
                rejected,
                codebase_path,
                codebase_name,
                codebase_dir,
                fix=make_javascript_fixer(self.llm, context_for),
                loop=self._loop,
            )

            write_javascript_results(
                test_run,
                codebase_dir,
                lambda case: dict(
                    case.source.model_dump(),
                    imports=case.imports,
                    integration_test=case.body,
                ),
            )

        elif target_language == "java":

            test_run = check_java_tests(
                "Integration",
                "IT",
                cases,
                rejected,
                codebase_path,
                codebase_name,
                codebase_dir,
                fix=make_java_fixer(self.llm, context_for),
                loop=self._loop,
            )

            write_java_results(
                test_run,
                codebase_dir,
                lambda case: dict(
                    case.source.model_dump(),
                    imports=case.imports,
                    integration_test=case.body,
                ),
            )

        elif target_language == "kotlin":

            test_run = check_kotlin_tests(
                "Integration",
                "IT",
                cases,
                rejected,
                codebase_path,
                codebase_name,
                codebase_dir,
                fix=make_kotlin_fixer(self.llm, context_for),
                loop=self._loop,
            )

            write_kotlin_results(
                test_run,
                codebase_dir,
                lambda case: dict(
                    case.source.model_dump(),
                    imports=case.imports,
                    integration_test=case.body,
                ),
            )

        elif target_language == "c":

            test_run = check_c_tests(
                "Integration",
                "IT",
                cases,
                rejected,
                codebase_path,
                codebase_name,
                codebase_dir,
                fix=make_c_fixer(self.llm, context_for),
                loop=self._loop,
            )

            write_c_results(
                test_run,
                codebase_dir,
                lambda case: dict(
                    case.source.model_dump(),
                    imports=case.imports,
                    integration_test=case.body,
                ),
            )

        elif target_language == "cpp":

            test_run = check_cpp_tests(
                "Integration",
                "IT",
                cases,
                rejected,
                codebase_path,
                codebase_name,
                codebase_dir,
                fix=make_cpp_fixer(self.llm, context_for),
                loop=self._loop,
            )

            write_cpp_results(
                test_run,
                codebase_dir,
                lambda case: dict(
                    case.source.model_dump(),
                    imports=case.imports,
                    integration_test=case.body,
                ),
            )

        else:
            raise ValueError(
                f"Unsupported integration-test language: {target_language}"
            )

        progress(test_run.message(), 100, True)

        return {"test_run": test_run}


    # Helper methods

    def _normalize_path(self, path_value: str) -> str:
        """
        @brief Normalizes a filesystem path to POSIX format.
        @param path_value The path to normalize.
        @return The normalized POSIX-style path, or an empty string if the input is empty.
        """
        if not path_value:
            return ""
        return Path(path_value).as_posix()

    def _is_in_directory(self, candidate_path: str, target_rel_dir: str) -> bool:
        """
        @brief Checks whether a file path belongs to a specified directory.
        @param candidate_path The file path being evaluated.
        @param target_rel_dir The relative directory to check membership against.
        @return True if the path belongs to the directory or one of its subdirectories.
        """
        candidate_path = self._normalize_path(candidate_path)

        if target_rel_dir == ".":
            return True

        parent_dir = Path(candidate_path).parent.as_posix()
        return parent_dir == target_rel_dir or parent_dir.startswith(target_rel_dir + "/")

    def _is_from_source_file(self, candidate_path: str, source_file_paths: list[str]) -> bool:
        """
        @brief Checks whether a retrieved result's file path matches any of the rule's source files.
        @param candidate_path The file path from the retrieved result's metadata.
        @param source_file_paths List of source file paths from the CondensedRule.
        @return True if the candidate matches any source file path.
        """
        candidate_normalized = self._normalize_path(candidate_path)
        for source_path in source_file_paths:
            source_normalized = self._normalize_path(source_path)
            if candidate_normalized == source_normalized:
                return True
            # Suffix match on path boundary (must align to a '/' separator)
            if (candidate_normalized.endswith("/" + source_normalized)
                    or source_normalized.endswith("/" + candidate_normalized)):
                return True
        return False

    def _format_code_result(self, doc: str, meta: dict, source_directory: str) -> str:
        """
        @brief Formats a retrieved code chunk and its metadata for inclusion in context.
        @param doc The retrieved code snippet content.
        @param meta Metadata associated with the snippet.
        @param source_directory The source directory of the current rule.
        @return A formatted string representing the code context entry.
        """
        file_path = self._normalize_path(str(meta.get("file", "unknown")))

        return (
            f"[CODE CHUNK]\n"
            f"Directory: {source_directory}\n"
            f"File: {file_path}\n"
            f"Container: {meta.get('container', 'unknown')}\n"
            f"Name: {meta.get('name', 'unknown')}\n"
            f"Type: {meta.get('type', 'unknown')}\n"
            f"Namespace: {meta.get('namespace', 'unknown')}\n"
            f"Lines: {meta.get('start_line', '?')}-{meta.get('end_line', '?')}\n"
            f"Content:\n{doc}"
        )

    def _format_summary_result(self, doc: str, meta: dict, source_directory: str) -> str:
        """
        @brief Formats a retrieved summary entry and its metadata for context.
        @param doc The retrieved summary text.
        @param meta Metadata associated with the summary node.
        @param source_directory The source directory of the current rule.
        @return A formatted string representing the summary context entry.
        """
        summary_path = self._normalize_path(str(meta.get("path", "unknown")))

        return (
            f"[SUMMARY NODE]\n"
            f"Directory: {source_directory}\n"
            f"Path: {summary_path}\n"
            f"Node Type: {meta.get('type', 'unknown')}\n"
            f"Name: {meta.get('name', 'unknown')}\n"
            f"Parent: {meta.get('parent', 'N/A')}\n"
            f"Content:\n{doc}"
        )


async def _generate_single_test(
    structured_llm,
    workflow,
    workflow_rules,
    code_context: list[str],
    summary_context: list[str],
    lsp_context: list[str] | None = None,
    target_language: str = "csharp",
) -> tuple:
    try:
        code_text = "\n\n".join(code_context) if code_context else "NO CONTEXT PROVIDED"
        summary_text = "\n\n".join(summary_context) if summary_context else "NO CONTEXT PROVIDED"
        lsp_text = "\n\n".join(lsp_context) if lsp_context else "NO CONTEXT PROVIDED"
        language_instructions = integration_test_generation_instructions(
            target_language
        )

        workflow_rules_text = ""

        for rule in workflow_rules:
            workflow_rules_text += f"""
            ----------------------------------------

            Rule ID:
            {rule.id}

            Business Rule:
            {rule.rule}

            Validation Explanation:
            {rule.explanation}

            Source Directory:
            {rule.source_directory}

            Source Files:
            {", ".join(rule.source_file_paths)}

            """


        system_message = (
            "You are a Senior Software Architect and expert Automated Test Engineer. "
            "Your objective is to generate a syntactically flawless end-to-end integration "
            "test for a complete software workflow. "
            "The workflow may span multiple files, services, and components. "
            "Generate tests that verify the interaction between these components."
        )

        prompt = f"""
        ### WORKFLOW TO TEST

        Workflow Name:
        {workflow.workflow_name}

        Workflow Description:
        {workflow.workflow_description}


        ### BUSINESS RULES IN THIS WORKFLOW

        {workflow_rules_text}


        ### RETRIEVED SOURCE CODE CONTEXT

        {code_text}


        ### RETRIEVED FILE SUMMARY CONTEXT

        {summary_text}

        ### RETRIEVED LSP CONTEXT

        {lsp_text}


        ### REQUIRED TASK

        1. Analyze the provided source code and file summaries.

        2. Determine how this workflow is implemented across the application.

        3. Generate exactly ONE executable integration test.

        4. The test must exercise the complete workflow from start to finish.

        5. The test should verify interaction between multiple components where possible.

        6. Use the application's existing initialization, dependency, and service architecture where supported by the retrieved source context.

        ### LANGUAGE AND TEST FRAMEWORK REQUIREMENTS

        {language_instructions}

        ### INTEGRATION TEST REQUIREMENTS

        - Generate exactly ONE executable integration test for this workflow.
        - The test must exercise the workflow across its relevant components.
        - Prefer testing actual interactions between components over isolated
        function behavior.
        - Only use classes, functions, methods, and APIs supported by the
        retrieved source context.
        - Do not invent domain models, constructors, properties, or methods.
        - Use only accessible application APIs.
        - Do not invent expected results that are unsupported by the business rules
        or source implementation.
        - Keep imports separate from the integration_test field.


        ### STRICT CONSTRAINTS

        - Only use components found in the provided context.
        - Do not create separate tests for each business rule.
        - The output must represent one end-to-end workflow test.
        - The integration_test field must contain only the executable test implementation.
        - Imports must be returned separately.
        - Use LSP context to understand symbols, definitions, references, and relationships between files.
        - Treat LSP information as supplementary structural metadata, not as proof of implementation details.
        - Prefer concrete source code over LSP metadata when determining how classes, methods, and components behave.
        - Do not assume a symbol is public or accessible simply because it appears in LSP context.

       ### SOURCE AND TYPE USAGE RULES

        - Prefer existing application classes, models, DTOs, and entities found
        in the retrieved source context.
        - Before instantiating a custom class, verify that its definition exists
        in the provided context.
        - Do not invent domain objects, constructors, properties, methods, or APIs.
        - Use only members that are accessible from the generated test.
        - Prefer the simplest valid input supported by the existing application API
        when a required type cannot be found.
        - Use only the testing framework and dependencies specified in the
        language-specific instructions.
        - Do not introduce additional testing libraries or dependencies.
        """

        messages = [("system", system_message), ("user", prompt)]
        output = await structured_llm.ainvoke(messages)
        return workflow, output, None
    except Exception as e:
        return workflow, None, e

if __name__ == "__main__":
    """
    @brief Script entry point for running BRAgent.
    @details Loads business rules from a JSON file and runs the validation pipeline.
    """
    if len(sys.argv) != 3:
        progress("Usage: python -m agent.BR_agent <codebase_path> <rules_json_path>")
        sys.exit(1)

    codebase = sys.argv[1]
    codebase_name = os.path.basename(codebase)
    rules_path = sys.argv[2]

    with open(rules_path, "r", encoding="utf-8") as f:
        raw_rules = json.load(f)

    # Convert raw JSON dicts back to BusinessRule objects
    input_rules = [ValidatedRule.model_validate(rule) for rule in raw_rules]   

    agent = ITAgent()
    agent.run(input_rules, codebase_name, codebase)
    progress("ITAgent has completed its task!", 100, True)
