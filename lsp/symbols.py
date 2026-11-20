"""
@file symbols.py
@brief The safe way for the agents to use code navigation (LSP).

@details get_symbol_context() asks the language servers which types and
members each given source file declares, with their full signatures, and
returns them as short text for an AI prompt.

It does nothing unless CHECKPOINT_LSP is on (in .env, or the environment),
and it never stops a step: if lsp-client is not installed, a language server
is missing, or a server crashes or hangs, those files get no text and the
reason comes back as a warning for the step to show.
"""

import asyncio
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

from agent.llm import ENV_PATH
from lsp.manager import LSPManager, describe_error

logger = logging.getLogger(__name__)

# Turned on with a line in .env:  CHECKPOINT_LSP=on
SETTING = "CHECKPOINT_LSP"
ON_VALUES = {"1", "true", "on", "yes"}

# Seconds for the whole lookup in one step, so a slow server costs at most this.
TOTAL_TIMEOUT = 300

# Lines of symbol text per file, so one huge file can't flood a prompt.
MAX_LINES_PER_FILE = 150

LANGUAGE_NAMES = {
    "csharp": "C#",
    "c": "C",
    "python": "Python",
    "javascript": "JavaScript",
    "typescript": "TypeScript",
    "go": "Go",
    "rust": "Rust",
    "java": "Java",
}

# LSP symbol kinds. The numbers are fixed by the protocol, so they mean the
# same thing for every language server.
FILE_KIND = 1
KIND_NAMES = {
    2: "module",
    3: "namespace",
    4: "package",
    5: "class",
    6: "method",
    7: "property",
    8: "field",
    9: "constructor",
    10: "enum",
    11: "interface",
    12: "function",
    13: "variable",
    14: "constant",
    22: "enum member",
    23: "struct",
    24: "event",
    25: "operator",
    26: "type parameter",
}

# Shown with their members indented below them. Everything else (methods,
# properties, fields, ...) is shown without its children: inside a function
# there are only local variables, which a test can't use.
CONTAINER_KINDS = {2, 3, 4, 5, 10, 11, 23}

# Access words written in front of a declaration (C#, Java, TypeScript, ...).
# Language servers don't report them, but tests can only use public members.
ACCESS_WORDS = re.compile(r"\b(public|private|protected|internal)\b")


@dataclass
class SymbolContext:
    """What get_symbol_context found."""

    # Symbol text by file, keyed by the path exactly as it was asked for.
    texts: dict[str, str] = field(default_factory=dict)

    # Plain-language reasons why some or all files got no text.
    warnings: list[str] = field(default_factory=list)


def lsp_enabled(env_path: Path = ENV_PATH) -> bool:
    """
    True when CHECKPOINT_LSP is on.

    An environment variable wins over .env. The .env file is read on every
    call, like the API key, so the setting changes without a restart.
    """
    value = os.environ.get(SETTING)
    if value is None:
        value = dotenv_values(env_path).get(SETTING)
    return (value or "").strip().lower() in ON_VALUES


def format_symbols(symbols, source_text: str = "", max_lines: int = MAX_LINES_PER_FILE) -> str:
    """
    The types and members in `symbols` as indented lines: the access word
    (when the code has one), the kind and the full signature, e.g.

        namespace ConsoleTables
          public class ConsoleTable
            public method ConsoleTable ConsoleTable.AddRow(params object[] values)
            private method int ConsoleTable.GetTextWidth(string value)

    No line numbers or positions: the AI needs names and signatures.

    Args:
        symbols: DocumentSymbol objects from LSPManager.get_file_context.
        source_text: The file's text, to read access words from.
        max_lines: Lines kept; the rest are counted in a last line.
    """
    source_lines = source_text.splitlines()
    lines = []

    def access(symbol):
        start = symbol.selection_range.start
        if start.line >= len(source_lines):
            return ""
        return " ".join(ACCESS_WORDS.findall(source_lines[start.line][: start.character]))

    def walk(items, depth):
        for symbol in items:
            kind = _kind_number(symbol.kind)

            if kind == FILE_KIND:
                walk(symbol.children, depth)
                continue

            parts = (access(symbol), KIND_NAMES.get(kind, "symbol"), _signature(symbol))
            lines.append("  " * depth + " ".join(part for part in parts if part))

            if kind in CONTAINER_KINDS:
                walk(symbol.children, depth + 1)

    walk(symbols, 0)

    if len(lines) > max_lines:
        left_out = len(lines) - max_lines
        lines = lines[:max_lines] + [f"... {left_out} more not shown"]

    return "\n".join(lines)


def _kind_number(kind) -> int:
    try:
        return int(getattr(kind, "value", kind))
    except (TypeError, ValueError):
        return 0


