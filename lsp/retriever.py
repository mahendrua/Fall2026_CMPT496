"""
LSP context retrieval functions.
"""

from lsp.context import CodebaseContext
from lsp.formatter import format_file_context


def retrieve_lsp_context(
        lsp_context: CodebaseContext | None,
        source_file_paths: list[str],
        max_related_files: int = 10,
    ) -> list[str]:
        """
        @brief Retrieves LSP context for a rule's source files and related files.

        @details
        Retrieves document symbols, definitions, and references from the
        original source files. It then follows definition and reference
        locations to retrieve context from related files.

        @param lsp_context Complete codebase LSP context.
        @param source_file_paths Files associated with the business rule.
        @param max_related_files Maximum number of additional related files.
        @return Formatted LSP context strings.
        """
        if lsp_context is None:
            return []

        # Retrieve the original source files first.
        source_contexts = {}
        for path in source_file_paths:
            file_context = lsp_context.get_file_context(path)

            if file_context is not None:
                key = str(file_context.file_path.resolve())
                source_contexts[key] = file_context

        # Identify files connected through definitions and references.
        related_paths = set()

        for file_context in source_contexts.values():
            for definition in file_context.definitions:
                related_paths.add(str(definition.location.file_path))

            for reference in file_context.references:
                related_paths.add(str(reference.location.file_path))

        # Retrieve related file contexts, excluding original source files.
        related_contexts = {}

        for path in sorted(related_paths):
            file_context = lsp_context.get_file_context(path)

            if file_context is None:
                continue

            key = str(file_context.file_path.resolve())

            if key in source_contexts or key in related_contexts:
                continue

            related_contexts[key] = file_context

            if len(related_contexts) >= max_related_files:
                break

        # Format the source files first, followed by related files.
        formatted_contexts = []

        for file_context in source_contexts.values():
            formatted = format_file_context(file_context)

            if formatted:
                formatted_contexts.append(
                    f"[LSP SOURCE FILE]\n"
                    f"File: {file_context.file_path}\n"
                    f"{formatted}"
                )

        for file_context in related_contexts.values():
            formatted = format_file_context(file_context)

            if formatted:
                formatted_contexts.append(
                    f"[LSP RELATED FILE]\n"
                    f"File: {file_context.file_path}\n"
                    f"{formatted}"
                )

        return formatted_contexts

