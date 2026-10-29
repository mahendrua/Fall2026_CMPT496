"""!
@file secret_verification_fixture_test.py
@brief The T-079 ten-secret fixture and the independent leak scanner (US-034).
@details The fixture's recorded positions must describe the files it writes,
and the scanner must find a planted value in every form and file type it
claims to read, report what it could not check as a problem rather than as
clean, and never quote a value. The production detector is then measured on
the fixture files, judged by the fixture and the scanner, not by itself.
Every value is fake and generated at run time; nothing here calls a model or
the network. Assertions report case IDs and counts, never values.
"""

import ast
import base64
import html
import json
import re
import sqlite3
import subprocess
import sys
import urllib.parse
import zlib
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
VERIFICATION = PROJECT_ROOT / "test" / "secret_verification"
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(VERIFICATION))

import leak_scanner  # noqa: E402
import secret_fixture  # noqa: E402
from agent.crawl_config import ACCEPTABLE_EXTENSIONS  # noqa: E402
from leak_scanner import (  # noqa: E402
    EMPTY, MISSING, NO_TEXT, RAW_INPUT, UNPARSEABLE, LeakScanner, missing_controls,
)
from secret_fixture import (  # noqa: E402
    CASE_IDS, DETECTED, KNOWN_MISS, PIPELINE_TEMPLATES, build_fixture, detector_samples,
    make_secrets, write_detector_samples,
)
from utils.secret_redaction import SecretRedactor  # noqa: E402

SECRETS = make_secrets()
VALUE = {s.case_id: s.value for s in SECRETS}
NEEDLES = [(s.case_id, s.value, s.fragments) for s in SECRETS]


def no_values(text):
    """Fail, without quoting it, if any planted value or fragment is in text."""
    __tracebackhide__ = True  # pytest would print text with the frame's arguments
    leaked = sorted(s.case_id for s in SECRETS if s.value in text or any(f in text for f in s.fragments))
    if leaked:
        raise AssertionError(f"planted value(s) of {', '.join(leaked)} found")


@pytest.fixture
def fixture(tmp_path):
    return build_fixture(tmp_path / "inputs")


# ---------------------------------------------------------------------------
# The fixture
# ---------------------------------------------------------------------------

def test_ten_distinct_secrets_with_stable_ids():
    assert CASE_IDS == tuple(f"S{n:02}" for n in range(1, 11))
    assert [s.case_id for s in SECRETS] == list(CASE_IDS)

    values = [s.value for s in SECRETS]
    assert len(set(values)) == 10
    assert all(len(v) >= 8 for v in values)
    # None is part of another, so every finding names one case.
    assert not [(a, b) for a in values for b in values if a is not b and a in b]

    assert {s.category for s in SECRETS} == {"PASSWORD", "API_KEY", "TOKEN", "AUTHORIZATION", "PRIVATE_KEY"}


def test_cases_cover_the_required_kinds_of_secret():
    kinds = " ".join(s.kind for s in SECRETS).lower()
    for kind in ("password", "api key", "token", "stripe", "aws", "github", "bearer",
                 "connection-string", "url", "private key", "query string"):
        assert kind in kinds, kind


def test_same_seed_same_files_other_seed_other_values(tmp_path):
    first = build_fixture(tmp_path / "a")
    second = build_fixture(tmp_path / "b")
    other = build_fixture(tmp_path / "c", seed=80)

    assert first.digests == second.digests
    assert not {s.value for s in first.secrets} & {s.value for s in other.secrets}


def test_refuses_to_write_over_an_existing_folder(tmp_path):
    (tmp_path / "inputs" / "LedgerLite").mkdir(parents=True)
    with pytest.raises(FileExistsError):
        build_fixture(tmp_path / "inputs")


def test_recorded_spans_describe_the_written_files(fixture):
    for o in fixture.occurrences:
        text = fixture.text(o.path)
        assert text[o.start:o.end] == fixture.secret(o.case_id).value, (o.case_id, o.path)
        assert text.count("\n", 0, o.start) + 1 == o.line, (o.case_id, o.path)

    for c in fixture.controls:
        text = fixture.text(c.path)
        assert text[c.start:c.end] == c.text, c.control_id
        assert text.count("\n", 0, c.start) + 1 == c.line, c.control_id

    # Every appearance of a value is recorded: nothing is planted by accident.
    for path in fixture.files:
        text = fixture.text(path)
        for s in fixture.secrets:
            assert text.count(s.value) == len([o for o in fixture.occurrences_in(path) if o.case_id == s.case_id])


