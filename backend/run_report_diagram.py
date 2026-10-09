"""
backend/run_report_diagram.py

One class diagram for the whole project (US-049, T-166), instead of one UML
PDF per file. Built from the types and relationships in the file summaries
(see run_report_data.py), grouped by folder, and drawn as SVG with the same
plantuml.jar the UML step uses. Each class links to "#type-<id>", which the
HTML report turns into a details panel.

No AI calls. When Java or the jar is missing, or PlantUML fails, the report
still gets the type list and the PlantUML text, with the reason the picture
is missing.
"""

import math
import re
import shutil
import subprocess
from pathlib import Path


# Large projects take a while to lay out; a stuck render must not hold up the run.
RENDER_TIMEOUT_SECONDS = 180

PLANTUML_KINDS = {"class": "class", "interface": "interface", "enum": "enum", "struct": "struct"}


def find_plantuml_jar(app_dir):
    """Same places the UML step looks (Commands.generate_uml)."""
    for path in (Path(app_dir) / "plantuml.jar", Path.home() / "plantuml.jar"):
        if path.exists():
            return path
    return None


def build_diagram(data, app_dir):
    """
    The diagram for one run's report. Never raises.

    @return {"types": [...], "edges": [...], "puml": str, "svg": str or "",
             "error": str or "", "skipped_relationships": int}
    """
    try:
        types, edges, skipped = project_types(data.get("files") or [])
        puml = to_plantuml(types, edges, data.get("codebase") or "project")
    except Exception as exc:
        return {"types": [], "edges": [], "puml": "", "svg": "",
                "error": f"Could not build the diagram: {exc}", "skipped_relationships": 0}

    result = {"types": types, "edges": edges, "puml": puml, "svg": "",
              "error": "", "skipped_relationships": skipped}

    if not types:
        result["error"] = "The file summaries list no classes, so there is nothing to draw."
        return result

    jar = find_plantuml_jar(app_dir)

    try:
        result["svg"] = render_svg(puml, jar)
    except Exception as exc:
        result["error"] = str(exc)
        return result

    # The layout's shape depends on the project: folders with no links between
    # them come out as one very wide row, deep inheritance as a tall column.
    # When the picture is far from a screen's shape, also try left-to-right and
    # the ELK layout (which packs unlinked folders into a grid), and keep the
    # one that shows biggest when fitted to a screen.
    if _off_shape(result["svg"]) > TRY_OTHER_LAYOUT_ABOVE:
        name = data.get("codebase") or "project"
        for other_puml in (to_plantuml(types, edges, name, left_to_right=True),
                           to_plantuml(types, edges, name, engine="elk")):
            try:
                other_svg = render_svg(other_puml, jar)
            except Exception:
                continue  # keep the best picture so far
            if _fit_scale(other_svg) > _fit_scale(result["svg"]):
                result["puml"], result["svg"] = other_puml, other_svg

    return result


# A typical screen for the report (width x height in pixels). A first picture
# whose shape is this far off (log of the ratio; log(2.5) ~ 0.92, i.e. 2.5
# times too wide or too tall) is drawn again with the other layouts.
SCREEN = (1600, 1000)
TRY_OTHER_LAYOUT_ABOVE = 0.92


def _size(svg):
    match = re.search(r'viewBox="[\d.]+ [\d.]+ ([\d.]+) ([\d.]+)"', svg)
    if not match or not float(match.group(1)) or not float(match.group(2)):
        return None
    return float(match.group(1)), float(match.group(2))


def _off_shape(svg):
    """How far the picture's shape is from a screen's: 0 is the same shape."""
    size = _size(svg)
    if size is None:
        return 0.0
    return abs(math.log((size[0] / size[1]) / (SCREEN[0] / SCREEN[1])))


def _fit_scale(svg):
    """How big the picture shows when fitted to the screen (1 = full size; no gain beyond)."""
    size = _size(svg)
    if size is None:
        return 0.0
    return min(1.0, SCREEN[0] / size[0], SCREEN[1] / size[1])


# ---------------------------------------------------------
# Types and relationships
# ---------------------------------------------------------

def base_name(name):
    """'StateMachine<TState, TTrigger>' -> 'StateMachine', for matching names."""
    return re.split(r"[<\[(]", str(name or ""), maxsplit=1)[0].strip().split(".")[-1]


