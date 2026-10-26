"""!
@file llm_redaction_test.py
@brief Tests for the redaction boundary in agent/llm.py (T-077, US-034).
@details Every way of calling a chat model must hand the provider, its
callbacks and the retries only the redacted request. FakeProvider records
what reached the point where a real provider makes its HTTP call; a real
ChatOpenAI is checked at the HTTP layer through an httpx mock transport.
All secrets are fake and no request leaves the machine.
"""

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from langchain_core.messages import HumanMessage, convert_to_messages
from langchain_core.runnables import RunnableLambda
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fake_chat_provider import FakeProvider, RecordingCallbacks  # noqa: E402

import agent.llm as llm_module  # noqa: E402
from agent.llm import (  # noqa: E402
    REDACTION_METADATA_KEY,
    LLMConfig,
    _RedactedInput,
    _retrying,
    build_chat_model,
    redacting,
)
from agent.request_redaction import redact_messages  # noqa: E402
from backend.token_usage import record_usage  # noqa: E402
from utils.secret_redaction import SecretRedactionError  # noqa: E402

FAKE = "Fake-Pass-111!"
P1 = "[REDACTED_PASSWORD_1]"
PROMPT = [
    ("system", "You review C# code."),
    ("user", f'string password = "{FAKE}";\nLogin("ann", "{FAKE}");'),
]


class Answer(BaseModel):
    summary: str


def protected(**fields):
    return redacting(FakeProvider)(**fields)


def assert_clean(model, recorder=None):
    """The provider got the request, redacted; callbacks saw no secret either."""
    assert model.sent, "the provider was never called"
    assert FAKE not in model.sent_text()
    assert P1 in model.sent_text()

    if recorder is not None:
        assert recorder.events, "no callback fired"
        assert FAKE not in recorder.seen_text()


# ---------------------------------------------------------------------------
# Why the boundary is generate()/stream(), not _generate()
# ---------------------------------------------------------------------------

class FiltersTooLate(FakeProvider):
    """Redacts where the retries live, inside _generate."""

    def _generate(self, messages, *args, **kwargs):
        safe, _ = redact_messages(messages)
        return super()._generate(safe, *args, **kwargs)


def test_filtering_inside_generate_would_leak_to_callbacks():
    recorder = RecordingCallbacks()
    late = FiltersTooLate()

    late.invoke(PROMPT, config={"callbacks": [recorder]})

    # The provider is safe, but on_chat_model_start already had the secret.
    assert FAKE not in late.sent_text()
    assert FAKE in recorder.seen_text()


def test_plain_structured_output_chain_leaks_its_input_to_callbacks():
    # Even with model-level redaction, the chain with_structured_output()
    # builds reports its raw input first -- why _RedactedInput exists.
    recorder = RecordingCallbacks()
    model = protected(answers={"Answer": {"summary": "fine"}})
    chain = FakeProvider.with_structured_output(model, Answer)

    chain.invoke(PROMPT, config={"callbacks": [recorder]})

    assert FAKE not in model.sent_text()
    leaked = [kind for kind, payload, _ in recorder.events if FAKE in repr(payload)]
    assert leaked == ["chain_start"]


def test_known_exposure_graph_state_reaches_chain_callbacks():
    """
    Documents what the model boundary cannot cover (see agent/llm.py):
    LangGraph reports a node's input state to chain callbacks before the node
    calls the model. The model call itself stays clean, and the app's own
    token-usage handler ignores chain events. If this starts failing, the
    exposure has been closed and the note in agent/llm.py should go.
    """
    from typing import TypedDict

    from langchain_core.callbacks import BaseCallbackHandler
    from langgraph.graph import END, START, StateGraph

    from backend.token_usage import _Fanout

    class State(TypedDict):
        code: str
        answer: str

    model = protected()

    def summarize(state):
        return {"answer": model.invoke([("user", state["code"])]).content}

    builder = StateGraph(State)
    builder.add_node("summarize", summarize)
    builder.add_edge(START, "summarize")
    builder.add_edge("summarize", END)
    graph = builder.compile()

    recorder = RecordingCallbacks()
    graph.invoke({"code": f'password = "{FAKE}"', "answer": ""}, config={"callbacks": [recorder]})

    model_starts = [p for kind, p, _ in recorder.events if kind == "chat_model_start"]
    chain_starts = [p for kind, p, _ in recorder.events if kind == "chain_start"]
    assert model_starts and FAKE not in repr(model_starts)
    assert FAKE not in model.sent_text()
    assert FAKE in repr(chain_starts)
    assert _Fanout.on_chain_start is BaseCallbackHandler.on_chain_start


