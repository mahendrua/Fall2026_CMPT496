import asyncio
from pathlib import Path

from agent.file_summary_agent import _summarize_one
from agent.file_summary_grounding import (
    collect_python_declarations,
    validate_file_summary_against_source,
)
from agent.structured_output.file_summary_output import (
    BusinessRule,
    FileSummaryOutput,
    FunctionSummary,
    MethodSummary,
    TypeSummary,
)
from src.build_database_JSON import records_from_file_summary


class FakeStructuredLLM:
    def __init__(self, output):
        self.output = output
        self.called = 0

    async def ainvoke(self, messages):
        self.called += 1
        return self.output


def method(name: str) -> MethodSummary:
    return MethodSummary(name=name, description=f"{name} method")


def function(name: str) -> FunctionSummary:
    return FunctionSummary(name=name, description=f"{name} function")


def type_summary(name: str, methods: list[str], plantuml: str = "class {name}") -> TypeSummary:
    return TypeSummary(
        name=name,
        kind="class",
        description=f"{name} type",
        methods=[method(item) for item in methods],
        plantuml=plantuml.format(name=name),
    )


def invented_circle_summary(path: str) -> FileSummaryOutput:
    return FileSummaryOutput(
        path=path,
        summary="Circle computes area and circumference.",
        types=[type_summary("Circle", ["area", "circumference"])],
        functions=[function("draw")],
        relationship_plantuml="@startuml\nclass Circle {\n+area()\n}\n@enduml",
        business_rules=[
            BusinessRule(rule="Circle.area returns pi r squared", source_file=path)
        ],
    )


def summarize(path: Path, output: FileSummaryOutput):
    llm = FakeStructuredLLM(output)
    return llm, asyncio.run(_summarize_one(llm, str(path)))


def test_collects_module_and_class_ownership():
    source = (
        "def calculate_average(values):\n"
        "    return sum(values) / len(values)\n"
        "\n"
        "class GradeBook:\n"
        "    def is_passing(self, grade):\n"
        "        def helper():\n"
        "            return True\n"
        "        return grade >= 50\n"
    )
    declarations = collect_python_declarations(source)
    assert declarations.parse_error is False
    assert declarations.module_functions == {"calculate_average"}
    assert declarations.classes == {"GradeBook": {"is_passing"}}


def test_nested_class_methods_are_not_owned_by_outer():
    source = (
        "class Outer:\n"
        "    def keep(self):\n"
        "        def local():\n"
        "            return 1\n"
        "        return local()\n"
        "    class Inner:\n"
        "        def inner_m(self):\n"
        "            return 2\n"
    )
    declarations = collect_python_declarations(source)
    assert declarations.classes["Outer"] == {"keep"}
    assert declarations.classes["Outer.Inner"] == {"inner_m"}
    assert "local" not in declarations.classes["Outer"]
    assert "local" not in declarations.module_functions


def test_circle_pass_drops_fabricated_methods_and_indexer_records(tmp_path, capsys):
    source = tmp_path / "circ.py"
    source.write_text("class Circle:\n    pass\n", encoding="utf-8")
    hallucinated = invented_circle_summary(str(source))

    llm, (file_path, output, err) = summarize(source, hallucinated)

    assert err is None
    assert llm.called == 1
    assert file_path == str(source)
    assert output.path == str(source)
    assert output.summary == "Circle computes area and circumference."
    assert [item.name for item in output.types] == ["Circle"]
    assert output.types[0].methods == []
    assert output.functions == []
    assert output.types[0].plantuml == "class Circle"
    assert output.relationship_plantuml.startswith("@startuml")
    assert output.business_rules[0].rule == "Circle.area returns pi r squared"
    captured = capsys.readouterr().out
    assert "Dropped ungrounded declarations" in captured
    assert "method Circle.area" in captured
    assert "method Circle.circumference" in captured

    ids, documents, metadatas = records_from_file_summary(output.model_dump())
    assert f"file:{source}" in ids
    assert f"type:{source}:class:Circle" in ids
    assert all(not item_id.startswith("method:") for item_id in ids)
    assert all("area" not in item_id and "circumference" not in item_id for item_id in ids)
    assert not any(document.startswith("Method '") for document in documents)


def test_valid_class_methods_and_module_functions_are_kept(tmp_path):
    source = tmp_path / "grades.py"
    source.write_text(
        "def calculate_average(values):\n"
        "    return sum(values) / len(values)\n"
        "\n"
        "class GradeBook:\n"
        "    def is_passing(self, grade):\n"
        "        return grade >= 50\n",
        encoding="utf-8",
    )
    llm_output = FileSummaryOutput(
        path=str(source),
        summary="Grade book helpers.",
        functions=[function("calculate_average"), function("invented_top_level")],
        types=[type_summary("GradeBook", ["is_passing", "curve"])],
    )

    _, (_, output, err) = summarize(source, llm_output)

    assert err is None
    assert [item.name for item in output.functions] == ["calculate_average"]
    assert [item.name for item in output.types[0].methods] == ["is_passing"]


def test_decorated_and_async_module_functions_are_kept(tmp_path):
    source = tmp_path / "async_lab.py"
    source.write_text(
        "from functools import wraps\n"
        "\n"
        "def log_call(function):\n"
        "    @wraps(function)\n"
        "    def wrapper(*args, **kwargs):\n"
        "        return function(*args, **kwargs)\n"
        "    return wrapper\n"
        "\n"
        "@log_call\n"
        "async def fetch_grade(student_id):\n"
        "    return student_id\n",
        encoding="utf-8",
    )
    llm_output = FileSummaryOutput(
        path=str(source),
        summary="Decorated async helper.",
        functions=[
            function("log_call"),
            function("fetch_grade"),
            function("wrapper"),
        ],
    )

    _, (_, output, err) = summarize(source, llm_output)

    assert err is None
    assert [item.name for item in output.functions] == ["log_call", "fetch_grade"]


