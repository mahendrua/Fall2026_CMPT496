"""
Deterministic declaration checks for LLM file summaries.

Filters invented types, class-body methods, and module-level functions
using Tree-sitter. Does not rewrite summary text, PlantUML, relationships,
dependencies, properties, or business-rule prose.

Directory-level (G2) rules are filtered against live Python sources under the
directory being summarized. Chroma summary paths are not treated as provenance.
"""
from __future__ import annotations

import os
import tokenize
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

import tree_sitter_language_pack as tslp
from tree_sitter import Node, Parser

from agent.crawl_config import ACCEPTABLE_EXTENSIONS, prune
from agent.structured_output.directory_output import BusinessRulesOutput
from agent.structured_output.file_summary_output import FileSummaryOutput
from backend.progress_logging import progress
from lsp.language import detect_language

_DROPPED_RULE_LIMIT = 80

_PARSERS: dict[str, Parser] = {}

def _get_parser(language: str) -> Parser:
    """
    Retrieve or initialize a cached Tree-sitter parser for a language.
    """
    if language not in _PARSERS:
        _PARSERS[language] = Parser(tslp.get_language(language))

    return _PARSERS[language]



@dataclass
class SourceDeclarations:
    """
    Language-neutral declarations extracted from one source file.

    Types map qualified type names to their directly declared members.
    Functions contains module-level or file-level functions.
    """

    language: str
    types: dict[str, set[str]] = field(default_factory=dict)
    functions: set[str] = field(default_factory=set)
    parse_error: bool = False
    open_types: set[str] = field(default_factory=set)
    extra_members: dict[str, set[str]] = field(default_factory=dict)


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


def _record_class_body_bindings(class_node: Node, qualname: str, declarations: SourceDeclarations) -> None:
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


def _record_dynamic_attachments(root: Node, declarations: SourceDeclarations) -> None:
    simple_index = _simple_name_index(list(declarations.types))

    def resolve_owner(name: str) -> str | None:
        if name in declarations.types:
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


def collect_python_declarations(source_text: str) -> SourceDeclarations:
    """
    Walk one Python file's Tree-sitter AST.

    Nested functions are ignored. Methods of nested classes are stored under
    a dotted owner (Outer.Inner) so they are not treated as Outer methods.
    """
    source = source_text.removeprefix("\ufeff")
    tree = _get_parser("python").parse(source.encode("utf-8"))
    if tree.root_node.has_error:
        return SourceDeclarations(language="python", parse_error=True)

    declarations = SourceDeclarations(language="python")
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
        declarations.types[qualname] = methods
        _record_class_body_bindings(class_node, qualname, declarations)
        if (
            _class_is_open(class_node)
            or bool(_OPEN_LOOKUP_METHODS & methods)
            or _class_body_has_exec(class_node)
        ):
            declarations.open_types.add(qualname)

    for child in root.named_children:
        inner = _unwrap_definition(child)
        if inner is None:
            continue
        if inner.type == "function_definition":
            func_name = _node_name(inner)
            if func_name:
                declarations.functions.add(func_name)
        elif inner.type == "class_definition":
            walk_class(inner)

    _record_dynamic_attachments(root, declarations)
    return declarations

