"""!
@file log_redaction_test.py
@brief Tests for backend/log_redaction.py (T-078, US-034).
@details Log records, tracebacks and anything written to stdout or stderr
must reach Electron without a secret the shared detector finds, while
keeping level, time, location and the one-JSON-object-per-line framing.
Two tests start real processes: one with only the guards installed, and the
real backend (main.py) answering requests that touch no file. Every secret
is fake; no model or network is involved. absent() fails with a count,
never the value.
"""

import io
import json
import logging
import os
import re
import subprocess
import sys
import textwrap
import threading
import traceback
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage

import backend.log_redaction as log_redaction
import utils.secret_redaction as secret_redaction
from agent.request_redaction import redact_messages
from backend.log_redaction import (
    LOG_WITHHELD,
    MAX_PENDING_CHARS,
    OUTPUT_WITHHELD,
    TRACEBACK_WITHHELD,
    UNFORMATTABLE,
    StreamGuard,
    install_log_redaction,
)
from utils.output_redaction import secret_scope

REPO = Path(__file__).resolve().parents[2]

FAKE = "Fake-Pass-111!"
# Assembled at run time so the repository never holds a real-format token.
GITHUB = "ghp" + "_" + "A1b2C3d4E5f6G7h8I9j0" * 2
P1 = "[REDACTED_PASSWORD_1]"

# The format backend/commands.py gives the root logger.
FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"


def absent(text, *secrets):
    """Fail, without quoting them, if any planted secret is in text."""
    __tracebackhide__ = True  # pytest would print text with the frame's arguments
    leaked = sum(secret in text for secret in secrets or (FAKE, GITHUB))
    if leaked:
        raise AssertionError(f"{leaked} planted secret(s) found in the output")


class Records(logging.Handler):
    """Keeps every record it is given, and the text the format makes of it."""

    def __init__(self):
        super().__init__()
        self.records, self.lines = [], []
        self.setFormatter(logging.Formatter(FORMAT))

    def emit(self, record):
        self.records.append(record)
        self.lines.append(self.format(record))

    def text(self):
        return "\n".join(self.lines)


@pytest.fixture
def restore_factory():
    previous = logging.getLogRecordFactory()
    yield
    logging.setLogRecordFactory(previous)


@pytest.fixture
def log(restore_factory):
    install_log_redaction()
    handler = Records()
    logger = logging.getLogger("t078")
    logger.handlers, logger.propagate = [handler], False
    logger.setLevel(logging.DEBUG)
    yield logger, handler
    logger.handlers = []


@pytest.fixture
def broken_detector(monkeypatch):
    """The detector fails, quoting what it was reading, as a bug might."""
    def fail(text):
        raise RuntimeError(f"could not read {text!r}")

    monkeypatch.setattr(secret_redaction, "_find", fail)


# ---------------------------------------------------------------------------
# Log records
# ---------------------------------------------------------------------------

def test_ordinary_records_keep_their_text_level_and_time(log):
    logger, handler = log
    logger.info("Run report written to %s", "C:/app/run_reports/Shop/run_report.html")

    line = handler.lines[0]
    assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} \| INFO \| t078 \| ", line)
    assert line.endswith("| Run report written to C:/app/run_reports/Shop/run_report.html")


def test_parameterized_and_preformatted_messages_are_cleaned(log):
    logger, handler = log
    logger.error("Login failed for %s: %s", "ann", f'password="{FAKE}"')
    logger.warning(f"Validation error for rule 3: bad credentials {GITHUB}")

    absent(handler.text())
    assert handler.lines[0].endswith(f'| ERROR | t078 | Login failed for ann: password="{P1}"')
    assert handler.lines[1].endswith("| WARNING | t078 | Validation error for rule 3: bad credentials [REDACTED_TOKEN_1]")

    first = handler.records[0]
    assert first.args is None
    assert (first.levelno, first.funcName) == (logging.ERROR, "test_parameterized_and_preformatted_messages_are_cleaned")
    assert first.pathname == __file__ and first.lineno > 0


