from pathlib import Path

import tree_sitter_language_pack as tslp
from tree_sitter import Parser

from utils.tree_parse import (
    LANGUAGE_DICT,
    MODULE_BODY_MAX_CHARS,
    MODULE_BODY_MAX_LINES,
    get_chunks,
    parse_dir,
    parse_file,
)


def _write_and_chunk(path: Path, source: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    bundles = parse_dir(str(path.parent))
    bundle = next(item for item in bundles if item.path.resolve() == path.resolve())
    return get_chunks(bundle)


def _module_chunks(chunks):
    return [chunk for chunk in chunks if chunk["type"] == "module_body"]


def _function_chunks(chunks):
    return [chunk for chunk in chunks if chunk["type"] == "function_definition"]


FIXTURE_DIR = (
    Path(__file__).parent.parent / "fixtures" / "python_parser"
)


def _assert_no_parser_diagnostics_on_stdout(captured):
    for marker in ("Parsing:", "Skipping", "without truncating"):
        assert marker not in captured.out


def test_parse_file_does_not_write_parsing_progress_to_stdout(capsys):
    path = FIXTURE_DIR / "sample_lab.py"
    parser = Parser(tslp.get_language(LANGUAGE_DICT[".py"]["name"]))

    bundle = parse_file(path, parser, LANGUAGE_DICT[".py"]["name"])

    assert bundle.language == "python"
    captured = capsys.readouterr()
    _assert_no_parser_diagnostics_on_stdout(captured)
    assert "Parsing:" in captured.err


def test_python_functions_are_chunked(capsys):
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
    assert _module_chunks(chunks) == []

    assert chunks_by_name["calculate_average"]["container"] == "Global"
    assert chunks_by_name["is_passing"]["container"] == "GradeBook"

    imports = chunks_by_name["calculate_average"]["imports"]
    assert "import math" in imports
    assert "from pathlib import Path" in imports
    _assert_no_parser_diagnostics_on_stdout(capsys.readouterr())

def test_decorated_and_async_python_functions_are_chunked():
    bundles = parse_dir(str(FIXTURE_DIR / "advanced"))

    assert len(bundles) == 1

    chunks = get_chunks(bundles[0])
    chunk_names = {chunk["name"] for chunk in chunks}

    assert {"log_call", "wrapper", "fetch_grade"} <= chunk_names
    assert _module_chunks(chunks) == []

    imports = chunks[0]["imports"]
    assert "import asyncio" in imports
    assert "from functools import wraps" in imports


def test_python_syntax_errors_are_skipped(capsys):
    bundles = parse_dir(str(FIXTURE_DIR / "invalid"))

    assert len(bundles) == 1
    assert bundles[0].tree.root_node.has_error is True
    assert get_chunks(bundles[0]) == []
    captured = capsys.readouterr()
    _assert_no_parser_diagnostics_on_stdout(captured)
    assert "Skipping" in captured.err
    assert "syntax errors" in captured.err

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
    assert _module_chunks(get_chunks(bundles[0])) == []


def test_script_only_input_print_produces_module_body(tmp_path):
    chunks = _write_and_chunk(
        tmp_path / "temp.py",
        "celsius = float(input('C: '))\nprint(celsius * 9 / 5 + 32)\n",
    )
    bodies = _module_chunks(chunks)
    assert len(bodies) == 1
    body = bodies[0]
    assert body["name"] == "__module__"
    assert body["container"] == "Global"
    assert body["language"] == "python"
    assert body["type"] == "module_body"
    assert body["start_line"] == 1
    assert body["end_line"] == 2
    assert "input(" in body["code"]
    assert "print(" in body["code"]
    assert _function_chunks(chunks) == []


def test_module_level_if_else_and_loops(tmp_path):
    source = (
        "value = 3\n"
        "if value > 0:\n"
        "    sign = 'pos'\n"
        "else:\n"
        "    sign = 'neg'\n"
        "for item in range(value):\n"
        "    total = item\n"
        "while False:\n"
        "    break\n"
    )
    chunks = _write_and_chunk(tmp_path / "control.py", source)
    bodies = _module_chunks(chunks)
    assert len(bodies) == 1
    assert "if value > 0:" in bodies[0]["code"]
    assert "for item in range(value):" in bodies[0]["code"]
    assert "while False:" in bodies[0]["code"]
    assert bodies[0]["start_line"] == 1
    assert bodies[0]["end_line"] == 9


def test_main_guard_and_separate_main_function(tmp_path):
    source = (
        "def main():\n"
        "    print('hi')\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )
    chunks = _write_and_chunk(tmp_path / "app.py", source)
    functions = _function_chunks(chunks)
    bodies = _module_chunks(chunks)
    assert [chunk["name"] for chunk in functions] == ["main"]
    assert len(bodies) == 1
    assert "if __name__ == '__main__':" in bodies[0]["code"]
    assert "def main" not in bodies[0]["code"]
    assert bodies[0]["start_line"] == 4
    assert bodies[0]["end_line"] == 5


def test_mixed_functions_and_module_statements(tmp_path):
    source = (
        "SCALE = 2\n"
        "def double(value):\n"
        "    return value * SCALE\n"
        "print(double(3))\n"
    )
    chunks = _write_and_chunk(tmp_path / "mixed.py", source)
    assert [chunk["name"] for chunk in _function_chunks(chunks)] == ["double"]
    bodies = _module_chunks(chunks)
    assert len(bodies) == 2
    assert bodies[0]["code"].strip() == "SCALE = 2"
    assert bodies[0]["start_line"] == 1
    assert bodies[0]["end_line"] == 1
    assert "print(double(3))" in bodies[1]["code"]
    assert bodies[1]["start_line"] == 4
    assert bodies[1]["end_line"] == 4


def test_imports_are_metadata_not_module_body(tmp_path):
    source = (
        "import math\n"
        "from pathlib import Path\n"
        "print(math.pi)\n"
    )
    chunks = _write_and_chunk(tmp_path / "imports.py", source)
    bodies = _module_chunks(chunks)
    assert len(bodies) == 1
    assert "import math" not in bodies[0]["code"]
    assert "from pathlib import Path" not in bodies[0]["code"]
    assert "print(math.pi)" in bodies[0]["code"]
    assert "import math" in bodies[0]["imports"]
    assert "from pathlib import Path" in bodies[0]["imports"]


def test_module_bodies_preserve_file_metadata_and_source_order(tmp_path):
    source = "first = 1\n\ndef hidden():\n    return 2\n\nsecond = 3\n"
    chunks = _write_and_chunk(tmp_path / "order.py", source)
    bodies = _module_chunks(chunks)
    assert [body["code"].strip() for body in bodies] == ["first = 1", "second = 3"]
    assert bodies[0]["start_line"] == 1
    assert bodies[1]["start_line"] == 6
    assert all(body["file"].endswith("order.py") for body in bodies)
    assert all(body["namespace"] == "N/A" for body in bodies)


def test_nonconsecutive_module_statements_do_not_span_defs(tmp_path):
    source = (
        "start = 0\n"
        "def middle():\n"
        "    return 1\n"
        "end = 2\n"
    )
    chunks = _write_and_chunk(tmp_path / "gap.py", source)
    bodies = _module_chunks(chunks)
    assert len(bodies) == 2
    assert "def middle" not in bodies[0]["code"]
    assert "def middle" not in bodies[1]["code"]
    assert bodies[0]["end_line"] == 1
    assert bodies[1]["start_line"] == 4


def test_size_boundary_splits_into_multiple_module_bodies(tmp_path):
    statements = [f"value_{index} = {index}" for index in range(MODULE_BODY_MAX_LINES + 5)]
    chunks = _write_and_chunk(tmp_path / "long.py", "\n".join(statements) + "\n")
    bodies = _module_chunks(chunks)
    assert len(bodies) >= 2
    assert bodies[0]["name"] == "__module__"
    assert bodies[1]["name"] == "__module__#2"
    assert bodies[0]["end_line"] - bodies[0]["start_line"] + 1 <= MODULE_BODY_MAX_LINES
    joined = "\n".join(body["code"] for body in bodies)
    assert "value_0 = 0" in joined
    assert f"value_{MODULE_BODY_MAX_LINES + 4} = {MODULE_BODY_MAX_LINES + 4}" in joined


def test_oversized_single_statement_is_emitted_whole(tmp_path, capsys):
    inner = "\n".join(["    total += 1"] * (MODULE_BODY_MAX_LINES + 10))
    source = "total = 0\nwhile True:\n" + inner + "\n"
    chunks = _write_and_chunk(tmp_path / "huge.py", source)
    bodies = _module_chunks(chunks)
    while_bodies = [body for body in bodies if "while True:" in body["code"]]
    assert len(while_bodies) == 1
    assert while_bodies[0]["end_line"] - while_bodies[0]["start_line"] + 1 > MODULE_BODY_MAX_LINES
    assert while_bodies[0]["code"].startswith("while True:")
    assert while_bodies[0]["code"].count("total += 1") == MODULE_BODY_MAX_LINES + 10
    captured = capsys.readouterr()
    _assert_no_parser_diagnostics_on_stdout(captured)
    assert "without truncating" in captured.err


def test_oversized_single_assignment_respects_character_bound_without_truncation(tmp_path, capsys):
    payload = "a" * (MODULE_BODY_MAX_CHARS + 50)
    source = f"label = '{payload}'\n"
    chunks = _write_and_chunk(tmp_path / "wide.py", source)
    bodies = _module_chunks(chunks)
    assert len(bodies) == 1
    assert payload in bodies[0]["code"]
    assert len(bodies[0]["code"]) > MODULE_BODY_MAX_CHARS
    captured = capsys.readouterr()
    _assert_no_parser_diagnostics_on_stdout(captured)
    assert "without truncating" in captured.err


def test_paths_with_spaces_are_preserved(tmp_path):
    path = tmp_path / "student lab" / "temp converter.py"
    chunks = _write_and_chunk(path, "print(32)\n")
    assert len(chunks) == 1
    assert chunks[0]["file"] == str(path)
    assert "student lab" in chunks[0]["file"]
    assert "temp converter.py" in chunks[0]["file"]


def test_parsing_does_not_execute_source(tmp_path):
    marker = tmp_path / "side_effect.ran"
    source = (
        "from pathlib import Path\n"
        f"Path(r'{marker}').write_text('executed', encoding='utf-8')\n"
        "print(1)\n"
    )
    chunks = _write_and_chunk(tmp_path / "sentinel.py", source)
    assert _module_chunks(chunks)
    assert not marker.exists()


def test_csharp_chunking_is_unchanged(tmp_path):
    path = tmp_path / "Widget.cs"
    path.write_text(
        "namespace Demo {\n"
        "  public class Widget {\n"
        "    public int Size { get; set; }\n"
        "    public int Area() { return Size; }\n"
        "  }\n"
        "}\n",
        encoding="utf-8",
    )
    parser = Parser(tslp.get_language(LANGUAGE_DICT[".cs"]["name"]))
    chunks = get_chunks(parse_file(path, parser, LANGUAGE_DICT[".cs"]["name"]))
    types = {chunk["type"] for chunk in chunks}
    names = {chunk["name"] for chunk in chunks}
    assert "module_body" not in types
    assert "Area" in names
    assert "function_definition" not in types


def test_javascript_chunking_is_unchanged(tmp_path):
    path = tmp_path / "util.js"
    path.write_text("function add(a, b) {\n  return a + b;\n}\n", encoding="utf-8")
    parser = Parser(tslp.get_language(LANGUAGE_DICT[".js"]["name"]))
    chunks = get_chunks(parse_file(path, parser, LANGUAGE_DICT[".js"]["name"]))
    assert [chunk["name"] for chunk in chunks] == ["add"]
    assert all(chunk["type"] != "module_body" for chunk in chunks)