def test_controls_never_overlap_or_contain_a_secret(fixture):
    for c in fixture.controls:
        no_values(c.text)
        for o in fixture.occurrences_in(c.path):
            assert not (o.start < c.end and c.start < o.end), (c.control_id, o.case_id)


def test_every_case_is_planted_in_a_file_type_the_pipeline_reads(fixture):
    for case_id in CASE_IDS:
        paths = {o.path for o in fixture.occurrences if o.case_id == case_id}
        assert any(Path(p).suffix in ACCEPTABLE_EXTENSIONS for p in paths), case_id


def test_repeated_occurrences_and_same_file_echoes(fixture):
    s01 = [o for o in fixture.occurrences if o.case_id == "S01"]
    assert {o.context for o in s01} >= {"labelled", "echo", "prose", "plantuml"}
    assert len({o.path for o in s01}) >= 3

    # An echo has no context of its own; it is found only because the same
    # file (one request) also names the secret.
    for echo in (o for o in fixture.occurrences if o.context == "echo"):
        assert any(o.path == echo.path and o.case_id == echo.case_id and o.context == "labelled"
                   for o in fixture.occurrences)

    assert {o.context for o in fixture.occurrences if o.case_id == "S03"} == {"labelled", "query"}
    assert len({o.path for o in fixture.occurrences if o.case_id == "S07"}) == 2


def test_known_gap_a_is_marked_on_the_plantuml_field(fixture):
    gaps = [o for o in fixture.occurrences if o.known_gap]
    assert [(o.case_id, o.path, o.known_gap) for o in gaps] == [("S01", "docs/architecture.md", "A")]


def test_detector_only_samples_are_kept_out_of_the_codebase(tmp_path, fixture):
    samples = write_detector_samples(tmp_path / "detector_only")
    names = {s.name for s in samples}

    assert not names & set(PIPELINE_TEMPLATES)
    assert not any((fixture.root / name).exists() for name in names)
    assert {s.expectation for s in samples} == {DETECTED, KNOWN_MISS}

    # The formats listed as detector-only really are formats the pipeline skips.
    for sample in samples:
        if sample.expectation == DETECTED:
            assert Path(sample.name).suffix not in ACCEPTABLE_EXTENSIONS, sample.name


def test_changed_files_notices_any_change(fixture):
    assert fixture.changed_files() == []

    path = fixture.path("tools/report_export.py")
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8", newline="\n")
    fixture.path("web/session.js").unlink()

    assert fixture.changed_files() == ["tools/report_export.py", "web/session.js"]


def test_no_repr_shows_a_value(fixture):
    no_values(repr(fixture))
    no_values(repr(fixture.secrets))
    no_values(repr(fixture.occurrences))
    no_values(repr(detector_samples()))
    no_values(repr(LeakScanner(NEEDLES)))


def test_no_complete_vendor_token_or_key_is_committed():
    """The values are built at run time, so the repository holds none of them."""
    committed = [
        VERIFICATION / "secret_fixture.py",
        VERIFICATION / "leak_scanner.py",
        Path(__file__),
    ]
    patterns = [
        r"AKIA[0-9A-Z]{16}",
        r"ghp_[A-Za-z0-9]{36}",
        r"sk_live_[0-9A-Za-z]{16,}",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    ]
    for path in committed:
        text = path.read_text(encoding="utf-8")
        no_values(text)
        for pattern in patterns:
            assert not re.search(pattern, text), (path.name, pattern)


# ---------------------------------------------------------------------------
# The scanner is independent
# ---------------------------------------------------------------------------

def test_scanner_imports_only_the_standard_library():
    tree = ast.parse((VERIFICATION / "leak_scanner.py").read_text(encoding="utf-8"))
    modules = {
        alias.name.split(".")[0]
        for node in ast.walk(tree) if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    }
    assert modules <= set(sys.stdlib_module_names), sorted(modules - set(sys.stdlib_module_names))


def test_scanner_loads_nothing_from_the_app():
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); import leak_scanner; "
        "print(sorted(m for m in sys.modules if m.split('.')[0] in ('utils', 'agent', 'backend', 'src')))"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", code, str(VERIFICATION)],
        capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip() == "[]"


# ---------------------------------------------------------------------------
# The scanner finds deliberate leaks
# ---------------------------------------------------------------------------