def raise_chained(explicit):
    try:
        raise ValueError(f'could not parse password = "{FAKE}"')
    except ValueError as inner:
        # The outer message repeats the value with no context of its own.
        if explicit:
            raise RuntimeError(f"Summarizing failed after retrying {FAKE}") from inner
        raise RuntimeError(f"Summarizing failed after retrying {FAKE}")


@pytest.mark.parametrize("explicit, link", [
    (True, "The above exception was the direct cause of the following exception"),
    (False, "During handling of the above exception, another exception occurred"),
], ids=["cause", "context"])
def test_tracebacks_and_their_chains_are_cleaned(log, explicit, link):
    logger, handler = log

    try:
        raise_chained(explicit)
    except RuntimeError:
        logger.exception("File summary failed")

    text = handler.text()
    absent(text)
    assert handler.lines[0].split("\n")[0].endswith("| ERROR | t078 | File summary failed")
    for part in ("Traceback (most recent call last)", link, "ValueError: could not parse password = ",
                 "RuntimeError: Summarizing failed after retrying [REDACTED_PASSWORD_1]",
                 "log_redaction_test.py", "in raise_chained"):
        assert part in text
    record = handler.records[0]
    assert record.exc_info is None and record.exc_text


def test_a_log_repeating_a_secret_found_earlier_in_the_run_is_cleaned(log):
    logger, handler = log

    with secret_scope():
        redact_messages([HumanMessage(content=f'private const string AdminPassword = "{FAKE}";')])
        logger.info("Retrying the login with %s", FAKE)

    absent(handler.text())
    assert handler.lines[0].endswith(f"Retrying the login with {P1}")


def leaky_stack(logger):
    logger.info("checkpoint", stack_info=True)  # password = "Fake-Pass-111!"


def test_stack_info_is_cleaned(log):
    logger, handler = log
    leaky_stack(logger)

    text = handler.text()
    absent(text)
    assert "Stack (most recent call last)" in text and "in leaky_stack" in text


def test_handlers_added_before_or_after_installing_are_covered(restore_factory):
    early, late = Records(), Records()
    logger = logging.getLogger("t078.order")
    logger.handlers, logger.propagate = [early], False
    logger.setLevel(logging.INFO)

    install_log_redaction()
    logger.addHandler(late)
    try:
        logger.info('password = "%s"', FAKE)
    finally:
        logger.handlers = []

    for handler in (early, late):
        absent(handler.text())
        assert handler.lines[0].endswith(f'password = "{P1}"')


def test_records_reaching_a_handler_by_propagation_are_cleaned(log):
    _, handler = log
    library = logging.getLogger("t078.library.http")

    library.warning("HTTP Request: GET https://api.example.test/v1/items?api_key=%s", "Fake1234Key5678x")

    absent(handler.text(), "Fake1234Key5678x")
    assert handler.lines[0].endswith("?api_key=[REDACTED_API_KEY_1]")


def test_arguments_that_do_not_fit_are_never_printed(log, capsys):
    logger, handler = log
    logger.info("%d files summarized", f'password="{FAKE}"')

    assert handler.lines[0].endswith(f"| INFO | t078 | {UNFORMATTABLE}")
    # logging's own error report would print the raw arguments to stderr.
    err = capsys.readouterr().err
    absent(err)
    assert "Logging error" not in err


def test_a_cleaning_failure_withholds_the_message_and_traceback(log, broken_detector):
    logger, handler = log

    try:
        raise_chained(True)
    except RuntimeError:
        logger.exception('Login failed: password = "%s"', FAKE)

    text = handler.text()
    absent(text)
    assert len(handler.records) == 1
    assert text.endswith(f"| ERROR | t078 | {LOG_WITHHELD}\n{TRACEBACK_WITHHELD}")


