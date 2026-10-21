"""
Deterministic Python declaration checks for LLM file summaries.

Filters invented types, class-body methods, and module-level functions
using Tree-sitter. Does not rewrite summary text, PlantUML, relationships,
dependencies, properties, or business-rule prose.
"""
from __future__ import annotations

import tokenize
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

import tree_sitter_language_pack as tslp
from tree_sitter import Node, Parser

from agent.structured_output.file_summary_output import FileSummaryOutput
from backend.progress_logging import progress

_DROPPED_RULE_LIMIT = 80

_PARSER: Parser | None = None


@dataclass
class PythonDeclarations:
    """Qualified class names -> direct method names; module-level functions."""

    classes: dict[str, set[str]] = field(default_factory=dict)
    module_functions: set[str] = field(default_factory=set)
    parse_error: bool = False
    open_classes: set[str] = field(default_factory=set)
    extra_members: dict[str, set[str]] = field(default_factory=dict)


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


_OPEN_LOOKUP_METHODS = frozenset({"__getattr__", "__getattribute__"})


def _class_is_open(class_node: Node) -> bool:
    """True when inheritance, metaclass, or class decorators make members unverifiable."""
    if class_node.parent is not None and class_node.parent.type == "decorated_definition":
        return True
    for child in class_node.named_children:
        if child.type == "argument_list" and child.named_child_count > 0:
            return True
    return False


def _direct_call_name(node: Node) -> str | None:
    if node.type != "call" or not node.named_children:
        return None
    func = node.named_children[0]
    if func.type == "identifier":
        return func.text.decode("utf-8")
    return None


def _statement_has_exec_call(node: Node) -> bool:
    for child in _walk_nodes(node):
        if _direct_call_name(child) == "exec":
            return True
    return False


def _class_body_has_exec(class_node: Node) -> bool:
    """True when the class body itself calls exec(), injecting members dynamically."""
    block = None
    for child in class_node.named_children:
        if child.type == "block":
            block = child
            break
    if block is None:
        return False
    for child in block.named_children:
        inner = _unwrap_definition(child)
        if inner is not None:
            continue
        if _statement_has_exec_call(child):
            return True
    return False


def _assignment_name(node: Node) -> str | None:
    if node.type == "identifier":
        return node.text.decode("utf-8")
    if node.type == "pattern_list":
        return None
    return None


def _record_class_body_bindings(class_node: Node, qualname: str, declarations: PythonDeclarations) -> None:
    members = declarations.extra_members.setdefault(qualname, set())
    block = None
    for child in class_node.named_children:
        if child.type == "block":
            block = child
            break
    if block is None:
        return
    for child in block.named_children:
        stmt = child
        if stmt.type == "expression_statement" and stmt.named_child_count:
            stmt = stmt.named_children[0]
        if stmt.type in ("assignment", "augmented_assignment", "typed_assignment"):
            left = stmt.named_children[0] if stmt.named_children else None
            name = _assignment_name(left) if left is not None else None
            if name:
                members.add(name)


def _walk_nodes(node: Node):
    yield node
    for child in node.children:
        yield from _walk_nodes(child)


def _record_dynamic_attachments(root: Node, declarations: PythonDeclarations) -> None:
    simple_index = _simple_name_index(list(declarations.classes))

    def resolve_owner(name: str) -> str | None:
        if name in declarations.classes:
            return name
        matches = simple_index.get(name, [])
        if len(matches) == 1:
            return matches[0]
        return None

    for node in _walk_nodes(root):
        if node.type == "assignment" and node.named_children:
            left = node.named_children[0]
            if left.type == "attribute" and left.named_child_count >= 2:
                obj = left.named_children[0]
                attr = left.named_children[-1]
                if obj.type == "identifier" and attr.type == "identifier":
                    owner = resolve_owner(obj.text.decode("utf-8"))
                    if owner:
                        declarations.extra_members.setdefault(owner, set()).add(
                            attr.text.decode("utf-8")
                        )
        if node.type == "call":
            func = node.named_children[0] if node.named_children else None
            args = None
            for child in node.named_children:
                if child.type == "argument_list":
                    args = child
                    break
            if (
                func is not None
                and func.type == "identifier"
                and func.text == b"setattr"
                and args is not None
                and args.named_child_count >= 2
            ):
                target, member = args.named_children[0], args.named_children[1]
                if target.type == "identifier" and member.type == "string":
                    owner = resolve_owner(target.text.decode("utf-8"))
                    raw = member.text.decode("utf-8").strip("'\"")
                    if owner and raw.isidentifier():
                        declarations.extra_members.setdefault(owner, set()).add(raw)


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
        _record_class_body_bindings(class_node, qualname, declarations)
        if (
            _class_is_open(class_node)
            or bool(_OPEN_LOOKUP_METHODS & methods)
            or _class_body_has_exec(class_node)
        ):
            declarations.open_classes.add(qualname)

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

    _record_dynamic_attachments(root, declarations)
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