def _signature(symbol) -> str:
    """
    The fullest form of a symbol's name. csharp-ls puts the whole signature
    in `detail` ("ConsoleTable ConsoleTable.AddRow(params object[] values)");
    other servers put only a type there ("func(x int) string"), which is
    then shown after the name.
    """
    name = " ".join(symbol.name.split())
    detail = " ".join((symbol.detail or "").split())

    if not detail or detail == name:
        return name
    if name.split("(")[0] in detail:
        return detail
    return f"{name}: {detail}"


def get_symbol_context(
    codebase_path,
    file_paths,
    *,
    enabled: bool | None = None,
    loop: asyncio.AbstractEventLoop | None = None,
    manager_factory=None,
    total_timeout: float = TOTAL_TIMEOUT,
) -> SymbolContext:
    """
    Symbol text for each source file, from the language servers.

    Args:
        codebase_path: Root folder of the analysed codebase.
        file_paths: Source files, relative to codebase_path or absolute.
        enabled: Overrides the CHECKPOINT_LSP setting.
        loop: Event loop to run on, for agents that keep their own; otherwise
            a new one is used. Must not be running.
        manager_factory: Builds the LSPManager from the codebase path. Tests
            pass fakes.
        total_timeout: Seconds for the whole lookup.

    Returns:
        A SymbolContext. Empty when the setting is off. Never raises.
    """
    result = SymbolContext()

    if enabled is None:
        enabled = lsp_enabled()

    if not enabled or not file_paths:
        return result

    if manager_factory is None:
        try:
            manager_factory = _real_manager_factory()
        except ImportError as error:
            logger.warning("Code navigation (LSP) is on, but lsp-client can't be imported: %s", error)
            result.warnings.append(
                "Code navigation (LSP) is on, but lsp-client is not installed, so it was "
                "skipped. Run: pip install -r requirements.txt"
            )
            return result

    work = _collect(Path(codebase_path).resolve(), file_paths, manager_factory, result, total_timeout)

    try:
        if loop is not None:
            loop.run_until_complete(work)
        else:
            asyncio.run(work)
    except Exception as error:
        work.close()
        logger.warning("Code navigation (LSP) stopped early", exc_info=True)
        result.warnings.append(f"Code navigation (LSP) stopped early: {describe_error(error)}")

    return result


def _real_manager_factory():
    """
    The real LSPManager, once lsp-client can be imported.

    lsp-client reads LSP_CLIENT_DISABLE_AUTO_INSTALLATION once, when it is
    first imported. Without it, a missing server makes lsp-client run
    `npm install -g`, `go install` or `rustup` on the user's machine.
    """
    os.environ.setdefault("LSP_CLIENT_DISABLE_AUTO_INSTALLATION", "true")

    import lsp_client  # noqa: F401  (ImportError: not installed)
    from loguru import logger as lsp_client_log

    # lsp-client logs each of its own steps; the warnings here say what matters.
    lsp_client_log.disable("lsp_client")

    return LSPManager


async def _collect(codebase: Path, file_paths, manager_factory, result: SymbolContext, total_timeout: float):
    """Fills `result` in place, so files already read survive a timeout."""
    manager = manager_factory(codebase)
    budget = asyncio.timeout(total_timeout)
    finished = False

    try:
        async with budget:
            async with manager:
                for asked in dict.fromkeys(map(str, file_paths)):
                    await _add_file(manager, codebase, asked, result)
                finished = True

    except Exception as error:
        if finished:
            # Every file was read; only stopping a server went wrong.
            logger.warning("A language server did not stop cleanly: %s", describe_error(error))
        elif budget.expired():
            # lsp-client may report this as an ExceptionGroup from stopping a
            # server mid-request rather than as a plain TimeoutError.
            logger.warning("Code navigation (LSP) ran out of time after %g s", total_timeout)
            result.warnings.append(
                f"Code navigation (LSP) took longer than {total_timeout:g} s, so it stopped there; "
                "the files it had finished are still used."
            )
        else:
            logger.warning("Code navigation (LSP) stopped early", exc_info=True)
            result.warnings.append(f"Code navigation (LSP) stopped early: {describe_error(error)}")

    finally:
        for language, reason in manager.failed.items():
            name = LANGUAGE_NAMES.get(language, language)
            result.warnings.append(f"Code navigation (LSP) is off for {name} in this run: {reason}.")


async def _add_file(manager, codebase: Path, asked: str, result: SymbolContext):
    path = Path(asked)
    if not path.is_absolute():
        path = codebase / path

    if not path.is_file():
        return

    try:
        context = await manager.get_file_context(path)
    except Exception as error:
        logger.warning("No symbols for %s: %s", asked, describe_error(error))
        return

    if context is None or not context.symbols:
        return

    source = path.read_text(encoding="utf-8", errors="replace")
    text = format_symbols(context.symbols, source)
    if text:
        result.texts[asked] = text
