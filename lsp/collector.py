
"""
@file collector.py
@brief Collects LSP context across an entire codebase.

Uses one LSPManager session to collect document symbols from supported
source files. The resulting CodebaseContext can be shared with analysis
agents so they do not need to initialize their own language servers.
"""

import asyncio
import logging
import os
from pathlib import Path

from agent.crawl_config import prune
from lsp.context import CodebaseContext
from lsp.language import LSP_LANGUAGES, detect_language
from lsp.manager import LSPManager

logger = logging.getLogger(__name__)


async def collect_codebase_context(
    codebase_path: str | Path,
    *,
    enabled: bool = True,
    request_timeout: float | None = None,
) -> CodebaseContext:
    """
    Collects document symbols for supported files in a codebase.

    Uses one LSPManager session and reuses language clients across files.
    Failed requests are recorded as warnings rather than stopping collection.
    """

    codebase_path = Path(codebase_path).resolve()

    # Create the result container, even when collection cannot run.
    context = CodebaseContext(codebase_path=codebase_path)

    if not enabled:
        context.warnings.append("LSP collection is disabled.")
        return context

    if not codebase_path.is_dir():
        context.warnings.append(
            f"Codebase directory does not exist: {codebase_path}"
        )
        return context

    files_by_language = {}

    # Find files that have a registered LSP client.
    for root, dirs, filenames in os.walk(codebase_path):
        prune(root, dirs)

        for filename in filenames:
            language = detect_language(filename)

            if language not in LSP_LANGUAGES:
                continue

            file_path = (Path(root) / filename).resolve()
            files_by_language.setdefault(language, []).append(file_path)

    if not files_by_language:
        context.warnings.append(
            "No files with supported LSP languages were found."
        )
        return context

    # Record detected languages (not necessarily successfully started servers).
    context.languages.update(files_by_language.keys())

    total_files = sum(len(files) for files in files_by_language.values())

    logger.info(
        "Starting LSP collection for %d languages and %d files.",
        len(files_by_language),
        total_files,
    )

    manager_kwargs = {}

    if request_timeout is not None:
        manager_kwargs["request_timeout"] = request_timeout

    try:
        # One manager session keeps each language server alive while its
        # files are processed, then shuts everything down when finished.
        async with LSPManager(
            codebase_path,
            **manager_kwargs,
        ) as manager:

            for language, files in files_by_language.items():
                logger.info(
                    "Collecting LSP context for %s (%d files).",
                    language,
                    len(files),
                )

                # Process sequentially initially to avoid overwhelming
                # language servers with simultaneous requests.
                for file_path in files:
                    relative_path = str(
                        file_path.relative_to(codebase_path)
                    )

                    try:
                        file_context = await manager.get_file_context(
                            file_path
                        )

                        if file_context is None:
                            reason = manager.failed.get(language)

                            if reason:
                                warning = (
                                    f"{relative_path}: "
                                    f"LSP unavailable for {language}: {reason}"
                                )
                            else:
                                warning = (
                                    f"{relative_path}: "
                                    "No LSP context returned."
                                )

                            context.warnings.append(warning)
                            continue

                        # Store context using the file's relative path
                        # so agents can retrieve it consistently.
                        context.files[relative_path] = file_context

                    except Exception as error:
                        warning = (
                            f"{relative_path}: "
                            f"LSP collection failed: {error}"
                        )

                        context.warnings.append(warning)
                        logger.warning(warning, exc_info=True)

    except Exception as error:
        warning = f"LSP collection encountered an error: {error}"
        context.warnings.append(warning)
        logger.exception(warning)

    logger.info(
        "LSP collection completed: %d/%d files collected, %d warnings.",
        len(context.files),
        total_files,
        len(context.warnings),
    )

    return context


def collect_codebase_context_sync(
    codebase_path: str | Path,
    *,
    enabled: bool = True,
    request_timeout: float | None = None,
) -> CodebaseContext:
    """
    Synchronous wrapper for workflows that are not running an event loop.
    """

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(
            collect_codebase_context(
                codebase_path,
                enabled=enabled,
                request_timeout=request_timeout,
            )
        )

    raise RuntimeError(
        "collect_codebase_context_sync cannot be called "
        "from an active event loop. Use await collect_codebase_context()."
    )
