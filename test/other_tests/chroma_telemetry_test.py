"""
@file chroma_telemetry_test.py
@brief ChromaDB sends anonymous usage events to PostHog unless its
anonymized_telemetry setting is off. The backend (main.py) turns it off before
any ChromaDB client exists; each client reads ANONYMIZED_TELEMETRY when it
builds its settings. Runs in a separate Python process, as the app does, with
a client in a temporary folder.
"""

import os
import subprocess
import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_backend_turns_chroma_telemetry_off_before_any_client_exists(tmp_path):
    code = (
        "import main, chromadb\n"
        f"client = chromadb.PersistentClient(path={str(tmp_path)!r})\n"
        "print('telemetry:', client.get_settings().anonymized_telemetry)\n"
    )
    env = {key: value for key, value in os.environ.items() if key.upper() != "ANONYMIZED_TELEMETRY"}

    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
    )

    assert "telemetry: False" in completed.stdout, completed.stderr[-2000:]
