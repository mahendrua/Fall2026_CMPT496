"""
@file manager.py
@brief Starts the language servers a run needs and asks them about files.

@details A language's server starts the first time a file in that language
is asked about, so a C# project with a README and a build script only ever
starts csharp-ls. A server that is not installed, crashes or hangs is
recorded in `failed` and skipped for the rest of the run; it never stops the
step. Nothing here prints: stdout is the backend's message channel to the
app, so problems go to the log.
"""

import asyncio
import logging
import os
import re
from contextlib import AsyncExitStack
from pathlib import Path
from typing import TYPE_CHECKING

from agent.crawl_config import prune
from lsp.context import FileContext
from lsp.language import detect_language

if TYPE_CHECKING:
    from lsp.base_client import LSPClient

logger = logging.getLogger(__name__)

# Seconds. csharp-ls answers its first request only after loading the
# project (about 9 s for ConsoleTables), so both limits are generous. They
# exist so that a server that hangs cannot hang the step.
START_TIMEOUT = 60
REQUEST_TIMEOUT = 60

# Seconds for each request while stopping a server (finishing requests,
# shutdown, exit), so a stuck server delays the step by seconds, not minutes.
SHUTDOWN_REQUEST_TIMEOUT = 2

NOT_ON_PATH = re.compile(r"Program '([^']+)' not found in PATH")


def load_client_classes():
    """
    Returns the language-specific client classes, by language.

    They are imported here rather than at the top of the module because each
    one imports lsp-client, which is optional: the backend has to start, and
    this module has to import, without it.
    """
    #language-specific client imports go here
    from lsp.clients.c import CClient
    from lsp.clients.csharp import CSharpClient
    from lsp.clients.go import GoClient
    from lsp.clients.java import JavaClient
    from lsp.clients.python import PythonClient
    from lsp.clients.rust import RustClient
    from lsp.clients.typescript import TypescriptClientAdapter

    return {
        # Existing adapters
        "csharp": CSharpClient,
        "c": CClient,

        # Python
        "python": PythonClient,

        # JavaScript and TypeScript
        "javascript": TypescriptClientAdapter,
        "typescript": TypescriptClientAdapter,

        # Other supported languages
        "go": GoClient,
        "rust": RustClient,
        "java": JavaClient,
    }


def describe_error(error: BaseException) -> str:
    """
    One readable line for an error, including the errors grouped inside it
    and the error that caused it.

    lsp-client reports a server that could not start as an ExceptionGroup
    whose real reason ("Program 'csharp-ls' not found in PATH.") is the cause
    of an error inside the group.
    """
    if isinstance(error, BaseExceptionGroup):
        parts = []
        for inner in error.exceptions:
            text = describe_error(inner)
            if text not in parts:
                parts.append(text)
        return "; ".join(parts)

    text = str(error).strip() or type(error).__name__
    if error.__cause__ is not None:
        cause = describe_error(error.__cause__)
        if cause not in text:
            text = f"{text} ({cause})"
    return text


def start_failure_reason(error: BaseException) -> str:
    """Why a server did not start, in plain words when it isn't installed."""
    text = describe_error(error)
    missing = NOT_ON_PATH.search(text)
    if missing:
        return f"its language server ({missing.group(1)}) is not installed or not on PATH"
    return text


