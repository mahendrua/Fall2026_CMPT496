
"""
@file backend/lsp/context_storage.py
@brief Persistent storage for collected LSP codebase context.
@details Serializes CodebaseContext and its nested models to JSON and
         reconstructs their original Python types when loading.
"""

import hashlib
import json
import logging
import re

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from lsp.context import CodebaseContext, FileContext
from lsp.models import (
    DocumentSymbol,
    Definition,
    Reference,
    Location,
    Range,
    Position,
)

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1


class ContextStorageError(Exception):
    """Raised when saved LSP context cannot be read or reconstructed."""


def get_context_storage_dir(codebase_path: str | Path, output_dir=None) -> Path:
    """
    Return the storage directory for a particular codebase.

    Storage layout:
        agent/lsp_context_output/<codebase_name>_<path_hash>/
    """

    codebase_path = Path(codebase_path).resolve()

    if output_dir is not None:
        base_dir = Path(output_dir).resolve()
    else:
        # context_storage.py is located in backend/lsp/.
        base_dir = Path(__file__).resolve().parents[2]

    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", codebase_path.name)

    path_hash = hashlib.sha256(
        str(codebase_path).encode("utf-8")
    ).hexdigest()[:10]

    return (
        base_dir
        / "agent"
        / "lsp_context_output"
        / f"{safe_name}_{path_hash}"
    )


def _serialize_symbol(symbol: DocumentSymbol) -> dict:
    """Convert a DocumentSymbol and its children into JSON-compatible data."""

    return {
        "name": symbol.name,
        "kind": symbol.kind,
        "range": symbol.range.model_dump(mode="json"),
        "selection_range": symbol.selection_range.model_dump(mode="json"),
        "detail": symbol.detail,
        "children": [
            _serialize_symbol(child)
            for child in symbol.children
        ],
    }


def _deserialize_symbol(data: dict) -> DocumentSymbol:
    """Reconstruct a DocumentSymbol, including nested children."""

    return DocumentSymbol(
        name=data["name"],
        kind=data["kind"],
        range=Range.model_validate(data["range"]),
        selection_range=Range.model_validate(data["selection_range"]),
        detail=data.get("detail"),
        children=[
            _deserialize_symbol(child)
            for child in data.get("children", [])
        ],
    )


def _serialize_file_context(file_context: FileContext) -> dict:
    """Convert a FileContext into JSON-compatible data."""

    return {
        "file_path": str(file_context.file_path),
        "language": file_context.language,
        "symbols": [
            _serialize_symbol(symbol)
            for symbol in file_context.symbols
        ],
        "definitions": [
            definition.model_dump(mode="json")
            for definition in file_context.definitions
        ],
        "references": [
            reference.model_dump(mode="json")
            for reference in file_context.references
        ],
    }


def _deserialize_file_context(data: dict) -> FileContext:
    """Reconstruct a FileContext and its nested LSP models."""

    return FileContext(
        file_path=Path(data["file_path"]),
        language=data["language"],
        symbols=[
            _deserialize_symbol(symbol)
            for symbol in data.get("symbols", [])
        ],
        definitions=[
            Definition.model_validate(definition)
            for definition in data.get("definitions", [])
        ],
        references=[
            Reference.model_validate(reference)
            for reference in data.get("references", [])
        ],
    )