# ---------------------------------------------------------------------------
# Every entry point
# ---------------------------------------------------------------------------

SYNC_CALLS = {
    "invoke": lambda m, cfg: m.invoke(PROMPT, config=cfg),
    "batch": lambda m, cfg: m.batch([PROMPT, PROMPT], config=cfg),
    "stream": lambda m, cfg: list(m.stream(PROMPT, config=cfg)),
    "generate": lambda m, cfg: m.generate([convert_to_messages(PROMPT)], callbacks=cfg["callbacks"]),
    "structured-invoke": lambda m, cfg: m.with_structured_output(Answer).invoke(PROMPT, config=cfg),
    "structured-batch": lambda m, cfg: m.with_structured_output(Answer).batch([PROMPT], config=cfg),
    "bind-tools": lambda m, cfg: m.bind_tools([Answer]).invoke(PROMPT, config=cfg),
}


async def _astream(m, cfg):
    return [chunk async for chunk in m.astream(PROMPT, config=cfg)]


ASYNC_CALLS = {
    "ainvoke": lambda m, cfg: m.ainvoke(PROMPT, config=cfg),
    "abatch": lambda m, cfg: m.abatch([PROMPT, PROMPT], config=cfg),
    "astream": _astream,
    "agenerate": lambda m, cfg: m.agenerate([convert_to_messages(PROMPT)], callbacks=cfg["callbacks"]),
    "structured-ainvoke": lambda m, cfg: m.with_structured_output(Answer).ainvoke(PROMPT, config=cfg),
    "structured-abatch": lambda m, cfg: m.with_structured_output(Answer).abatch([PROMPT], config=cfg),
    "bind-tools-async": lambda m, cfg: m.bind_tools([Answer]).ainvoke(PROMPT, config=cfg),
}


@pytest.mark.parametrize("call", SYNC_CALLS.values(), ids=SYNC_CALLS.keys())
def test_sync_entry_points_send_only_redacted_requests(call):
    recorder = RecordingCallbacks()
    model = protected(answers={"Answer": {"summary": "fine"}})

    call(model, {"callbacks": [recorder]})

    assert_clean(model, recorder)


@pytest.mark.parametrize("call", ASYNC_CALLS.values(), ids=ASYNC_CALLS.keys())
def test_async_entry_points_send_only_redacted_requests(call):
    recorder = RecordingCallbacks()
    model = protected(answers={"Answer": {"summary": "fine"}})

    asyncio.run(call(model, {"callbacks": [recorder]}))

    assert_clean(model, recorder)


def test_redacted_input_wrapper_streams_redacted_input():
    received = []
    wrapper = _RedactedInput(RunnableLambda(lambda x: received.append(x) or "done"))

    assert list(wrapper.stream(PROMPT)) == ["done"]
    assert FAKE not in repr(received) and P1 in repr(received)


# ---------------------------------------------------------------------------
# Behaviour the agents depend on is unchanged
# ---------------------------------------------------------------------------

def test_structured_output_still_parses():
    model = protected(answers={"Answer": {"summary": "fine"}})

    assert model.with_structured_output(Answer).invoke(PROMPT) == Answer(summary="fine")
    assert asyncio.run(model.with_structured_output(Answer).ainvoke(PROMPT)) == Answer(summary="fine")

    raw = model.with_structured_output(Answer, include_raw=True).invoke(PROMPT)
    assert raw["parsed"] == Answer(summary="fine")

    # The schema still went to the provider as a bound tool.
    assert model.sent[0]["kwargs"]["tools"][0]["function"]["name"] == "Answer"


