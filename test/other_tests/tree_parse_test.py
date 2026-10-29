"""!
@file tree_parse_test.py
@brief Tests for utils/tree_parse.py on a small made-up C# project.
@details The parser runs on a temporary folder, so no sample codebase is
needed. test_parse checks which files become bundles; test_chunk checks the
chunks: the constructor and method with their class, lines, language,
namespace and imports, and the properties grouped per class.
"""

from pathlib import Path

from utils.tree_parse import get_chunks, parse_dir

ORDER_CS = """using System;
using System.Collections.Generic;

namespace Shop.Orders
{
    public class Order
    {
        public string Customer { get; set; }
        public decimal Total { get; set; }

        public Order(string customer)
        {
            Customer = customer;
        }

        public bool IsEmpty()
        {
            return Total == 0;
        }
    }
}
"""


def write_project(tmp_path: Path) -> Path:
    """A codebase with one C# file, plus a build output and a text file to skip."""
    project = tmp_path / "Shop"
    (project / "src").mkdir(parents=True)
    (project / "bin").mkdir()
    (project / "src" / "Order.cs").write_bytes(ORDER_CS.encode("utf-8"))
    (project / "bin" / "Generated.cs").write_bytes(b"class Generated {}")
    (project / "notes.txt").write_bytes(b"not code")
    return project


def test_parse(tmp_path):
    bundles = parse_dir(str(write_project(tmp_path)))

    assert [bundle.path.name for bundle in bundles] == ["Order.cs"]
    (bundle,) = bundles
    assert bundle.language == "csharp"
    assert bundle.content == ORDER_CS.encode("utf-8")
    assert bundle.tree.root_node.type == "compilation_unit"


def test_chunk(tmp_path):
    (bundle,) = parse_dir(str(write_project(tmp_path)))
    chunks = {chunk["name"]: chunk for chunk in get_chunks(bundle)}

    assert set(chunks) == {"Order", "IsEmpty", "Order_Properties"}

    constructor, method = chunks["Order"], chunks["IsEmpty"]
    assert constructor["type"] == "constructor_declaration"
    assert (constructor["start_line"], constructor["end_line"]) == (11, 14)
    assert method["type"] == "method_declaration"
    assert (method["start_line"], method["end_line"]) == (16, 19)
    assert "return Total == 0;" in method["code"]

    properties = chunks["Order_Properties"]
    assert properties["type"] == "grouped_properties"
    assert "Customer" in properties["code"] and "Total" in properties["code"]

    for chunk in chunks.values():
        assert chunk["container"] == "Order"
        assert chunk["language"] == "csharp"
        assert chunk["namespace"] == "Shop.Orders"
        assert chunk["imports"] == "using System;\nusing System.Collections.Generic;"
        assert Path(chunk["file"]).name == "Order.cs"