def collect_javascript_declarations(source_text: str) -> SourceDeclarations:
    """
    Collect JavaScript classes, methods, and file-level functions.

    Nested classes are stored using qualified names. Classes with inheritance
    are marked open because inherited members cannot be verified from this
    file alone.
    """
    source = source_text.removeprefix("\ufeff")
    tree = _get_parser("javascript").parse(source.encode("utf-8"))

    if tree.root_node.has_error:
        return SourceDeclarations(language="javascript", parse_error=True)

    declarations = SourceDeclarations(language="javascript")
    root = tree.root_node

    def field_text(node: Node, field_name: str) -> str | None:
        child = node.child_by_field_name(field_name)
        if child is None:
            return None
        return child.text.decode("utf-8")

    def walk_class(
        class_node: Node,
        parent_qualname: str | None = None,
        declared_name: str | None = None,
    ) -> None:
        name = declared_name or field_text(class_node, "name")

        if not name:
            return

        qualname = (
            f"{parent_qualname}.{name}"
            if parent_qualname
            else name
        )

        methods: set[str] = set()

        body = class_node.child_by_field_name("body")

        if body is None:
            body = next(
                (
                    child
                    for child in class_node.named_children
                    if child.type == "class_body"
                ),
                None,
            )

        else:
            for child in body.named_children:

                if child.type == "method_definition":
                    method_name = field_text(child, "name")

                    if method_name:
                        methods.add(method_name)

                    # Search inside the method for nested classes.
                    find_nested_classes(child, qualname)

                elif child.type == "class_declaration":
                    walk_class(child, qualname)

                else:
                    # Also search other class-body scopes, such as
                    # static initialization blocks.
                    find_nested_classes(child, qualname)

        declarations.types[qualname] = methods

        has_heritage = any(
            child.type == "class_heritage"
            for child in class_node.named_children
        )

        if has_heritage:
            declarations.open_types.add(qualname)


    def find_nested_classes(
        node: Node,
        parent_qualname: str,
    ) -> None:
        """
        Recursively locate class declarations inside a class scope.

        Nested classes are associated with the enclosing class rather
        than being treated as file-level declarations.
        """
        for child in node.named_children:
            if child.type == "class_declaration":
                walk_class(child, parent_qualname)
            else:
                find_nested_classes(child, parent_qualname)
                
    for child in root.named_children:

        # Exported declarations wrap the actual declaration in
        # an export_statement. Unwrap it so existing logic handles it.
        if child.type == "export_statement":
            child = next(
                (
                    node
                    for node in child.named_children
                    if node.type in (
                        "function_declaration",
                        "class_declaration",
                        "lexical_declaration",
                        "variable_declaration",
                    )
                ),
                None,
            )

            if child is None:
                continue

        if child.type == "function_declaration":
            name = field_text(child, "name")
            if name:
                declarations.functions.add(name)

        elif child.type == "class_declaration":
            walk_class(child)

        elif child.type == "lexical_declaration":
            for declaration in child.named_children:
                if declaration.type != "variable_declarator":
                    continue

                name_node = declaration.child_by_field_name("name")
                value_node = declaration.child_by_field_name("value")

                if (
                    name_node is not None
                    and name_node.type == "identifier"
                    and value_node is not None
                    and value_node.type == "class"
                ):
                    class_name = name_node.text.decode("utf-8")
                    walk_class(
                        value_node,
                        declared_name=class_name,
                    )
                    continue

                if (
                    name_node is not None
                    and name_node.type == "identifier"
                    and value_node is not None
                    and value_node.type in (
                        "arrow_function",
                        "function_expression",
                    )
                ):
                    declarations.functions.add(
                        name_node.text.decode("utf-8")
                    )

        elif child.type == "variable_declaration":
            for declaration in child.named_children:
                if declaration.type != "variable_declarator":
                    continue

                name_node = declaration.child_by_field_name("name")
                value_node = declaration.child_by_field_name("value")

                if (
                    name_node is not None
                    and name_node.type == "identifier"
                    and value_node is not None
                    and value_node.type in (
                        "arrow_function",
                        "function_expression",
                    )
                ):
                    declarations.functions.add(
                        name_node.text.decode("utf-8")
                    )

        elif child.type == "expression_statement":
            assignment = next(
                (
                    node
                    for node in child.named_children
                    if node.type == "assignment_expression"
                ),
                None,
            )

            if assignment is None:
                continue

            left = assignment.child_by_field_name("left")
            right = assignment.child_by_field_name("right")

            if (
                left is None
                or right is None
                or left.type != "member_expression"
                or right.type not in (
                    "function_expression",
                    "arrow_function",
                )
            ):
                continue

            object_node = left.child_by_field_name("object")
            method_node = left.child_by_field_name("property")

            if object_node is None or method_node is None:
                continue

            # Account.create = function() {}
            if object_node.type == "identifier":
                class_name = object_node.text.decode("utf-8")

            # Account.prototype.withdraw = function() {}
            elif object_node.type == "member_expression":
                prototype_object = object_node.child_by_field_name("object")
                prototype_property = object_node.child_by_field_name(
                    "property"
                )

                if (
                    prototype_object is None
                    or prototype_property is None
                    or prototype_property.text.decode("utf-8") != "prototype"
                    or prototype_object.type != "identifier"
                ):
                    continue

                class_name = prototype_object.text.decode("utf-8")

            else:
                continue

            method_name = method_node.text.decode("utf-8")

            declarations.extra_members.setdefault(
                class_name, set()
            ).add(method_name)

    return declarations