def test_roles_reach_the_provider_unchanged():
    model = protected()

    model.invoke(PROMPT)

    sent = model.sent[0]["messages"]
    assert [m.type for m in sent] == ["system", "human"]
    assert sent[0].content == "You review C# code."
    assert sent[1].content == f'string password = "{P1}";\nLogin("ann", "{P1}");'


def test_callbacks_get_counts_and_no_values():
    recorder = RecordingCallbacks()
    model = protected(answers={"Answer": {"summary": "fine"}})

    model.with_structured_output(Answer).invoke(PROMPT, config={"callbacks": [recorder]})

    starts = [(kind, meta) for kind, _, meta in recorder.events if kind.endswith("start")]
    assert {kind for kind, _ in starts} == {"chain_start", "chat_model_start"}

    model_start = next(meta for kind, meta in starts if kind == "chat_model_start")
    assert model_start[REDACTION_METADATA_KEY] == {"PASSWORD": 2}
    assert FAKE not in recorder.seen_text()


def test_token_usage_is_still_recorded():
    model = protected(answers={"Answer": {"summary": "fine"}})

    with record_usage("generate_file_summaries") as usage:
        model.invoke(PROMPT)
        model.with_structured_output(Answer).invoke(PROMPT)
        asyncio.run(model.with_structured_output(Answer).ainvoke(PROMPT))

    summary = usage.summary()
    assert summary["calls"] == 3
    assert summary["input_tokens"] == 300
    assert summary["output_tokens"] == 30
    assert summary["models"] == ["fake-model"]


@pytest.fixture
def no_waiting(monkeypatch):
    """Retries without the real back-off."""

    async def skip(_seconds):
        return None

    monkeypatch.setattr(llm_module, "time", SimpleNamespace(sleep=lambda _seconds: None))
    monkeypatch.setattr(llm_module, "asyncio", SimpleNamespace(sleep=skip))


def test_retries_resend_only_the_redacted_request(no_waiting):
    recorder = RecordingCallbacks()
    model = redacting(_retrying(FakeProvider, "Fake"))(failures=2)

    assert model.invoke(PROMPT, config={"callbacks": [recorder]}).content == "ok"

    assert len(model.sent) == 3
    assert_clean(model, recorder)


def test_async_retries_resend_only_the_redacted_request(no_waiting):
    model = redacting(_retrying(FakeProvider, "Fake"))(
        failures=1, answers={"Answer": {"summary": "fine"}}
    )

    answer = asyncio.run(model.with_structured_output(Answer).ainvoke(PROMPT))

    assert answer == Answer(summary="fine")
    assert len(model.sent) == 2
    assert_clean(model)


def test_concurrent_calls_on_one_model_stay_separate():
    model = protected()

    def prompt(i):
        own, neighbour = f"Fake-Pass-{i:03d}!", f"Fake-Pass-{i + 1:03d}!"
        return [("system", f'Login("ann", "{neighbour}")'), ("user", f'password = "{own}"')]

    async def main():
        await asyncio.gather(*(model.ainvoke(prompt(i)) for i in range(50)))

    asyncio.run(main())

    assert len(model.sent) == 50
    for call in model.sent:
        system, user = call["messages"]
        # Each request numbered from 1, and the neighbour's value -- a secret
        # only in another request -- left alone.
        assert user.content == f'password = "{P1}"'
        assert "[REDACTED_" not in system.content


# ---------------------------------------------------------------------------
# Fail closed
# ---------------------------------------------------------------------------

def unreadable():
    return [HumanMessage.model_construct(content=[object()])]


async def _astream_unreadable(m, cfg):
    return [chunk async for chunk in m.astream(unreadable(), config=cfg)]


