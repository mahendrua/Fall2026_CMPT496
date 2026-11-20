"""
backend/progress_logging.py

Messages to the frontend. main.js reads the backend's stdout as one JSON
object per line: progress updates, token counts, and each command's
response. Every one of them goes out through send() or send_response(),
which clean their text of secrets first (T-078, US-034) and keep the
framing: one object, one line, the same fields.
"""

import json
import logging
import sys
from contextlib import contextmanager
from contextvars import ContextVar

from utils.output_redaction import OutputRedactionError, safe_text, sanitize_data

logger = logging.getLogger(__name__)

_request_id = ContextVar("checkpoint_request_id", default=None)

# Stand in for text that could not be checked. The message itself still goes,
# with its type, numbers and request ID, so the frontend's bars and request
# tracking carry on.
PROGRESS_WITHHELD = "[progress message withheld: it could not be checked for secrets]"
RESPONSE_WITHHELD = "The backend's response was withheld because it could not be checked for secrets."

# Kept as they are in a message whose text could not be checked.
_PROTOCOL_FIELDS = ("type", "request_id")


@contextmanager
def request_scope(request_id):
    token = _request_id.set(request_id)
    try:
        yield
    finally:
        _request_id.reset(token)


def current_request_id():
    return _request_id.get()


def _include_request_id(data):
    request_id = current_request_id()
    if request_id is not None:
        data["request_id"] = request_id
    return data


def _write_line(line):
    """One line on stdout. Through main.py's StreamGuard, as text already checked."""
    stdout = sys.stdout
    write_checked = getattr(stdout, "write_checked", None)

    if write_checked is None:
        print(line, flush=True)
    else:
        write_checked(line + "\n")
        stdout.flush()


def _withheld(data):
    """data with only what cannot carry a secret: protocol fields, numbers, flags."""
    return {
        key: value if key in _PROTOCOL_FIELDS or not isinstance(value, str) else PROGRESS_WITHHELD
        for key, value in data.items()
        if value is None or isinstance(value, (str, bool, int, float))
    }


def send(data):
    """Send one message to the frontend, its text cleaned of secrets."""
    _include_request_id(data)

    try:
        clean, _ = sanitize_data(data)
    except OutputRedactionError:
        clean = _withheld(data)

    _write_line(json.dumps(clean))


def _jsonable(value):
    # How main.py has always sent what JSON has no type for.
    return list(value) if hasattr(value, "__iter__") else str(value)


def send_response(result):
    """
    Send a command's response to the frontend, its text cleaned of secrets.

    Serialized as main.py always has (iterables as lists, anything else as
    text). A response that cannot be checked becomes a failure saying so,
    still carrying its request ID.
    """
    request_id = result.get("request_id") if isinstance(result, dict) else None

    try:
        data = json.loads(json.dumps(result, default=_jsonable))
    except Exception as exc:
        clean = {"success": False, "error": safe_text(str(exc))}
    else:
        try:
            clean, _ = sanitize_data(data)
        except OutputRedactionError:
            clean = {"success": False, "error": RESPONSE_WITHHELD}

    if request_id is not None:
        clean["request_id"] = request_id

    _write_line(json.dumps(clean))


class FrontendProgressHandler(logging.Handler):
    def emit(self, record):
        send({
            "type": "progress",
            "stage": record.getMessage()
        })


def configure_progress_logging():
    # Attach the forwarder to this module's own logger, never the root
    # logger: root also carries INFO records from chromadb, httpx,
    # langchain and friends, and those would be emitted on stdout as
    # bogus progress messages for the frontend to display.
    logger.propagate = False

    # Avoid adding the handler twice
    if any(isinstance(h, FrontendProgressHandler) for h in logger.handlers):
        return

    logger.addHandler(FrontendProgressHandler())

def progress(message, percent=None, step_complete=False):

    data = {
        "type": "progress",
        "stage": message
    }

    if percent is not None:
        data["progress"] = percent

    if step_complete:
        data["step_complete"] = True

    send(data)

def pipeline_progress(stage, percent, step_percent=True):
    """
    Announce a pipeline stage. percent places the stage on the pipeline bar.
    step_percent=False says the stage reports no percentage of its own, so
    the step bar should not show one.
    """

    data = {
        "type": "pipeline_progress",
        "stage": stage,
        "progress": percent
    }

    if not step_percent:
        data["step_percent"] = False

    send(data)
