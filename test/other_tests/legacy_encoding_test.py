"""
@file legacy_encoding_test.py
@brief Offline tests that a source file saved in the older Windows-1252
encoding (common in legacy codebases) is parsed for the code database instead
of stopping the build, keeping its text, while UTF-8 files are read unchanged.
"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from utils.tree_parse import get_chunks, parse_dir, read_source_bytes

SOURCE = 'namespace Legacy { public class Invoice { public string Label() { return "Facture réglée"; } } }'


def test_windows_1252_source_file_is_parsed_with_its_text(tmp_path):
    (tmp_path / "Invoice.cs").write_bytes(SOURCE.encode("cp1252"))

    chunks = [chunk for bundle in parse_dir(str(tmp_path)) for chunk in get_chunks(bundle)]

    assert any("Facture réglée" in chunk["code"] for chunk in chunks)


def test_utf8_source_bytes_are_read_unchanged(tmp_path):
    path = tmp_path / "Invoice.cs"
    path.write_bytes(SOURCE.encode("utf-8"))

    assert read_source_bytes(path) == SOURCE.encode("utf-8")
