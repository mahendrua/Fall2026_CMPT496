"""
@file backend_shim.py
@brief Starts the real backend (main.py) against an isolated app folder, with model creation disabled.

    python backend_shim.py <app_dir> [main.py arguments...]

Used by verify_screen.js. APP_DIR is pointed at <app_dir>, so saved results,
reviews and the error log are read from and written to that folder only.
Every way of building a model raises and leaves a MODEL_REQUESTED file in
<app_dir>, so a screen that tried to call a model is caught.
"""

import runpy
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

app_dir = Path(sys.argv.pop(1)).resolve()

import agent.llm as llm  # noqa: E402


def refuse(*args, **kwargs):
    (app_dir / "MODEL_REQUESTED").write_text("A model was requested.", encoding="utf-8")
    raise RuntimeError("Model creation is disabled while verifying the Consistency Review screen.")


# Patched before the agents are imported: they bind make_llm by name.
llm.make_llm = refuse
llm.build_chat_model = refuse

import backend.commands as commands  # noqa: E402
commands.APP_DIR = app_dir

runpy.run_path(str(REPO / "main.py"), run_name="__main__")
