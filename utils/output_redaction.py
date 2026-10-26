"""
utils/output_redaction.py

Keeps secrets out of what Checkpoint records and shows: logs, progress
messages, run records, and the summaries, rules, tests and reviews it saves
(T-078, US-034). T-077 cleans what is sent to an AI provider; this cleans
what comes back, what is copied from the analysed code into outputs, and
what the app writes about either.

It reuses utils/secret_redaction.py, so the detector, the placeholders and
the counts are the same as in requests:

    sanitize_text(text)        one piece of text
    sanitize_data(data)        JSON-shaped data (dicts, lists, strings,
                               numbers, booleans, None). Every string is
                               cleaned, with the name of the field holding it
                               as context: {"db_password": "..."} is a secret
                               even though the value alone says nothing.
                               Keys, numbers and reference fields are copied
                               unchanged (see REFERENCE_KEYS).
    safe_text(text)            sanitize_text() that never raises, for logs
    save_json(path, data)      clean, serialize, then write and swap in
    save_text(path, text)

Each call has its own SecretRedactor, so placeholder numbers are local to
one piece of output; they do not say which request placeholder, or which
other output's placeholder, stood for the same secret.

Context-free echoes
-------------------
A secret is found by its context (password = "..."). An answer, an error
message or an excerpt cut out of a file can repeat it without that context.
Inside secret_scope(), every request agent/request_redaction.py cleans and
every output cleaned here adds what it found to the scope, and every output
cleaned later in the scope is also cleaned of those values wherever they
appear. Only values of 8 or more characters mixing letters with digits or
symbols are kept that way, the rule T-077 uses within one request, so
ordinary words are never blanked out. The values stay in memory on the
scope's own object and go when it closes. Each run opens its own scope, and
scopes never share secrets.

When something cannot be checked
--------------------------------
An object this module does not know, or an error inside the detector, never
lets unchecked text through: sanitize_text() and sanitize_data() raise
OutputRedactionError, whose message never contains the content; safe_text()
returns a fixed notice instead; the save functions write nothing and leave
the previous file as it was.
"""

import json
import os
import uuid
from collections.abc import Mapping
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from pathlib import Path

from utils.secret_redaction import KnownSecrets, SecretRedactor


# Stands in for text that could not be checked.
WITHHELD = "[withheld: this text could not be checked for secrets]"

# Fields that identify or locate something rather than describe it. Their
# values are copied unchanged so the links between outputs keep working: a
# rule still names its source files, a finding its artifacts, a response its
# request. None of them is a secret word, so no value here would be read as
# one because of its field name.
REFERENCE_KEYS = frozenset({
    # identifiers
    "id", "check_id", "finding_id", "finding_ids", "request_id", "rule_id",
    "rule_ids", "run_id", "test_id", "content_hash", "discriminator",
    # where things are
    "codebase", "path", "directory", "directory_name", "directory_path",
    "source_file", "source_file_paths", "source_directory", "artifact_file",
    "symbol", "selection", "candidates",
})


class OutputRedactionError(RuntimeError):
    """Output could not be checked for secrets, so it was not shown or saved."""


# The KnownSecrets of the run in progress, or None outside one.
_scope_var: ContextVar = ContextVar("checkpoint_known_secrets", default=None)


@contextmanager
def secret_scope():
    """
    Remember the secrets found while this block runs, to clean them out of
    every output it produces.

    Nests: inside an open scope this reuses it, so a pipeline's later stages
    know what its earlier ones found. A new scope starts empty, and what it
    learned is gone once it closes.
    """
    known = _scope_var.get()

    if known is not None:
        yield known
        return

    token = _scope_var.set(KnownSecrets())
    try:
        yield _scope_var.get()
    finally:
        _scope_var.reset(token)


def remember(redactor):
    """
    Add what a redactor found to the open scope, if any.

    agent/request_redaction.py calls this for every AI request, so a secret
    found in a request is known when the answer comes back.
    """
    known = _scope_var.get()

    if known is not None:
        known.learn(redactor)


# ---------------------------------------------------------
# Cleaning
# ---------------------------------------------------------

def _unsupported(value):
    return OutputRedactionError(f"Cannot check a {type(value).__name__} for secrets.")


def _unchanged(key, text):
    return text


