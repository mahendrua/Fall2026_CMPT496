"""
@file offline_backend.py
@brief Starts Checkpoint's real backend offline for T-079, as Electron does (US-034).
@details

    python -u offline_backend.py --app APP --capture CAPTURE --codebase ROOT [--seed N]

runs APP/main.py as __main__ in this process, so main.py itself installs
the log and stream protection and serves request lines on stdin, exactly as
under Electron. Before it starts, three things are put in place, and
nothing else is changed:

    provider    offline_model.install(): the Google provider package is the
                offline model. agent/llm.py still builds it, with its own
                retries and redaction around it.
    callbacks   a LangChain callback handler, registered the way the app's
                token-usage handler is, records what every model call and
                every chain hands to callbacks (model_callbacks.jsonl,
                chain_callbacks.jsonl). It writes to its own files, never to
                stdout or stderr.
    network     connections and name lookups to anything but this machine
                are refused and recorded (network.jsonl). Loopback stays
                open: asyncio needs it on Windows. Child processes (dotnet,
                java) are not covered; the harness configures them offline.

The app's own modules are imported from APP, never from the repository.
"""

import argparse
import json
import runpy
import socket
import sys
import threading
from contextvars import ContextVar
from pathlib import Path

HERE = Path(__file__).resolve().parent

NETWORK_FILE = "network.jsonl"
MODEL_CALLBACKS_FILE = "model_callbacks.jsonl"
CHAIN_CALLBACKS_FILE = "chain_callbacks.jsonl"
INBOUND_FILE = "model_responses.jsonl"
STARTED_FILE = "backend_started.jsonl"

# The app's own top-level packages: these must come from the app copy.
APP_PACKAGES = ("agent", "backend", "src", "utils", "main")

_write_lock = threading.Lock()


def _append(capture, name, entry):
    line = json.dumps(entry, ensure_ascii=False, default=_default)
    with _write_lock:
        with open(capture / name, "a", encoding="utf-8") as file:
            file.write(line + "\n")


def _default(value):
    """JSON for what callbacks are handed: models dumped, collections listed, the rest as repr()."""
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump(mode="json")
        except Exception:
            pass
    if isinstance(value, (set, frozenset, tuple)) or type(value).__name__ == "deque":
        return list(value)
    return repr(value)


# ---------------------------------------------------------------------------
# Network guard
# ---------------------------------------------------------------------------

def _is_local(host):
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    host = str(host or "").strip("[]").lower()
    return host in ("localhost", "::1", "") or host.startswith("127.")


def guard_network(capture):
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    def refuse(kind, host, port):
        _append(capture, NETWORK_FILE, {"kind": kind, "host": str(host), "port": port})
        raise OSError(f"T-079 harness: network access to {host} is blocked")

    def connect(self, address):
        if isinstance(address, tuple) and not _is_local(address[0]):
            refuse("connect", address[0], address[1] if len(address) > 1 else None)
        return real_connect(self, address)

    def connect_ex(self, address):
        if isinstance(address, tuple) and not _is_local(address[0]):
            refuse("connect", address[0], address[1] if len(address) > 1 else None)
        return real_connect_ex(self, address)

    def getaddrinfo(host, port, *args, **kwargs):
        if host is not None and not _is_local(host):
            _append(capture, NETWORK_FILE, {"kind": "lookup", "host": str(host), "port": port})
            raise socket.gaierror(socket.EAI_NONAME, "T-079 harness: name lookups are blocked")
        return real_getaddrinfo(host, port, *args, **kwargs)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.getaddrinfo = getaddrinfo


# ---------------------------------------------------------------------------
# Callback recorder
# ---------------------------------------------------------------------------

def install_callback_recorder(capture):
    from langchain_core.callbacks import BaseCallbackHandler
    from langchain_core.tracers.context import register_configure_hook

    class Recorder(BaseCallbackHandler):
        """Records what LangChain hands to any callback or tracer."""

        def on_chat_model_start(self, serialized, messages, *, run_id, metadata=None, **kwargs):
            _append(capture, MODEL_CALLBACKS_FILE, {
                "event": "chat_model_start",
                "run_id": str(run_id),
                "metadata": metadata,
                "serialized": serialized,
                "invocation_params": kwargs.get("invocation_params"),
                "messages": [[m.model_dump(mode="json") for m in batch] for batch in messages],
            })

        def on_llm_start(self, serialized, prompts, *, run_id, metadata=None, **kwargs):
            _append(capture, MODEL_CALLBACKS_FILE, {
                "event": "llm_start", "run_id": str(run_id), "metadata": metadata, "prompts": prompts,
            })

        def on_llm_end(self, response, *, run_id, **kwargs):
            _append(capture, MODEL_CALLBACKS_FILE, {"event": "llm_end", "run_id": str(run_id)})
            # The answer itself is inbound: kept apart, for reference only.
            _append(capture, INBOUND_FILE, {"run_id": str(run_id), "response": response})

        def on_llm_error(self, error, *, run_id, **kwargs):
            _append(capture, MODEL_CALLBACKS_FILE, {
                "event": "llm_error", "run_id": str(run_id), "type": type(error).__name__,
                "status": getattr(error, "status_code", None),
            })
            _append(capture, INBOUND_FILE, {"run_id": str(run_id), "error": str(error)})

        def on_chain_start(self, serialized, inputs, *, run_id, **kwargs):
            _append(capture, CHAIN_CALLBACKS_FILE, {
                "run_id": str(run_id), "name": kwargs.get("name"), "inputs": inputs,
            })

    recorder = Recorder()
    # The default, not a set(): threads started without a copied context see it too.
    var = ContextVar("t079_callback_recorder", default=recorder)
    register_configure_hook(var, inheritable=True)
    return recorder


# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--app", required=True)
    parser.add_argument("--capture", required=True)
    parser.add_argument("--codebase", required=True)
    parser.add_argument("--seed", type=int, default=79)
    args = parser.parse_args(argv)

    app = Path(args.app).resolve()
    capture = Path(args.capture).resolve()
    capture.mkdir(parents=True, exist_ok=True)

    # The app first, this folder last: app modules come from APP only.
    sys.path[:] = [str(app)] + [p for p in sys.path if p and Path(p).resolve() != HERE] + [str(HERE)]

    guard_network(capture)

    import offline_model
    offline_model.install(capture, args.codebase, args.seed)
    install_callback_recorder(capture)

    import os
    os.chdir(app)

    main_py = app / "main.py"
    _append(capture, STARTED_FILE, {"main": str(main_py), "python": sys.executable})
    sys.argv = [str(main_py), str(app)]
    try:
        runpy.run_path(str(main_py), run_name="__main__")
    finally:
        _append(capture, STARTED_FILE, {"modules": _app_modules(app)})


def _app_modules(app):
    """How many app modules came from the copy, and any that did not."""
    from_app, outside = 0, []
    for name, module in list(sys.modules.items()):
        if name.split(".")[0] not in APP_PACKAGES:
            continue
        path = getattr(module, "__file__", None)
        if not path:
            continue
        if app in Path(path).resolve().parents:
            from_app += 1
        else:
            outside.append(name)
    return {"from_app": from_app, "outside_app": sorted(outside)}


if __name__ == "__main__":
    main()
