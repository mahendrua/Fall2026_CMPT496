"""G2-only language fallback against the trusted function_lab fixture."""

import shutil
from pathlib import Path
from types import SimpleNamespace

from agent.python_test_harness import make_cases, trusted_python_test_mode
from agent.testing_language import detect_test_language

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "python_e2e"


def test_function_lab_g2_paths_select_python_but_stay_script_gated(tmp_path):
    lab = tmp_path / "function_lab"
    shutil.copytree(FIXTURE_ROOT / "function_lab", lab)

    source_file_paths = []
    language = detect_test_language([source_file_paths], codebase_path=str(lab))
    assert language == "python"
    assert source_file_paths == []
    assert trusted_python_test_mode(source_file_paths, str(lab)) == "script"

    generated = SimpleNamespace(
        id=1,
        unit_test=(
            "def test_must_not_invent_a_script_target():\n"
            "    assert True\n"
        ),
        imports=["import subprocess", "import sys"],
        source_file_paths=source_file_paths,
        test_mode="script",
    )
    cases, rejected = make_cases(
        "UT",
        [(generated.id, generated)],
        body_of=lambda item: item.unit_test,
        imports_of=lambda item: item.imports,
        codebase_path=str(lab),
    )
    assert cases == []
    assert rejected[0].test_mode == "script"
    assert "resolved source file" in rejected[0].reason
    assert generated.source_file_paths == []
    assert (lab / "math_lab.py").is_file()
