import asyncio
import json
from pathlib import Path

from agent.file_summary_agent import FileSummaryAgent, _summarize_one
from agent.file_summary_grounding import (
    collect_python_declarations,
    explicit_method_calls,
    filter_business_rules_against_source,
)
from agent.structured_output.file_summary_output import (
    BusinessRule,
    FileSummaryOutput,
    FunctionSummary,
    MethodSummary,
    TypeSummary,
)


class FakeStructuredLLM:
    def __init__(self, output):
        self.output = output
        self.called = 0

    async def ainvoke(self, messages):
        self.called += 1
        return self.output


class StubChatModel:
    def with_structured_output(self, schema, **kwargs):
        return FakeStructuredLLM(FileSummaryOutput(path="unused", summary="unused"))


def rule(text: str) -> BusinessRule:
    return BusinessRule(rule=text)


def summary_with_rules(path: str, texts: list[str], **overrides) -> FileSummaryOutput:
    values = dict(
        path=path,
        summary="summary",
        types=[
            TypeSummary(
                name="Circle",
                kind="class",
                description="shape",
                methods=[MethodSummary(name="area", description="area")],
                plantuml="class Circle",
            )
        ],
        functions=[FunctionSummary(name="draw", description="draw")],
        business_rules=[rule(text) for text in texts],
    )
    values.update(overrides)
    return FileSummaryOutput(**values)


def summarize(path: Path, output: FileSummaryOutput):
    llm = FakeStructuredLLM(output)
    return llm, asyncio.run(_summarize_one(llm, str(path)))


def test_explicit_calls_require_parentheses():
    assert explicit_method_calls("Circle.area() computes pi r squared") == [
        ("Circle", "area")
    ]
    assert explicit_method_calls("Circle.area computes pi r squared") == []
    assert explicit_method_calls("Outer.Inner.method() applies") == [
        ("Outer.Inner", "method")
    ]


def test_invented_circle_area_call_is_dropped(tmp_path, capsys):
    source = tmp_path / "circ.py"
    source.write_text("class Circle:\n    pass\n", encoding="utf-8")
    hallucinated = summary_with_rules(
        str(source),
        ["Circle.area() computes pi*r*r"],
    )

    _, (_, output, err) = summarize(source, hallucinated)

    assert err is None
    assert output.business_rules == []
    captured = capsys.readouterr().out
    assert "Dropped ungrounded business rules" in captured
    assert "Circle.area()" in captured


def test_declared_circle_area_call_is_kept(tmp_path):
    source = tmp_path / "circ.py"
    source.write_text(
        "class Circle:\n"
        "    def area(self):\n"
        "        return 3.14\n",
        encoding="utf-8",
    )
    llm_output = summary_with_rules(
        str(source),
        ["Circle.area() computes pi*r*r"],
    )

    _, (_, output, err) = summarize(source, llm_output)

    assert err is None
    assert [item.rule for item in output.business_rules] == [
        "Circle.area() computes pi*r*r"
    ]
    assert output.business_rules[0] is llm_output.business_rules[0]


def test_inherited_method_call_is_kept(tmp_path):
    source = tmp_path / "shapes.py"
    source.write_text(
        "class Shape:\n"
        "    def area(self):\n"
        "        return 0\n"
        "class Circle(Shape):\n"
        "    pass\n",
        encoding="utf-8",
    )
    declarations = collect_python_declarations(source.read_text(encoding="utf-8"))
    assert "Circle" in declarations.open_classes

    llm_output = summary_with_rules(
        str(source),
        ["Circle.area() uses the base implementation"],
    )
    _, (_, output, err) = summarize(source, llm_output)
    assert err is None
    assert output.business_rules[0].rule.startswith("Circle.area()")


