"""!
@file uml_command_output_test.py
@brief The UML step must not print plain text where the app reads its messages.
@details main.js reads the backend's stdout as one JSON message per line and fails
the running request on anything else ("Malformed response from Python backend").
uml_json_to_pdf prints "PDF written to: ..." for terminal use, which made every
full pipeline end with that error on 2026-10-21 although the run had succeeded.
"""

import json

import backend.commands as commands


def test_uml_step_writes_only_json_to_stdout(tmp_path, monkeypatch, capsys):
    cmd = commands.Commands()
    cmd.app_dir = tmp_path
    (tmp_path / "plantuml.jar").write_bytes(b"")

    def fake_build_report(args):
        print("PDF written to: C:/somewhere/Cart.pdf")
        return []

    monkeypatch.setattr(commands, "uml_parse_args", lambda: None)
    monkeypatch.setattr(commands, "uml_build_report", fake_build_report)

    result = cmd.generate_uml(str(tmp_path / "Cart.json"))

    out, err = capsys.readouterr()
    assert result["success"]
    for line in filter(None, out.splitlines()):
        json.loads(line)  # anything else would be "Malformed response" in the app
    assert "PDF written to" in err
