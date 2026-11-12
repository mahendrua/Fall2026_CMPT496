import json
from pathlib import Path

import pytest

from agent.BR_input_loader import load_rule_candidates
from agent.directory_agent import DirectoryAgent
from agent.file_summary_grounding import filter_directory_business_rules_against_source
from agent.structured_output.directory_output import BusinessRulesOutput, DirectoryOutput
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage


INVENTED_AREA = "Circle.area() computes the covered region across files."
DECLARED_AREA = "Circle.area() uses the shared radius from the input file."
CROSS_FILE = "Order.submit() requires Account.verify() before checkout."
PROSE_RULE = "Orders over 100 require manager approval."
INFERRED_AREA = "Inference: Circle.area() is required for exported reports."


class FakeStructuredLLM:
    def __init__(self, output):
        self.output = output

    def invoke(self, messages):
        return self.output


class FakeLLM:
    def __init__(self, output):
        self.output = output

    def with_structured_output(self, schema):
        return FakeStructuredLLM(self.output)


class FakeSummaryCollection:
    def __init__(self, documents, metadatas):
        self._documents = documents
        self._metadatas = metadatas

    def get(self, include=None):
        return {"documents": list(self._documents), "metadatas": list(self._metadatas)}


def rules_output(observed=(), inferred=(), directory_name="shapes", directory_path="shapes"):
    return BusinessRulesOutput(
        directory_name=directory_name,
        directory_path=directory_path,
        observed_rules=list(observed),
        inferred_rules=list(inferred),
    )


