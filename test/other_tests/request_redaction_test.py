"""!
@file request_redaction_test.py
@brief Tests for agent/request_redaction.py (T-077, US-034).
@details Redaction of the message structures a chat model sends: the
(role, text) pairs the agents build, LangChain messages with content blocks
and tool calls, and prompt values. Every secret is fake; no model is called.
"""

import asyncio
import copy
from concurrent.futures import ThreadPoolExecutor

import pytest
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.prompt_values import ChatPromptValue, ImagePromptValue, StringPromptValue

from agent.request_redaction import redact_messages, redact_model_input, redact_prompts
from utils.output_redaction import secret_scope
from utils.secret_redaction import SecretRedactionError

FAKE = "Fake-Pass-111!"
OTHER = "Fake-Pass-222!"
TOKEN_VALUE = "Fake-Token-123456789"
P1 = "[REDACTED_PASSWORD_1]"
P2 = "[REDACTED_PASSWORD_2]"


def agent_prompt(code):
    """The shape every agent passes to invoke()/ainvoke()."""
    return [
        ("system", "You are a senior software architect."),
        ("user", f"File path:\nsrc/Db.cs\n\nCode:\n{code}\n"),
    ]


# ---------------------------------------------------------------------------
# The (role, text) pairs the agents send
# ---------------------------------------------------------------------------

def test_agent_prompt_keeps_roles_and_shape():
    prompt = agent_prompt(f'string password = "{FAKE}";')

    out, report = redact_model_input(prompt)

    assert out == [
        ("system", "You are a senior software architect."),
        ("user", f'File path:\nsrc/Db.cs\n\nCode:\nstring password = "{P1}";\n'),
    ]
    assert isinstance(out, list) and all(isinstance(pair, tuple) for pair in out)
    assert report.replaced == {"PASSWORD": 1}


def test_secret_found_in_one_message_is_replaced_in_all():
    # The system message mentions the value with nothing to say it is a
    # secret; the user message's code does.
    prompt = [
        ("system", f'Ignore the test login ("ann", "{FAKE}").'),
        ("user", f'DB_PASSWORD={FAKE}\nother_pwd = "{OTHER}"'),
    ]

    out, report = redact_model_input(prompt)

    assert out == [
        ("system", f'Ignore the test login ("ann", "{P1}").'),
        ("user", f'DB_PASSWORD={P1}\nother_pwd = "{P2}"'),
    ]
    assert report.replaced == {"PASSWORD": 3}
    assert report.distinct == {"PASSWORD": 2}


@pytest.mark.parametrize("model_input, expected", [
    (f'password = "{FAKE}"', f'password = "{P1}"'),
    (StringPromptValue(text=f'password = "{FAKE}"'), StringPromptValue(text=f'password = "{P1}"')),
    ([f'password = "{FAKE}"'], [f'password = "{P1}"']),
    ((("user", f'password = "{FAKE}"'),), (("user", f'password = "{P1}"'),)),
    ([["user", f'password = "{FAKE}"']], [["user", f'password = "{P1}"']]),
    (
        [{"role": "user", "content": f'password = "{FAKE}"', "name": "ann"}],
        [{"role": "user", "content": f'password = "{P1}"', "name": "ann"}],
    ),
    (
        ChatPromptValue(messages=[HumanMessage(content=f'password = "{FAKE}"')]),
        ChatPromptValue(messages=[HumanMessage(content=f'password = "{P1}"')]),
    ),
], ids=["str", "string-prompt", "str-list", "tuple-of-pairs", "list-pair", "dict", "chat-prompt"])
def test_every_input_form_comes_back_in_the_same_form(model_input, expected):
    out, report = redact_model_input(model_input)

    assert out == expected
    assert type(out) is type(expected)
    assert report.total == 1


# ---------------------------------------------------------------------------
# LangChain messages: content blocks, tool calls, metadata
# ---------------------------------------------------------------------------

