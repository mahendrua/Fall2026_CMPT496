from pathlib import Path

from src.build_database import empty_code_index_message
from utils.tree_parse import LANGUAGE_DICT


def test_empty_index_message_when_no_supported_files():
    source_dir = Path("/labs/empty")
    message = empty_code_index_message(source_dir, parsed_bundle_count=0)
    assert "No code files detected" in message
    assert str(source_dir) in message
    for extension in LANGUAGE_DICT:
        assert extension in message
    assert message.endswith("Supported code files are .cs, .js, .py.")


def test_empty_index_message_when_files_have_no_chunks():
    source_dir = Path("/labs/comments_only")
    message = empty_code_index_message(source_dir, parsed_bundle_count=2)
    assert "No indexable code chunks found" in message
    assert str(source_dir) in message
    assert "module-level statements" in message
    assert "No code files detected" not in message
