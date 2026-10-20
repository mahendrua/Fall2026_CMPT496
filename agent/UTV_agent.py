"""
@file UTV_agent.py
@brief Defines the UTVAgent, a LangGraph-based agent for validating generated unit tests from business rules.
@details Implements a validator-writer workflow that re-checks tests already saved in unit_tests.json.
Python tests are executed with agent/python_test_harness.py (pytest, no LLM).
C# tests use agent/test_harness.py (xUnit, optional AI repair), matching UTAgent.runner_node.

The same checks run automatically at the end of unit test generation (UTAgent.runner_node).
This agent is for re-checking afterwards, e.g. from the "validate only" button.
"""
import logging
logger = logging.getLogger(__name__)
from agent.states.UTV_agent_state import UTVGraphState
from agent.structured_output.UTV_output import UnitTest
from langgraph.graph import StateGraph, START, END
from agent.llm import make_llm
from agent.test_harness import (
    check_tests as check_csharp_tests,
    make_cases as make_csharp_cases,
    make_fixer,
    write_results as write_csharp_results,
)
from agent.python_test_harness import (
    check_tests as check_python_tests,
    make_cases as make_python_cases,
    write_results as write_python_results,
)
from agent.testing_language import detect_unit_test_language
import os
import sys
import asyncio
import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
from pathlib import Path
from backend.progress_logging import progress
from utils.chroma_utils import collection_name

# Code chunks retrieved per broken test for the repair prompt.
REPAIR_CODEBASE_K = 15