def test_a_record_made_while_cleaning_gets_no_content(log, monkeypatch):
    logger, handler = log
    real = log_redaction.safe_text

    def logs_while_cleaning(text, fallback):
        logger.warning(f'nested: password = "{FAKE}"')
        return real(text, fallback)

    monkeypatch.setattr(log_redaction, "safe_text", logs_while_cleaning)
    logger.info('outer: password = "%s"', FAKE)

    absent(handler.text())
    assert handler.lines[0].endswith(f"| WARNING | t078 | {LOG_WITHHELD}")
    assert handler.lines[1].endswith(f'| INFO | t078 | outer: password = "{P1}"')


def test_installing_twice_wraps_once(restore_factory):
    install_log_redaction()
    factory = logging.getLogRecordFactory()
    install_log_redaction()

    assert logging.getLogRecordFactory() is factory
    assert factory.cleans_secrets


# ---------------------------------------------------------------------------
# Streams
# ---------------------------------------------------------------------------

def test_a_printed_line_is_cleaned_once_complete():
    out = io.StringIO()
    guard = StreamGuard(out)

    print(f'Connecting with password = "{FAKE}"', file=guard)
    guard.write("partial line")
    absent(out.getvalue())
    assert out.getvalue() == f'Connecting with password = "{P1}"\n'

    guard.flush()
    absent(out.getvalue())
    assert out.getvalue().endswith("\npartial line")


def test_text_written_in_one_piece_is_checked_whole():
    out = io.StringIO()
    body = "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7\nFakeKeyBodyLine2abc123def456ghi789\n"
    StreamGuard(out).write(f"Loaded key:\n-----BEGIN PRIVATE KEY-----\n{body}-----END PRIVATE KEY-----\n")

    absent(out.getvalue(), "MIIEvQIBADANBgkqhkiG9w0BAQEFAASC", "FakeKeyBodyLine2abc123def456ghi789")
    assert "[REDACTED_PRIVATE_KEY_1]" in out.getvalue()


def chained_error():
    try:
        raise_chained(True)
    except RuntimeError as exc:
        return exc


def test_an_uncaught_exception_is_printed_and_checked_in_one_piece(monkeypatch):
    out = io.StringIO()
    monkeypatch.setattr(sys, "stderr", StreamGuard(out))
    exc = chained_error()

    log_redaction._excepthook(type(exc), exc, exc.__traceback__)

    absent(out.getvalue())
    assert out.getvalue() == "".join(traceback.format_exception(exc)).replace(FAKE, P1)


def test_an_uncaught_exception_in_a_thread_is_printed_and_checked_in_one_piece(monkeypatch):
    out = io.StringIO()
    monkeypatch.setattr(sys, "stderr", StreamGuard(out))
    exc = chained_error()

    log_redaction._thread_excepthook(SimpleNamespace(
        exc_type=type(exc), exc_value=exc, exc_traceback=exc.__traceback__,
        thread=SimpleNamespace(name="summarizer-3"),
    ))

    absent(out.getvalue())
    assert out.getvalue().startswith("Exception in thread summarizer-3:\nTraceback (most recent call last)")
    assert f"RuntimeError: Summarizing failed after retrying {P1}" in out.getvalue()


def test_a_traceback_printed_line_by_line_is_linked_by_the_run_scope():
    out = io.StringIO()
    exc = chained_error()

    # traceback.print_exception() writes each frame separately; the run's
    # scope carries what the inner error gave away to the outer line.
    with secret_scope():
        traceback.print_exception(exc, file=StreamGuard(out))

    absent(out.getvalue())
    assert f"RuntimeError: Summarizing failed after retrying {P1}" in out.getvalue()


def test_only_the_default_exception_hooks_are_replaced(monkeypatch):
    def custom(*args):
        pass

    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    monkeypatch.setattr(sys, "excepthook", sys.__excepthook__)
    monkeypatch.setattr(threading, "excepthook", custom)

    log_redaction.guard_streams()

    assert isinstance(sys.stdout, StreamGuard) and isinstance(sys.stderr, StreamGuard)
    assert sys.excepthook is log_redaction._excepthook
    assert threading.excepthook is custom