def save_codebase_context(
    context: CodebaseContext,
    output_dir=None,
) -> Path:
    """
    Save collected LSP context to disk.

    Args:
        context: The collected CodebaseContext.
        output_dir: Optional writable application output directory.

    Returns:
        Path to the saved context.json file.
    """

    if not isinstance(context, CodebaseContext):
        raise TypeError("context must be a CodebaseContext instance")

    storage_dir = get_context_storage_dir(
        context.codebase_path,
        output_dir,
    )

    logger.info("LSP context storage location: %s", storage_dir)
    storage_dir.mkdir(parents=True, exist_ok=True)

    context_data = {
        "codebase_path": str(context.codebase_path.resolve()),
        "languages": sorted(context.languages),
        "warnings": context.warnings,
        "files": {
            relative_path: _serialize_file_context(file_context)
            for relative_path, file_context in context.files.items()
        },
    }

    metadata = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "codebase_path": str(context.codebase_path.resolve()),
        "file_count": len(context.files),
        "languages": sorted(context.languages),
    }

    context_file = storage_dir / "context.json"
    metadata_file = storage_dir / "metadata.json"

    try:
        # Write temporary files first to avoid leaving partially written JSON.
        context_temp = storage_dir / "context.tmp"
        metadata_temp = storage_dir / "metadata.tmp"

        context_temp.write_text(
            json.dumps(context_data, indent=2),
            encoding="utf-8",
        )

        metadata_temp.write_text(
            json.dumps(metadata, indent=2),
            encoding="utf-8",
        )

        context_temp.replace(context_file)
        metadata_temp.replace(metadata_file)

    except (OSError, TypeError, ValueError) as exc:
        raise ContextStorageError(
            f"Failed to save LSP context: {exc}"
        ) from exc

    logger.info(
        "Saved LSP context for %s (%d files)",
        context.codebase_path,
        len(context.files),
    )

    return context_file


def load_codebase_context(
    codebase_path: str | Path,
    output_dir=None,
) -> CodebaseContext | None:
    """
    Load previously collected LSP context.

    Returns:
        A reconstructed CodebaseContext, or None if no saved context exists.

    Raises:
        ContextStorageError: If the saved context is invalid or incompatible.
    """

    codebase_path = Path(codebase_path).resolve()

    storage_dir = get_context_storage_dir(
        codebase_path,
        output_dir,
    )

    context_file = storage_dir / "context.json"
    metadata_file = storage_dir / "metadata.json"

    if not context_file.exists() or not metadata_file.exists():
        logger.info("No saved LSP context found for %s", codebase_path)
        return None

    try:
        context_data = json.loads(context_file.read_text(encoding="utf-8"))
        metadata = json.loads(metadata_file.read_text(encoding="utf-8"))

        if metadata.get("schema_version") != SCHEMA_VERSION:
            raise ContextStorageError(
                "Unsupported LSP context schema version: "
                f"{metadata.get('schema_version')}"
            )

        saved_path = Path(metadata["codebase_path"]).resolve()

        if saved_path != codebase_path:
            raise ContextStorageError(
                "Saved LSP context belongs to a different codebase."
            )

        files = {
            relative_path: _deserialize_file_context(file_data)
            for relative_path, file_data in context_data.get("files", {}).items()
        }

        context = CodebaseContext(
            codebase_path=Path(context_data["codebase_path"]),
            files=files,
            languages=set(context_data.get("languages", [])),
            warnings=context_data.get("warnings", []),
        )

        logger.info(
            "Loaded LSP context for %s (%d files)",
            codebase_path,
            len(context.files),
        )

        return context

    except ContextStorageError:
        raise

    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ContextStorageError(
            f"Failed to load LSP context: {exc}"
        ) from exc


def has_codebase_context(
    codebase_path: str | Path,
    output_dir=None,
) -> bool:
    """Check whether both saved context files exist."""

    storage_dir = get_context_storage_dir(codebase_path, output_dir)

    return (
        (storage_dir / "context.json").is_file()
        and (storage_dir / "metadata.json").is_file()
    )


def delete_codebase_context(
    codebase_path: str | Path,
    output_dir=None,
) -> bool:
    """
    Delete saved LSP context for a codebase.

    Returns:
        True if the saved context directory was deleted, otherwise False.
    """

    import shutil

    storage_dir = get_context_storage_dir(codebase_path, output_dir)

    if not storage_dir.exists():
        return False

    shutil.rmtree(storage_dir)

    logger.info("Deleted LSP context for %s", codebase_path)

    return True