def test_getattr_lookup_keeps_method_calls(tmp_path):
    source = tmp_path / "getattr_lab.py"
    source.write_text(
        "class Circle:\n"
        "    def __getattr__(self, name):\n"
        "        return lambda: 1\n",
        encoding="utf-8",
    )
    declarations = collect_python_declarations(source.read_text(encoding="utf-8"))
    assert "Circle" in declarations.open_classes
    llm_output = summary_with_rules(
        str(source),
        ["Circle.area() is resolved dynamically"],
    )
    _, (_, output, err) = summarize(source, llm_output)
    assert err is None
    assert output.business_rules[0].rule.startswith("Circle.area()")


def test_getattribute_lookup_keeps_method_calls(tmp_path):
    source = tmp_path / "getattribute_lab.py"
    source.write_text(
        "class Circle:\n"
        "    def __getattribute__(self, name):\n"
        "        return object.__getattribute__(self, name)\n",
        encoding="utf-8",
    )
    declarations = collect_python_declarations(source.read_text(encoding="utf-8"))
    assert "Circle" in declarations.open_classes
    llm_output = summary_with_rules(
        str(source),
        ["Circle.area() is resolved by getattribute"],
    )
    _, (_, output, err) = summarize(source, llm_output)
    assert err is None
    assert output.business_rules[0].rule.startswith("Circle.area()")


def test_class_body_exec_keeps_method_calls(tmp_path):
    source = tmp_path / "exec_lab.py"
    source.write_text(
        "class Circle:\n"
        '    exec("def area(self): return 1")\n',
        encoding="utf-8",
    )
    declarations = collect_python_declarations(source.read_text(encoding="utf-8"))
    assert "Circle" in declarations.open_classes
    llm_output = summary_with_rules(
        str(source),
        ["Circle.area() is injected by exec"],
    )
    _, (_, output, err) = summarize(source, llm_output)
    assert err is None
    assert output.business_rules[0].rule.startswith("Circle.area()")


def test_unrelated_compile_assignment_does_not_open_class(tmp_path):
    source = tmp_path / "compile_lab.py"
    source.write_text(
        "class Circle:\n"
        "    snippet = compile('1 + 1', '<string>', 'eval')\n",
        encoding="utf-8",
    )
    declarations = collect_python_declarations(source.read_text(encoding="utf-8"))
    assert "Circle" not in declarations.open_classes
    llm_output = summary_with_rules(
        str(source),
        ["Circle.area() computes pi*r*r"],
    )
    _, (_, output, err) = summarize(source, llm_output)
    assert err is None
    assert output.business_rules == []


def test_nested_getattr_does_not_open_outer_class(tmp_path):
    source = tmp_path / "nested_getattr.py"
    source.write_text(
        "class Outer:\n"
        "    def keep(self):\n"
        "        return 1\n"
        "    class Inner:\n"
        "        def __getattr__(self, name):\n"
        "            return lambda: 1\n",
        encoding="utf-8",
    )
    declarations = collect_python_declarations(source.read_text(encoding="utf-8"))
    assert "Outer.Inner" in declarations.open_classes
    assert "Outer" not in declarations.open_classes
    llm_output = FileSummaryOutput(
        path=str(source),
        summary="nested",
        business_rules=[
            rule("Inner.area() is dynamic on Inner"),
            rule("Outer.missing() is not declared on Outer"),
        ],
    )
    _, (_, output, err) = summarize(source, llm_output)
    assert err is None
    texts = [item.rule for item in output.business_rules]
    assert "Inner.area() is dynamic on Inner" in texts
    assert "Outer.missing() is not declared on Outer" not in texts


def test_metaclass_circle_keeps_invented_calls(tmp_path):
    source = tmp_path / "meta.py"
    source.write_text(
        "class Circle(metaclass=type):\n"
        "    pass\n",
        encoding="utf-8",
    )
    declarations = collect_python_declarations(source.read_text(encoding="utf-8"))
    assert "Circle" in declarations.open_classes
    llm_output = summary_with_rules(
        str(source),
        ["Circle.area() is provided by a metaclass"],
    )
    _, (_, output, err) = summarize(source, llm_output)
    assert err is None
    assert output.business_rules[0].rule.startswith("Circle.area()")