def conversation():
    return [
        SystemMessage(content="Fix the failing test.", id="sys-1"),
        HumanMessage(
            content=[
                {"type": "text", "text": f'string password = "{FAKE}";'},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="}},
                {"type": "text", "text": "Why does it fail?", "cache_control": {"type": "ephemeral"}},
            ],
            name="ann",
            id="human-1",
        ),
        AIMessage(
            content="Connecting to check.",
            tool_calls=[{
                "name": "connect",
                "args": {"host": "db", "name": "shop", "db_password": FAKE,
                         "Authorization": f"Bearer {TOKEN_VALUE}", "retries": 3},
                "id": "call_1",
                "type": "tool_call",
            }],
            additional_kwargs={"tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "connect",
                    "arguments": f'{{"host": "db", "db_password": "{FAKE}"}}',
                },
            }]},
            response_metadata={"model_name": "fake-model", "finish_reason": "tool_calls"},
            usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            id="ai-1",
        ),
        ToolMessage(
            content=f"Login failed for password {FAKE}",
            tool_call_id="call_1",
            name="connect",
            artifact={"raw": "kept as is"},
            id="tool-1",
        ),
    ]


def test_messages_keep_type_role_ids_and_metadata():
    messages = conversation()

    out, _ = redact_messages(messages)

    assert [type(m) for m in out] == [type(m) for m in messages]
    assert [m.id for m in out] == ["sys-1", "human-1", "ai-1", "tool-1"]
    assert out[1].name == "ann"
    assert out[2].response_metadata == {"model_name": "fake-model", "finish_reason": "tool_calls"}
    assert out[2].usage_metadata == messages[2].usage_metadata
    assert out[3].artifact == {"raw": "kept as is"}
    assert out[3].name == "connect"


def test_content_blocks_keep_their_structure():
    out, _ = redact_messages(conversation())

    assert out[1].content == [
        {"type": "text", "text": f'string password = "{P1}";'},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="}},
        {"type": "text", "text": "Why does it fail?", "cache_control": {"type": "ephemeral"}},
    ]


def test_tool_calls_are_redacted_and_still_pair_with_their_results():
    out, report = redact_messages(conversation())
    ai, tool = out[2], out[3]

    # Argument names say which values are secrets; "name" here is an
    # argument, not structure, and is left alone because it is not secret.
    assert ai.tool_calls == [{
        "name": "connect",
        "args": {"host": "db", "name": "shop", "db_password": P1,
                 "Authorization": "Bearer [REDACTED_AUTHORIZATION_1]", "retries": 3},
        "id": "call_1",
        "type": "tool_call",
    }]
    assert ai.additional_kwargs["tool_calls"][0]["function"] == {
        "name": "connect",
        "arguments": f'{{"host": "db", "db_password": "{P1}"}}',
    }
    assert ai.additional_kwargs["tool_calls"][0]["id"] == "call_1"
    assert tool.tool_call_id == ai.tool_calls[0]["id"]
    assert tool.content == f"Login failed for password {P1}"
    assert report.replaced == {"AUTHORIZATION": 1, "PASSWORD": 4}
    assert report.distinct == {"AUTHORIZATION": 1, "PASSWORD": 1}