DECLARATION_COLLECTORS = {
    "python": collect_python_declarations,
    "javascript": collect_javascript_declarations,
}


def collect_source_declarations(
    source_text: str,
    language: str,
) -> SourceDeclarations | None:
    """
    Collect declarations using the parser registered for a language.

    Returns None when no declaration parser is implemented.
    """
    collector = DECLARATION_COLLECTORS.get(language)
    if collector is None:
        return None

    return collector(source_text)

def _simple_name_index(qualified_names: list[str]) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for qualname in qualified_names:
        simple = qualname.rsplit(".", 1)[-1]
        index.setdefault(simple, []).append(qualname)
    return index


def _matching_type_owners(type_name: str, declarations: SourceDeclarations) -> list[str]:
    """Qualified owners that could match an LLM type name. Exact key first."""
    if type_name in declarations.types:
        return [type_name]
    return list(_simple_name_index(list(declarations.types)).get(type_name, []))


def validate_file_summary_against_source(
    summary: FileSummaryOutput,
    file_path: str,
    source_text: str,
) -> FileSummaryOutput:
    """
    Drop types, methods, and file-level functions that are not declared.

    Uses the declaration collector registered for the detected language.
    Unsupported languages and syntax errors skip filtering.
    """
    language = detect_language(file_path)
    if language is None:
        return summary

    try:
        declarations = collect_source_declarations(source_text, language)
        if declarations is None:
            return summary
    
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
        owners = _matching_type_owners(type_summary.name, declarations)
        if not owners:
            dropped.append(f"type {type_summary.name}")
            continue
        if len(owners) > 1:
            for method in type_summary.methods:
                dropped.append(f"method {type_summary.name}.{method.name}")
            type_summary.methods = []
            kept_types.append(type_summary)
            continue
        allowed_methods = declarations.types[owners[0]]
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
        if function.name in declarations.functions:
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


