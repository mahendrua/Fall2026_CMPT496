"""!
@file tree_parse.py
@brief Library for parsing files using tree-sitter.
@details Provides utility functions to traverse directories and convert source 
code into Abstract Syntax Trees (AST) using the Tree-sitter library.
"""

import tree_sitter_language_pack as tslp
from tree_sitter import Parser, Tree, Node, Query, QueryCursor
from pathlib import Path
from dataclasses import dataclass
import os
import sys
from agent.crawl_config import prune

# Consecutive Python module-level statements are packed until either bound
# is exceeded, then a new module_body chunk starts at the next statement.
MODULE_BODY_MAX_LINES = 200
MODULE_BODY_MAX_CHARS = 8000

_PYTHON_MODULE_SKIP_TYPES = frozenset({
    "import_statement",
    "import_from_statement",
    "future_import_statement",
    "function_definition",
    "class_definition",
    "decorated_definition",
})

# Create language dictionary to associate file extensions with tree-sitter
# language packs and desired S-expression queries for chunk extraction.
LANGUAGE_DICT = {
    ".cs": {
        "name": "csharp",
        "query": """
            (method_declaration name: (identifier) @name) @chunk
            (constructor_declaration name: (identifier) @name) @chunk
            (property_declaration name: (identifier) @name) @chunk
        """,
        "container_type": "class_declaration",
        "property_type": "property_declaration",
        "import_query": "(using_directive) @import",
        "namespace_query": """
            (namespace_declaration name: (_) @ns)
            (file_scoped_namespace_declaration name: (_) @ns)
        """
    },
    ".js": {
        "name": "javascript",
        "query": """
            (function_declaration name: (identifier) @name) @chunk
            (method_definition name: (property_identifier) @name) @chunk
            (pair key: (property_identifier) @name) @chunk
        """,
        "container_type": "class_declaration",
        "property_type": "pair",
        "import_query": "(import_statement) @import",
        "namespace_query": "" # JS has no namespaces, so this is left empty
    },
    ".py": {
        "name": "python",
        "query": """
            (function_definition name: (identifier) @name) @chunk
        """,
        "container_type": "class_definition",
        "property_type": None,
        "import_query": """
            (import_statement) @import
            (import_from_statement) @import
        """,
        "namespace_query": ""
    }
}

@dataclass
class CodeBundle:
    """!
    @brief Data container for a parsed source file.
    @param path The pathlib.Path of the source file.
    @param content The raw bytes of the file content.
    @param tree The tree_sitter.Tree object resulting from the parse.
    """
    path: Path
    content: bytes
    tree: Tree
    language: str = ""

def read_source_bytes(filepath: Path) -> bytes:
    """!
    @brief Reads a source file as UTF-8 bytes for parsing.
    @details Older codebases often save files in Windows-1252, which is not
    valid UTF-8 and used to stop the code database build. Such files are
    converted to UTF-8, keeping their text; UTF-8 files are returned unchanged.
    @param filepath The pathlib.Path of the source file.
    @return The file content as UTF-8 bytes.
    """
    code_bytes = filepath.read_bytes()
    try:
        code_bytes.decode("utf-8")
    except UnicodeDecodeError:
        code_bytes = code_bytes.decode("cp1252", errors="replace").encode("utf-8")
    return code_bytes

def parse_file(filepath: Path, parser: Parser, language: str) -> CodeBundle:
    """!
    @brief Parses a specific file into a CodeBundle.
    @param filepath Path object representing the file to be parsed.
    @param parser An initialized tree_sitter.Parser instance for the target language.
    @return A CodeBundle instance containing the file metadata and AST.
    @exception TypeError Raised if inputs are not of the expected type.
    @exception FileNotFoundError Raised if the provided filepath does not exist.
    """
    # Validate inputs
    if not isinstance(filepath, Path):
        raise TypeError(f"Expected 'filepath' to be a pathlib.Path, got {type(filepath).__name__}")
    
    if not isinstance(parser, Parser):
        raise TypeError(f"Expected 'parser' to be a tree_sitter.Parser, got {type(parser).__name__}")
    
    if not filepath.exists():
        raise FileNotFoundError(f"Source file not found: {filepath}")
    
    # Parse file. Stdout is reserved for Checkpoint's JSON protocol.
    print(f"Parsing: {filepath}", file=sys.stderr)
    code_bytes = read_source_bytes(filepath)
    ast = parser.parse(code_bytes)

    # Assemble bundle
    bundle = CodeBundle(path=filepath, content=code_bytes, tree=ast, language = language)

    return bundle

