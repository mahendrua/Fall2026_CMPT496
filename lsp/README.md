# Code navigation (LSP)

Asks a language server (the kind of tool an editor like VS Code uses to
understand code) for the real types and members a source file declares, with
their full signatures, as a short list for the AI, e.g.

```
namespace ConsoleTables
  public class ConsoleTable
    public method ConsoleTable ConsoleTable.AddRow(params object[] values)
    private method int ConsoleTable.GetTextWidth(string value)
```

Written by Ian (ayottei) on `ian-498`. Ported onto `cmpt-496` and made
optional and safe for the app. No agent uses it yet.

## Turning it on

It is off unless `.env` says otherwise.

1. `pip install -r requirements.txt` (adds `lsp-client` 0.3.9).
2. Install the language server for the project's language and make sure it is
   on PATH. For C#: `dotnet tool install -g csharp-ls`.
3. Add `CHECKPOINT_LSP=on` to `.env`. It is read on every use, so no restart
   is needed.

| Language | Server | Tested |
|---|---|---|
| C# | `csharp-ls` | Yes, 0.28.0 on Windows (ConsoleTables, Stateless) |
| Python | `pyright-langserver` (`npm install -g pyright`) | No |
| JavaScript, TypeScript | `typescript-language-server` | No |
| Go | `gopls` | No |
| Rust | `rust-analyzer` | No |
| Java | `jdtls` | No |
| C | `clangd` | No |

## What it never does

- **Stop a step.** If `lsp-client` is not installed, a server is missing, or a
  server crashes or hangs, those files get no list and the step gets a
  warning instead.
- **Install anything.** `lsp-client` would otherwise run `npm install -g`,
  `go install` or `rustup` when a server is missing.
- **Print to stdout**, which is the backend's message channel to the app.
- **Start servers it doesn't need.** A server starts the first time a file in
  its language is asked about, so a C# project with a README only starts
  `csharp-ls`.

## Time limits

60 s for a server to start, 60 s per request (csharp-ls needs about 9 s to
load a project before its first answer), and 300 s for one step's whole
lookup. A server that stops answering is not asked again in that run.

## Known limits

- Servers don't report `public`/`private`. The list shows the access word
  written in the code, so a C# member declared without one (private by
  default) shows none.
- When `csharp-ls` loads a C# project it writes build files into that
  project's `obj/` folders, as opening it in an editor does (15 files for
  ConsoleTables, 35 for Stateless). Nothing else in the project changes, and
  the crawlers skip `obj/`.
- `npm`-installed servers on Windows (Pyright, TypeScript) may not start,
  because `lsp-client` launches the program directly and those are `.cmd`
  files. Untested.
- Not tried in the packaged app.

## Tests

```powershell
.venv\Scripts\python.exe -m pytest test/other_tests/lsp_symbols_test.py
```

They use fake servers. Two of them use the real `lsp-client` and `csharp-ls`
when both are installed, and are skipped otherwise.
