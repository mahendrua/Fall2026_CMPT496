"""
@file formatter.py
@brief Formats collected Language Server Protocol (LSP) context for
       consumption by language model agents.

@details
This module provides reusable formatting utilities for converting
structured LSP context into readable text suitable for LLM prompts.

It supports:
- Formatting document symbols and nested symbol hierarchies.
- Formatting file-level language information.
- Formatting symbol definitions and references.
- Producing consistent LSP context representations across pipeline agents.

The formatter is designed to be shared by multiple agents, including
FileSummaryAgent, DirectoryAgent, UTAgent, and ITAgent.

LSP context is treated as supplementary information. Agents should
continue to function when context is unavailable.
"""

from lsp.context import FileContext


def format_lsp_symbol(symbol, depth=0):
    """Format a document symbol and its nested children."""

    indent = "  " * depth
    detail = f" — {symbol.detail}" if symbol.detail else ""

    lines = [
        f"{indent}- {symbol.kind}: {symbol.name}{detail}"
    ]

    for child in symbol.children:
        lines.extend(format_lsp_symbol(child, depth + 1))

    return lines


def format_file_context(file_context: FileContext | None) -> str:
    """Format the LSP information for one file."""

    if file_context is None:
        return ""

    sections = [
        f"Language: {file_context.language}",
    ]

    if file_context.symbols:
        sections.append("Document Symbols:")

        for symbol in file_context.symbols:
            sections.extend(format_lsp_symbol(symbol))

    if file_context.definitions:
        sections.append("Definitions:")

        for definition in file_context.definitions[:30]:
            location = definition.location
            start = location.range.start

            sections.append(
                f"- {location.file_path}: line {start.line + 1}"
            )

    if file_context.references:
        sections.append("References:")

        for reference in file_context.references[:50]:
            location = reference.location
            start = location.range.start

            sections.append(
                f"- {location.file_path}: line {start.line + 1}"
            )

    return "\n".join(sections)