"""
@file run_electron_check.py
@brief T-079: the full pipeline started from the real Electron window, on the
ten-secret fixture, offline (US-034).
@details

    .venv\\Scripts\\python.exe test\\secret_verification\\run_electron_check.py [--out DIR]

Builds the same workspace as run_verification.py, starts
node_modules/.bin/electron on verify_electron.js (the real main.js,
preload.js and renderer.js; the backend started through offline_backend.py),
and checks:

    what the window was sent    every message main.js passed to the renderer
    what the window showed      the Complete screen and the Consistency Review screen
    what the backend printed    its stdout and stderr, as main.js read them

electron_report.json, report.json and two screenshots go to --out (default
runs/electron-<time>/, ignored by git). The workspace is removed afterwards.
Exit status 0 when every check passed.

Not covered: the other screens (View Insights file browser, Errors, API
keys), which read files from the repository's own folders rather than
through the backend; see the manual checklist in the README.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))

from leak_scanner import LeakScanner  # noqa: E402
from run_verification import Workspace  # noqa: E402

ELECTRON = REPO / "node_modules" / ".bin" / ("electron.cmd" if os.name == "nt" else "electron")


def run(out, seed=79, timeout=1200):
    root = Path(tempfile.mkdtemp(prefix="t079_electron_"))
    try:
        ws = Workspace(root, seed)
        needles_file = root / "needles.json"     # raw values, inside the workspace only
        needles_file.write_text(json.dumps([
            {"case_id": n.case_id, "value": n.value, "fragments": list(n.fragments)} for n in ws.needles()
        ]), encoding="utf-8")
        manifest = root / "electron_manifest.json"
        manifest.write_text(json.dumps({
            "python": sys.executable, "backend": str(HERE / "offline_backend.py"),
            "app": str(ws.app), "capture": str(ws.capture), "codebase": str(ws.fixture.root),
            "seed": seed, "env": ws.backend_env(), "needles": str(needles_file),
        }), encoding="utf-8")

        shots = root / "electron_out"
        # Run from an editor built on Electron (VS Code), this is set and
        # would start Electron as plain Node, with no window.
        env = {k: v for k, v in os.environ.items() if k != "ELECTRON_RUN_AS_NODE"}
        completed = subprocess.run(
            [str(ELECTRON), str(HERE / "verify_electron.js"), str(manifest), str(shots)],
            cwd=REPO, env=env, capture_output=True, timeout=timeout,
        )
        electron = json.loads((shots / "electron_report.json").read_text(encoding="utf-8")) \
            if (shots / "electron_report.json").is_file() else {"results": [], "failed": 1, "facts": {}}

        # main.js read the backend's pipes itself: its stdout became the
        # messages verify_electron.js recorded, and its stderr went to
        # Electron's console (main.js prints it there), captured here.
        scanner = LeakScanner(ws.needles(), raw_roots=[ws.inputs])
        console = completed.stdout.decode("utf-8", "replace") + "\n" + completed.stderr.decode("utf-8", "replace")
        scanner.scan_text("electron_console", console, "electron stdout and stderr")
        report = scanner.report()

        data = {
            "task": "T-079 (US-034) Electron check",
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "electron_exit_code": completed.returncode,
            "electron": electron,
            "electron_console": {"chars": len(console), "leaks": report.leaks,
                                 "cases": sorted({f.case_id for f in report.findings})},
            "network_attempts": sum(1 for line in (ws.capture / "network.jsonl").read_text(encoding="utf-8").splitlines()
                                    if line.strip()) if (ws.capture / "network.jsonl").is_file() else 0,
            "fixture_unchanged": ws.fixture.changed_files() == [],
        }
        data["ok"] = (completed.returncode == 0 and electron.get("failed") == 0 and not report.leaks
                      and data["network_attempts"] == 0 and data["fixture_unchanged"])

        text = json.dumps(data, indent=2)
        if LeakScanner(ws.needles()).contains_planted(text):
            raise RuntimeError("The Electron evidence holds a planted value, so it was not written.")
        out = Path(out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "report.json").write_text(text, encoding="utf-8")
        if not report.leaks:   # kept only when it holds no planted value
            (out / "electron_console.txt").write_text(console, encoding="utf-8")
        for png in shots.glob("*.png"):
            shutil.copyfile(png, out / png.name)
        return data
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="T-079 Electron check (offline).")
    parser.add_argument("--out", help="where report.json and the screenshots go")
    args = parser.parse_args(argv)
    out = Path(args.out) if args.out else HERE / "runs" / f"electron-{datetime.now():%Y%m%d-%H%M%S}"
    data = run(out)
    for result in data["electron"].get("results", []):
        print(("PASS  " if result["ok"] else "FAIL  ") + result["label"])
    print(f"console leaks: {data['electron_console']['leaks']}, network attempts: {data['network_attempts']}, "
          f"fixture unchanged: {data['fixture_unchanged']}")
    print(("OK" if data["ok"] else "FAILED") + f". Evidence: {out}")
    return 0 if data["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