def parse_dir(dirpath: str) -> list[CodeBundle]:
    """!
    @brief Recursively parses all C# files within a given directory.
    @param dirpath String representing the path to the directory.
    @return A list of CodeBundle objects for all discovered .cs files.
    @exception TypeError Raised if dirpath is not a string.
    @exception NotADirectoryError Raised if the provided path is not a directory.
    """
    # Validate inputs
    if not isinstance(dirpath, str):
        raise TypeError(f"Expected 'dirpath' to be a string, got {type(dirpath).__name__}")

    # Create Path
    pathway = Path(dirpath)

    # Validate pathway is valid path
    if not pathway.is_dir():
        raise NotADirectoryError(f"The path {dirpath} is not a valid directory.")

    # Initialize all parsers
    parsers = {}
    for ext, lang_info in LANGUAGE_DICT.items():
        lang = tslp.get_language(lang_info["name"])
        lang_parser = Parser(lang)
        parsers[ext] = lang_parser

    bundle_list = []

    # Find source files, skipping the same folders the crawlers skip
    # (bin/, obj/, Checkpoint's generated tests, ...). rglob skipped nothing,
    # so build output and our own generated tests ended up in the vector store.
    found = []
    for root, dirs, filenames in os.walk(pathway):
        prune(root, dirs)
        for name in filenames:
            found.append(Path(root) / name)

    # Parse all files
    extensions = LANGUAGE_DICT.keys()
    for ext in extensions:
        for filepath in (p for p in found if p.suffix.lower() == ext):
            code_bytes = read_source_bytes(filepath)
            tree = parsers[ext].parse(code_bytes)
            bundle = CodeBundle(path = filepath, content = code_bytes, tree = tree, language = LANGUAGE_DICT[ext]["name"])
            bundle_list.append(bundle)

    return bundle_list

def get_chunks(bundle: CodeBundle) -> list[dict]:
    """!
    @brief Selects and extracts semantic code blocks from a bundle based on a predefined query.
    @details Uses S-expression queries to identify method, constructor, and property 
    declarations. For each match, it extracts the source code, associated metadata, 
    and climbing context like the parent class name.
    
    @param bundle The CodeBundle instance containing the tree and source bytes.
    @return A list of dictionaries, each containing name, code, class, file, type, 
    line range, and language info.
    @exception TypeError Raised if the bundle or its internal attributes are of the incorrect type.
    """
    # Validate CodeBundle attributes exist and are of the expected type
    if not isinstance(bundle, CodeBundle):
        raise TypeError(f"Expected 'bundle' to be a CodeBundle instance, got {type(bundle).__name__}")
    
    if not isinstance(bundle.path, Path):
        raise TypeError(f"Expected 'bundle.path' to be a pathlib.Path, got {type(bundle.path).__name__}")
    
    if not isinstance(bundle.content, bytes):
        raise TypeError(f"Expected 'bundle.content' to be bytes, got {type(bundle.content).__name__}")
    
    if not isinstance(bundle.tree, Tree):
        raise TypeError(f"Expected 'bundle.tree' to be a tree_sitter.Tree, got {type(bundle.tree).__name__}")
    
    if not isinstance(bundle.language, str):
        raise TypeError(f"Expected 'bundle.language' to be a string, got {type(bundle.language).__name__}")
    
    ext = bundle.path.suffix.lower()
    config = LANGUAGE_DICT.get(ext)

    if bundle.tree.root_node.has_error:
        print(
            f"Skipping {bundle.path}: source contains syntax errors.",
            file=sys.stderr,
        )
        return []

    # Validate that the file extension is supported
    if not config: 
        return []

    # Extract File-Level Metadata
    if config["namespace_query"] == "":
        file_namespace = "N/A"
    else:
        namespaces = get_metadata_by_query(bundle.tree.root_node, config["name"], config["namespace_query"], "ns")
        file_namespace = ", ".join(namespaces)
    
    if config["import_query"] == "":
        file_imports = "N/A"
    else:
        imports = get_metadata_by_query(bundle.tree.root_node, config["name"], config["import_query"], "import")
        file_imports = "\n".join(imports)

    # xtract Chunks
    query = Query(bundle.tree.language, config["query"])
    cursor = QueryCursor(query)
    matches = cursor.matches(bundle.tree.root_node)

    chunks = []
    grouped_properties = {}

    for pattern, captures in matches:
        chunk_node = captures.get("chunk")[0]
        name_node = captures.get("name")[0]

        if chunk_node and name_node:
            parent_container = get_container_name(chunk_node, config["container_type"])
            code_text = bundle.content[chunk_node.start_byte:chunk_node.end_byte].decode("utf-8")
            
            # Handle Property Grouping
            if (
                config["property_type"] is not None
                and chunk_node.type == config["property_type"]
            ):
                if parent_container not in grouped_properties:
                    grouped_properties[parent_container] = []
                
                grouped_properties[parent_container].append(code_text)
                continue

            # Standard Chunk Extraction
            chunk = {
                "name": name_node.text.decode("utf-8"),
                "code": code_text,
                "container": parent_container,
                "file": str(bundle.path),
                "type": chunk_node.type,
                "start_line": chunk_node.start_point[0] + 1,
                "end_line": chunk_node.end_point[0] + 1, # Added end_line
                "language": bundle.language,
                "namespace": file_namespace,
                "imports": file_imports
            }
            chunks.append(chunk)

    # Add Grouped Properties
    for container, props in grouped_properties.items():
        chunk = {
            "name": f"{container}_Properties",
            "code": "\n".join(props),
            "container": container,
            "file": str(bundle.path),
            "type": "grouped_properties",
            "start_line": -1,
            "end_line": -1,
            "language": bundle.language,
            "namespace": file_namespace,
            "imports": file_imports
        }
        chunks.append(chunk)

    if ext == ".py":
        chunks.extend(
            _python_module_body_chunks(
                bundle,
                file_namespace=file_namespace,
                file_imports=file_imports,
            )
        )

    return chunks