def test_dynamic_method_attachment_is_kept(tmp_path):
    source = tmp_path / "dyn.py"
    source.write_text(
        "class Circle:\n"
        "    pass\n"
        "Circle.area = lambda self: 1\n",
        encoding="utf-8",
    )
    llm_output = summary_with_rules(
        str(source),
        ["Circle.area() is attached dynamically"],
    )
    _, (_, output, err) = summarize(source, llm_output)
    assert err is None
    assert output.business_rules[0].rule.startswith("Circle.area()")


def test_class_attribute_call_is_kept_as_uncertain(tmp_path):
    source = tmp_path / "attr.py"
    source.write_text(
        "class Circle:\n"
        "    area = 3.14\n",
        encoding="utf-8",
    )
    llm_output = summary_with_rules(
        str(source),
        ["Circle.area() is stored as a constant"],
    )
    _, (_, output, err) = summarize(source, llm_output)
    assert err is None
    assert output.business_rules[0].rule.startswith("Circle.area()")


def test_property_method_call_is_kept(tmp_path):
    source = tmp_path / "prop.py"
    source.write_text(
        "class Circle:\n"
        "    @property\n"
        "    def area(self):\n"
        "        return 1\n",
        encoding="utf-8",
    )
    llm_output = summary_with_rules(
        str(source),
        ["Circle.area() is a property"],
    )
    _, (_, output, err) = summarize(source, llm_output)
    assert err is None
    assert output.business_rules[0].rule.startswith("Circle.area()")


def test_ambiguous_nested_owner_is_kept(tmp_path):
    source = tmp_path / "nested.py"
    source.write_text(
        "class OuterA:\n"
        "    class Inner:\n"
        "        def from_a(self):\n"
        "            return 1\n"
        "class OuterB:\n"
        "    class Inner:\n"
        "        def from_b(self):\n"
        "            return 2\n",
        encoding="utf-8",
    )
    llm_output = FileSummaryOutput(
        path=str(source),
        summary="nested",
        business_rules=[
            rule("Inner.from_a() belongs to one of the nested classes"),
            rule("OuterB.Inner.from_a() does not exist on OuterB.Inner"),
            rule("OuterA.Inner.from_a() is declared"),
        ],
    )
    _, (_, output, err) = summarize(source, llm_output)
    assert err is None
    texts = [item.rule for item in output.business_rules]
    assert "Inner.from_a() belongs to one of the nested classes" in texts
    assert "OuterA.Inner.from_a() is declared" in texts
    assert "OuterB.Inner.from_a() does not exist on OuterB.Inner" not in texts


def test_async_and_decorated_methods_are_kept(tmp_path):
    source = tmp_path / "async_lab.py"
    source.write_text(
        "from functools import wraps\n"
        "\n"
        "def deco(fn):\n"
        "    return fn\n"
        "\n"
        "class Worker:\n"
        "    @deco\n"
        "    async def fetch(self):\n"
        "        return 1\n",
        encoding="utf-8",
    )
    llm_output = FileSummaryOutput(
        path=str(source),
        summary="async",
        business_rules=[rule("Worker.fetch() loads a grade")],
    )
    _, (_, output, err) = summarize(source, llm_output)
    assert err is None
    assert output.business_rules[0].rule.startswith("Worker.fetch()")


def test_imported_path_exists_is_kept(tmp_path):
    source = tmp_path / "paths.py"
    source.write_text(
        "from pathlib import Path\n"
        "class Circle:\n"
        "    pass\n",
        encoding="utf-8",
    )
    llm_output = summary_with_rules(
        str(source),
        ["Path.exists() reports whether a file is present"],
    )
    _, (_, output, err) = summarize(source, llm_output)
    assert err is None
    assert "Path.exists()" in output.business_rules[0].rule