def _rule_is_contradicted(rule_text: str, declarations: SourceDeclarations) -> bool:
    for owner, method in explicit_method_calls(rule_text):
        owners = _matching_type_owners(owner, declarations)
        if len(owners) != 1:
            continue
        qualname = owners[0]
        if qualname in declarations.open_types:
            continue
        declared = declarations.types.get(qualname, set())
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
    Drop G1 rules that explicitly call undeclared local type members.

    Uses the declaration collector registered for the detected language.
    Uncertain rules, unsupported languages, and syntax errors are kept.
    """
    language = detect_language(file_path)
    if language is None:
        return summary

    try:
        declarations = collect_source_declarations(source_text, language)
        if declarations is None:
            return summary
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


_MIXED_LANGUAGE_EXTENSIONS = frozenset(ACCEPTABLE_EXTENSIONS) - {".py", ".md"}


@dataclass
class _DirectoryPythonScope:
    python_only: bool = False
    coverage_uncertain: bool = False
    file_declarations: list[SourceDeclarations] = field(default_factory=list)


def _collect_directory_python_scope(directory_abs_path: str) -> _DirectoryPythonScope:
    """
    Walk live source files under a directory.

    Coverage is uncertain when the walk or a Python read/parse fails. Mixed
    language means a non-Python code file was found in the same scope.
    """
    root = Path(directory_abs_path)
    if not root.is_dir():
        return _DirectoryPythonScope(coverage_uncertain=True)

    python_paths: list[Path] = []
    mixed_language = False
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            prune(dirpath, dirnames)
            for name in filenames:
                suffix = Path(name).suffix.lower()
                full = Path(dirpath) / name
                if suffix in _MIXED_LANGUAGE_EXTENSIONS:
                    mixed_language = True
                elif suffix == ".py":
                    python_paths.append(full)
    except Exception as exc:
        progress(
            f"Skipped G2 business-rule grounding for {root.name}: "
            f"directory scan failed ({exc})"
        )
        return _DirectoryPythonScope(coverage_uncertain=True)

    if mixed_language or not python_paths:
        return _DirectoryPythonScope(python_only=False)

    file_declarations: list[SourceDeclarations] = []
    for path in python_paths:
        try:
            source_text = path.read_text(encoding="utf-8")
            declarations = collect_python_declarations(source_text)
        except Exception as exc:
            progress(
                f"Skipped G2 business-rule grounding for {root.name}: "
                f"{path.name} could not be read or parsed ({exc})"
            )
            return _DirectoryPythonScope(python_only=True, coverage_uncertain=True)
        if declarations.parse_error:
            progress(
                f"Skipped G2 business-rule grounding for {root.name}: "
                f"{path.name} has syntax errors"
            )
            return _DirectoryPythonScope(python_only=True, coverage_uncertain=True)
        file_declarations.append(declarations)

    return _DirectoryPythonScope(
        python_only=True,
        file_declarations=file_declarations,
    )


def _directory_rule_is_contradicted(
    rule_text: str,
    file_declarations: list[SourceDeclarations],
) -> bool:
    """
    True when a call-like owner.method( is missing from every matching class.

    Each file's declarations stay separate so same-named classes are not merged.
    Unknown or module-prefixed owners that do not match a local class are kept.
    """
    for owner, method in explicit_method_calls(rule_text):
        matches: list[tuple[SourceDeclarations, str]] = []
        for declarations in file_declarations:
            for qualname in _matching_type_owners(owner, declarations):
                matches.append((declarations, qualname))
        if not matches:
            continue
        all_closed_and_missing = True
        for declarations, qualname in matches:
            if declarations.parse_error or qualname in declarations.open_types:
                all_closed_and_missing = False
                break
            declared = declarations.types.get(qualname, set())
            extra = declarations.extra_members.get(qualname, set())
            if method in declared or method in extra:
                all_closed_and_missing = False
                break
        if all_closed_and_missing:
            return True
    return False


def _filter_rule_texts(
    rules: list[str],
    file_declarations: list[SourceDeclarations],
    directory_name: str,
    field_name: str,
) -> list[str]:
    kept: list[str] = []
    dropped: list[str] = []
    for text in rules:
        try:
            contradicted = _directory_rule_is_contradicted(text, file_declarations)
        except Exception as exc:
            progress(
                f"Skipped G2 business-rule grounding for {directory_name}: "
                f"{field_name} analysis failed ({exc})"
            )
            kept.append(text)
            continue
        if contradicted:
            dropped.append(_truncate_rule(text))
        else:
            kept.append(text)
    if dropped:
        progress(
            "Dropped ungrounded G2 business rules in "
            f"{directory_name}: {'; '.join(dropped)}"
        )
    return kept


def filter_directory_business_rules_against_source(
    output: BusinessRulesOutput,
    directory_abs_path: str,
    codebase_root: str,
) -> BusinessRulesOutput:
    """
    Drop G2 rules that call a local Python class method that is not declared.

    Python-only directories with complete, parseable coverage are filtered.
    Mixed-language scopes, missing Python files, syntax errors, unreadable
    files, and walk failures leave the rules unchanged. Non-call-like prose
    and unknown owners are kept.
    """
    del codebase_root
    try:
        scope = _collect_directory_python_scope(directory_abs_path)
    except Exception as exc:
        progress(
            f"Skipped G2 business-rule grounding for "
            f"{Path(directory_abs_path).name}: scan failed ({exc})"
        )
        return output

    if not scope.python_only or scope.coverage_uncertain or not scope.file_declarations:
        return output

    directory_name = output.directory_name or Path(directory_abs_path).name
    output.observed_rules = _filter_rule_texts(
        output.observed_rules,
        scope.file_declarations,
        directory_name,
        "observed_rules",
    )
    output.inferred_rules = _filter_rule_texts(
        output.inferred_rules,
        scope.file_declarations,
        directory_name,
        "inferred_rules",
    )
    return output