def _walk(value, visit, key=None, keep=frozenset()):
    """
    A copy of JSON-shaped value with visit(key, text) applied to every string
    outside a kept field, where key names the field holding it. List items
    have no field name of their own: a list of snippets under a file name is
    text, not a field called after the file.
    """
    if isinstance(value, str):
        return visit(key, value)

    if value is None or isinstance(value, (bool, int, float)):
        return value

    if isinstance(value, Mapping):
        return {
            k: _walk(v, _unchanged) if k in keep else _walk(v, visit, k, keep)
            for k, v in value.items()
        }

    if isinstance(value, (list, tuple)):
        return [_walk(item, visit, None, keep) for item in value]

    raise _unsupported(value)


def _sanitize(walk):
    """
    Run walk(visit) twice with one redactor: first to learn every secret and
    every placeholder already present, then to build the cleaned copy.

    Returns (copy, RedactionReport).
    """
    problem = None

    try:
        redactor = SecretRedactor()

        def learn(key, text):
            redactor.scan_field(key, text)
            return text

        walk(learn)

        known = _scope_var.get()
        if known is not None:
            known.teach(redactor)

        clean = walk(redactor.redact_field)
        report = redactor.report()

        if known is not None:
            known.learn(redactor)

    except OutputRedactionError as exc:
        problem = str(exc)
    except Exception:
        # The detector's own error may quote what it was reading.
        problem = "The text could not be checked for secrets."

    # Raised here, not inside the except block, so it carries no reference
    # to the failure and whatever that quoted.
    if problem is not None:
        raise OutputRedactionError(problem)

    return clean, report


def sanitize_text(text):
    """
    text with its secrets replaced.

    Returns (text, RedactionReport). Raises OutputRedactionError.
    """
    return _sanitize(lambda visit: visit(None, text))


def sanitize_data(data, keep=REFERENCE_KEYS):
    """
    A copy of JSON-shaped data with the secrets in its strings replaced.

    Field names give context: a value under "password" or "api_key" is a
    secret even with no telling name inside it. Keys are never rewritten, and
    the values of keep fields are copied unchanged. The input is not changed.

    Returns (copy, RedactionReport). Raises OutputRedactionError, including
    for any object that is not JSON-shaped (dump models first).
    """
    return _sanitize(lambda visit: _walk(data, visit, keep=keep))


def safe_text(text, fallback=WITHHELD):
    """sanitize_text() that never raises: fallback stands in for text that cannot be checked."""
    try:
        return sanitize_text(text)[0]
    except Exception:
        return fallback


# ---------------------------------------------------------
# Saving
# ---------------------------------------------------------

def _not_saved(path, problem):
    return OutputRedactionError(f"{Path(path).name} was not saved: {problem}")


def _write_atomically(path, text):
    """
    Write text beside path, then swap it in, so an interrupted or failed
    write never leaves half a file. Text mode, as the writers this replaces
    used: line endings follow the platform.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")

    try:
        with open(tmp, "x", encoding="utf-8") as file:
            file.write(text)
        os.replace(tmp, path)
    except BaseException:
        with suppress(OSError):
            os.unlink(tmp)
        raise


def save_json(path, data, keep=REFERENCE_KEYS, **dump_options):
    """
    Save JSON-shaped data to path with its secrets replaced.

    Cleaned and serialized in memory first, then written and swapped in, so
    a failure at any point leaves the previous file as it was, never a
    partial or unchecked one. dump_options go to json.dumps (indent=2 unless
    given).

    Returns the RedactionReport. Raises OutputRedactionError, naming only the
    file, when the data cannot be checked; OSError when it cannot be written.
    """
    try:
        clean, report = sanitize_data(data, keep)
        text = json.dumps(clean, **{"indent": 2, **dump_options})
    except OutputRedactionError as exc:
        problem = str(exc)
    except (TypeError, ValueError):
        problem = "it could not be written as JSON."
    else:
        problem = None

    if problem is not None:
        raise _not_saved(path, problem)

    _write_atomically(path, text)
    return report


def save_text(path, text):
    """
    Save text to path with its secrets replaced, as save_json() does.

    Returns the RedactionReport. Raises OutputRedactionError or OSError.
    """
    try:
        clean, report = sanitize_text(text)
    except OutputRedactionError as exc:
        problem = str(exc)
    else:
        problem = None

    if problem is not None:
        raise _not_saved(path, problem)

    _write_atomically(path, clean)
    return report
