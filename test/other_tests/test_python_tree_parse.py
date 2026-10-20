from pathlib import Path

from utils.tree_parse import get_chunks, parse_dir


FIXTURE_DIR = (
    Path(__file__).parent.parent / "fixtures" / "python_parser"
)


def test_python_functions_are_chunked():
    bundles = parse_dir(str(FIXTURE_DIR))

    sample_bundle = next(
        bundle
        for bundle in bundles
        if bundle.path.name == "sample_lab.py"
    )

    assert sample_bundle.language == "python"

    chunks = get_chunks(sample_bundle)
    chunks_by_name = {chunk["name"]: chunk for chunk in chunks}

    assert set(chunks_by_name) == {"calculate_average", "is_passing"}

    assert chunks_by_name["calculate_average"]["container"] == "Global"
    assert chunks_by_name["is_passing"]["container"] == "GradeBook"

    imports = chunks_by_name["calculate_average"]["imports"]
    assert "import math" in imports
    assert "from pathlib import Path" in imports

def test_decorated_and_async_python_functions_are_chunked():
    bundles = parse_dir(str(FIXTURE_DIR / "advanced"))

    assert len(bundles) == 1

    chunks = get_chunks(bundles[0])
    chunk_names = {chunk["name"] for chunk in chunks}

    assert {"log_call", "wrapper", "fetch_grade"} <= chunk_names

    imports = chunks[0]["imports"]
    assert "import asyncio" in imports
    assert "from functools import wraps" in imports


def test_python_syntax_errors_are_skipped():
    bundles = parse_dir(str(FIXTURE_DIR / "invalid"))

    assert len(bundles) == 1
    assert bundles[0].tree.root_node.has_error is True
    assert get_chunks(bundles[0]) == []

def test_python_files_in_ignored_directories_are_skipped(tmp_path):
    lab_directory = tmp_path / "lab"
    lab_directory.mkdir()

    source_file = lab_directory / "main.py"
    source_file.write_text(
        "def visible_function():\n"
        "    return True\n",
        encoding="utf-8",
    )

    virtual_environment = lab_directory / ".venv"
    virtual_environment.mkdir()

    ignored_file = virtual_environment / "generated.py"
    ignored_file.write_text(
        "def ignored_function():\n"
        "    return False\n",
        encoding="utf-8",
    )

    bundles = parse_dir(str(lab_directory))
    parsed_names = {bundle.path.name for bundle in bundles}

    assert parsed_names == {"main.py"}