def _plantuml_encode(text):
    compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
    data = compressor.compress(text.encode("utf-8")) + compressor.flush()
    data += b"\0" * (-len(data) % 3)
    alphabet = leak_scanner._PLANTUML_ALPHABET
    out = []
    for i in range(0, len(data), 3):
        value = int.from_bytes(data[i:i + 3], "big")
        out += [alphabet[(value >> shift) & 0x3F] for shift in (18, 12, 6, 0)]
    return "".join(out)


def _png_with_text(path, key, text):
    def chunk(kind, body):
        return len(body).to_bytes(4, "big") + kind + body + zlib.crc32(kind + body).to_bytes(4, "big")

    header = (1).to_bytes(4, "big") * 2 + bytes([8, 0, 0, 0, 0])
    pixels = zlib.compress(b"\x00\x00")
    path.write_bytes(
        leak_scanner._PNG_SIGNATURE
        + chunk(b"IHDR", header)
        + chunk(b"zTXt", key.encode() + b"\0\0" + zlib.compress(text.encode("latin-1")))
        + chunk(b"IDAT", pixels)
        + chunk(b"IEND", b"")
    )


def _pdf(path, paragraphs, narrow=False, code=None):
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, Preformatted, SimpleDocTemplate

    styles = getSampleStyleSheet()
    size = (160, 400) if narrow else (612, 792)
    doc = SimpleDocTemplate(str(path), pagesize=size, leftMargin=20, rightMargin=20, topMargin=20, bottomMargin=20)
    story = [Paragraph(html.escape(p), styles["BodyText"]) for p in paragraphs]
    if code:
        story.append(Preformatted(code, styles["Code"]))
    doc.build(story)


# (name, case, write(folder) -> file name, reader the scanner should use)
LEAKS = [
    ("raw text", "S01", lambda d: _write(d / "log.txt", f"login failed for {VALUE['S01']}\n")),
    ("JSON value", "S02", lambda d: _write(d / "summary.json", json.dumps({"a": {"b": [f"key {VALUE['S02']}"]}}))),
    ("JSON key", "S03", lambda d: _write(d / "rules.json", json.dumps({VALUE["S03"]: 1}))),
    ("JSON escaped twice", "S10", lambda d: _write(
        d / "nested.json", json.dumps({"log": json.dumps({"key": VALUE["S10"]})}))),
    ("HTML entities", "S04", lambda d: _write(
        d / "report.html", "<p>" + "".join(f"&#{ord(c)};" for c in VALUE["S04"]) + "</p>")),
    ("URL encoding", "S01", lambda d: _write(d / "request.log", "GET /x?p=" + urllib.parse.quote(VALUE["S01"], safe=""))),
    ("base64", "S02", lambda d: _write(d / "header.txt", base64.b64encode(VALUE["S02"].encode()).decode())),
    ("split across lines", "S03", lambda d: _write(
        d / "notes.md", f"token {VALUE['S03'][:15]}\n{VALUE['S03'][15:]} done")),
    ("one line of the key", "S10", lambda d: _write(d / "stderr.txt", "bad line: " + VALUE["S10"].splitlines()[2])),
    ("UTF-16 text", "S06", lambda d: _write_bytes(d / "console.txt", ("x " + VALUE["S06"]).encode("utf-16"))),
    ("UTF-16 bytes", "S07", lambda d: _write_bytes(d / "dump.bin", b"\x00\x01" + VALUE["S07"].encode("utf-16-le"))),
    ("file name", "S05", lambda d: _write(d / f"{VALUE['S05']}.txt", "nothing inside")),
    ("SQLite row", "S08", lambda d: _sqlite(d / "chroma.sqlite3", f"Password={VALUE['S08']};")),
    ("PNG text chunk", "S01", lambda d: _png(d / "diagram.png", f'-password : string = "{VALUE["S01"]}"')),
    ("SVG PlantUML source", "S01", lambda d: _write(
        d / "diagram.svg", f"<svg><!--SRC=[{_plantuml_encode('class A { -p = ' + VALUE['S01'] + ' }')}]--></svg>")),
    ("PDF paragraph", "S09", lambda d: _pdf_file(d / "uml.pdf", [f"Connects as {VALUE['S09']} daily."])),
    ("PDF wrapped word", "S03", lambda d: _pdf_file(d / "wrapped.pdf", [VALUE["S03"]], narrow=True)),
    ("PDF code block", "S07", lambda d: _pdf_file(d / "code.pdf", ["Code:"], code=f'auth = "{VALUE["S07"]}"')),
]


