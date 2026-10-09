"""!
@file run_report_diagram_test.py
@brief Tests for backend/run_report_diagram.py, the whole-project class diagram (US-049, T-166).
@details The file entries are shaped like run_report_data's output. Drawing the
picture needs Java and plantuml.jar; that one test is skipped without them.
"""

import shutil
from pathlib import Path

import pytest

import backend.run_report_diagram as diagram
from backend.run_report_diagram import build_diagram, find_plantuml_jar, project_types, to_plantuml

APP_DIR = Path(__file__).resolve().parents[2]


def file(path, types, relationships=(), external=()):
    return {
        "path": path,
        "folder": path.rsplit("/", 1)[0] if "/" in path else ".",
        "types": [dict({"description": "", "inherits_from": [], "enum_values": [], "properties": [], "methods": []}, **t)
                  for t in types],
        "relationships": list(relationships),
        "external_relationships": list(external),
    }


def link(source, target, kind="association"):
    return {"source": source, "target": target, "relationship_type": kind}


FILES = [
    # One class split over two files in the same folder.
    file("app/orders/order.py", [{"name": "Order", "kind": "class", "methods": [{"name": "total"}]}],
         external=[link("Order", "Customer"), link("Order", "Exception", "inheritance")]),
    file("app/orders/order_io.py", [{"name": "Order", "kind": "class", "methods": [{"name": "save"}]}]),
    file("app/people/customer.py", [{"name": "Customer", "kind": "class", "inherits_from": ["Person"]},
                                    {"name": "Person", "kind": "interface"}]),
    # Same class name as in app/people, but a different class.
    file("tools/customer.py", [{"name": "Customer", "kind": "class"}]),
    file("app/orders/status.py", [{"name": "Status", "kind": "enum", "enum_values": ["NEW", "PAID"]}]),
]


def ids(types):
    return {(t["folder"], t["name"]): t["id"] for t in types}


def test_partial_classes_merge_and_same_names_stay_apart():
    types, _, _ = project_types(FILES)
    names = sorted((t["folder"], t["name"]) for t in types)

    assert names == [("app/orders", "Order"), ("app/orders", "Status"), ("app/people", "Customer"),
                     ("app/people", "Person"), ("tools", "Customer")]
    order = next(t for t in types if t["name"] == "Order")
    assert order["files"] == ["app/orders/order.py", "app/orders/order_io.py"]
    assert [m["name"] for m in order["methods"]] == ["total", "save"]


def test_links_between_project_classes_only():
    types, edges, skipped = project_types(FILES)
    id_of = ids(types)

    # Customer exists in two folders, neither of them app/orders: too unclear to draw.
    # Exception is a library class: not drawn either.
    assert skipped == 2
    assert {"source": id_of[("app/people", "Customer")], "target": id_of[("app/people", "Person")],
            "kind": "implements"} in edges
    assert len(edges) == 1


def test_link_prefers_the_class_in_the_same_folder():
    files = FILES + [file("tools/report.py", [{"name": "Report", "kind": "class"}], external=[link("Report", "Customer")])]
    types, edges, _ = project_types(files)
    id_of = ids(types)

    assert {"source": id_of[("tools", "Report")], "target": id_of[("tools", "Customer")], "kind": "association"} in edges


def test_plantuml_text_is_safe_and_grouped_by_folder():
    files = [file("src/x.ts", [{"name": 'Weird"Name]]\n!include evil', "kind": "class"}, {"name": "Box<T>", "kind": "struct"}])]
    types, edges, _ = project_types(files)
    puml = to_plantuml(types, edges, "demo")

    assert 'package "src" as P1 {' in puml
    assert 'class "Weird Name   !include evil" as T1 [[#type-T1]]' in puml
    assert 'struct "Box<T>" as T2 [[#type-T2]]' in puml
    assert "\n!include" not in puml
    assert "left to right direction" in to_plantuml(types, edges, "demo", left_to_right=True)


def fake_render(sizes):
    """A render_svg stand-in whose picture size depends on the layout asked for."""
    def render(puml, jar):
        layout = "elk" if "!pragma layout elk" in puml else "lr" if "left to right" in puml else "tb"
        if sizes[layout] is None:
            raise RuntimeError("PlantUML could not draw the diagram")
        width, height = sizes[layout]
        return f'<svg viewBox="0 0 {width} {height}"></svg>'
    return render


def test_a_badly_shaped_picture_is_redrawn_with_another_layout(tmp_path, monkeypatch):
    monkeypatch.setattr(diagram, "find_plantuml_jar", lambda app_dir: tmp_path / "plantuml.jar")
    monkeypatch.setattr(diagram, "render_svg", fake_render({"tb": (9000, 300), "lr": (300, 9000), "elk": (1600, 1000)}))

    result = build_diagram({"codebase": "demo", "files": FILES}, tmp_path)

    assert result["svg"] == '<svg viewBox="0 0 1600 1000"></svg>'
    assert "!pragma layout elk" in result["puml"]  # the saved text matches the picture


def test_a_well_shaped_picture_is_kept_and_a_failed_retry_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr(diagram, "find_plantuml_jar", lambda app_dir: tmp_path / "plantuml.jar")

    monkeypatch.setattr(diagram, "render_svg", fake_render({"tb": (1500, 1000), "lr": None, "elk": None}))
    assert build_diagram({"codebase": "demo", "files": FILES}, tmp_path)["svg"] == '<svg viewBox="0 0 1500 1000"></svg>'

    monkeypatch.setattr(diagram, "render_svg", fake_render({"tb": (9000, 300), "lr": None, "elk": None}))
    result = build_diagram({"codebase": "demo", "files": FILES}, tmp_path)
    assert result["svg"] == '<svg viewBox="0 0 9000 300"></svg>' and result["error"] == ""


def test_no_jar_gives_a_reason_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(diagram, "find_plantuml_jar", lambda app_dir: None)
    result = build_diagram({"codebase": "demo", "files": FILES}, tmp_path)

    assert result["svg"] == ""
    assert "plantuml.jar was not found" in result["error"]
    assert len(result["types"]) == 5 and result["puml"].startswith("@startuml")


def test_no_classes_gives_a_reason(tmp_path):
    result = build_diagram({"codebase": "demo", "files": [file("notes.md", [])]}, tmp_path)
    assert result["svg"] == "" and "no classes" in result["error"]


@pytest.mark.skipif(shutil.which("java") is None or find_plantuml_jar(APP_DIR) is None,
                    reason="needs Java and plantuml.jar")
def test_draws_svg_with_a_link_per_class():
    result = build_diagram({"codebase": "demo", "files": FILES}, APP_DIR)

    assert result["error"] == ""
    assert result["svg"].startswith("<svg")
    for t in result["types"]:
        assert f'href="#type-{t["id"]}"' in result["svg"]
