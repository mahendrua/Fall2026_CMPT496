import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar
logger = logging.getLogger(__name__)

_request_id = ContextVar("checkpoint_request_id", default=None)


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


class FrontendProgressHandler(logging.Handler):
    def emit(self, record):
        print(
            json.dumps(_include_request_id({
                "type": "progress",
                "stage": record.getMessage()
            })),
            flush=True,
        )


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

    _include_request_id(data)

    print(
        json.dumps(data),
        flush=True
    )

def pipeline_progress(stage, percent):

    print(
        json.dumps(_include_request_id({
            "type": "pipeline_progress",
            "stage": stage,
            "progress": percent
        })),
        flush=True
    )