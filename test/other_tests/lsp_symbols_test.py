"""
@file lsp_symbols_test.py
@brief Offline tests for the code navigation (LSP) layer ported from Ian's
branch: it stays off unless switched on, never stops a step, starts only the
servers it needs, gives up on servers that are missing or hang, and turns
symbols into a short list for the AI.

They use fake language server clients, so neither lsp-client nor a language
server is needed. The last two tests use the real lsp-client (and csharp-ls)
when installed and are skipped otherwise.
"""

import asyncio
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from lsp.manager import LSPManager
from lsp.models import DocumentSymbol, Position, Range
from lsp.symbols import SETTING, format_symbols, get_symbol_context, lsp_enabled

SOURCE = """namespace Shop
{
    public class Basket
    {
        public decimal Total { get; }
        public Basket(int size) { }
        public void Add(string item) { var count = 1; }
        private int Count() { return 0; }
    }
}
"""

BASKET_LIST = """namespace Shop
  public class Basket
    public property decimal Basket.Total
    public constructor Basket.Basket(int size)
    public method void Basket.Add(string item)
    private method int Basket.Count()"""


def symbol(name, kind, line=0, character=0, detail=None, children=()):
    where = Range(start=Position(line=line, character=character), end=Position(line=line, character=character + 1))
    return DocumentSymbol(
        name=name, kind=kind, range=where, selection_range=where, detail=detail, children=list(children)
    )


def at(line, word):
    """Line and column of a word in SOURCE, as a language server reports them."""
    return line, SOURCE.splitlines()[line].index(word)


def basket_symbols():
    """What csharp-ls returns for SOURCE: file > namespace > class > members."""
    return [
        symbol("Basket.cs", 1, children=[
            symbol("Shop", 3, *at(0, "Shop"), detail="Shop", children=[
                symbol("Basket", 5, *at(2, "Basket"), children=[
                    symbol("Total", 7, *at(4, "Total"), detail="decimal Basket.Total"),
                    symbol("Basket(int size)", 9, *at(5, "Basket"), detail="Basket.Basket(int size)"),
                    symbol(
                        "Add(string item)", 6, *at(6, "Add"), detail="void Basket.Add(string item)",
                        children=[symbol("count", 13, *at(6, "count"))],
                    ),
                    symbol("Count()", 6, *at(7, "Count"), detail="int Basket.Count()"),
                ]),
            ]),
        ])
    ]


def fake_client(language, log, start_error=None, start_delay=0.0, hang_on=(), answers=None):
    """
    A client class standing in for lsp.base_client.LSPClient and its server.

    `log` records starts, stops and requests. `hang_on` names files whose
    request never finishes (as when a server stops answering).
    """

    class FakeClient:
        def __init__(self, workspace_path):
            self.request_timeout = None

        def set_request_timeout(self, seconds):
            self.request_timeout = seconds

        async def __aenter__(self):
            log.append(f"{language} start")
            if start_delay:
                await asyncio.sleep(start_delay)
            if start_error is not None:
                raise start_error
            return self

        async def __aexit__(self, *exc_info):
            log.append(f"{language} stop")

        async def open_file(self, file_path, file_content):
            pass

        async def get_document_symbols(self, file_path):
            name = Path(file_path).name
            log.append(f"{language} symbols {name}")
            if name in hang_on:
                await asyncio.sleep(60)
            return (answers or {}).get(name, basket_symbols())

    return FakeClient


def manager_with(client_classes, **limits):
    return lambda codebase: LSPManager(codebase, client_classes=client_classes, **limits)


def write_files(root, *names):
    for name in names:
        (root / name).write_text(SOURCE, encoding="utf-8")


def test_lsp_is_off_unless_switched_on(tmp_path, monkeypatch):
    monkeypatch.delenv(SETTING, raising=False)
    env_file = tmp_path / ".env"

    env_file.write_text("ACTIVE_LLM_KEY=GOOGLE__default\n", encoding="utf-8")
    assert not lsp_enabled(env_file)

    env_file.write_text(f"{SETTING}=on\n", encoding="utf-8")
    assert lsp_enabled(env_file)

    env_file.write_text(f"{SETTING}=off\n", encoding="utf-8")
    assert not lsp_enabled(env_file)

    # An environment variable wins over .env.
    monkeypatch.setenv(SETTING, "1")
    assert lsp_enabled(env_file)


def test_when_off_nothing_is_started(tmp_path):
    write_files(tmp_path, "Basket.cs")

    def must_not_run(codebase):
        raise AssertionError("no language server should be started when LSP is off")

    result = get_symbol_context(tmp_path, ["Basket.cs"], enabled=False, manager_factory=must_not_run)

    assert result.texts == {}
    assert result.warnings == []