def test_signed_and_binary_blocks_are_left_untouched():
    # Base64 that happens to contain something secret-shaped stays intact:
    # changing it would corrupt the image or break the signature.
    aws_like = "AK" + "IA" + "FAKEFAKEFAKE1234"
    blocks = [
        {"type": "thinking", "thinking": f'password = "{FAKE}"', "signature": f"sig-{aws_like}"},
        {"type": "redacted_thinking", "data": f"opaque-{aws_like}"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": aws_like}},
        {"type": "image", "base64": aws_like, "mime_type": "image/png"},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{aws_like}"}},
    ]
    message = AIMessage(content=blocks)

    out, _ = redact_messages([message])

    expected = copy.deepcopy(blocks)
    expected[0]["thinking"] = f'password = "{P1}"'
    assert out[0].content == expected


def test_credentials_in_ordinary_urls_are_still_redacted():
    message = HumanMessage(content=[
        {"type": "image_url", "image_url": {"url": f"https://ann:{FAKE}@example.com/chart.png"}},
    ])

    out, _ = redact_messages([message])

    assert out[0].content[0]["image_url"]["url"] == f"https://ann:{P1}@example.com/chart.png"


# ---------------------------------------------------------------------------
# Inputs untouched, output independent, idempotent
# ---------------------------------------------------------------------------

def test_input_messages_are_not_changed():
    messages = conversation()
    before = [m.model_dump() for m in messages]

    out, _ = redact_messages(messages)

    assert [m.model_dump() for m in messages] == before
    assert all(new is not old for new, old in zip(out, messages))

    # Changing the copy must not reach back into the original.
    out[2].tool_calls[0]["args"]["host"] = "changed"
    out[1].content[0]["text"] = "changed"
    out[2].response_metadata["model_name"] = "changed"
    assert [m.model_dump() for m in messages] == before


def test_input_pairs_are_not_changed():
    prompt = agent_prompt(f'string password = "{FAKE}";')
    before = copy.deepcopy(prompt)

    redact_model_input(prompt)

    assert prompt == before


def test_redacting_a_redacted_request_changes_nothing():
    once, _ = redact_messages(conversation())

    again, report = redact_messages(once)

    assert [m.model_dump() for m in again] == [m.model_dump() for m in once]
    assert report.total == 0


def test_request_without_secrets_comes_back_equal():
    prompt = agent_prompt("public int Add(int a, int b) => a + b;")

    out, report = redact_model_input(prompt)

    assert out == prompt
    assert report.total == 0


# ---------------------------------------------------------------------------
# Batches and concurrency: one redactor per request
# ---------------------------------------------------------------------------

def test_each_prompt_in_a_batch_is_its_own_request():
    prompts = [
        [HumanMessage(content=f'password = "{FAKE}"')],
        # Mentions the first prompt's value without context, and has its own.
        [HumanMessage(content=f'note "{FAKE}"\npassword = "{OTHER}"')],
    ]

    out, report = redact_prompts(prompts)

    assert out[0][0].content == f'password = "{P1}"'
    assert out[1][0].content == f'note "{FAKE}"\npassword = "{P1}"'
    assert report.replaced == {"PASSWORD": 2}
    assert report.distinct == {"PASSWORD": 2}


def _request(i):
    """Request i: its own secret, plus request i+1's secret without context."""
    own, neighbour = f"Fake-Pass-{i:03d}!", f"Fake-Pass-{i + 1:03d}!"
    prompt = [
        ("system", f'Login("ann", "{neighbour}") is unrelated.'),
        ("user", f'string password = "{own}";'),
    ]
    return own, neighbour, prompt


def _check(i, out):
    own, neighbour, _ = _request(i)
    assert out == [
        ("system", f'Login("ann", "{neighbour}") is unrelated.'),
        ("user", f'string password = "{P1}";'),
    ]
    assert own not in repr(out)


def test_concurrent_requests_do_not_share_secrets_threads():
    def run(i):
        out, _ = redact_model_input(_request(i)[2])
        return i, out

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(run, range(200)))

    for i, out in results:
        _check(i, out)


def test_concurrent_requests_do_not_share_secrets_asyncio():
    async def run(i):
        await asyncio.sleep(0)
        out, _ = await asyncio.to_thread(redact_model_input, _request(i)[2])
        return i, out

    async def main():
        return await asyncio.gather(*(run(i) for i in range(100)))

    for i, out in asyncio.run(main()):
        _check(i, out)


# ---------------------------------------------------------------------------
# What cannot be checked is refused, never passed through
# ---------------------------------------------------------------------------

class Opaque:
    """Some object a provider SDK might serialise, but we cannot read."""

    def __str__(self):
        return f'password = "{FAKE}"'


class CustomMessage(HumanMessage):
    """A subclass could add a field that reaches the provider unchecked."""


@pytest.mark.parametrize("bad", [
    pytest.param(lambda: [HumanMessage(content=[{"type": "text", "text": Opaque()}])], id="object-in-block"),
    # Built without validation, as code that skips pydantic could.
    pytest.param(lambda: [HumanMessage.model_construct(content=[Opaque()])], id="object-in-content-list"),
    pytest.param(lambda: [HumanMessage(content="x", additional_kwargs={"extra": {FAKE.encode()}})], id="set-of-bytes"),
    pytest.param(lambda: [HumanMessage(content="x", additional_kwargs={1: FAKE})], id="non-text-key"),
    pytest.param(lambda: [CustomMessage(content=f'password = "{FAKE}"')], id="message-subclass"),
    pytest.param(lambda: [("user", Opaque())], id="object-in-pair"),
    pytest.param(lambda: [42], id="number-as-message"),
    pytest.param(lambda: [("user", "a", "b")], id="three-item-tuple"),
    pytest.param(lambda: ImagePromptValue(image_url={"url": f"https://ann:{FAKE}@x/img.png"}), id="image-prompt"),
    pytest.param(lambda: {"role": "user", "content": f'password = "{FAKE}"'}, id="bare-dict-input"),
    pytest.param(lambda: Opaque(), id="object-input"),
])
def test_unreadable_request_is_refused_without_echoing_it(bad):
    with pytest.raises(SecretRedactionError) as error:
        redact_model_input(bad())

    assert FAKE not in str(error.value)
    assert "password" not in str(error.value)


