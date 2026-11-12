"""Shared state for the GUI test case generation workflow."""

from typing import TypedDict

from agent.structured_output.gui_test_output import GeneratedGuiTestCase


class GuiTestAgentState(TypedDict):
    source_directory: str
    output_directory: str
    source_context: str
    source_files: list[str]
    source_context_truncated: bool
    test_cases: list[GeneratedGuiTestCase]
    generated_files: list[str]