def explicit_method_calls(rule_text: str) -> list[tuple[str, str]]:
    """
    Tokenize rule text for call-like refs: Owner.method( or Outer.Inner.method(.

    Bare Class.member without '(' is ignored so ordinary dotted prose is kept.
    """
    try:
        tokens = [
            token
            for token in tokenize.tokenize(BytesIO(rule_text.encode("utf-8")).readline)
            if token.type not in (tokenize.ENCODING, tokenize.ENDMARKER, tokenize.NL, tokenize.NEWLINE)
        ]
    except tokenize.TokenError:
        return []

    calls: list[tuple[str, str]] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.type != tokenize.NAME:
            index += 1
            continue
        parts = [token.string]
        cursor = index + 1
        while (
            cursor + 1 < len(tokens)
            and tokens[cursor].string == "."
            and tokens[cursor + 1].type == tokenize.NAME
        ):
            parts.append(tokens[cursor + 1].string)
            cursor += 2
        if (
            len(parts) >= 2
            and cursor < len(tokens)
            and tokens[cursor].string == "("
        ):
            calls.append((".".join(parts[:-1]), parts[-1]))
            index = cursor + 1
            continue
        index += 1
    return calls


def _truncate_rule(rule_text: str) -> str:
    text = " ".join(rule_text.split())
    if len(text) <= _DROPPED_RULE_LIMIT:
        return text
    return text[: _DROPPED_RULE_LIMIT - 3] + "..."


def _rule_is_contradicted(rule_text: str, declarations: PythonDeclarations) -> bool:
    for owner, method in explicit_method_calls(rule_text):
        owners = _matching_class_owners(owner, declarations)
        if len(owners) != 1:
            continue
        qualname = owners[0]
        if qualname in declarations.open_classes:
            continue
        declared = declarations.classes.get(qualname, set())
        extra = declarations.extra_members.get(qualname, set())
        if method in declared or method in extra:
            continue
        return True
    return False


def filter_business_rules_against_source(
    summary: FileSummaryOutput,
    file_path: str,
    source_text: str,
) -> FileSummaryOutput:
    """
    Drop G1 rules that call a local class method that is not declared.

    Uncertain rules (imports, inheritance, dynamic members, attributes,
    ambiguous owners, syntax errors) are kept. Non-Python files are unchanged.
    """
    if Path(file_path).suffix.lower() != ".py":
        return summary

    try:
        declarations = collect_python_declarations(source_text)
    except Exception as exc:
        progress(
            f"Skipped business-rule grounding for {Path(file_path).name}: parse failed ({exc})"
        )
        return summary

    if declarations.parse_error:
        progress(
            f"Skipped business-rule grounding for {Path(file_path).name}: source has syntax errors"
        )
        return summary

    kept = []
    dropped: list[str] = []
    for rule in summary.business_rules:
        try:
            contradicted = _rule_is_contradicted(rule.rule, declarations)
        except Exception as exc:
            progress(
                f"Skipped business-rule grounding for {Path(file_path).name}: "
                f"rule analysis failed ({exc})"
            )
            kept.append(rule)
            continue
        if contradicted:
            dropped.append(_truncate_rule(rule.rule))
        else:
            kept.append(rule)
    summary.business_rules = kept
    if dropped:
        progress(
            "Dropped ungrounded business rules in "
            f"{Path(file_path).name}: {'; '.join(dropped)}"
        )
    return summary