def _node_line_span(node: Node) -> tuple[int, int]:
    return node.start_point[0] + 1, node.end_point[0] + 1


def _module_group_metrics(content: bytes, nodes: list[Node]) -> tuple[int, int, str]:
    start = nodes[0].start_byte
    end = nodes[-1].end_byte
    code = content[start:end].decode("utf-8")
    start_line, _ = _node_line_span(nodes[0])
    _, end_line = _node_line_span(nodes[-1])
    return end_line - start_line + 1, len(code), code


def _module_group_is_oversized(content: bytes, nodes: list[Node]) -> bool:
    lines, chars, _ = _module_group_metrics(content, nodes)
    return lines > MODULE_BODY_MAX_LINES or chars > MODULE_BODY_MAX_CHARS


def _python_module_statement_runs(root: Node) -> list[list[Node]]:
    """Group adjacent executable module-level nodes; skip defs/classes/imports."""
    runs: list[list[Node]] = []
    current: list[Node] = []
    for child in root.named_children:
        if child.type in _PYTHON_MODULE_SKIP_TYPES:
            if current:
                runs.append(current)
                current = []
            continue
        current.append(child)
    if current:
        runs.append(current)
    return runs


def _python_module_body_chunks(
    bundle: CodeBundle,
    file_namespace: str,
    file_imports: str,
) -> list[dict]:
    """
    Index executable top-level Python statements as module_body chunks.

    A single statement larger than the size bound is emitted in full with a
    warning. Syntax is never truncated. Consecutive statements are split only
    at statement boundaries.
    """
    chunks = []
    body_index = 0

    def emit(nodes: list[Node], oversized: bool = False) -> None:
        nonlocal body_index
        body_index += 1
        lines, chars, code = _module_group_metrics(bundle.content, nodes)
        start_line, _ = _node_line_span(nodes[0])
        _, end_line = _node_line_span(nodes[-1])
        if oversized:
            print(
                f"Emitting oversized module_body in {bundle.path} "
                f"({lines} lines, {chars} characters) without truncating.",
                file=sys.stderr,
            )
        name = "__module__" if body_index == 1 else f"__module__#{body_index}"
        chunks.append({
            "name": name,
            "code": code,
            "container": "Global",
            "file": str(bundle.path),
            "type": "module_body",
            "start_line": start_line,
            "end_line": end_line,
            "language": bundle.language,
            "namespace": file_namespace,
            "imports": file_imports,
        })

    for run in _python_module_statement_runs(bundle.tree.root_node):
        current: list[Node] = []
        for node in run:
            trial = current + [node]
            if current and _module_group_is_oversized(bundle.content, trial):
                emit(current)
                current = [node]
            else:
                current = trial
            if current and _module_group_is_oversized(bundle.content, current):
                emit(current, oversized=True)
                current = []
        if current:
            emit(current)

    return chunks


def get_container_name(node: Node, container_type: str) -> str:
    """!
    @brief Identifies the parent container name for a given AST node.
    @details Traverses upward from the provided node through its ancestors 
    until a node of the specified container_type is found. It then extracts the text 
    from the container's identifier child. For nested containers, it concatenates
    parent container names with a dot separator.
    
    @param node The tree_sitter.Node to start the upward search from.
    @return The name of the parent container as a string, or "Global" if no container is found.
    @exception TypeError Raised if the input node is not a tree_sitter.Node instance.
    """
    # Validate input
    if not isinstance(node, Node):
        raise TypeError(f"Expected 'node' to be a tree_sitter.Node, got {type(node).__name__}")
    
    containers = []
    current = node.parent
    
    while current:
        if current.type == container_type:
            # Look for the naming child (usually 'identifier' or 'property_identifier')
            for child in current.children:
                if "identifier" in child.type:
                    containers.append(child.text.decode("utf-8"))
                    break
        current = current.parent
    
    if not containers:
        return "Global"
    
    # return concatenated container names from outermost to innermost, dot separated
    return ".".join(reversed(containers))

def get_metadata_by_query(root: Node, lang: str, query_str: str, capture_name: str) -> list[str]:
    """!
    @brief Helper to extract text from a node using a specific query.
    """
    if not query_str: 
        raise ValueError("Query string cannot be empty.")
    query = Query(tslp.get_language(lang), query_str)
    cursor = QueryCursor(query)
    matches = cursor.matches(root)
    
    results = []
    for _, captures in matches:
        for node in captures.get(capture_name, []):
            results.append(node.text.decode("utf-8").strip())
    return results