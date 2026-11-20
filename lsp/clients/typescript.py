
from pathlib import Path

from lsp.base_client import LSPClient
from lsp_client.clients.typescript import TypescriptClient


class TypescriptClientAdapter(LSPClient):
    """LSP adapter for typescript using TypescriptClient."""

    def __init__(self, workspace_path: str | Path):
        super().__init__(
            workspace_path=workspace_path,
            client_class=TypescriptClient,
        )
