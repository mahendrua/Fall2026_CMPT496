"""
agent/request_redaction.py

Applies utils/secret_redaction.py to what a chat model is about to send
(T-077, US-034).

The agents hand their models a list of (role, text) pairs. By the time it
reaches the provider that has become LangChain messages, and a message can
carry text in several places: its content (a string, or a list of content
blocks), its additional_kwargs, and on an AI message the arguments of its
tool calls. Every string in those places is checked; everything else is
copied as it is:

    roles and message types        system stays system, user stays user
    ids and names                  message ids, tool-call ids, tool names
    tool_call_id                   so a tool result still pairs with its call
    content-block structure        type, mime_type, index, cache_control ...
    signed or encrypted payloads   signature, encrypted_content, redacted_thinking
    binary payloads                base64 image/audio/file data and data: URLs
    metadata                       response_metadata, usage_metadata, artifact

Binary payloads are not scanned: they are not text the model reads as text,
and changing a byte would corrupt them.

One request gets one SecretRedactor. Every part of the request is scanned
before any is rewritten, so a secret found in the user message is also
replaced where it appears in the system message, under the same
placeholder. A batch of prompts is several requests: each gets its own.

The input is never changed; a redacted copy is returned. Anything this
module does not know how to inspect (an unknown message class, bytes, a
custom object inside a content block) raises SecretRedactionError instead
of being passed through unchecked.

Inside utils.output_redaction.secret_scope(), what each request's redactor
found is also kept for the rest of the run, in memory, so the answers and
errors that come back can be cleaned of it too (T-078).
"""

import re
from collections.abc import Mapping

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    ChatMessage,
    ChatMessageChunk,
    FunctionMessage,
    FunctionMessageChunk,
    HumanMessage,
    HumanMessageChunk,
    SystemMessage,
    SystemMessageChunk,
    ToolMessage,
    ToolMessageChunk,
)
from langchain_core.prompt_values import ChatPromptValue, StringPromptValue

from utils.output_redaction import remember
from utils.secret_redaction import RedactionReport, SecretRedactionError, SecretRedactor


# Message classes whose every text-bearing field is known. A subclass could
# add a field that reaches the provider unchecked, so it is refused.
_SUPPORTED_MESSAGES = frozenset({
    HumanMessage, HumanMessageChunk,
    SystemMessage, SystemMessageChunk,
    AIMessage, AIMessageChunk,
    ToolMessage, ToolMessageChunk,
    ChatMessage, ChatMessageChunk,
    FunctionMessage, FunctionMessageChunk,
})

# Message fields besides content and additional_kwargs that carry text.
_TOOL_CALL_FIELDS = ("tool_calls", "invalid_tool_calls", "tool_call_chunks")

# Keys whose values identify or describe a block rather than carry text the
# model reads. They stay byte-for-byte so tool calls still pair with their
# results and signed reasoning blocks still verify.
_STRUCTURAL_KEYS = frozenset({
    "type", "role", "id", "tool_call_id", "call_id", "index", "name",
    "mime_type", "media_type", "source_type", "file_id", "status",
    "cache_control", "signature", "encrypted_content",
})

# Keys holding tool arguments. Inside them every key is an argument name, so
# nothing there is structural: {"name": "...", "password": "..."} is data.
_ARGUMENT_KEYS = frozenset({"args", "arguments", "input", "partial_json"})

# Blocks whose "data" is binary or opaque: Anthropic's base64 image source,
# its redacted (encrypted) thinking, and LangChain's base64 content blocks.
_BINARY_BLOCK_TYPES = frozenset({"base64", "redacted_thinking"})

_DATA_URL_RE = re.compile(r"^data:[\w.+\-]+/[\w.+\-]+(?:;[\w.+\-]+=[\w.+\-]+)*;base64,")


def _unsupported(value):
    return SecretRedactionError(
        f"Cannot check a {type(value).__name__} in an AI request for secrets."
    )


def _binary_keys(block):
    """Keys of this block whose values are binary payloads, not text."""
    keys = {"base64"}

    if block.get("type") in _BINARY_BLOCK_TYPES or block.get("source_type") == "base64":
        keys.add("data")

    return keys