class LSPManager:
    """
    Manages language-specific LSP clients for a given codebase.

    Use it as an async context manager: servers start on first use, and all
    of them stop when the context exits.
    """

    def __init__(
        self,
        codebase_path: str | Path,
        client_classes: dict | None = None,
        start_timeout: float = START_TIMEOUT,
        request_timeout: float = REQUEST_TIMEOUT,
    ):
        """
        Args:
            codebase_path: Root folder of the codebase (the LSP workspace).
            client_classes: Client class by language. Defaults to the real
                clients; tests pass fakes.
            start_timeout: Seconds a server may take to start.
            request_timeout: Seconds a server may take to answer one request.
        """
        self.codebase_path = Path(codebase_path).resolve()
        self._client_classes = client_classes
        self.start_timeout = start_timeout
        self.request_timeout = request_timeout

        self._stack = AsyncExitStack()
        self.clients = {}

        # Every client started, including ones given up on later: all of
        # them are stopped on exit.
        self._started = []

        # Why a language's server can't be used in this run, by language.
        self.failed = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        for client in self._started:
            client.set_request_timeout(SHUTDOWN_REQUEST_TIMEOUT)

        await self._stack.aclose()

    def detect_languages(self) -> set[str]:
        """
        Detects the programming languages used in the codebase.

        Skips the folders the crawlers skip (bin, obj, node_modules, ...).

        Returns:
            set[str]: A set of detected programming languages.
        """
        detected_languages = set()
        for root, dirs, filenames in os.walk(self.codebase_path):
            prune(root, dirs)
            for filename in filenames:
                language = detect_language(filename)
                if language:
                    detected_languages.add(language)
        return detected_languages

    def get_language(self, file_path: str | Path) -> str | None:
        """
        Detects the programming language of a specific file.

        Args:
            file_path: Path to the source file.

        Returns:
            The detected programming language, or None if it cannot be detected.
        """
        return detect_language(file_path)

    def get_client(self, file_path: str | Path):
        """
        Returns the started client for a file's language, or None if that
        language's server has not been started (get_file_context starts it).
        """
        language = self.get_language(file_path)

        if language is None:
            return None

        return self.clients.get(language)

    def get_client_class(self, language: str):
        """
        Returns the appropriate LSP client class for a given programming language.

        Args:
            language (str): The programming language.
        """
        if language is None:
            raise ValueError("Language cannot be None")

        if self._client_classes is None:
            self._client_classes = load_client_classes()

        return self._client_classes.get(language)

    def create_client(self, language: str) -> "LSPClient | None":
        """
        Creates a language-specific LSP client for the given programming language.

        Args:
            language (str): The programming language.
        """
        client_class = self.get_client_class(language)

        if client_class is None:
            return None

        return client_class(self.codebase_path)

    async def start_client(self, language: str) -> "LSPClient | None":
        """
        Returns the client for a language, starting its server on first use.

        Returns None when the language has no server (markdown, yaml, ...) or
        its server could not be started; `failed` says why for the second.
        """
        if language in self.failed:
            return None

        if language in self.clients:
            return self.clients[language]

        try:
            client = self.create_client(language)
        except Exception as error:
            return self._give_up(language, f"could not be set up: {describe_error(error)}")

        if client is None:
            return None

        client.set_request_timeout(self.request_timeout)

        try:
            async with asyncio.timeout(self.start_timeout):
                client = await self._stack.enter_async_context(client)
        except TimeoutError:
            return self._give_up(
                language, f"its language server did not start within {self.start_timeout:g} s"
            )
        except Exception as error:
            return self._give_up(language, start_failure_reason(error))

        self._started.append(client)
        self.clients[language] = client
        return client

    def _give_up(self, language: str, reason: str) -> None:
        """Stops using a language's server for the rest of the run."""
        self.failed[language] = reason
        # A started server stays on the exit stack, so it is still shut down.
        self.clients.pop(language, None)
        logger.warning("Code navigation (LSP) is off for %s in this run: %s", language, reason)
        return None

    async def get_file_context(self, file_path: str | Path):
        """
        Returns the symbols in one file, from its language's server.

        Returns None when the file's language has no usable server or the
        file is outside the codebase. A server that stops answering is given
        up for the rest of the run, so one hung server can't make every file
        wait for its own time limit.
        """
        file_path = Path(file_path).resolve()

        language = self.get_language(file_path)

        if language is None:
            return None

        # ensure the file is in the workspace
        try:
            file_path.relative_to(self.codebase_path)
        except ValueError:
            logger.warning("Skipping %s: it is outside the LSP workspace %s", file_path, self.codebase_path)
            return None

        client = await self.start_client(language)

        if client is None:
            return None

        file_content = file_path.read_text(encoding="utf-8", errors="replace")

        try:
            await client.open_file(file_path, file_content)
            symbols = await client.get_document_symbols(file_path)
        except TimeoutError:
            self._give_up(
                language,
                f"its language server stopped answering (no reply within {self.request_timeout:g} s)",
            )
            return None

        return FileContext(file_path=file_path, language=language, symbols=symbols)
