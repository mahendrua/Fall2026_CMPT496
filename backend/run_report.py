"""
backend/run_report.py

Writes the run report (US-049, T-167): reads the run's outputs, draws the
whole-project diagram and saves one HTML page, in

    run_reports/<codebase>/run_report.html        the report
    run_reports/<codebase>/run_report_data.json   what it was built from
    run_reports/<codebase>/project_diagram.puml   the diagram's PlantUML text
    run_reports/<codebase>/project_diagram.svg    the diagram on its own

The full pipeline calls write_run_report when a run finishes. It never
raises: a report problem is logged and returned, and the run still succeeds.

From a terminal, to make a report from outputs already on disk:

    python -m backend.run_report targetCodebases/<project> --open
"""

import argparse
import logging
import webbrowser
from pathlib import Path

from backend.run_report_data import collect, report_folder, save
from backend.run_report_diagram import build_diagram
from backend.run_report_html import render


logger = logging.getLogger(__name__)

REPORT_NAME = "run_report.html"


def write_run_report(codebase, app_dir, run_log_path=None, open_browser=False):
    """
    Build and save the report for the latest run on this codebase.

    @param codebase Path to the codebase, or just its folder name.
    @param app_dir The app folder, where agent/ and run_logs/ are.
    @param run_log_path A specific run log, instead of the newest full run.
    @param open_browser Open the report in the default browser when done.
    @return {"path": str or None, "error": str or None, "diagram_error": str}
    """
    try:
        codebase = Path(codebase)
        codebase_path = codebase.resolve() if codebase.is_dir() else None

        data = collect(codebase.name, app_dir, codebase_path=codebase_path, run_log_path=run_log_path)
        diagram = build_diagram(data, app_dir)

        folder = report_folder(app_dir, codebase.name)
        save(data, app_dir)

        if diagram.get("puml"):
            (folder / "project_diagram.puml").write_text(diagram["puml"], encoding="utf-8")
        if diagram.get("svg"):
            (folder / "project_diagram.svg").write_text(diagram["svg"], encoding="utf-8")

        path = folder / REPORT_NAME
        path.write_text(render(data, diagram), encoding="utf-8")
        logger.info("Run report written to %s", path)

        if open_browser:
            _open(path)

        return {"path": str(path), "error": None, "diagram_error": diagram.get("error", "")}

    except Exception as exc:
        logger.exception("Could not write the run report")
        return {"path": None, "error": str(exc), "diagram_error": ""}


def _open(path):
    """Open the report in the default browser; not being able to is not an error."""
    try:
        webbrowser.open(Path(path).resolve().as_uri())
    except Exception:
        logger.warning("Could not open %s in a browser", path, exc_info=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Write the run report for a codebase from its existing outputs.")
    parser.add_argument("codebase", help="Path to the codebase (or its folder name), e.g. targetCodebases/<project>")
    parser.add_argument("--run-log", help="Use this run log instead of the newest full run in run_logs/.")
    parser.add_argument("--open", action="store_true", help="Open the report in the browser when done.")
    args = parser.parse_args(argv)

    app_dir = Path(__file__).resolve().parent.parent
    codebase = Path(args.codebase)
    if not codebase.is_absolute() and not codebase.exists():
        codebase = app_dir / codebase

    result = write_run_report(codebase, app_dir, run_log_path=args.run_log, open_browser=args.open)

    if result["error"]:
        print(f"Report failed: {result['error']}")
        return 1

    print(f"Report: {result['path']}")
    if result["diagram_error"]:
        print(f"Diagram: {result['diagram_error']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
