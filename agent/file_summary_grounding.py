"""
Deterministic Python declaration checks for LLM file summaries.

Filters invented types, class-body methods, and module-level functions
using Tree-sitter. Does not rewrite summary text, PlantUML, relationships,
dependencies, properties, or business-rule prose.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import tree_sitter_language_pack as tslp
from tree_sitter import Node, Parser

from agent.structured_output.file_summary_output import FileSummaryOutput
from backend.progress_logging import progress

_PARSER: Parser | None = None


@dataclass
class PythonDeclarations:
    """Qualified class names -> direct method names; module-level functions."""

    classes: dict[str, set[str]] = field(default_factory=dict)
    module_functions: set[str] = field(default_factory=set)
    parse_error: bool = False


def _python_parser() -> Parser:
    global _PARSER
    if _PARSER is None:
        _PARSER = Parser(tslp.get_language("python"))
    return _PARSER


def _node_name(node: Node) -> str | None:
    for child in node.children:
        if child.type == "identifier":
            return child.text.decode("utf-8")
    return None


def _unwrap_definition(node: Node) -> Node | None:
    if node.type in ("function_definition", "class_definition"):
        return node
    if node.type != "decorated_definition":
        return None
    for child in node.named_children:
        if child.type in ("function_definition", "class_definition"):
            return child
    return None


def _iter_block_definitions(owner: Node):
    block = None
    for child in owner.named_children:
        if child.type == "block":
            block = child
            break
    if block is None:
        return
    for child in block.named_children:
        inner = _unwrap_definition(child)
        if inner is not None:
            yield inner


def _class_qualname(class_node: Node) -> str | None:
    name = _node_name(class_node)
    if not name:
        return None
    parts = [name]
    current = class_node.parent
    while current is not None:
        if current.type == "class_definition":
            parent_name = _node_name(current)
            if parent_name:
                parts.append(parent_name)
        current = current.parent
    return ".".join(reversed(parts))


def collect_python_declarations(source_text: str) -> PythonDeclarations:
    """
    Walk one Python file's Tree-sitter AST.

    Nested functions are ignored. Methods of nested classes are stored under
    a dotted owner (Outer.Inner) so they are not treated as Outer methods.
    """
    source = source_text.removeprefix("\ufeff")
    tree = _python_parser().parse(source.encode("utf-8"))
    if tree.root_node.has_error:
        return PythonDeclarations(parse_error=True)

    declarations = PythonDeclarations()
    root = tree.root_node

    def walk_class(class_node: Node) -> None:
        qualname = _class_qualname(class_node)
        if not qualname:
            return
        methods: set[str] = set()
        for inner in _iter_block_definitions(class_node):
            if inner.type == "function_definition":
                method_name = _node_name(inner)
                if method_name:
                    methods.add(method_name)
            elif inner.type == "class_definition":
                walk_class(inner)
        declarations.classes[qualname] = methods

    for child in root.named_children:
        inner = _unwrap_definition(child)
        if inner is None:
            continue
        if inner.type == "function_definition":
            func_name = _node_name(inner)
            if func_name:
                declarations.module_functions.add(func_name)
        elif inner.type == "class_definition":
            walk_class(inner)

    return declarations


def _simple_name_index(qualified_names: list[str]) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for qualname in qualified_names:
        simple = qualname.rsplit(".", 1)[-1]
        index.setdefault(simple, []).append(qualname)
    return index


def _matching_class_owners(type_name: str, declarations: PythonDeclarations) -> list[str]:
    """Qualified owners that could match an LLM type name. Exact key first."""
    if type_name in declarations.classes:
        return [type_name]
    return list(_simple_name_index(list(declarations.classes)).get(type_name, []))


def validate_file_summary_against_source(
    summary: FileSummaryOutput,
    file_path: str,
    source_text: str,
) -> FileSummaryOutput:
    """
    Drop Python types, methods, and top-level functions that are not declared.

    Non-Python files are unchanged. Syntax errors skip filtering.
    """
    if Path(file_path).suffix.lower() != ".py":
        return summary

    try:
        declarations = collect_python_declarations(source_text)
    except Exception as exc:
        progress(
            f"Skipped declaration grounding for {Path(file_path).name}: parse failed ({exc})"
        )
        return summary

    if declarations.parse_error:
        progress(
            f"Skipped declaration grounding for {Path(file_path).name}: source has syntax errors"
        )
        return summary

    dropped: list[str] = []
    kept_types = []
    for type_summary in summary.types:
        owners = _matching_class_owners(type_summary.name, declarations)
        if not owners:
            dropped.append(f"type {type_summary.name}")
            continue
        if len(owners) > 1:
            for method in type_summary.methods:
                dropped.append(f"method {type_summary.name}.{method.name}")
            type_summary.methods = []
            kept_types.append(type_summary)
            continue
        allowed_methods = declarations.classes[owners[0]]
        kept_methods = []
        for method in type_summary.methods:
            if method.name in allowed_methods:
                kept_methods.append(method)
            else:
                dropped.append(f"method {type_summary.name}.{method.name}")
        type_summary.methods = kept_methods
        kept_types.append(type_summary)
    summary.types = kept_types

    kept_functions = []
    for function in summary.functions:
        if function.name in declarations.module_functions:
            kept_functions.append(function)
        else:
            dropped.append(f"function {function.name}")
    summary.functions = kept_functions

    if dropped:
        progress(
            "Dropped ungrounded declarations in "
            f"{Path(file_path).name}: {', '.join(dropped)}"
        )
    return summary