def write_py(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def filter_dir(directory: Path, observed=(), inferred=()):
    return filter_directory_business_rules_against_source(
        rules_output(observed, inferred, directory.name, directory.name),
        str(directory),
        str(directory),
    )


def test_invented_circle_area_is_dropped(tmp_path, capsys):
    write_py(tmp_path / "circle.py", "class Circle:\n    pass\n")
    write_py(tmp_path / "render.py", "def render():\n    return True\n")
    result = filter_dir(tmp_path, observed=[INVENTED_AREA, PROSE_RULE], inferred=[INFERRED_AREA])
    assert INVENTED_AREA not in result.observed_rules
    assert PROSE_RULE in result.observed_rules
    assert INFERRED_AREA not in result.inferred_rules
    assert "Dropped ungrounded G2" in capsys.readouterr().out


def test_declared_circle_area_is_kept(tmp_path):
    write_py(
        tmp_path / "circle.py",
        "class Circle:\n    def area(self):\n        return 1\n",
    )
    write_py(tmp_path / "render.py", "def render():\n    return True\n")
    result = filter_dir(tmp_path, observed=[DECLARED_AREA])
    assert result.observed_rules == [DECLARED_AREA]


def test_two_circles_one_with_area_is_kept(tmp_path):
    write_py(tmp_path / "a.py", "class Circle:\n    pass\n")
    write_py(
        tmp_path / "b.py",
        "class Circle:\n    def area(self):\n        return 1\n",
    )
    result = filter_dir(tmp_path, observed=[INVENTED_AREA])
    assert result.observed_rules == [INVENTED_AREA]


def test_two_closed_circles_without_area_are_dropped(tmp_path):
    write_py(tmp_path / "a.py", "class Circle:\n    pass\n")
    write_py(tmp_path / "b.py", "class Circle:\n    def radius(self):\n        return 1\n")
    result = filter_dir(tmp_path, observed=[INVENTED_AREA])
    assert result.observed_rules == []


def test_inherited_and_dynamic_classes_are_kept(tmp_path):
    write_py(tmp_path / "inherited.py", "class Circle(Shape):\n    pass\n")
    write_py(
        tmp_path / "lookup.py",
        "class Widget:\n    def __getattr__(self, name):\n        return name\n",
    )
    result = filter_dir(
        tmp_path,
        observed=[
            "Circle.area() uses the inherited formula.",
            "Widget.missing() is resolved dynamically.",
        ],
    )
    assert len(result.observed_rules) == 2


def test_metaclass_and_exec_classes_are_kept(tmp_path):
    write_py(tmp_path / "meta.py", "class Circle(metaclass=Meta):\n    pass\n")
    write_py(
        tmp_path / "dyn.py",
        "class Box:\n    exec('def volume(self): return 1')\n",
    )
    result = filter_dir(
        tmp_path,
        observed=[
            "Circle.area() is injected by the metaclass.",
            "Box.volume() is created at class body time.",
        ],
    )
    assert len(result.observed_rules) == 2


def test_cross_file_order_and_account_calls_are_kept(tmp_path):
    write_py(
        tmp_path / "order.py",
        "class Order:\n    def submit(self):\n        return True\n",
    )
    write_py(
        tmp_path / "account.py",
        "class Account:\n    def verify(self):\n        return True\n",
    )
    result = filter_dir(tmp_path, observed=[CROSS_FILE])
    assert result.observed_rules == [CROSS_FILE]


@pytest.mark.parametrize("extra_name", ["script.sh", "script.bash", "page.html", "style.css"])
def test_python_mixed_with_accepted_non_python_sources_is_not_filtered(tmp_path, extra_name):
    write_py(tmp_path / "circle.py", "class Circle:\n    pass\n")
    (tmp_path / extra_name).write_text("/* extra source */\n", encoding="utf-8")
    result = filter_dir(tmp_path, observed=[INVENTED_AREA])
    assert result.observed_rules == [INVENTED_AREA]


def test_mixed_python_and_csharp_is_not_filtered(tmp_path):
    write_py(tmp_path / "circle.py", "class Circle:\n    pass\n")
    (tmp_path / "Circle.cs").write_text("class Circle {}", encoding="utf-8")
    result = filter_dir(tmp_path, observed=[INVENTED_AREA])
    assert result.observed_rules == [INVENTED_AREA]


def test_python_plus_markdown_still_filters_invented_calls(tmp_path):
    write_py(tmp_path / "circle.py", "class Circle:\n    pass\n")
    write_py(tmp_path / "render.py", "def render():\n    return True\n")
    (tmp_path / "README.md").write_text("# shapes\n", encoding="utf-8")
    result = filter_dir(tmp_path, observed=[INVENTED_AREA, PROSE_RULE])
    assert result.observed_rules == [PROSE_RULE]


def test_csharp_only_directory_is_not_filtered(tmp_path):
    (tmp_path / "Circle.cs").write_text(
        "class Circle { public double Area() => 1; }",
        encoding="utf-8",
    )
    (tmp_path / "Other.cs").write_text("class Other {}", encoding="utf-8")
    result = filter_dir(tmp_path, observed=[INVENTED_AREA])
    assert result.observed_rules == [INVENTED_AREA]


def test_nested_directory_does_not_use_sibling_classes(tmp_path):
    nested = tmp_path / "shapes"
    write_py(nested / "circle.py", "class Circle:\n    pass\n")
    write_py(nested / "draw.py", "def draw():\n    return True\n")
    write_py(
        tmp_path / "other.py",
        "class Circle:\n    def area(self):\n        return 1\n",
    )
    result = filter_dir(nested, observed=[INVENTED_AREA])
    assert result.observed_rules == []


def test_root_directory_includes_nested_python_files(tmp_path):
    write_py(tmp_path / "shapes" / "circle.py", "class Circle:\n    pass\n")
    write_py(tmp_path / "render.py", "def render():\n    return True\n")
    result = filter_dir(tmp_path, observed=[INVENTED_AREA])
    assert result.observed_rules == []


def test_ambiguous_nested_inner_is_kept_if_any_declares_method(tmp_path):
    write_py(
        tmp_path / "a.py",
        "class Outer:\n    class Inner:\n        def run(self):\n            return 1\n",
    )
    write_py(tmp_path / "b.py", "class Inner:\n    pass\n")
    result = filter_dir(tmp_path, observed=["Inner.run() executes the nested workflow."])
    assert result.observed_rules == ["Inner.run() executes the nested workflow."]


def test_module_qualified_owner_is_kept_when_unresolved(tmp_path):
    write_py(tmp_path / "circle.py", "class Circle:\n    pass\n")
    write_py(tmp_path / "render.py", "def render():\n    return True\n")
    rule = "shapes.Circle.area() uses the package formula."
    result = filter_dir(tmp_path, observed=[rule])
    assert result.observed_rules == [rule]


def test_syntax_error_file_fails_open(tmp_path, capsys):
    write_py(tmp_path / "circle.py", "class Circle:\n    pass\n")
    write_py(tmp_path / "broken.py", "def incomplete(\n")
    result = filter_dir(tmp_path, observed=[INVENTED_AREA])
    assert result.observed_rules == [INVENTED_AREA]
    assert "syntax errors" in capsys.readouterr().out


def test_unreadable_file_fails_open(tmp_path, monkeypatch, capsys):
    write_py(tmp_path / "circle.py", "class Circle:\n    pass\n")
    blocked = write_py(tmp_path / "secret.py", "class Secret:\n    pass\n")
    original = Path.read_text

    def fake_read(self, *args, **kwargs):
        if Path(self) == blocked:
            raise PermissionError("denied")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fake_read)
    result = filter_dir(tmp_path, observed=[INVENTED_AREA])
    assert result.observed_rules == [INVENTED_AREA]
    assert "could not be read" in capsys.readouterr().out