def _write(path, text):
    path.write_text(text, encoding="utf-8")
    return path.name


def _write_bytes(path, data):
    path.write_bytes(data)
    return path.name


def _sqlite(path, text):
    # Longer than a page, so the value sits in an overflow page, as long chunks do.
    db = sqlite3.connect(path)
    try:
        db.execute("CREATE TABLE embedding_fulltext_search (id INTEGER, string_value TEXT)")
        db.execute("INSERT INTO embedding_fulltext_search VALUES (1, ?)", ("x" * 5000 + text,))
        db.commit()
    finally:
        db.close()
    return path.name


def _png(path, text):
    _png_with_text(path, "plantuml", text)
    return path.name


def _pdf_file(path, paragraphs, **options):
    _pdf(path, paragraphs, **options)
    return path.name


@pytest.mark.parametrize("label,case_id,write", LEAKS, ids=[label for label, _, _ in LEAKS])
def test_finds_a_deliberate_leak(tmp_path, label, case_id, write):
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    name = write(outputs)

    scanner = LeakScanner(NEEDLES)
    scanner.scan_tree("outputs", outputs)
    report = scanner.report()

    assert not report.ok
    assert {f.case_id for f in report.findings} == {case_id}, label
    assert report.inspections
    # The location says where; for a file named after a secret it is withheld.
    if label == "file name":
        assert all("location withheld" in f.location for f in report.findings)
    else:
        assert all(f.location.startswith(f"outputs/{name}") for f in report.findings), label
    no_values(json.dumps(report.to_dict()))


def test_finds_every_case_in_a_raw_copy_of_the_codebase(tmp_path, fixture):
    """A sink that copied the source as it is leaks all ten, file by file and line by line."""
    leaking = tmp_path / "outputs" / "copied"
    for path in fixture.files:
        target = leaking / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(fixture.path(path).read_bytes())

    scanner = LeakScanner(fixture.needles(), raw_roots=[fixture.root])
    scanner.scan_tree("copied", leaking, base=leaking)
    report = scanner.report()

    assert {f.case_id for f in report.findings} == set(CASE_IDS)
    for o in fixture.occurrences:
        assert any(f.case_id == o.case_id and f.location == f"{o.path}:{o.line}" for f in report.findings), \
            (o.case_id, o.path, o.line)
    assert report.by_case()["S01"]["leaks"] == 4
    no_values(json.dumps(report.to_dict()))


def test_clean_outputs_are_ok(tmp_path, fixture):
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    controls = {c.control_id: c.text for c in fixture.controls}

    (outputs / "summary.json").write_text(json.dumps({
        "summary": "Uses [REDACTED_PASSWORD_1] and [REDACTED_API_KEY_1].",
        "controls": list(controls.values()),
    }), encoding="utf-8")
    (outputs / "report.html").write_text("<p>" + html.escape(controls["C20"]) + "</p>", encoding="utf-8")
    _pdf(outputs / "uml.pdf", [controls["C01"], controls["C24"]], code=controls["C25"])

    scanner = LeakScanner(fixture.needles())
    scanner.scan_tree("outputs", outputs)
    report = scanner.report()

    assert report.ok, report.to_dict()
    assert report.for_sink("outputs")["inspected"] == 3
    assert all(i.chars > 0 for i in report.inspections)
    saved = json.loads((outputs / "summary.json").read_text(encoding="utf-8"))
    assert missing_controls("\n".join(saved["controls"]), controls) == []


def test_pdf_text_is_really_extracted(tmp_path):
    """The PDF reader reads the drawn text, so a clean PDF is checked, not skipped."""
    path = tmp_path / "plain.pdf"
    _pdf(path, ["Invoices above 5000 CAD need manager approval."], code="+ApproveInvoice(invoice : Invoice) : bool")

    page_text, _, _, problems = leak_scanner._pdf_parts(path.read_bytes())
    text = "\n".join(page_text)

    assert problems == []
    assert "Invoices above 5000 CAD need manager approval." in text
    assert "+ApproveInvoice(invoice : Invoice) : bool" in text


# ---------------------------------------------------------------------------
# What the scanner could not check is never clean
# ---------------------------------------------------------------------------

def _no_text_pdf(path):
    from reportlab.pdfgen.canvas import Canvas
    canvas = Canvas(str(path))
    canvas.rect(10, 10, 100, 100)
    canvas.save()


