"""
@file build_database_errors_test.py
@brief Offline test that building the summary database before the file
summaries exist reports an error for that step instead of ending the backend
(it used to call sys.exit, so every later click failed until the app was
restarted). Nothing is created: the check runs before the database opens.
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

import backend.commands as commands_module


def test_summary_database_without_summaries_reports_an_error(tmp_path, monkeypatch):
    app = tmp_path / "app"
    app.mkdir()
    monkeypatch.chdir(app)
    commands = commands_module.Commands()
    commands.app_dir = app
    codebase = tmp_path / "targets" / f"no-summaries-{tmp_path.name}"

    result = commands.build_summary_database(str(codebase))

    assert result["success"] is False
    assert "Create the file summaries first" in result["error"]
