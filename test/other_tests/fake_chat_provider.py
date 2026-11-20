"""!
@file fake_chat_provider.py
@brief A chat model that stands in for a provider and records what it was sent.
@details Used by the T-077 redaction tests. FakeProvider._generate is where a
real provider would make the HTTP call, so `sent` holds exactly what would
have left the machine. Wrap the class with agent.llm.redacting(), and
optionally agent.llm._retrying(), to give it the production protection.
No network, no API key.
"""

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import Field


class ProviderBusy(Exception):
    """What a provider SDK raises for 503 Service Unavailable."""

    status_code = 503


class FakeProvider(BaseChatModel):
    """
    Replies "ok", or with a tool call when a schema is bound for structured
    output. Raises ProviderBusy for the first `failures` calls.
    """

    sent: list = Field(default_factory=list)
    failures: int = 0
    answers: dict = Field(default_factory=dict)

    @property
    def _llm_type(self):
        return "fake-provider"

    def _record(self, messages, kwargs):
        # Copies, so a later change to the objects cannot rewrite history.
        self.sent.append({
            "messages": [m.model_copy(deep=True) for m in messages],
            "kwargs": dict(kwargs),
        })

        if self.failures:
            self.failures -= 1
            raise ProviderBusy("503 Service Unavailable")

    def _reply(self, kwargs):
        tools = kwargs.get("tools") or []
        usage = {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110}

        if not tools:
            return AIMessage(content="ok", usage_metadata=usage,
                             response_metadata={"model_name": "fake-model"})

        name = tools[0]["function"]["name"]
        return AIMessage(
            content="",
            tool_calls=[{"name": name, "args": self.answers.get(name, {}), "id": "call_1"}],
            usage_metadata=usage,
            response_metadata={"model_name": "fake-model"},
        )

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self._record(messages, kwargs)
        return ChatResult(generations=[ChatGeneration(message=self._reply(kwargs))])

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        self._record(messages, kwargs)
        for piece in ("o", "k"):
            yield ChatGenerationChunk(message=AIMessageChunk(content=piece))

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self.bind(tools=[convert_to_openai_tool(t) for t in tools], **kwargs)

    def sent_text(self):
        """Everything sent so far, as one string, for "is the secret in it" checks."""
        return repr([(call["messages"], call["kwargs"]) for call in self.sent])


class RecordingCallbacks(BaseCallbackHandler):
    """Records every payload LangChain hands to callbacks and tracers."""

    def __init__(self):
        super().__init__()
        self.events = []

    def on_chain_start(self, serialized, inputs, **kwargs):
        self.events.append(("chain_start", inputs, kwargs.get("metadata")))

    def on_chat_model_start(self, serialized, messages, **kwargs):
        self.events.append(("chat_model_start", messages, kwargs.get("metadata")))

    def on_llm_start(self, serialized, prompts, **kwargs):
        self.events.append(("llm_start", prompts, kwargs.get("metadata")))

    def on_llm_end(self, response, **kwargs):
        self.events.append(("llm_end", response, None))

    def on_llm_error(self, error, **kwargs):
        self.events.append(("llm_error", error, None))

    def kinds(self):
        return [kind for kind, _, _ in self.events]

    def seen_text(self):
        return repr(self.events)