def test_rules_without_method_calls_are_kept(tmp_path):
    source = tmp_path / "math_lab.py"
    source.write_text(
        "PASSING_GRADE = 50\n"
        "\n"
        "def calculate_average(values):\n"
        "    return sum(values) / len(values)\n"
        "\n"
        "def is_passing(grade):\n"
        "    return grade >= PASSING_GRADE\n",
        encoding="utf-8",
    )
    llm_output = FileSummaryOutput(
        path=str(source),
        summary="lab",
        functions=[
            FunctionSummary(name="calculate_average", description="avg"),
            FunctionSummary(name="is_passing", description="pass"),
        ],
        business_rules=[
            rule("The passing grade threshold is 50"),
            rule("calculate_average returns the arithmetic mean of its values"),
        ],
    )
    _, (_, output, err) = summarize(source, llm_output)
    assert err is None
    assert [item.rule for item in output.business_rules] == [
        "The passing grade threshold is 50",
        "calculate_average returns the arithmetic mean of its values",
    ]


def test_syntax_error_keeps_rules(tmp_path, capsys):
    source = tmp_path / "broken.py"
    source.write_text("def incomplete_function(\n    return 42\n", encoding="utf-8")
    llm_output = summary_with_rules(
        str(source),
        ["Circle.area() computes pi*r*r"],
    )
    _, (_, output, err) = summarize(source, llm_output)
    assert err is None
    assert output.business_rules[0].rule.startswith("Circle.area()")
    assert "syntax errors" in capsys.readouterr().out


def test_blank_file_still_skips_llm(tmp_path):
    source = tmp_path / "empty.py"
    source.write_bytes(b"")
    llm = FakeStructuredLLM(
        summary_with_rules(str(source), ["Circle.area() computes pi*r*r"])
    )
    file_path, output, err = asyncio.run(_summarize_one(llm, str(source)))
    assert err is None
    assert llm.called == 0
    assert output.summary == "Empty source file."
    assert output.business_rules == []


def test_csharp_rules_are_not_filtered(tmp_path):
    source = tmp_path / "Calculator.cs"
    source.write_text("class Calculator { }\n", encoding="utf-8")
    llm_output = FileSummaryOutput(
        path=str(source),
        summary="C#",
        business_rules=[rule("Calculator.Add() invents a method")],
    )
    _, (_, output, err) = summarize(source, llm_output)
    assert err is None
    assert output.business_rules[0].rule.startswith("Calculator.Add()")


def test_g1_writer_omits_contradicted_rules(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "circ.py"
    source.write_text("class Circle:\n    pass\n", encoding="utf-8")
    hallucinated = summary_with_rules(
        str(source),
        ["Circle.area() computes pi*r*r", "Radius must be non-negative"],
    )
    _, (_, output, err) = summarize(source, hallucinated)
    assert err is None
    assert [item.rule for item in output.business_rules] == [
        "Radius must be non-negative"
    ]
    assert output.business_rules[0].source_file == str(source)

    agent = FileSummaryAgent(llm=StubChatModel())
    agent.write_file_summary_node(
        {
            "codebase_name": "lab",
            "filename_counters": {},
            "file_summaries": [output],
        }
    )
    batch_rules = {}
    if output.business_rules:
        batch_rules[output.path] = output.business_rules
    agent.write_business_rules_node(
        {
            "codebase_name": "lab",
            "business_rules_by_file": batch_rules,
        }
    )
    rules_path = Path("agent/file_summary_agent_output/lab/business_rules/business_rules.json")
    payload = json.loads(rules_path.read_text(encoding="utf-8"))
    dumped = json.dumps(payload)
    assert "Circle.area()" not in dumped
    assert "Radius must be non-negative" in dumped


def test_direct_filter_leaves_non_python_unchanged():
    original = FileSummaryOutput(
        path="calc.cs",
        summary="C#",
        business_rules=[rule("Calculator.Add() invents a method")],
    )
    result = filter_business_rules_against_source(
        original, "calc.cs", "class Calculator {}"
    )
    assert result.business_rules[0] is original.business_rules[0]
