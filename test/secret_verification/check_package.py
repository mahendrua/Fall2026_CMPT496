"""
@file check_package.py
@brief T-079: a smoke check of the packaged (PyInstaller) backend (US-034).
@details

    .venv\\Scripts\\python.exe test\\secret_verification\\check_package.py <dist>\\main [--out DIR]

Starts <dist>/main/main.exe as Electron would (its own folder as the working
directory) and sends it requests that need no AI provider -- a frozen build
cannot be given the offline model:

    record_error            Electron filing an error that quotes a password
    an unknown command      whose name quotes a URL password
    generate_uml            a saved summary holding a labelled secret and a
                            PlantUML private field (known gap A), drawn into a PDF
    get_errors              the error log, as the Errors view reads it

Its stdout (read by main.js's rules), stderr, error_log.json and the PDF are
checked with the independent scanner. AI requests, the pipeline and run
memory are NOT checked here: run_verification.py covers them with the
unfrozen code.
"""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))

from leak_scanner import LeakScanner, Needle  # noqa: E402
from secret_fixture import make_secrets  # noqa: E402

STREAM_TYPES = {"progress", "pipeline_progress", "token_usage", "token_usage_stage"}


def check(dist, out, seed=79, timeout=600):
    dist = Path(dist)
    exe = dist / "main.exe"
    values = {s.case_id: s.value for s in make_secrets(seed)}
    needles = [Needle(s.case_id, s.value, s.fragments) for s in make_secrets(seed)]

    work = Path(tempfile.mkdtemp(prefix="t079_package_"))
    added = []
    try:
        # PlantUML is copied in by hand when packaging (README); do the same.
        jar = dist / "plantuml.jar"
        if not jar.exists():
            shutil.copyfile(REPO / "plantuml.jar", jar)
            added.append(jar)

        summary = work / "Vault-cs__1.json"
        summary.write_text(json.dumps({
            "path": "src/Vault.cs",
            "summary": f'Stores the admin password = "{values["S01"]}" for approvals.',
            "types": [{
                "name": "Vault", "kind": "class", "description": "Holds credentials.",
                "plantuml": f'class Vault {{\n  -password : string = "{values["S01"]}"\n  +Open() : bool\n}}',
            }],
        }), encoding="utf-8")

        requests = [
            ("record_error", {"source_command": "renderer",
                              "message": f'Could not render: password = "{values["S01"]}"'}),
            (f"export --password={values['S09']}", {}),
            ("generate_uml", {"summary_path": str(summary)}),
            ("get_errors", {}),
        ]
        lines = "".join(
            json.dumps({"type": "command", "request_id": f"pkg-{n}", "command": c, "args": a}) + "\n"
            for n, (c, a) in enumerate(requests, 1)
        )
        completed = subprocess.run([str(exe)], cwd=dist, input=lines.encode("utf-8"),
                                   capture_output=True, timeout=timeout)

        stdout = completed.stdout.decode("utf-8", "replace")
        messages, malformed = [], 0
        for line in filter(str.strip, stdout.splitlines()):
            try:
                message = json.loads(line)
                if not isinstance(message, dict) or not message.get("request_id"):
                    malformed += 1
                messages.append(message)
            except json.JSONDecodeError:
                malformed += 1
        responses = {m["request_id"]: m for m in messages
                     if isinstance(m, dict) and m.get("type") not in STREAM_TYPES and "request_id" in m}

        scanner = LeakScanner(needles)
        scanner.scan_text("package_stdout", stdout, "main.exe stdout")
        scanner.scan_text("package_stderr", completed.stderr.decode("utf-8", "replace"), "main.exe stderr")
        scanner.require_file("package_error_log", dist / "error_log.json", base=dist)
        pdf = summary.with_suffix(".pdf")
        scanner.require_file("package_uml_pdf", pdf, base=work)
        report = scanner.report()

        checks = {
            "the packaged backend answered every request": len(responses) == len(requests),
            "every stdout line is a message main.js accepts": malformed == 0,
            "the frontend's error was recorded": responses.get("pkg-1", {}).get("success") is True,
            "the unknown command failed": responses.get("pkg-2", {}).get("success") is False,
            "the UML PDF was drawn": responses.get("pkg-3", {}).get("success") is True and pdf.is_file(),
            "the error log was read back": responses.get("pkg-4", {}).get("success") is True,
            "placeholders replaced the secrets in the responses": "[REDACTED_PASSWORD_" in stdout,
            "no planted value in stdout, stderr, error_log.json or the PDF": report.leaks == 0,
            "every sink was read": report.complete,
        }
        data = {
            "task": "T-079 (US-034) packaged backend smoke check",
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "executable": exe.name,
            "exit_code": completed.returncode,
            "checks": checks,
            "leaks": report.leaks,
            "cases_leaked": sorted({f.case_id for f in report.findings}),
            "problems": [p.__dict__ for p in report.problems],
            "not_checked": "AI requests, the pipeline and run memory (a frozen build cannot load the offline model)",
            "ok": all(checks.values()),
        }
        text = json.dumps(data, indent=2)
        if LeakScanner(needles).contains_planted(text):
            raise RuntimeError("The evidence holds a planted value, so it was not written.")
        Path(out).mkdir(parents=True, exist_ok=True)
        (Path(out) / "package_report.json").write_text(text, encoding="utf-8")
        return data
    finally:
        shutil.rmtree(work, ignore_errors=True)
        for path in added:
            path.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="T-079 packaged backend smoke check (offline, no AI).")
    parser.add_argument("dist", help="the packaged backend folder (holding main.exe)")
    parser.add_argument("--out", help="where package_report.json goes")
    args = parser.parse_args(argv)
    out = Path(args.out) if args.out else HERE / "runs" / f"package-{datetime.now():%Y%m%d-%H%M%S}"
    data = check(args.dist, out)
    for name, ok in data["checks"].items():
        print(("PASS  " if ok else "FAIL  ") + name)
    print(("OK" if data["ok"] else "FAILED") + f". Evidence: {out}")
    return 0 if data["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
