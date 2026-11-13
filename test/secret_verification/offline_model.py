"""
@file offline_model.py
@brief The chat model T-079 puts where the provider SDK would be (US-034).
@details install() puts OfflineProvider where langchain_google_genai's
ChatGoogleGenerativeAI would be, so agent/llm.py's build_chat_model() builds
it exactly as it builds Gemini: wrapped with the app's own retries
(_retrying) and secret redaction (redacting). Nothing above the provider SDK
is replaced, and the app's .env selects it like any other key.

OfflineProvider._generate and _agenerate are where a real provider would
make its HTTP call. Every request they receive is appended, as received, to
provider_requests.jsonl: that file holds exactly what would have left the
machine. The answers come from offline_answers.Answers. No network is used;
the API key is a SecretStr, as in the real SDKs, and is never recorded.

What this does not reproduce: Gemini's own structured-output mode (this
model answers through LangChain's default tool calling), its HTTP client,
and its errors' exact types (a ProviderError carries the status code the
app's retry logic reads).
"""

import itertools
import json
import sys
import threading
import types
from pathlib import Path
from typing import Optional

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import SecretStr

from offline_answers import NORMAL, Answers

REQUESTS_FILE = "provider_requests.jsonl"
PHASE_FILE = "phase.txt"

# The module whose chat model is replaced: the app's .env selects "google".
PROVIDER_MODULE = "langchain_google_genai"
PROVIDER_CLASS = "ChatGoogleGenerativeAI"

_state = {"answers": None, "capture": None}
_lock = threading.Lock()
_numbers = itertools.count(1)


def _phase():
    try:
        return (_state["capture"] / PHASE_FILE).read_text(encoding="utf-8").strip() or NORMAL
    except OSError:
        return NORMAL


def _tool_name(kwargs):
    tools = kwargs.get("tools") or []
    return tools[0]["function"]["name"] if tools else None


class OfflineProvider(BaseChatModel):
    """Answers from the scenario and records every request it is sent."""

    model: str = "offline-gemini"
    api_key: Optional[SecretStr] = None
    max_retries: int = 0
    max_output_tokens: Optional[int] = None

    @property
    def _llm_type(self):
        return "t079-offline-provider"

    def _record(self, messages, kwargs, outcome):
        entry = {
            "number": next(_numbers),
            "tool": _tool_name(kwargs),
            "tool_choice": kwargs.get("tool_choice"),
            "tools": kwargs.get("tools"),
            "other_kwargs": sorted(k for k in kwargs if k not in ("tools", "tool_choice")),
            "messages": [m.model_dump(mode="json") for m in messages],
            "outcome": outcome,
        }
        line = json.dumps(entry, ensure_ascii=False)
        with _lock:
            with open(_state["capture"] / REQUESTS_FILE, "a", encoding="utf-8") as file:
                file.write(line + "\n")

    def _respond(self, messages, kwargs):
        tool = _tool_name(kwargs)
        text = "\n".join(m.content if isinstance(m.content, str) else json.dumps(m.content) for m in messages)

        try:
            args = _state["answers"].answer(tool, text, _phase())
        except Exception as exc:
            self._record(messages, kwargs, {"error": type(exc).__name__, "status": getattr(exc, "status_code", None)})
            raise

        self._record(messages, kwargs, {"answered": True})
        usage = {"input_tokens": max(1, len(text) // 4), "output_tokens": 50, "total_tokens": max(1, len(text) // 4) + 50}
        message = AIMessage(
            content="",
            tool_calls=[{"name": tool, "args": args, "id": f"call_{tool}"}],
            usage_metadata=usage,
            response_metadata={"model_name": self.model},
        )
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return self._respond(messages, kwargs)

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        return self._respond(messages, kwargs)

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self.bind(tools=[convert_to_openai_tool(t) for t in tools], tool_choice=tool_choice, **kwargs)


def install(capture, codebase, seed):
    """
    Make the app's "google" provider this offline model, for this process.

    capture: folder for provider_requests.jsonl (and phase.txt, which the
    harness writes to script failures). codebase: the fixture root as the
    pipeline will see it.
    """
    capture = Path(capture)
    capture.mkdir(parents=True, exist_ok=True)
    _state["capture"] = capture
    _state["answers"] = Answers(codebase, seed)

    module = types.ModuleType(PROVIDER_MODULE)
    module.__doc__ = "T-079 offline stand-in for the Google provider package."
    setattr(module, PROVIDER_CLASS, OfflineProvider)
    sys.modules[PROVIDER_MODULE] = module
    return module


def read_requests(capture):
    """The recorded requests, in order."""
    path = Path(capture) / REQUESTS_FILE
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
