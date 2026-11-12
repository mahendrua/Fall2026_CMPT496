"""
@file plantuml_sandbox_test.py
@brief Diagram text comes from the AI (UML step) or from code that includes
AI-written names (run report), so both places that run PlantUML must run it in
SANDBOX mode, where `!include` cannot copy a local file into the picture, and
with a time limit so one diagram cannot hang the step.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
import pytest

import utils.uml_json_to_pdf as uml
from backend.run_report_diagram import find_plantuml_jar, render_svg

PLANTUML_CMD = argparse.Namespace(plantuml_cmd="plantuml", plantuml_jar=None)


def test_uml_step_runs_plantuml_in_sandbox_mode_with_a_time_limit(tmp_path, monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(kwargs)
        (tmp_path / "demo.png").write_bytes(b"png")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(uml.subprocess, "run", fake_run)

    uml.render_plantuml("class Demo", "demo", tmp_path, PLANTUML_CMD)

    assert calls[0]["env"]["PLANTUML_SECURITY_PROFILE"] == "SANDBOX"
    assert calls[0]["timeout"] == uml.RENDER_TIMEOUT_SECONDS


def test_uml_step_skips_a_diagram_that_takes_too_long(tmp_path, monkeypatch):
    def slow_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))

    monkeypatch.setattr(uml.subprocess, "run", slow_run)

    with pytest.raises(uml.DiagramSyntaxError, match="longer than"):
        uml.render_plantuml("class Demo", "demo", tmp_path, PLANTUML_CMD)


@pytest.mark.skipif(shutil.which("java") is None or find_plantuml_jar(PROJECT_ROOT) is None,
                    reason="needs Java and plantuml.jar")
def test_run_report_diagram_cannot_include_a_local_file(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text('note "PRETEND_SECRET_FROM_A_LOCAL_FILE" as N1\n', encoding="utf-8")
    puml = f"@startuml\n!include {secret.as_posix()}\nclass Demo\n@enduml\n"

    try:
        svg = render_svg(puml, find_plantuml_jar(PROJECT_ROOT))
    except RuntimeError as error:
        # PlantUML refused the whole diagram, which also keeps the file out.
        assert "PRETEND_SECRET" not in str(error)
        return

    assert "PRETEND_SECRET" not in svg