def test_checked_text_keeps_its_order_and_is_not_rewritten():
    out = io.StringIO()
    guard = StreamGuard(out)

    guard.write(f'pending password = "{FAKE}" ')
    guard.write_checked('{"type": "progress", "stage": "already clean"}\n')

    absent(out.getvalue())
    assert out.getvalue() == f'pending password = "{P1}" {{"type": "progress", "stage": "already clean"}}\n'


def test_a_guard_that_cannot_check_writes_a_fixed_notice(broken_detector):
    out = io.StringIO()
    print(f'password = "{FAKE}"', file=StreamGuard(out))

    assert out.getvalue() == OUTPUT_WITHHELD


def test_unterminated_output_is_not_held_forever():
    out = io.StringIO()
    StreamGuard(out).write("x" * (MAX_PENDING_CHARS + 1))

    assert len(out.getvalue()) == MAX_PENDING_CHARS + 1


def test_the_guard_is_otherwise_the_stream_it_wraps():
    out = io.StringIO()
    guard = StreamGuard(out)

    assert guard.writable() and not guard.isatty()
    with pytest.raises(TypeError):
        guard.write(b"bytes")


# ---------------------------------------------------------------------------
# Real processes
# ---------------------------------------------------------------------------

def run_python(args, stdin=None):
    env = {**os.environ, "HF_HUB_OFFLINE": "1", "PYTHONIOENCODING": "utf-8"}
    env.pop("LANGSMITH_TRACING", None)
    return subprocess.run(
        [sys.executable, *args], input=stdin, capture_output=True, text=True,
        encoding="utf-8", cwd=REPO, env=env, timeout=180,
    )


GUARDED_PROCESS = textwrap.dedent("""
    import logging, sys, threading, warnings
    sys.path.insert(0, ".")
    from backend.log_redaction import guard_streams, install_log_redaction
    install_log_redaction()
    guard_streams()
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s | %(message)s")
    from backend.progress_logging import progress

    secret = sys.argv[1]
    progress(f'Checking password = "{secret}"', 40)
    print(f'stray: password = "{secret}"')
    logging.getLogger("library").warning("retry with %s", f'password="{secret}"')
    warnings.warn(f'deprecated: password = "{secret}"')

    def boom():
        try:
            raise ValueError(f'bad password = "{secret}"')
        except ValueError as inner:
            # Repeats the value with no context of its own.
            raise RuntimeError(f"gave up after retrying {secret}") from inner

    worker = threading.Thread(target=boom, name="worker-1")
    worker.start()
    worker.join()
    boom()
""")


def test_a_guarded_process_keeps_secrets_off_both_streams():
    result = run_python(["-c", GUARDED_PROCESS, FAKE])

    absent(result.stdout)
    absent(result.stderr)
    assert result.returncode == 1

    progress_line, stray = result.stdout.splitlines()
    assert json.loads(progress_line) == {"type": "progress", "stage": f'Checking password = "{P1}"', "progress": 40}
    assert stray == f'stray: password = "{P1}"'

    for part in (f'WARNING | retry with password="{P1}"', f'UserWarning: deprecated: password = "{P1}"',
                 "Exception in thread worker-1:\nTraceback (most recent call last)",
                 f'ValueError: bad password = "{P1}"', f"RuntimeError: gave up after retrying {P1}"):
        assert part in result.stderr
    assert result.stderr.count("Traceback (most recent call last)") == 4


def test_the_backend_answers_in_clean_json_lines():
    requests = (
        json.dumps({"type": f'password = "{FAKE}"', "request_id": "r-1"}) + "\n"
        + "not json\n"
    )
    result = run_python(["main.py"], stdin=requests)

    absent(result.stdout)
    absent(result.stderr)
    assert result.returncode == 0

    lines = [json.loads(line) for line in result.stdout.splitlines()]
    assert lines == [
        {"success": False, "error": f'Unknown request type: password = "{P1}"', "request_id": "r-1"},
        {"success": False, "error": "Expecting value: line 1 column 1 (char 0)"},
    ]