def test_unreadable_message_list_is_refused():
    with pytest.raises(SecretRedactionError):
        redact_messages([HumanMessage(content="ok"), CustomMessage(content="x")])

    with pytest.raises(SecretRedactionError):
        redact_prompts([[HumanMessage(content="ok")], ["not a message"]])


def test_report_is_safe_to_log():
    out, report = redact_messages(conversation())

    for shown in (str(report), repr(report)):
        assert FAKE not in shown
        assert TOKEN_VALUE not in shown

    assert all(isinstance(m, BaseMessage) for m in out)


# ---------------------------------------------------------------------------
# Inside a run, later requests know what earlier ones found (T-079)
# ---------------------------------------------------------------------------
# The agents send a model's earlier answers back: a directory summary to its
# judge and to the parent directory, a broken test to its repair. An answer
# can repeat a secret without the name that identified it.

def test_a_secret_found_earlier_in_the_run_is_replaced_in_a_later_request():
    with secret_scope():
        redact_model_input(agent_prompt(f'string password = "{FAKE}";'))
        out, report = redact_model_input([("user", f"Judge this summary: approval needs {FAKE}.")])

    assert out == [("user", f"Judge this summary: approval needs {P1}.")]
    assert report.replaced == {"PASSWORD": 1}


def test_a_later_request_numbers_known_secrets_after_its_own_placeholders():
    with secret_scope():
        redact_model_input(agent_prompt(f'string password = "{OTHER}";'))
        out, report = redact_model_input([("user", f"Earlier: {P1}. Now: {OTHER}.")])

    assert out == [("user", f"Earlier: {P1}. Now: {P2}.")]
    assert report.replaced == {"PASSWORD": 1}


def test_outside_a_run_each_request_only_has_its_own_context():
    # Unchanged: with no run open (a key check, a terminal tool), nothing
    # is remembered from one request to the next.
    redact_model_input(agent_prompt(f'string password = "{FAKE}";'))
    out, report = redact_model_input([("user", f"approval needs {FAKE}.")])

    assert out == [("user", f"approval needs {FAKE}.")]
    assert report.total == 0


def test_a_new_run_does_not_know_an_earlier_runs_secrets():
    with secret_scope():
        redact_model_input(agent_prompt(f'string password = "{FAKE}";'))

    with secret_scope():
        out, _ = redact_model_input([("user", f"approval needs {FAKE}.")])

    assert out == [("user", f"approval needs {FAKE}.")]


def test_known_secrets_do_not_touch_ordinary_text_in_a_later_request():
    # Only values long and mixed enough to stand on their own are kept.
    with secret_scope():
        redact_model_input(agent_prompt('smtp_pass = "letmein"\nstring password = "Pw-12";'))
        out, report = redact_model_input([("user", "Tried letmein and Pw-12 again.")])

    assert out == [("user", "Tried letmein and Pw-12 again.")]
    assert report.total == 0


def test_concurrent_runs_do_not_share_what_their_requests_learned():
    # Two runs at once (two threads, as two backend commands would be): each
    # one's later request is cleaned of its own secret only.
    import threading

    both_learned = threading.Barrier(2, timeout=10)
    sent, errors = {}, []

    def run(name, mine):
        try:
            with secret_scope():
                redact_model_input(agent_prompt(f'string password = "{mine}";'))
                both_learned.wait()
                sent[name] = redact_model_input([("user", f"Saw {FAKE} and {OTHER}.")])[0][0][1]
        except Exception as exc:  # reported below, without the values
            errors.append(type(exc).__name__)

    threads = [threading.Thread(target=run, args=args) for args in (("a", FAKE), ("b", OTHER))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert sent["a"] == f"Saw {P1} and {OTHER}."
    assert sent["b"] == f"Saw {FAKE} and {P1}."
