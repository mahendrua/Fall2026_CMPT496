import asyncio
import json
from pathlib import Path

from agent.file_summary_agent import (
    FileSummaryAgent,
    _source_text_is_blank,
    _summarize_one,
)
from agent.structured_output.file_summary_output import FileSummaryOutput
from src.build_database_JSON import records_from_file_summary


class BoomStructuredLLM:
    async def ainvoke(self, messages):
        raise AssertionError("LLM should not be called")


class StubChatModel:
    def with_structured_output(self, schema, **kwargs):
        return BoomStructuredLLM()


def summarize(path: Path):
    return asyncio.run(_summarize_one(BoomStructuredLLM(), str(path)))


def assert_empty_declarations(summary: FileSummaryOutput, file_path: Path):
    assert summary.path == str(file_path)
    assert summary.summary == "Empty source file."
    assert summary.dependencies == []
    assert summary.functions == []
    assert summary.types == []
    assert summary.relationships == []
    assert summary.external_relationships == []
    assert summary.relationship_plantuml == ""
    assert summary.business_rules == []
    dumped = summary.model_dump_json()
    assert "Circle" not in dumped


def test_strip_does_not_remove_utf8_bom():
    assert "\ufeff".strip() != ""
    assert _source_text_is_blank("\ufeff")
    assert _source_text_is_blank("\ufeff \n")
    assert not _source_text_is_blank("\ufeffprint(1)\n")


def test_empty_python_file_skips_llm(tmp_path):
    source = tmp_path / "circ.py"
    source.write_bytes(b"")

    file_path, output, err = summarize(source)

    assert err is None
    assert file_path == str(source)
    assert_empty_declarations(output, source)


def test_whitespace_only_python_file_skips_llm(tmp_path):
    source = tmp_path / "blank.py"
    source.write_text(" \n\t\n", encoding="utf-8")

    file_path, output, err = summarize(source)

    assert err is None
    assert_empty_declarations(output, source)


def test_bom_only_file_skips_llm(tmp_path):
    source = tmp_path / "bom_only.py"
    source.write_bytes(b"\xef\xbb\xbf")

    file_path, output, err = summarize(source)

    assert err is None
    assert_empty_declarations(output, source)


def test_empty_csharp_file_skips_llm(tmp_path):
    source = tmp_path / "Calculator.cs"
    source.write_bytes(b"")

    file_path, output, err = summarize(source)

    assert err is None
    assert_empty_declarations(output, source)


def assert_llm_was_invoked(path: Path):
    file_path, output, err = summarize(path)
    assert file_path == str(path)
    assert output is None
    assert isinstance(err, AssertionError)
    assert "LLM should not be called" in str(err)


def test_module_level_script_invokes_llm(tmp_path):
    source = tmp_path / "script.py"
    source.write_text("VALUE = 2\nprint(VALUE)\n", encoding="utf-8")
    assert_llm_was_invoked(source)


def test_real_function_invokes_llm(tmp_path):
    source = tmp_path / "math_lab.py"
    source.write_text(
        "def calculate_average(values):\n"
        "    return sum(values) / len(values)\n",
        encoding="utf-8",
    )
    assert_llm_was_invoked(source)


def test_comments_only_file_invokes_llm(tmp_path):
    source = tmp_path / "notes.py"
    source.write_text("# placeholder\n", encoding="utf-8")
    assert_llm_was_invoked(source)


def test_unreadable_file_keeps_error_path(tmp_path):
    missing = tmp_path / "missing.py"

    file_path, output, err = summarize(missing)

    assert file_path == str(missing)
    assert output is None
    assert err is not None
    assert isinstance(err, FileNotFoundError)


def test_writer_and_business_rules_omit_invented_circle(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "circ.py"
    source.write_bytes(b"")
    _, summary, err = summarize(source)
    assert err is None

    agent = FileSummaryAgent(llm=StubChatModel())
    agent.write_file_summary_node(
        {
            "codebase_name": "empty_lab",
            "filename_counters": {},
            "file_summaries": [summary],
        }
    )

    written = Path("agent/file_summary_agent_output/empty_lab/circ-py__1.json")
    assert written.is_file()
    data = json.loads(written.read_text(encoding="utf-8"))
    assert data["path"] == str(source)
    assert data["functions"] == []
    assert data["types"] == []
    assert data["dependencies"] == []
    assert data["relationships"] == []
    assert data["external_relationships"] == []
    assert data["business_rules"] == []
    assert "Circle" not in written.read_text(encoding="utf-8")

    batch_rules = {}
    if summary.business_rules:
        batch_rules[summary.path] = summary.business_rules

    agent.write_business_rules_node(
        {
            "codebase_name": "empty_lab",
            "business_rules_by_file": batch_rules,
        }
    )
    rules_path = Path(
        "agent/file_summary_agent_output/empty_lab/business_rules/business_rules.json"
    )
    rules = json.loads(rules_path.read_text(encoding="utf-8"))
    assert str(source) not in rules
    assert not any("circ.py" in key for key in rules)


def test_production_indexer_records_have_no_circle_nodes(tmp_path):
    source = tmp_path / "circ.py"
    source.write_bytes(b"")
    _, summary, err = summarize(source)
    assert err is None

    ids, documents, metadatas = records_from_file_summary(summary.model_dump())

    assert ids == [f"file:{source}"]
    assert all(not item_id.startswith(("type:", "func:", "method:", "property:")) for item_id in ids)
    assert all("Circle" not in item_id for item_id in ids)
    assert all("Circle" not in document for document in documents)
    assert metadatas == [{"type": "file", "path": str(source)}]