def _walk(value, visit, key=None, in_arguments=False):
    """
    A copy of value with visit(key, text) applied to every string that
    carries text, where key is the name of the field holding it.
    """
    if isinstance(value, str):
        if not in_arguments and key in _STRUCTURAL_KEYS:
            return value
        if _DATA_URL_RE.match(value):
            return value
        return visit(key, value)

    if value is None or isinstance(value, (bool, int, float)):
        return value

    if isinstance(value, Mapping):
        binary = set() if in_arguments else _binary_keys(value)
        copy = {}

        for k, v in value.items():
            if not isinstance(k, str):
                raise _unsupported(k)

            if k in binary and isinstance(v, str):
                copy[k] = v
            else:
                copy[k] = _walk(v, visit, k, in_arguments or k in _ARGUMENT_KEYS)

        return copy

    if isinstance(value, (list, tuple)):
        items = [_walk(item, visit, key, in_arguments) for item in value]
        return tuple(items) if isinstance(value, tuple) else items

    raise _unsupported(value)


def _walk_message(message, visit):
    """A copy of message with its text-bearing fields walked."""
    if type(message) not in _SUPPORTED_MESSAGES:
        raise _unsupported(message)

    update = {
        "content": _walk(message.content, visit, "content"),
        "additional_kwargs": _walk(message.additional_kwargs, visit, "additional_kwargs"),
    }

    for field in _TOOL_CALL_FIELDS:
        if field in type(message).model_fields:
            update[field] = _walk(getattr(message, field), visit, field)

    # deep=True so the copy shares no dict or list with the original.
    return message.model_copy(update=update, deep=True)


def _walk_message_like(item, visit):
    """
    One entry of a model input, kept in the form it came in: a message,
    a (role, content) pair, a {"role": ..., "content": ...} dict, or a
    string (shorthand for a user message).
    """
    if isinstance(item, BaseMessage):
        return _walk_message(item, visit)

    if isinstance(item, str):
        return visit("content", item)

    if isinstance(item, (tuple, list)) and len(item) == 2 and isinstance(item[0], str):
        role, content = item
        pair = (role, _walk(content, visit, "content"))
        return pair if isinstance(item, tuple) else list(pair)

    if isinstance(item, Mapping):
        return _walk(item, visit)

    raise _unsupported(item)


def _walk_input(model_input, visit):
    """A copy of a chat model input (string, prompt value or message list)."""
    if isinstance(model_input, str):
        return visit("content", model_input)

    if type(model_input) is StringPromptValue:
        return StringPromptValue(text=visit("content", model_input.text))

    # ChatPromptValueConcrete is a ChatPromptValue with a narrower type.
    if isinstance(model_input, ChatPromptValue):
        return type(model_input)(
            messages=[_walk_message(m, visit) for m in model_input.messages]
        )

    if isinstance(model_input, (list, tuple)):
        items = [_walk_message_like(item, visit) for item in model_input]
        return tuple(items) if isinstance(model_input, tuple) else items

    # ImagePromptValue and anything else this does not know how to read.
    raise _unsupported(model_input)


def _redact_request(walk):
    """
    Run walk(visit) twice with one redactor: first only to learn every secret
    in the request, then to build the redacted copy.
    """
    redactor = SecretRedactor()

    def learn(key, text):
        redactor.scan_field(key, text)
        return text

    walk(learn)
    redacted = walk(redactor.redact_field)
    remember(redactor)
    return redacted, redactor.report()


# ---------------------------------------------------------
# Public API
# ---------------------------------------------------------

def redact_messages(messages):
    """
    One request's messages, redacted.

    Returns (list of new messages, RedactionReport).
    """
    return _redact_request(
        lambda visit: [_walk_message(message, visit) for message in messages]
    )


def redact_prompts(prompts):
    """
    A batch of message lists, as BaseChatModel.generate() receives it. Each
    list is its own request, with its own placeholders.

    Returns (list of lists of new messages, combined RedactionReport).
    """
    redacted, total = [], RedactionReport({}, {})

    for messages in prompts:
        copy, report = redact_messages(messages)
        redacted.append(copy)
        total += report

    return redacted, total


def redact_model_input(model_input):
    """
    Anything a chat model's invoke() or stream() accepts, redacted and in the
    same form: a string stays a string, (role, text) pairs stay pairs.

    Returns (redacted input, RedactionReport).
    """
    return _redact_request(lambda visit: _walk_input(model_input, visit))
