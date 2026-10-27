"""
main.py

Backend entry point for the Codebase Analysis system.

This file exposes commands to external callers
(JavaScript application, API layer, Electron, etc.)
through the CommandDispatcher.
"""

import json
import sys

sys.stdout.reconfigure(
    encoding="utf-8",
    line_buffering=True
)

if __name__ == "__main__":
    # Before the rest of the backend is imported, so nothing it logs or
    # prints reaches Electron unchecked for secrets (T-078). Not when
    # imported, e.g. by tests, which keep their own streams.
    from backend.log_redaction import guard_streams, install_log_redaction

    install_log_redaction()
    guard_streams()


from backend.dispatcher import CommandDispatcher
from backend.preview_collections import CollectionPreview

from backend.progress_logging import configure_progress_logging, request_scope, send_response
from backend.run_protection import protected_run

configure_progress_logging()

def format_response(result):
    """Ensure every backend response follows the same JSON structure."""
    if isinstance(result, dict):
        if "success" not in result:
            result["success"] = True
        return result

    return {
        "success": True,
        "message": str(result)
    }


# Initialize backend services
# ---------------------------------------------------------

dispatcher = CommandDispatcher()

preview_service = CollectionPreview()



# ---------------------------------------------------------
# Command execution entrypoint
# ---------------------------------------------------------

def execute_command(command: str, **kwargs):
    """
    Execute a backend command.

    Example:

        execute_command(
            "build_database",
            codebase="C:/project"
        )

    """

    return dispatcher.dispatch(
        command,
        **kwargs
    )



# ---------------------------------------------------------
# Collection preview entrypoint
# ---------------------------------------------------------

def preview_command(action: str, **kwargs):
    """
    Execute collection preview operations.

    Example:

        preview_command(
            "list",
            db_type="summary"
        )

    """

    if action == "list":

        return preview_service.list_collections(
            kwargs["db_type"]
        )


    elif action == "preview":

        return preview_service.preview_collection(
            kwargs["collection"],
            kwargs.get("limit",5)
        )


    elif action == "all":

        return preview_service.preview_collections(
            kwargs["db_type"],
            kwargs.get("limit", 5)
        )


    else:

        return {
            "success": False,
            "error": (
                f"Unknown preview action: {action}"
            )
        }



# ---------------------------------------------------------
# Request loop
# ---------------------------------------------------------

def handle_request(line):
    """Run one request line from the frontend and return its response."""

    request_id = None
    try:
        request = json.loads(line)
        request_id = request.get("request_id") if isinstance(request, dict) else None

        if request["type"] == "command":
            with request_scope(request_id):
                result = format_response(
                    execute_command(
                        request["command"],
                        request_id=request_id,
                        **request.get("args", {})
                    )
                )
            if request_id is not None:
                result["request_id"] = request_id
        elif request["type"] == "preview":
            result = format_response(
                preview_command(
                    request["action"],
                    **request.get("args", {})
                )
            )
        else:
            result = {
                "success": False,
                "error": f"Unknown request type: {request.get('type')}",
                **({"request_id": request_id} if request_id is not None else {})
            }

        return result
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            **({"request_id": request_id} if request_id is not None else {})
        }


def serve(stdin):
    """
    Answer request lines until stdin closes, one JSON response line each.

    Each request is its own protected run (backend/run_protection.py): what
    it finds is remembered while it runs, so its progress, errors and
    response are cleaned of it too, and forgotten afterwards; and nothing it
    does is traced to LangSmith.
    """
    for line in iter(stdin.readline, ""):
        with protected_run():
            send_response(handle_request(line))


if __name__ == "__main__":
    serve(sys.stdin)