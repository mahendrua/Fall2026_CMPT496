import asyncio
from collections import deque
from pathlib import Path

import pytest

from agent.file_summary_agent import FileSummaryAgent
from agent.structured_output.file_summary_output import FileSummaryOutput

TEST_CODEBASE = Path(__file__).parent / "TestCodebase"
# Circles/circ.py is empty, so it is summarized without an AI call.
SOURCE_FILES = {"circ.py", "math.py", "math2.py", "shapes.py", "shapes2.py"}


class FakeStructuredLLM:
    """Answers every file with the same structured summary and counts the calls."""

    def __init__(self):
        self.calls = 0

    async def ainvoke(self, messages):
        self.calls += 1
        return FileSummaryOutput(path="path written by the AI", summary="A summary.")


class FakeChatModel:
    def __init__(self):
        self.structured = FakeStructuredLLM()

    def with_structured_output(self, schema, **kwargs):
        return self.structured


@pytest.fixture
def file_summary_agent():
    return FileSummaryAgent(llm=FakeChatModel())


def test_crawler_node(file_summary_agent):
    initial_state = {
        "directory_path": str(TEST_CODEBASE),
        "files": deque()
    }

    result = file_summary_agent.crawler_node(initial_state)

    # The .txt files and the bin/ and obj/ folders are skipped.
    assert {Path(path).name for path in result["files"]} == SOURCE_FILES
    assert result["total_number_of_files"] == len(SOURCE_FILES)
    assert result["codebase_name"] == "TestCodebase"


def test_summarizer_node(file_summary_agent):
    files = sorted(str(path) for path in TEST_CODEBASE.rglob("*.py"))
    state = {"files": deque(files)}

    # run() sets up this event loop for the summarizer's batched calls.
    file_summary_agent._loop = asyncio.new_event_loop()
    try:
        first = file_summary_agent.summarizer_node(state)
        second = file_summary_agent.summarizer_node(state)
    finally:
        file_summary_agent._loop.close()

    # Files are summarized in batches of four.
    assert len(first["current_files"]) == 4
    assert len(second["current_files"]) == 1
    summaries = first["file_summaries"] + second["file_summaries"]
    # Each summary keeps the real file path, not the path the AI wrote back.
    assert sorted(summary.path for summary in summaries) == files
    texts = {Path(summary.path).name: summary.summary for summary in summaries}
    assert texts.pop("circ.py") == "Empty source file."
    assert set(texts.values()) == {"A summary."}
    assert file_summary_agent.structured_llm.calls == 4


def test_full_graph(file_summary_agent, tmp_path, monkeypatch):
    # The agent writes under ./agent/file_summary_agent_output, so run it from
    # a temporary folder rather than the project.
    monkeypatch.chdir(tmp_path)

    file_summary_agent.run(str(TEST_CODEBASE))

    output_dir = tmp_path / "agent" / "file_summary_agent_output" / "TestCodebase"
    written = {path.name for path in output_dir.glob("*.json")}
    expected = {name.replace(".", "-") + "__1.json" for name in SOURCE_FILES}
    assert expected <= written