DUPLICATE_INNER_SOURCE = (
    "class OuterA:\n"
    "    class Inner:\n"
    "        def from_a(self):\n"
    "            return 1\n"
    "class OuterB:\n"
    "    class Inner:\n"
    "        def from_b(self):\n"
    "            return 2\n"
)


def test_qualified_nested_classes_keep_only_their_own_methods(tmp_path):
    source = tmp_path / "nested_dup.py"
    source.write_text(DUPLICATE_INNER_SOURCE, encoding="utf-8")
    llm_output = FileSummaryOutput(
        path=str(source),
        summary="Two nested Inners.",
        types=[
            type_summary("OuterA.Inner", ["from_a", "from_b"]),
            type_summary("OuterB.Inner", ["from_a", "from_b"]),
            type_summary("Inner", ["from_a", "from_b"]),
            type_summary("Square", ["area"]),
        ],
    )

    _, (_, output, err) = summarize(source, llm_output)

    assert err is None
    by_name = {item.name: item for item in output.types}
    assert set(by_name) == {"OuterA.Inner", "OuterB.Inner", "Inner"}
    assert [item.name for item in by_name["OuterA.Inner"].methods] == ["from_a"]
    assert [item.name for item in by_name["OuterB.Inner"].methods] == ["from_b"]
    assert by_name["Inner"].name == "Inner"
    assert by_name["Inner"].methods == []
    assert "Square" not in by_name


def test_incomplete_class_header_is_treated_as_empty_class(tmp_path, capsys):
    """Tree-sitter recovers 'class Circle:\\n' as an empty block without has_error."""
    source = tmp_path / "circ.py"
    source.write_text("class Circle:\n", encoding="utf-8")
    declarations = collect_python_declarations("class Circle:\n")
    assert declarations.parse_error is False
    assert declarations.classes == {"Circle": set()}

    _, (_, output, err) = summarize(source, invented_circle_summary(str(source)))

    assert err is None
    assert [item.name for item in output.types] == ["Circle"]
    assert output.types[0].methods == []
    assert "syntax errors" not in capsys.readouterr().out


def test_nested_local_function_is_not_a_class_method(tmp_path):
    source = tmp_path / "nested.py"
    source.write_text(
        "class Outer:\n"
        "    def keep(self):\n"
        "        def local():\n"
        "            return 1\n"
        "        return local()\n"
        "    class Inner:\n"
        "        def inner_m(self):\n"
        "            return 2\n",
        encoding="utf-8",
    )
    llm_output = FileSummaryOutput(
        path=str(source),
        summary="Nested types.",
        types=[
            type_summary("Outer", ["keep", "local", "inner_m"]),
            type_summary("Inner", ["inner_m"]),
        ],
    )

    _, (_, output, err) = summarize(source, llm_output)

    assert err is None
    by_name = {item.name: item for item in output.types}
    assert [item.name for item in by_name["Outer"].methods] == ["keep"]
    assert [item.name for item in by_name["Inner"].methods] == ["inner_m"]


def test_comments_only_file_drops_invented_circle(tmp_path):
    source = tmp_path / "notes.py"
    source.write_text("# placeholder\n", encoding="utf-8")
    _, (_, output, err) = summarize(source, invented_circle_summary(str(source)))

    assert err is None
    assert output.types == []
    assert output.functions == []
    assert output.summary == "Circle computes area and circumference."


def test_syntax_error_does_not_strip_declarations(tmp_path, capsys):
    source = tmp_path / "broken.py"
    source.write_text("def incomplete_function(\n    return 42\n", encoding="utf-8")
    hallucinated = invented_circle_summary(str(source))

    _, (_, output, err) = summarize(source, hallucinated)

    assert err is None
    assert [item.name for item in output.types[0].methods] == ["area", "circumference"]
    assert "syntax errors" in capsys.readouterr().out


def test_csharp_summaries_are_not_filtered(tmp_path):
    source = tmp_path / "Calculator.cs"
    source.write_text("class Calculator { }\n", encoding="utf-8")
    llm_output = FileSummaryOutput(
        path=str(source),
        summary="C# calculator.",
        types=[type_summary("Calculator", ["Add", "Invented"])],
    )

    _, (_, output, err) = summarize(source, llm_output)

    assert err is None
    assert [item.name for item in output.types[0].methods] == ["Add", "Invented"]


def test_blank_python_file_still_skips_llm(tmp_path):
    source = tmp_path / "empty.py"
    source.write_bytes(b"")
    llm = FakeStructuredLLM(invented_circle_summary(str(source)))

    file_path, output, err = asyncio.run(_summarize_one(llm, str(source)))

    assert err is None
    assert llm.called == 0
    assert file_path == str(source)
    assert output.summary == "Empty source file."
    assert output.types == []
    assert output.functions == []


def test_direct_validator_leaves_non_python_unchanged():
    summary = FileSummaryOutput(
        path="calc.cs",
        summary="C#",
        types=[type_summary("Calculator", ["Add"])],
    )
    result = validate_file_summary_against_source(summary, "calc.cs", "class Calculator {}")
    assert [item.name for item in result.types[0].methods] == ["Add"]