def test_traversal_failure_fails_open(tmp_path, monkeypatch, capsys):
    write_py(tmp_path / "circle.py", "class Circle:\n    pass\n")

    def boom(*args, **kwargs):
        raise OSError("walk failed")

    monkeypatch.setattr("agent.file_summary_grounding.os.walk", boom)
    result = filter_dir(tmp_path, observed=[INVENTED_AREA])
    assert result.observed_rules == [INVENTED_AREA]
    assert "directory scan failed" in capsys.readouterr().out


def test_missing_directory_fails_open(tmp_path):
    missing = tmp_path / "gone"
    result = filter_directory_business_rules_against_source(
        rules_output([INVENTED_AREA]),
        str(missing),
        str(tmp_path),
    )
    assert result.observed_rules == [INVENTED_AREA]


def test_prose_without_calls_is_kept(tmp_path):
    write_py(tmp_path / "circle.py", "class Circle:\n    pass\n")
    write_py(tmp_path / "policy.py", "THRESHOLD = 100\n")
    result = filter_dir(tmp_path, observed=[PROSE_RULE, "Circle.area is documented."])
    assert result.observed_rules == [PROSE_RULE, "Circle.area is documented."]


def _extraction_state(tmp_path, observed, inferred=()):
    write_py(tmp_path / "circle.py", "class Circle:\n    pass\n")
    write_py(tmp_path / "render.py", "def render():\n    return True\n")
    collection = FakeSummaryCollection(
        documents=["Circle summary", "Render summary"],
        metadatas=[
            {"path": str(tmp_path / "circle.py"), "type": "file", "name": "circle.py"},
            {"path": str(tmp_path / "render.py"), "type": "file", "name": "render.py"},
        ],
    )
    output = rules_output(observed, inferred, tmp_path.name, ".")
    agent = DirectoryAgent(model=FakeLLM(output))
    state = {
        "current_directory": str(tmp_path),
        "directory_path": str(tmp_path),
        "codebase_name": tmp_path.name,
        "directory_summary": DirectoryOutput(
            directory_name=tmp_path.name,
            directory_path=".",
            purpose="shapes",
        ),
        "summary_collection": collection,
        "accumulated_business_rules": {},
    }
    return agent, state


def test_business_rules_node_filters_before_accumulate(tmp_path):
    agent, state = _extraction_state(tmp_path, [INVENTED_AREA, PROSE_RULE], [INFERRED_AREA])
    result = agent.business_rules_node(state)
    output = result["accumulated_business_rules"][str(tmp_path)]
    assert output.observed_rules == [PROSE_RULE]
    assert output.inferred_rules == []
    assert output.directory_name == tmp_path.name
    assert output.directory_path in {".", str(tmp_path)}


def test_generic_fake_chat_model_still_skips_extraction(tmp_path):
    agent = DirectoryAgent(model=GenericFakeChatModel(messages=iter([AIMessage(content="{}")])))
    state = {
        "current_directory": str(tmp_path),
        "directory_path": str(tmp_path),
        "codebase_name": tmp_path.name,
        "directory_summary": DirectoryOutput(
            directory_name=tmp_path.name,
            directory_path=".",
            purpose="shapes",
        ),
        "summary_collection": FakeSummaryCollection([], []),
        "accumulated_business_rules": {},
    }
    result = agent.business_rules_node(state)
    output = result["accumulated_business_rules"][str(tmp_path)]
    assert output.observed_rules == []
    assert output.inferred_rules == []


def test_rollup_json_and_loader_see_only_kept_rules(tmp_path):
    agent, state = _extraction_state(tmp_path, [INVENTED_AREA, PROSE_RULE], [INFERRED_AREA])
    extracted = agent.business_rules_node(state)
    output_dir = tmp_path / "out"
    writer_state = {
        "output_directory": str(output_dir),
        "codebase_name": "MyCodebase",
        "accumulated_business_rules": extracted["accumulated_business_rules"],
    }
    agent.rollup_writer_node(writer_state)

    json_path = output_dir / "MyCodebase" / "business_rules" / "business_rules.json"
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    entry = next(iter(payload.values()))
    assert INVENTED_AREA not in entry["observed_rules"]
    assert PROSE_RULE in entry["observed_rules"]
    assert entry["inferred_rules"] == []

    codebase = tmp_path / "MyCodebase"
    codebase.mkdir()
    loaded = load_rule_candidates(
        codebase,
        False,
        json_path,
        app_dir=tmp_path,
    )
    assert [candidate.rule for candidate in loaded.candidates] == [PROSE_RULE]
    assert loaded.candidates[0].origin == "directory_observed"
    assert loaded.candidates[0].source_file_paths == []