def project_types(files):
    """
    One entry per type in the project, and the links between them.

    Partial classes (one class spread over several files in the same folder)
    become one entry. A relationship is kept
    only when both ends are types of this project; links to library types
    (Exception, IEnumerable...) are counted, not drawn.
    """
    types, by_key = [], {}

    for item in files:
        for entry in item.get("types") or []:
            name = base_name(entry.get("name"))
            if not name:
                continue

            key = (item.get("folder", "."), name)
            node = by_key.get(key)

            if node is None:
                node = {
                    "id": f"T{len(types) + 1}",
                    "name": entry.get("name") or name,
                    "kind": entry.get("kind") if entry.get("kind") in PLANTUML_KINDS else "class",
                    "folder": item.get("folder", "."),
                    "files": [],
                    "description": entry.get("description", ""),
                    "inherits_from": [],
                    "enum_values": [],
                    "properties": [],
                    "methods": [],
                }
                by_key[key] = node
                types.append(node)

            if item.get("path") not in node["files"]:
                node["files"].append(item.get("path"))
            for field in ("inherits_from", "enum_values", "properties", "methods"):
                for value in entry.get(field) or []:
                    if value not in node[field]:
                        node[field].append(value)

    by_name = {}
    for node in types:
        by_name.setdefault(base_name(node["name"]), []).append(node)

    def resolve(name, folder):
        candidates = by_name.get(base_name(name), [])
        same_folder = [node for node in candidates if node["folder"] == folder]
        if same_folder:
            return same_folder[0]
        if len(candidates) == 1:
            return candidates[0]
        return None  # a library type, or a name used in several folders

    edges, seen, skipped = [], set(), 0

    def add(source, target, kind):
        nonlocal skipped
        if source is None or target is None:
            skipped += 1
            return
        if source is target:
            return
        if kind == "inheritance" and target["kind"] == "interface" and source["kind"] != "interface":
            kind = "implements"
        key = (source["id"], target["id"], kind)
        if key not in seen:
            seen.add(key)
            edges.append({"source": source["id"], "target": target["id"], "kind": kind})

    for item in files:
        folder = item.get("folder", ".")
        for link in (item.get("relationships") or []) + (item.get("external_relationships") or []):
            if not isinstance(link, dict):
                continue
            kind = "inheritance" if link.get("relationship_type") == "inheritance" else "association"
            add(resolve(link.get("source"), folder), resolve(link.get("target"), folder), kind)

    # inherits_from is filled even when the AI left the relationship out.
    for node in types:
        for parent in node["inherits_from"]:
            target = resolve(parent, node["folder"])
            if target is not None:
                add(node, target, "inheritance")

    return types, edges, skipped


# ---------------------------------------------------------
# PlantUML
# ---------------------------------------------------------

ARROWS = {"inheritance": "--|>", "implements": "..|>", "association": "-->"}


def _quote(text):
    """Text safe inside a quoted PlantUML name: no quotes, brackets or line breaks."""
    return re.sub(r"[\"\[\]\r\n\t]", " ", str(text)).strip()


def to_plantuml(types, edges, codebase_name, left_to_right=False, engine=None):
    lines = [
        "@startuml",
        "set separator none",
        "hide members",
        "hide empty methods",
        "skinparam shadowing false",
        "skinparam packageStyle rectangle",
        "skinparam defaultFontName Arial",
        "skinparam class {",
        "  BackgroundColor #FFFFFF",
        "  BorderColor #5B6B7F",
        "  ArrowColor #5B6B7F",
        "}",
        "skinparam package {",
        "  BackgroundColor #F6F8FA",
        "  BorderColor #A0AEC0",
        "  FontColor #33415C",
        "}",
    ]

    if engine:
        lines.insert(1, f"!pragma layout {engine}")
    if left_to_right:
        lines.append("left to right direction")

    folders = {}
    for node in types:
        folders.setdefault(node["folder"], []).append(node)

    for index, folder in enumerate(sorted(folders, key=str.lower), 1):
        label = codebase_name if folder == "." else folder
        lines.append(f'package "{_quote(label)}" as P{index} {{')
        for node in folders[folder]:
            kind = PLANTUML_KINDS[node["kind"]]
            lines.append(f'  {kind} "{_quote(node["name"])}" as {node["id"]} [[#type-{node["id"]}]]')
        lines.append("}")

    for edge in edges:
        lines.append(f'{edge["source"]} {ARROWS[edge["kind"]]} {edge["target"]}')

    lines.append("@enduml")
    return "\n".join(lines) + "\n"


def render_svg(puml, jar_path, timeout=RENDER_TIMEOUT_SECONDS):
    """Draw the diagram with plantuml.jar and return the SVG text."""
    if jar_path is None:
        raise RuntimeError("plantuml.jar was not found, so the diagram could not be drawn.")

    java = shutil.which("java")
    if java is None:
        raise RuntimeError("Java is not installed (or not on PATH), so the diagram could not be drawn.")

    try:
        completed = subprocess.run(
            [java, "-Djava.awt.headless=true", "-DPLANTUML_LIMIT_SIZE=32768",
             "-jar", str(jar_path), "-tsvg", "-charset", "UTF-8", "-pipe"],
            input=puml.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            # No console window flashing up when run from the app on Windows.
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"PlantUML took longer than {timeout} seconds, so the diagram was skipped.")

    svg = completed.stdout.decode("utf-8", errors="replace")
    start = svg.find("<svg")

    if completed.returncode != 0 or start < 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip().splitlines()
        raise RuntimeError(
            "PlantUML could not draw the diagram"
            + (f": {detail[-1]}" if detail else ".")
        )

    return svg[start:]
