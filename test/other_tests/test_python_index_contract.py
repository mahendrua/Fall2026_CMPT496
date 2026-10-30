"""Parser/index-contract tests. This is not a Chroma or embedding integration test."""

import shutil
from pathlib import Path

from utils.tree_parse import get_chunks, parse_dir

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "python_e2e"


def _copy_lab(tmp_path, name):
    dest = tmp_path / name
    shutil.copytree(FIXTURE_ROOT / name, dest)
    return dest


def _chunks_for(lab_dir, filename):
    bundles = parse_dir(str(lab_dir))
    bundle = next(item for item in bundles if item.path.name == filename)
    return bundle, get_chunks(bundle)


def test_function_lab_produces_function_chunks(tmp_path):
    lab = _copy_lab(tmp_path, "function_lab")
    bundle, chunks = _chunks_for(lab, "math_lab.py")
    names = {chunk["name"] for chunk in chunks if chunk["type"] == "function_definition"}
    assert bundle.language == "python"
    assert bundle.path.name == "math_lab.py"
    assert names == {"double", "classify"}
    classify = next(chunk for chunk in chunks if chunk["name"] == "classify")
    assert "value >= 0" in classify["code"]
    assert all(chunk["type"] != "module_body" for chunk in chunks)


def test_script_lab_produces_module_body_matching_executable_statements(tmp_path):
    lab = _copy_lab(tmp_path, "script_lab")
    # Exact text, line endings included: Git can check fixtures out with
    # Windows line endings, which read_text() would turn into "\n".
    source = (lab / "grade_script.py").read_bytes().decode("utf-8")
    bundle, chunks = _chunks_for(lab, "grade_script.py")
    bodies = [chunk for chunk in chunks if chunk["type"] == "module_body"]
    assert bundle.path.name == "grade_script.py"
    assert len(bodies) == 1
    assert "score = int(input())" in bodies[0]["code"]
    assert "print(\"PASS\")" in bodies[0]["code"]
    assert bodies[0]["code"] in source
    assert all(chunk["type"] != "function_definition" for chunk in chunks)


def test_multi_lab_keeps_per_file_paths(tmp_path):
    lab = _copy_lab(tmp_path, "multi_lab")
    bundles = parse_dir(str(lab))
    names = {bundle.path.name for bundle in bundles}
    assert names == {"tax.py", "rates.py"}
    tax_chunks = get_chunks(next(bundle for bundle in bundles if bundle.path.name == "tax.py"))
    assert any(chunk["name"] == "gst_on" for chunk in tax_chunks)
    assert any(chunk["file"].endswith("tax.py") for chunk in tax_chunks)


def test_whitespace_file_does_not_fabricate_meaningful_chunks(tmp_path):
    lab = _copy_lab(tmp_path, "empty_lab")
    bundle, chunks = _chunks_for(lab, "blank.py")
    assert bundle.path.name == "blank.py"
    meaningful = [
        chunk
        for chunk in chunks
        if chunk["type"] in {"function_definition", "module_body"}
        and chunk["code"].strip()
    ]
    assert meaningful == []