FAILING_CALLS = {
    "invoke": lambda m, cfg: m.invoke(unreadable(), config=cfg),
    "stream": lambda m, cfg: list(m.stream(unreadable(), config=cfg)),
    "generate": lambda m, cfg: m.generate([unreadable()], callbacks=cfg["callbacks"]),
    "structured": lambda m, cfg: m.with_structured_output(Answer).invoke(unreadable(), config=cfg),
    "ainvoke": lambda m, cfg: asyncio.run(m.ainvoke(unreadable(), config=cfg)),
    "astream": lambda m, cfg: asyncio.run(_astream_unreadable(m, cfg)),
    "structured-async": lambda m, cfg: asyncio.run(
        m.with_structured_output(Answer).ainvoke(unreadable(), config=cfg)
    ),
}


@pytest.mark.parametrize("call", FAILING_CALLS.values(), ids=FAILING_CALLS.keys())
def test_unreadable_request_is_stopped_before_callbacks_or_provider(call):
    recorder = RecordingCallbacks()
    model = protected()

    with pytest.raises(SecretRedactionError):
        call(model, {"callbacks": [recorder]})

    assert model.sent == []
    assert recorder.events == []


# ---------------------------------------------------------------------------
# Real provider classes, built the way the agents get them
# ---------------------------------------------------------------------------

PROVIDER_KEY = "Fake-Provider-Key-123456789"


@pytest.mark.parametrize("provider, package, key_field", [
    ("openai", "langchain_openai", "openai_api_key"),
    ("openrouter", "langchain_openai", "openai_api_key"),
    ("google", "langchain_google_genai", "google_api_key"),
])
@pytest.mark.parametrize("retry", [True, False], ids=["agents", "key-check"])
def test_every_built_model_redacts_and_keeps_its_key(provider, package, key_field, retry):
    pytest.importorskip(package)

    model = build_chat_model(LLMConfig(provider, "fake-model", PROVIDER_KEY), retry=retry)

    assert type(model).__name__.startswith("Redacting")
    assert type(model).generate is not type(model).__mro__[1].generate
    assert getattr(model, key_field).get_secret_value() == PROVIDER_KEY


def test_openai_http_request_is_redacted_and_still_authenticated(monkeypatch):
    langchain_openai = pytest.importorskip("langchain_openai")
    requests = []

    def provider(request):
        requests.append(request)
        body = json.loads(request.content)
        content = '{"summary": "fine"}' if body.get("response_format") else "ok"
        return httpx.Response(200, json={
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 0,
            "model": "fake-model",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 20, "completion_tokens": 2, "total_tokens": 22},
        })

    transport = httpx.MockTransport(provider)

    class Intercepted(langchain_openai.ChatOpenAI):
        def __init__(self, **kwargs):
            super().__init__(
                http_client=httpx.Client(transport=transport),
                http_async_client=httpx.AsyncClient(transport=transport),
                **kwargs,
            )

    # build_chat_model imports ChatOpenAI when it is called.
    monkeypatch.setattr(langchain_openai, "ChatOpenAI", Intercepted)
    model = build_chat_model(LLMConfig("openai", "fake-model", PROVIDER_KEY))

    # The analysed code even contains the provider key itself.
    prompt = PROMPT + [("user", f'OPENAI_API_KEY = "{PROVIDER_KEY}"')]

    with record_usage("test") as usage:
        assert model.invoke(prompt).content == "ok"
        assert model.with_structured_output(Answer).invoke(prompt) == Answer(summary="fine")
        assert asyncio.run(model.ainvoke(prompt)).content == "ok"

    assert len(requests) == 3
    for request in requests:
        body = request.content.decode()
        assert FAKE not in body
        assert P1 in body
        assert "[REDACTED_API_KEY_1]" in body
        assert PROVIDER_KEY not in body
        # The key still authenticates the call; only the content changed.
        assert request.headers["authorization"] == f"Bearer {PROVIDER_KEY}"
        roles = [m["role"] for m in json.loads(body)["messages"]]
        assert roles == ["system", "user", "user"]

    assert usage.summary()["calls"] == 3
    assert usage.summary()["input_tokens"] == 60