def test_loading_the_lsp_layer_does_not_need_lsp_client():
    """The backend imports this at startup, and lsp-client is optional."""
    script = "import sys, lsp.symbols, lsp.manager; print('lsp_client' in sys.modules)"
    run = subprocess.run(
        [sys.executable, "-c", script], cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=120
    )

    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == "False"


def test_symbols_become_a_short_list_with_signatures_and_access_words():
    text = format_symbols(basket_symbols(), SOURCE)

    assert text == BASKET_LIST
    # Positions and a method's local variables are left out.
    assert "count" not in text
    assert "line" not in text


def test_a_long_list_is_cut_with_a_count_of_what_was_left_out():
    methods = [symbol(f"Step{n}()", 6, detail=f"void Steps.Step{n}()") for n in range(10)]

    text = format_symbols([symbol("Steps", 5, children=methods)], max_lines=4)

    assert text.splitlines() == [
        "class Steps",
        "  method void Steps.Step0()",
        "  method void Steps.Step1()",
        "  method void Steps.Step2()",
        "... 7 more not shown",
    ]


def test_a_type_only_detail_is_shown_after_the_name():
    """gopls and others put only the type in detail, not the whole signature."""
    text = format_symbols([symbol("Total", 12, detail="func() int")])

    assert text == "function Total: func() int"


def test_only_the_servers_needed_are_started_and_nothing_is_printed(tmp_path, capsys):
    write_files(tmp_path, "Basket.cs", "Order.cs")
    (tmp_path / "README.md").write_text("# Shop\n", encoding="utf-8")
    log = []
    clients = {"csharp": fake_client("csharp", log), "python": fake_client("python", log)}

    loop = asyncio.new_event_loop()
    try:
        result = get_symbol_context(
            tmp_path, ["Basket.cs", "README.md", "Order.cs", "Basket.cs"],
            enabled=True, loop=loop, manager_factory=manager_with(clients),
        )
    finally:
        loop.close()

    assert result.texts == {"Basket.cs": BASKET_LIST, "Order.cs": BASKET_LIST}
    assert result.warnings == []
    # One C# server for both files; none for Python or the README.
    assert log == ["csharp start", "csharp symbols Basket.cs", "csharp symbols Order.cs", "csharp stop"]
    # stdout is the backend's message channel to the app.
    assert capsys.readouterr().out == ""


def test_a_missing_server_is_a_warning_and_other_languages_still_work(tmp_path):
    write_files(tmp_path, "Basket.cs", "Order.cs", "shop.py")
    log = []
    cause = RuntimeError("auto-installation is disabled.")
    cause.__cause__ = RuntimeError("Program 'csharp-ls' not found in PATH.")
    missing = ExceptionGroup("All servers failed to start for CSharpLSPClient", [cause])
    clients = {
        "csharp": fake_client("csharp", log, start_error=missing),
        "python": fake_client("python", log),
    }

    result = get_symbol_context(
        tmp_path, ["Basket.cs", "Order.cs", "shop.py"], enabled=True, manager_factory=manager_with(clients)
    )

    assert list(result.texts) == ["shop.py"]
    assert result.warnings == [
        "Code navigation (LSP) is off for C# in this run: "
        "its language server (csharp-ls) is not installed or not on PATH."
    ]
    # Tried once, not once per file.
    assert log.count("csharp start") == 1


def test_a_server_that_never_starts_is_given_up_after_its_time_limit(tmp_path):
    write_files(tmp_path, "Basket.cs")
    log = []
    clients = {"csharp": fake_client("csharp", log, start_delay=60)}

    started = time.perf_counter()
    result = get_symbol_context(
        tmp_path, ["Basket.cs"], enabled=True, manager_factory=manager_with(clients, start_timeout=0.2)
    )

    assert time.perf_counter() - started < 10
    assert result.texts == {}
    assert result.warnings == [
        "Code navigation (LSP) is off for C# in this run: its language server did not start within 0.2 s."
    ]


def test_a_server_that_stops_answering_is_not_asked_again(tmp_path):
    write_files(tmp_path, "A.cs", "B.cs", "C.cs")
    log = []

    class Silent(fake_client("csharp", log)):
        async def get_document_symbols(self, file_path):
            log.append(f"csharp symbols {Path(file_path).name}")
            raise TimeoutError  # what lsp-client raises when a request runs out of time

    result = get_symbol_context(
        tmp_path, ["A.cs", "B.cs", "C.cs"], enabled=True,
        manager_factory=manager_with({"csharp": Silent}, request_timeout=7),
    )

    assert result.texts == {}
    assert log == ["csharp start", "csharp symbols A.cs", "csharp stop"]
    assert result.warnings == [
        "Code navigation (LSP) is off for C# in this run: "
        "its language server stopped answering (no reply within 7 s)."
    ]


