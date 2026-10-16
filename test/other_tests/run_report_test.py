"""!
@file run_report_test.py
@brief Tests for the run report page and for writing it at the end of a full run
(US-049, T-165 and T-167).
@details The diagram's PlantUML call is replaced with a stub, so these need no Java.
The full-pipeline tests stub out the pipeline itself: they check only what
happens after it, that a report problem never fails a successful run.
"""

import json
import re

import pytest

import backend.commands as commands
import backend.run_report as run_report
import backend.run_report_diagram as diagram
from backend.run_report_html import render


def minimal_data(**extra):
    data = {
        "codebase": "shop", "codebase_path": None, "generated_at": "2020-01-01T10:00:00", "run": None,
        "overview": {"summary": "", "responsibilities": [], "counts": {}},
        "folders": [], "files": [], "rules": [], "notes": [],
        "tests": {"unit": {"checked": False, "tests": []}, "integration": {"checked": False, "tests": []}},
    }
    data.update(extra)
    return data


def embedded_data(page):
    block = re.search(r'<script type="application/json" id="report-data">(.*?)</script>', page, re.S)
    return json.loads(block.group(1))


# ---------- the page (T-165) ----------

def test_page_carries_everything_and_loads_nothing_from_outside():
    page = render(minimal_data(), {"svg": '<svg viewBox="0 0 10 10"></svg>', "types": [], "edges": []})

    assert page.startswith("<!doctype html>")
    assert '<div class="canvas" id="canvas"><svg viewBox="0 0 10 10"></svg></div>' in page
    assert not re.search(r'<(script|link|img)[^>]+(src|href)="https?:', page)
    assert embedded_data(page)["codebase"] == "shop"
    assert "svg" not in embedded_data(page)["diagram"]  # the picture is in the page once, not twice


def test_page_opens_on_the_business_view():
    page = render(minimal_data())
    assert '<button type="button" data-view="business" aria-pressed="true">Business</button>' in page
    assert '<a href="#diagram" data-tab="diagram" data-dev>' in page  # hidden from business readers


def test_text_from_the_outputs_cannot_break_the_page():
    rule = {"id": 1, "rule": "</script><script>alert(1)</script>", "status": "proven", "folder": ".",
            "source_files": [], "evidence": {"a.py": ["@@SVG@@ <!-- x"]}, "reasoning": "", "reason": "", "tests": []}
    page = render(minimal_data(codebase="<b>shop</b>", rules=[rule]))

    assert "<h1>&lt;b&gt;shop&lt;/b&gt;</h1>" in page
    assert page.count("</script>") == 2  # only the page's own data and code blocks
    assert embedded_data(page)["rules"][0]["rule"] == rule["rule"]
    assert embedded_data(page)["rules"][0]["evidence"]["a.py"] == ["@@SVG@@ <!-- x"]


def test_page_without_a_diagram_still_renders():
    page = render(minimal_data(), {"svg": "", "error": "Java is not installed", "types": [], "edges": []})
    assert embedded_data(page)["diagram"]["error"] == "Java is not installed"
    assert '<div class="canvas" id="canvas"></div>' in page


# ---------- writing it (T-167) ----------

def test_write_run_report_saves_the_page_and_its_data(tmp_path, monkeypatch):
    monkeypatch.setattr(diagram, "render_svg", lambda puml, jar: '<svg viewBox="0 0 10 10"></svg>')
    codebase = tmp_path / "shop"
    codebase.mkdir()

    result = run_report.write_run_report(codebase, tmp_path)

    folder = tmp_path / "run_reports" / "shop"
    assert result["error"] is None and result["path"] == str(folder / "run_report.html")
    assert (folder / "run_report_data.json").exists()
    assert embedded_data((folder / "run_report.html").read_text(encoding="utf-8"))["codebase"] == "shop"


def test_write_run_report_never_raises(tmp_path, monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(run_report, "collect", broken)
    result = run_report.write_run_report(tmp_path / "shop", tmp_path)

    assert result == {"path": None, "error": "disk full", "diagram_error": ""}


@pytest.fixture
def finished_run(tmp_path, monkeypatch):
    """A Commands whose pipeline 'ran' successfully, with the report call recorded."""
    calls = []
    cmd = commands.Commands()
    cmd.app_dir = tmp_path
    monkeypatch.setattr(cmd, "_run_command", lambda name, task, **kw: {
        "success": True, "command": name, "result": {"steps": [], "message": "Full pipeline completed"}})

    def report(codebase, app_dir, open_browser=False):
        calls.append(open_browser)
        return {"path": str(tmp_path / "run_report.html"), "error": None, "diagram_error": ""}

    monkeypatch.setattr(commands, "write_run_report", report)
    return cmd, calls, tmp_path


def test_full_run_writes_the_report_for_the_app_to_open(finished_run):
    cmd, calls, tmp_path = finished_run
    result = cmd.full_pipeline(str(tmp_path))

    # The app's Open Report button opens it, so the backend opens no browser.
    assert calls == [False]
    assert result["success"] and result["result"]["run_report"] == str(tmp_path / "run_report.html")


def test_report_problem_is_a_warning_not_a_failure(finished_run, monkeypatch):
    cmd, _, tmp_path = finished_run
    monkeypatch.setattr(commands, "write_run_report",
                        lambda *a, **k: {"path": None, "error": "disk full", "diagram_error": ""})

    result = cmd.full_pipeline(str(tmp_path))

    assert result["success"] is True
    assert result["result"]["warning"] == "The run report could not be written: disk full"


def test_failed_run_gets_no_report(finished_run, monkeypatch):
    cmd, calls, tmp_path = finished_run
    monkeypatch.setattr(cmd, "_run_command", lambda name, task, **kw: {"success": False, "error": "429"})

    assert cmd.full_pipeline(str(tmp_path))["success"] is False
    assert calls == []