class UTVAgent:
    """
    @brief LangGraph-based agent for validating generated unit tests.

    @details
    The UTVAgent constructs and executes a LangGraph workflow that:
    - Detects whether saved tests are Python or C#
    - For Python: writes pytest files, runs them, and records JSON results
    - For C#: writes xUnit tests, repairs compile failures, and runs `dotnet test`
    """

    def __init__(self, model=None):
        """
        @brief Initializes the UTVAgent.
        @param model An optional language model used only for C# compile repair.
            Python validation never constructs an LLM.
        """
        progress("Intializing unit test validation agent...", 5)
        self._injected_model = model
        self.llm = None
        self.graph = self.build_graph()

    def build_graph(self) -> StateGraph:
        """
        @brief Constructs the StateGraph that defines the UTVAgent workflow.
        @return A compiled StateGraph object.

        @details
        Graph structure:
            validator → writer → END
        """

        builder = StateGraph(UTVGraphState)

        # Set nodes
        builder.add_node("validator", self.validator_node)
        builder.add_node("writer", self.writer_node)

        # Set edges
        builder.set_entry_point("validator")
        builder.add_edge("validator", "writer")
        builder.add_edge("writer", END)
        return builder.compile()

    def run(self, input_tests: list[UnitTest], codebase_name: str, codebase_path: str, output_dir=None):
        """
        @brief Executes the UTVAgent workflow.
        @param input_tests List of UnitTest candidates, as saved in unit_tests.json.
        @param codebase_name Name of the target codebase, used to look up the correct ChromaDB collection.
        @param codebase_path Filesystem path of the target codebase.
        @param output_dir Optional override for UT_agent_output (tests).
        @return Final state of the graph after execution.
        """
        progress(
            "Running unit test validation pipeline...",
            5
        )

        # Language errors must happen before any harness JSON is written.
        target_language = detect_unit_test_language(input_tests)
        progress(f"Selected {target_language} unit-test validation.", 8)

        code_collection = None
        if target_language == "csharp":
            if self._injected_model is None:
                self.llm = make_llm()
            else:
                self.llm = self._injected_model

            if getattr(sys, 'frozen', False):
                base_dir = Path(sys.executable).parent
            else:
                base_dir = Path(__file__).parent.parent

            db_dir = (base_dir / "vectorStores").resolve()

            # Code context only improves C# repairs; a codebase without a code
            # database is still validated, just with less help for the AI.
            try:
                embedding_fn = SentenceTransformerEmbeddingFunction(model_name="all-MiniLM-L6-v2")
                client = chromadb.PersistentClient(path=str(db_dir))
                code_collection = client.get_collection(
                    name=collection_name(codebase_name, "code"),
                    embedding_function=embedding_fn
                )
                progress("Loaded code database.", 10)
            except Exception as e:
                code_collection = None
                progress(f"No code database for {codebase_name}; repairing without code context. ({e})", 10)

        initial_state = {
            "target_language": target_language,
            "current_tests": input_tests,
            "test_run": None,
            "code_collection": code_collection,
            "codebase_name": codebase_name,
            "codebase_path": codebase_path,
            "output_directory": output_dir or "./agent/UT_agent_output",
        }

        self._loop = asyncio.new_event_loop() if target_language == "csharp" else None
        try:
            return self.graph.invoke(initial_state)
        finally:
            if self._loop is not None:
                self._loop.close()
                self._loop = None

    def validator_node(self, state: UTVGraphState) -> UTVGraphState:
        """
        @brief Writes and runs the candidate tests with the language-specific harness.

        @details Python uses agent/python_test_harness.py (same branch as
        UTAgent.runner_node). C# uses agent/test_harness.py, including AI repair
        for tests that do not compile.

        @param state Current workflow state containing current_tests.
        @return Updated state with test_run.
        """
        progress(
            "Validating unit tests...",
            20
        )
        codebase_name = state["codebase_name"]
        codebase_path = state["codebase_path"]
        base_output_dir = state.get("output_directory", "./agent/UT_agent_output")
        codebase_subdir = os.path.join(base_output_dir, codebase_name)
        test_items = [
            (test.id, test)
            for test in state["current_tests"]
        ]

        if state["target_language"] == "python":
            cases, rejected = make_python_cases(
                "UT",
                test_items,
                body_of=lambda test: test.unit_test,
                imports_of=lambda test: test.imports,
            )
            test_run = check_python_tests(
                "Unit",
                "UT",
                cases,
                rejected,
                codebase_path,
                codebase_name,
                codebase_subdir,
            )
        else:
            code_collection = state.get("code_collection")
            cases, rejected = make_csharp_cases(
                "UT",
                test_items,
                body_of=lambda test: test.unit_test,
                imports_of=lambda test: test.imports,
            )

            def context_for(case):
                if code_collection is None or not code_collection.count():
                    return ""
                test = case.source
                query_text = f"{test.rule} {' '.join(case.errors[:3])}"
                results = code_collection.query(
                    query_texts=[query_text],
                    n_results=min(REPAIR_CODEBASE_K, code_collection.count())
                )
                docs = results.get("documents", [[]])[0]
                metas = results.get("metadatas", [[]])[0]
                return "\n\n".join(
                    self._format_code_result(doc, meta, test.source_directory)
                    for doc, meta in zip(docs, metas)
                )

            test_run = check_csharp_tests(
                "Unit",
                "UT",
                cases,
                rejected,
                codebase_path,
                codebase_name,
                codebase_subdir,
                fix=make_fixer(self.llm, context_for),
                loop=self._loop,
            )

        return {"test_run": test_run}

    def writer_node(self, state: UTVGraphState) -> UTVGraphState:
        """
        @brief Writes the validation results to JSON output files.

        @details
        Writes validated_tests.json, discarded_tests.json and test_report.json under
        {output_directory}/{codebase_name}/. Does not rewrite unit_tests.json or
        unit_tests.txt. Runs exactly once after the harness returns.

        @param state Current workflow state containing test_run.
        @return Empty dict (terminal node).
        """
        progress(
            "Writing validated and discarded unit tests...",
            90
        )

        codebase_name = state["codebase_name"]
        base_output_dir = state.get("output_directory", "./agent/UT_agent_output")
        codebase_subdir = os.path.join(base_output_dir, codebase_name)
        test_run = state["test_run"]
        to_json = lambda case: dict(
            case.source.model_dump(),
            imports=case.imports,
            unit_test=case.body,
        )

        if state["target_language"] == "python":
            write_python_results(test_run, codebase_subdir, to_json)
        else:
            write_csharp_results(test_run, codebase_subdir, to_json)

        progress(test_run.message(), 100, True)
        return {}

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
        @param meta Metadata associated with the snippet.
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
