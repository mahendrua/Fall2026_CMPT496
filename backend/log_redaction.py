"""
backend/log_redaction.py

Keeps secrets out of the backend's console output (T-078, US-034): Python
log records, and anything else written to stdout or stderr. Electron reads
both: stdout as its one-JSON-object-per-line protocol, stderr into its own
console.

    install_log_redaction()   every log record is cleaned as it is created,
                              before any handler sees it
    guard_streams()           sys.stdout and sys.stderr clean what is
                              written to them, and uncaught exceptions are
                              printed in one piece

main.py installs both before it imports the rest of the backend.

Log records
-----------
The cleaning happens in the record factory rather than in a handler or a
filter: the factory runs for every record, whichever logger made it and
whichever handlers exist (including ones a library adds later), before
propagation passes it to any of them.

A record keeps its level, logger name, time, file, line and function. Its
message is formatted once (msg % args), cleaned, and stored as the message
with the arguments removed, so no handler can format the raw arguments
again. An exception is formatted the standard way, chained causes and
context included, cleaned, and stored as exc_text; exc_info is dropped, so
no formatter can render the raw exception again. stack_info is cleaned too.

When the arguments do not fit the format, a fixed notice is logged instead
of logging's own error report, which would print the raw arguments to
stderr. When cleaning fails, fixed notices stand in for the message and the
traceback. Nothing here logs, and a record set off while a record is being
cleaned gets no content, so a failure cannot feed itself.

Streams
-------
StreamGuard holds text until a newline arrives (print() writes a line and
its newline separately), then cleans everything up to the last newline in
one piece, so a traceback or a private key printed in one write is checked
whole. flush() cleans and writes a pending partial line. write_checked()
writes text that is already clean, such as the protocol messages
backend/progress_logging.py builds, without checking it twice.

Python's own exception hooks write a traceback a line at a time, so a
chained exception that repeats a secret without context would be checked
apart from the line that gave it away. Inside a run, the run's secret scope
links the pieces; for a crash outside any run, guard_streams() replaces the
default hooks (uncaught exceptions, and uncaught exceptions in threads) with
ones that print the whole traceback in one write. Hooks someone else set
are left alone. Other code printing a traceback line by line outside a run
(traceback.print_exc()) is checked line by line.

Not covered: bytes written to a stream's .buffer, and output written to the
file descriptor directly (faulthandler, or a child process sharing the
console; the backend's child processes capture their own output).
"""

import logging
import sys
import threading
import traceback

from utils.output_redaction import safe_text


LOG_WITHHELD = "[log message withheld: it could not be checked for secrets]"
UNFORMATTABLE = "[log message withheld: its arguments did not match its format]"
TRACEBACK_WITHHELD = "[traceback withheld: it could not be checked for secrets]"
OUTPUT_WITHHELD = "[output withheld: it could not be checked for secrets]\n"

# Text held back waiting for a newline is cleaned and written anyway past
# this size, so a stream that never ends a line cannot grow without bound.
MAX_PENDING_CHARS = 64 * 1024

# Formats tracebacks exactly as logging's default Formatter does.
_traceback_formatter = logging.Formatter()

# Set while a record on this thread is being cleaned.
_cleaning = threading.local()


def _clean_record(record):
    if getattr(_cleaning, "active", False):
        # Cleaning set off another record: give it no content at all.
        record.msg, record.args = LOG_WITHHELD, None
        record.exc_info = record.exc_text = record.stack_info = None
        return

    _cleaning.active = True
    try:
        try:
            message = record.getMessage()
        except Exception:
            message = None

        record.msg = UNFORMATTABLE if message is None else safe_text(message, LOG_WITHHELD)
        record.args = None

        if record.exc_info:
            try:
                text = _traceback_formatter.formatException(record.exc_info)
            except Exception:
                text = None

            record.exc_text = TRACEBACK_WITHHELD if text is None else safe_text(text, TRACEBACK_WITHHELD)
            record.exc_info = None

        elif record.exc_text:
            record.exc_text = safe_text(record.exc_text, TRACEBACK_WITHHELD)

        if record.stack_info:
            record.stack_info = safe_text(record.stack_info, TRACEBACK_WITHHELD)

    finally:
        _cleaning.active = False


def install_log_redaction():
    """Clean every log record created from now on. Calling it again changes nothing."""
    previous = logging.getLogRecordFactory()

    if getattr(previous, "cleans_secrets", False):
        return

    def factory(*args, **kwargs):
        record = previous(*args, **kwargs)
        _clean_record(record)
        return record

    factory.cleans_secrets = True
    logging.setLogRecordFactory(factory)


class StreamGuard:
    """
    A text stream that cleans what is written to it, a line at a time.

    Everything it does not define (encoding, fileno, isatty, reconfigure,
    buffer ...) is the wrapped stream's.
    """

    def __init__(self, stream):
        self._stream = stream
        self._pending = ""
        self._lock = threading.RLock()
        self._local = threading.local()

    def __getattr__(self, name):
        if name == "_stream":  # before __init__ has set it (copy, pickle)
            raise AttributeError(name)
        return getattr(self._stream, name)

    def write(self, text):
        if not isinstance(text, str):
            raise TypeError(f"write() argument must be str, not {type(text).__name__}")

        with self._lock:
            self._pending += text
            end = self._pending.rfind("\n") + 1

            if not end and len(self._pending) > MAX_PENDING_CHARS:
                end = len(self._pending)

            if end:
                ready, self._pending = self._pending[:end], self._pending[end:]
                self._emit(ready)

        return len(text)

    def flush(self):
        with self._lock:
            self._release()
            self._stream.flush()

    def write_checked(self, text):
        """Write text that is already clean, after anything still pending."""
        with self._lock:
            self._release()
            self._stream.write(text)

    def _release(self):
        ready, self._pending = self._pending, ""
        if ready:
            self._emit(ready)

    def _emit(self, text):
        if getattr(self._local, "busy", False):
            # Written by the cleaning itself: nothing analysed, but unchecked.
            self._stream.write(OUTPUT_WITHHELD)
            return

        self._local.busy = True
        try:
            clean = safe_text(text, OUTPUT_WITHHELD)
        finally:
            self._local.busy = False

        self._stream.write(clean)


def _print_exception(header, exc_type, exc, tb):
    stream = sys.stderr
    if stream is None:
        return

    stream.write(header + "".join(traceback.format_exception(exc_type, exc, tb)))
    stream.flush()


def _excepthook(exc_type, exc, tb):
    """sys.excepthook: the default's output, in one write."""
    _print_exception("", exc_type, exc, tb)


def _thread_excepthook(args):
    """threading.excepthook: the default's output, in one write."""
    if args.exc_type is SystemExit:
        return

    name = args.thread.name if args.thread is not None else threading.get_ident()
    _print_exception(f"Exception in thread {name}:\n", args.exc_type, args.exc_value, args.exc_traceback)


def guard_streams():
    """
    Put a StreamGuard around sys.stdout and sys.stderr, and have uncaught
    exceptions printed in one piece. Calling it again changes nothing.
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        if stream is not None and not isinstance(stream, StreamGuard):
            setattr(sys, name, StreamGuard(stream))

    if sys.excepthook is sys.__excepthook__:
        sys.excepthook = _excepthook

    if threading.excepthook is threading.__excepthook__:
        threading.excepthook = _thread_excepthook
