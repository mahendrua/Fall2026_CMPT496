from dataclasses import dataclass, field
from pathlib import Path

from lsp.models import DocumentSymbol, Definition, Reference

@dataclass
class FileContext:
    """
    Represents the context of a file in the workspace.

    Attributes:
        file_path (Path): The path to the file.
        document_symbols (list[DocumentSymbol]): A list of document symbols in the file.
        definitions (list[Definition]): A list of definitions in the file.
        references (list[Reference]): A list of references in the file.
    """
    file_path: Path
    language: str

    symbols: list[DocumentSymbol] = field(default_factory=list)
    definitions: list[Definition] = field(default_factory=list)
    references: list[Reference] = field(default_factory=list)


@dataclass
class CodebaseContext:
    codebase_path: Path

    files: dict[str, FileContext] = field(default_factory=dict)
    languages: set[str] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)

    def get_file_context(self, file_path: str | Path):
        path = Path(file_path)

        if not path.is_absolute():
            path = self.codebase_path / path

        try:
            key = str(
                path.resolve().relative_to(self.codebase_path.resolve())
            )
        except ValueError:
            return None

        return self.files.get(key)

    def get_multiple_file_contexts(self, file_paths):
        contexts = {}

        for path in file_paths:
            file_context = self.get_file_context(path)

            if file_context is not None:
                key = str(
                    Path(path).resolve()
                    .relative_to(self.codebase_path.resolve())
                )
                contexts[key] = file_context

        return contexts