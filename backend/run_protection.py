"""
backend/run_protection.py

What every backend request and command runs inside (T-078, US-034):

    with protected_run():
        ...

It opens the run's secret scope (utils/output_redaction.py), so a secret one
of the run's AI requests gave away is also cleaned out of the run's outputs
where it reappears without context, and it keeps the run out of LangSmith
tracing.

Tracing
-------
LangChain uploads a trace of every run to LangSmith when LANGSMITH_TRACING
(or LANGCHAIN_TRACING_V2) is "true" in the environment. Such a trace would
carry what agent/llm.py cannot clean: LangGraph reports each node's input --
the graph state, holding source code, summaries and rules -- to the
callbacks before the node calls its model, and a tracer uploads it as it is.

So inside a protected run tracing is switched off, for this context only,
with langsmith's own tracing_context(enabled=False); a tracer set with
langchain_core's tracing_v2_enabled() is set aside too. The environment,
other programs, and anything outside the run are left as they were. When
the environment asks for tracing, a warning says once that Checkpoint's
runs are not traced, and why.

Not covered: a tracer passed in explicitly as a callback (the app passes
none), and LangChain's debug and verbose console output (set_debug(True)),
which the app never turns on.
"""

import logging
from contextlib import contextmanager

from langchain_core.tracers.context import tracing_v2_callback_var
from langsmith import utils as langsmith_utils
from langsmith.run_helpers import tracing_context

from utils.output_redaction import secret_scope

logger = logging.getLogger(__name__)

TRACING_NOTICE = (
    "LangSmith tracing is switched on in this environment, but Checkpoint does not "
    "trace its runs: a trace would upload the analysed code and summaries without "
    "their secrets removed (US-034). Other programs are not affected."
)

# Whether TRACING_NOTICE has been logged by this process.
_noticed = False


def _notice_requested_tracing():
    global _noticed

    if _noticed:
        return

    try:
        # Inside a protected run this is already False, so nested runs are quiet.
        requested = langsmith_utils.tracing_is_enabled()
    except Exception:
        requested = False

    if requested:
        _noticed = True
        logger.warning(TRACING_NOTICE)


@contextmanager
def untraced():
    """No LangSmith tracing for anything run inside, in this context only."""
    _notice_requested_tracing()

    token = tracing_v2_callback_var.set(None)
    try:
        with tracing_context(enabled=False):
            yield
    finally:
        tracing_v2_callback_var.reset(token)


@contextmanager
def protected_run():
    """
    The run's secret scope, with tracing off. Nests: an inner protected run
    shares the outer one's scope. Yields the scope's KnownSecrets.
    """
    with secret_scope() as known, untraced():
        yield known