@pytest.mark.parametrize("label,make,kind", [
    ("missing file", lambda d: scanner_require(d / "absent.json"), MISSING),
    ("empty file", lambda d: scanner_require(_touch(d / "empty.json")), EMPTY),
    ("invalid JSON", lambda d: scanner_require(_text(d / "bad.json", "{not json")), UNPARSEABLE),
    ("PDF without text", lambda d: scanner_require(_made(d / "blank.pdf", _no_text_pdf)), NO_TEXT),
    ("empty folder", lambda d: scanner_tree(_folder(d / "nothing")), EMPTY),
    ("missing folder", lambda d: scanner_tree(d / "never_made"), MISSING),
])
def test_unchecked_artifacts_are_problems(tmp_path, label, make, kind):
    scanner = make(tmp_path)
    report = scanner.report()

    assert report.leaks == 0
    assert not report.ok, label
    assert kind in {p.kind for p in report.problems}, label


def scanner_require(path):
    scanner = LeakScanner(NEEDLES)
    scanner.require_file("artifact", path, base=path.parent)
    return scanner


def scanner_tree(folder):
    scanner = LeakScanner(NEEDLES)
    scanner.scan_tree("outputs", folder, base=folder.parent)
    return scanner


def _touch(path):
    path.write_bytes(b"")
    return path


def _text(path, text):
    path.write_text(text, encoding="utf-8")
    return path


def _made(path, make):
    make(path)
    return path


def _folder(path):
    path.mkdir()
    return path


def test_nothing_inspected_is_not_ok():
    assert not LeakScanner(NEEDLES).report().ok


def test_raw_fixture_inputs_are_refused_as_sinks(fixture):
    scanner = LeakScanner(fixture.needles(), raw_roots=[fixture.root])
    scanner.scan_tree("outputs", fixture.root)
    scanner.scan_file("outputs", fixture.path("web/session.js"))
    report = scanner.report()

    assert report.leaks == 0
    assert {p.kind for p in report.problems} == {RAW_INPUT}
    assert not report.ok


def test_missing_controls_names_changed_controls():
    controls = {"C01": "ApprovalThreshold = 5000m", "C05": 'var primaryKey = "InvoiceId";'}
    assert missing_controls('ApprovalThreshold = 5000m; var primaryKey = "InvoiceId";', controls) == []
    assert missing_controls('ApprovalThreshold = 5000m; var primaryKey = "[REDACTED_API_KEY_1]";', controls) == ["C05"]


# ---------------------------------------------------------------------------
# The production detector, measured on the fixture
# ---------------------------------------------------------------------------
# Each file is redacted on its own, as one file summary request would carry
# it. The fixture says what must go and what must stay; the scanner says
# what is left.

GAP_A = pytest.mark.xfail(strict=True, reason="Known gap A: PlantUML -password : string = \"...\" is not detected")


@pytest.mark.parametrize("path", [
    pytest.param(p, marks=GAP_A) if p == "docs/architecture.md" else p for p in PIPELINE_TEMPLATES
])
def test_detector_on_each_pipeline_file(fixture, path):
    text = fixture.text(path)
    redactor = SecretRedactor()
    redactor.scan(text)
    redacted = redactor.redact(text)

    scanner = LeakScanner(fixture.needles())
    scanner.scan_text("detector", redacted, path)
    report = scanner.report()

    controls = {c.control_id: c.text for c in fixture.controls_in(path)}
    assert missing_controls(redacted, controls) == []
    assert redactor.report().replaced == fixture.expected_counts(path)
    assert redactor.report().distinct == fixture.expected_distinct(path)
    assert report.leaks == 0, sorted({f.case_id for f in report.findings})


@pytest.mark.parametrize("sample", [s for s in detector_samples() if s.expectation == DETECTED],
                         ids=lambda s: s.name)
def test_detector_on_formats_the_pipeline_does_not_read(sample):
    redactor = SecretRedactor()
    redactor.scan(sample.text)
    redacted = redactor.redact(sample.text)

    scanner = LeakScanner(NEEDLES)
    scanner.scan_text("detector_only", redacted, sample.name)

    assert scanner.report().leaks == 0, sorted({f.case_id for f in scanner.report().findings})
    assert missing_controls(redacted, {c.control_id: c.text for c in sample.controls}) == []
    assert redactor.report().total == len(sample.occurrences)