def test_the_whole_lookup_has_a_time_limit_and_keeps_finished_files(tmp_path):
    write_files(tmp_path, "Basket.cs", "Slow.cs")
    log = []
    clients = {"csharp": fake_client("csharp", log, hang_on={"Slow.cs"})}

    started = time.perf_counter()
    result = get_symbol_context(
        tmp_path, ["Basket.cs", "Slow.cs"], enabled=True,
        manager_factory=manager_with(clients), total_timeout=0.5,
    )

    assert time.perf_counter() - started < 10
    assert result.texts == {"Basket.cs": BASKET_LIST}
    assert result.warnings == [
        "Code navigation (LSP) took longer than 0.5 s, so it stopped there; "
        "the files it had finished are still used."
    ]
    # The server is still stopped.
    assert log[-1] == "csharp stop"


def test_running_out_of_time_reads_plainly_when_stopping_the_server_also_fails(tmp_path):
    """lsp-client reports a request cut off at shutdown as an ExceptionGroup."""
    write_files(tmp_path, "Slow.cs")
    log = []

    class CutOff(fake_client("csharp", log, hang_on={"Slow.cs"})):
        async def __aexit__(self, *exc_info):
            log.append("csharp stop")
            raise ExceptionGroup("unhandled errors in a TaskGroup", [TimeoutError()])

    result = get_symbol_context(
        tmp_path, ["Slow.cs"], enabled=True, manager_factory=manager_with({"csharp": CutOff}), total_timeout=0.5
    )

    assert result.texts == {}
    assert result.warnings == [
        "Code navigation (LSP) took longer than 0.5 s, so it stopped there; "
        "the files it had finished are still used."
    ]
    assert log[-1] == "csharp stop"


def test_missing_files_and_files_outside_the_codebase_are_skipped(tmp_path):
    codebase = tmp_path / "shop"
    codebase.mkdir()
    write_files(tmp_path, "Outside.cs")
    log = []

    result = get_symbol_context(
        codebase, ["../Outside.cs", "Missing.cs"], enabled=True,
        manager_factory=manager_with({"csharp": fake_client("csharp", log)}),
    )

    assert result.texts == {}
    assert result.warnings == []
    assert log == []


def test_lsp_client_not_installed_is_a_warning(tmp_path, monkeypatch):
    write_files(tmp_path, "Basket.cs")
    monkeypatch.setitem(sys.modules, "lsp_client", None)  # makes `import lsp_client` fail
    monkeypatch.setenv("LSP_CLIENT_DISABLE_AUTO_INSTALLATION", "unset")
    monkeypatch.delenv("LSP_CLIENT_DISABLE_AUTO_INSTALLATION")

    result = get_symbol_context(tmp_path, ["Basket.cs"], enabled=True)

    assert result.texts == {}
    assert result.warnings == [
        "Code navigation (LSP) is on, but lsp-client is not installed, so it was skipped. "
        "Run: pip install -r requirements.txt"
    ]
    # Set before lsp-client is imported, so it can never install servers itself.
    assert os.environ["LSP_CLIENT_DISABLE_AUTO_INSTALLATION"] == "true"


def test_flat_symbol_lists_from_older_servers_are_read():
    pytest.importorskip("lsp_client")
    from lsp.base_client import LSPClient
    from lsp_client.utils.types import lsp_type

    flat = lsp_type.SymbolInformation(
        name="Total",
        kind=lsp_type.SymbolKind.Property,
        location=lsp_type.Location(
            uri="file:///Basket.cs",
            range=lsp_type.Range(
                start=lsp_type.Position(line=4, character=23), end=lsp_type.Position(line=4, character=28)
            ),
        ),
    )

    converted = LSPClient._convert_document_symbol(object.__new__(LSPClient), flat)

    assert (converted.name, converted.kind, converted.children) == ("Total", 7, [])
    assert converted.selection_range.start.line == 4


@pytest.mark.skipif(shutil.which("csharp-ls") is None, reason="csharp-ls is not installed")
def test_real_csharp_server(tmp_path):
    """End to end with lsp-client and csharp-ls, when both are installed."""
    pytest.importorskip("lsp_client")
    (tmp_path / "Shop.csproj").write_text(
        '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>'
        "<TargetFramework>net8.0</TargetFramework></PropertyGroup></Project>",
        encoding="utf-8",
    )
    write_files(tmp_path, "Basket.cs")

    result = get_symbol_context(tmp_path, ["Basket.cs"], enabled=True)

    assert result.warnings == []
    assert result.texts["Basket.cs"].splitlines() == BASKET_LIST.splitlines()
