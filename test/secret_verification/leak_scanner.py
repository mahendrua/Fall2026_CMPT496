"""
@file leak_scanner.py
@brief Finds planted fake secrets in what Checkpoint sent, printed or saved,
independently of the app's own detector (T-079, US-034).
@details The app's redactor decides what to replace; this decides whether it
worked, so it shares nothing with it. It imports nothing from the app and
knows only the planted values. Each value is looked for as written and in
the forms a writer could have turned it into: JSON, Python and HTML
escaping, URL and base64 encoding, UTF-16, and broken across lines. Each
file is read the way it is meant to be read:

    .json                      parsed and walked: every key and string value
    .html .htm .xml .svg       as written, and with tags and entities removed;
                               PlantUML sources embedded in SVG are decoded
    .pdf                       text drawn on the pages, from the decoded
                               content streams; document strings
    .png                       text chunks (PlantUML keeps its source there);
                               the pixels are not read
    .sqlite3 .sqlite .db       every table queried, every value read
    anything else              as UTF-8 or UTF-16 text, or as raw bytes

A finding names the case, the sink and where: file, line, JSON pointer or
table row. It never holds the value or the text around it. A file that
cannot be read or parsed, a required artifact that is missing or empty, and
a raw fixture input passed in as an output are problems: a report with a
problem is incomplete, never clean.

Not covered: values inside images, other compression formats (zip, gzip),
and a value encoded in a way listed nowhere above.
"""

import base64
import html
import json
import re
import sqlite3
import urllib.parse
import zlib
from bisect import bisect_right
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

# A needle shorter than this would match by chance.
MIN_NEEDLE_LENGTH = 8

# Problem kinds.
MISSING = "missing"            # a required artifact does not exist
EMPTY = "empty"                # a required artifact has nothing in it
UNREADABLE = "unreadable"      # it could not be opened or decoded
UNPARSEABLE = "unparseable"    # its format could not be parsed
NO_TEXT = "no_text"            # it was read but held no text to check
PARTIAL = "partial"            # only part of it could be checked
RAW_INPUT = "raw_input"        # a raw fixture input was passed in as output


@dataclass(frozen=True)
class Needle:
    """A planted value to look for, and the lines of it that identify it on their own."""

    case_id: str
    value: str = field(repr=False)
    fragments: tuple = field(default=(), repr=False)


@dataclass(frozen=True)
class Finding:
    case_id: str
    sink: str
    location: str
    encoding: str
    part: str = "value"


@dataclass(frozen=True)
class Problem:
    sink: str
    location: str
    kind: str
    detail: str


@dataclass(frozen=True)
class Inspection:
    """One thing that was read: how, and how much text it held."""

    sink: str
    location: str
    reader: str
    chars: int


# ---------------------------------------------------------------------------
# Encodings
# ---------------------------------------------------------------------------

def _url_lower(text):
    return re.sub(r"%[0-9A-F]{2}", lambda m: m.group(0).lower(), text)


def _forms(text):
    """(encoding, form) for each way a writer could have written text, first name kept for duplicates."""
    data = text.encode("utf-8")
    json_text = json.dumps(text, ensure_ascii=False)[1:-1]
    url = urllib.parse.quote(text, safe="")

    candidates = (
        ("raw", text),
        ("json", json_text),
        ("json-ascii", json.dumps(text)[1:-1]),
        ("json-slash", json_text.replace("/", "\\/")),
        ("json-twice", json.dumps(json_text, ensure_ascii=False)[1:-1]),
        ("python-repr", repr(text)[1:-1]),
        ("html", html.escape(text, quote=True)),
        ("html-noquote", html.escape(text, quote=False)),
        ("url", url),
        ("url-lower", _url_lower(url)),
        ("url-plus", urllib.parse.quote_plus(text)),
        ("url-path", urllib.parse.quote(text)),
        ("base64", base64.b64encode(data).decode("ascii")),
        ("base64-url", base64.urlsafe_b64encode(data).decode("ascii")),
        ("base64-nopad", base64.b64encode(data).decode("ascii").rstrip("=")),
    )

    seen, forms = set(), []
    for encoding, form in candidates:
        if form and form not in seen:
            seen.add(form)
            forms.append((encoding, form))
    return forms


def _parts(needle):
    yield "value", needle.value
    for number, fragment in enumerate(needle.fragments, 1):
        yield f"fragment {number}", fragment


class _Matcher:
    """Every form of every needle, compiled once."""

    def __init__(self, needles):
        self.lookup = {}
        squeezed = {}
        binary = {}

        for needle in needles:
            for part, text in _parts(needle):
                for encoding, form in _forms(text):
                    self.lookup.setdefault(form, []).append((needle.case_id, part, encoding))

                squeezed.setdefault(re.sub(r"\s+", "", text), []).append((needle.case_id, part))

                for codec in ("utf-8", "utf-16-le", "utf-16-be"):
                    binary.setdefault(text.encode(codec), []).append((needle.case_id, part, codec))

        self.pattern = _alternation(self.lookup)
        self.squeezed = squeezed
        self.squeezed_pattern = _alternation(squeezed)
        self.binary = binary
        self.binary_pattern = re.compile(
            b"|".join(re.escape(form) for form in sorted(binary, key=len, reverse=True))
        )

    def hits(self, text):
        """(start, end, case_id, part, encoding) for every exact match."""
        for match in self.pattern.finditer(text):
            for case_id, part, encoding in self.lookup[match.group(0)]:
                yield match.start(), match.end(), case_id, part, encoding

    def split_hits(self, text):
        """Matches that only appear once whitespace (a line break, a wrap) is ignored."""
        kept = [i for i, c in enumerate(text) if not c.isspace()]
        squeezed = "".join(text[i] for i in kept)

        for match in self.squeezed_pattern.finditer(squeezed):
            start, end = kept[match.start()], kept[match.end() - 1] + 1
            if any(c.isspace() for c in text[start:end]):
                for case_id, part in self.squeezed[match.group(0)]:
                    yield start, end, case_id, part, "split by whitespace"

    def binary_hits(self, data):
        for match in self.binary_pattern.finditer(data):
            for case_id, part, codec in self.binary[match.group(0)]:
                yield match.start(), match.end(), case_id, part, f"bytes ({codec})"

    def contains_any(self, text):
        return self.pattern.search(text) is not None or next(self.split_hits(text), None) is not None


def _alternation(forms):
    return re.compile("|".join(re.escape(form) for form in sorted(forms, key=len, reverse=True)))


class _Lines:
    """Line numbers for character offsets."""

    def __init__(self, text):
        self.starts = [0] + [m.end() for m in re.finditer(r"\n", text)]

    def __call__(self, offset):
        return bisect_right(self.starts, offset)


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

_PDF_OBJECT = re.compile(rb"(\d+)\s+(\d+)\s+obj\b(.*?)\bendobj", re.S)
_PDF_STREAM = re.compile(rb"^(?P<dict>.*?)\bstream(?:\r\n|\n|\r)(?P<data>.*)\bendstream\s*$", re.S)
_PDF_FILTER = re.compile(rb"/Filter\s*(\[[^\]]*\]|/\w+)")
_PDF_OPERATOR = re.compile(rb"[A-Za-z'\"*]+")
_PDF_SHOW = {b"Tj", b"TJ", b"'", b'"'}

_IMAGE_CODECS = {b"DCTDecode", b"DCT", b"JPXDecode", b"JBIG2Decode", b"CCITTFaxDecode", b"CCF"}


class _UnsupportedFilter(Exception):
    pass


def _ascii85(data):
    data = re.sub(rb"\s", b"", data)
    if data.startswith(b"<~"):
        data = data[2:]
    if data.endswith(b"~>"):
        data = data[:-2]
    return base64.a85decode(data)


def _ascii_hex(data):
    data = re.sub(rb"[^0-9A-Fa-f]", b"", data.split(b">", 1)[0])
    if len(data) % 2:
        data += b"0"
    return bytes.fromhex(data.decode("ascii"))


def _decode_stream(data, filters):
    """The stream's bytes with its filters undone, or None for image codecs (pixels)."""
    for name in filters:
        if name in (b"FlateDecode", b"Fl"):
            data = zlib.decompressobj().decompress(data)
        elif name in (b"ASCII85Decode", b"A85"):
            data = _ascii85(data)
        elif name in (b"ASCIIHexDecode", b"AHx"):
            data = _ascii_hex(data)
        elif name in _IMAGE_CODECS:
            return None
        else:
            raise _UnsupportedFilter(name.decode("latin-1"))
    return data


_ESCAPES = {ord("n"): b"\n", ord("r"): b"\r", ord("t"): b"\t", ord("b"): b"\b", ord("f"): b"\f"}


def _literal(data, i):
    """(bytes, next index) of the PDF literal string starting at data[i] == '('."""
    depth, i, out = 1, i + 1, bytearray()

    while i < len(data):
        c = data[i]

        if c == 0x5C:  # backslash
            i += 1
            if i >= len(data):
                break
            e = data[i]
            if e in _ESCAPES:
                out += _ESCAPES[e]
            elif 0x30 <= e <= 0x37:
                digits = data[i:i + 3]
                n = len(re.match(rb"[0-7]{1,3}", digits).group(0))
                out.append(int(data[i:i + n], 8) & 0xFF)
                i += n - 1
            elif e in (0x0D, 0x0A):  # line continuation
                if e == 0x0D and data[i + 1:i + 2] == b"\n":
                    i += 1
            else:
                out.append(e)
        elif c == 0x28:
            depth += 1
            out.append(c)
        elif c == 0x29:
            depth -= 1
            if depth == 0:
                return bytes(out), i + 1
            out.append(c)
        else:
            out.append(c)
        i += 1

    return bytes(out), i


def _strings(data):
    """
    Yield (string bytes, operator that follows) for every literal and hex
    string in a content stream or object body. Dictionaries (<< >>) and
    inline image data are skipped.
    """
    i, n, pending = 0, len(data), []

    while i < n:
        c = data[i]

        if c == 0x28:
            text, i = _literal(data, i)
            pending.append(text)
            continue

        if c == 0x3C:
            if data[i + 1:i + 2] == b"<":
                i += 2
                continue
            end = data.find(b">", i)
            if end < 0:
                break
            try:
                pending.append(_ascii_hex(data[i + 1:end]))
            except ValueError:
                pass
            i = end + 1
            continue

        if c == 0x25:  # comment
            end = data.find(b"\n", i)
            i = n if end < 0 else end + 1
            continue

        match = _PDF_OPERATOR.match(data, i)
        if match:
            operator = match.group(0)
            i = match.end()

            if operator == b"ID":  # inline image: binary up to EI
                end = data.find(b"EI", i)
                i = n if end < 0 else end + 2
                pending = []
            elif pending:
                yield b"".join(pending), operator
                pending = []
            continue

        i += 1

    if pending:
        yield b"".join(pending), None


def _pdf_parts(data):
    """
    (page text, document strings, every decoded stream, problems) from PDF bytes.

    Page text is what the text operators draw, one piece per operation.
    Standard fonts use WinAnsiEncoding, read here as cp1252.
    """
    page_text, doc_strings, decoded, problems = [], [], [], []

    if b"/Encrypt" in data:
        problems.append((PARTIAL, "the PDF is encrypted, so its streams could not be read"))

    if re.search(rb"/Identity-H|/Subtype\s*/Type0", data):
        problems.append((PARTIAL, "a font uses glyph codes this scanner cannot turn back into text"))

    objects = list(_PDF_OBJECT.finditer(data))
    if not objects:
        raise ValueError("no PDF objects")

    for obj in objects:
        body = obj.group(3)
        stream = _PDF_STREAM.match(body)

        if stream is None:
            doc_strings += [s.decode("cp1252", "replace") for s, _ in _strings(body)]
            continue

        header = stream.group("dict")
        doc_strings += [s.decode("cp1252", "replace") for s, _ in _strings(header)]

        found = _PDF_FILTER.search(header)
        filters = re.findall(rb"/(\w+)", found.group(1)) if found else []
        raw = stream.group("data")

        try:
            content = _decode_stream(raw, filters)
        except _UnsupportedFilter as exc:
            problems.append((PARTIAL, f"a stream uses the {exc} filter, which this scanner cannot decode"))
            continue
        except (zlib.error, ValueError):
            problems.append((PARTIAL, "a stream could not be decoded"))
            continue

        if content is None:
            continue

        decoded.append(content)

        if b"/Image" in header or b"/XObject" in header and b"/Form" not in header:
            continue

        for text, operator in _strings(content):
            if operator in _PDF_SHOW:
                page_text.append(text.decode("cp1252", "replace"))

    return page_text, doc_strings, decoded, problems


# ---------------------------------------------------------------------------
# PNG and SVG
# ---------------------------------------------------------------------------

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _png_texts(data):
    """(keyword, text) of every tEXt, zTXt and iTXt chunk."""
    if not data.startswith(_PNG_SIGNATURE):
        raise ValueError("not a PNG")

    pos, texts = len(_PNG_SIGNATURE), []

    while pos + 8 <= len(data):
        length = int.from_bytes(data[pos:pos + 4], "big")
        kind = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length

        if kind == b"tEXt":
            key, _, value = body.partition(b"\0")
            texts.append((key.decode("latin-1"), value.decode("latin-1")))
        elif kind == b"zTXt":
            key, _, rest = body.partition(b"\0")
            texts.append((key.decode("latin-1"), zlib.decompress(rest[1:]).decode("latin-1")))
        elif kind == b"iTXt":
            key, _, rest = body.partition(b"\0")
            compressed, rest = rest[0], rest[2:]
            _, _, rest = rest.partition(b"\0")      # language tag
            _, _, value = rest.partition(b"\0")     # translated keyword
            if compressed:
                value = zlib.decompress(value)
            texts.append((key.decode("latin-1"), value.decode("utf-8", "replace")))
        elif kind == b"IEND":
            break

    return texts


# PlantUML's URL text encoding: deflate, then 6 bits per character.
_PLANTUML_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-_"
# Where PlantUML puts it in an SVG: a SRC=[...] comment, or <?plantuml-src ...?>.
_PLANTUML_SOURCE = re.compile(r"(?:SRC=\[|<\?plantuml-src\s+)([0-9A-Za-z\-_~]+)")


def _plantuml_decode(encoded):
    if encoded.startswith("~h"):
        return bytes.fromhex(encoded[2:]).decode("utf-8", "replace")

    bits = bytearray()
    for i in range(0, len(encoded), 4):
        chunk = [_PLANTUML_ALPHABET.index(c) for c in encoded[i:i + 4]]
        chunk += [0] * (4 - len(chunk))
        value = (chunk[0] << 18) | (chunk[1] << 12) | (chunk[2] << 6) | chunk[3]
        bits += bytes([(value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF])

    return zlib.decompressobj(-15).decompress(bytes(bits)).decode("utf-8", "replace")


_TAG = re.compile(r"<[^>]*>")


# ---------------------------------------------------------------------------
# The scanner
# ---------------------------------------------------------------------------

_JSON_SUFFIXES = {".json"}
_MARKUP_SUFFIXES = {".html", ".htm", ".xml", ".svg"}
_SQLITE_SUFFIXES = {".sqlite3", ".sqlite", ".db"}


def _pointer(parts):
    return "/" + "/".join(str(p).replace("~", "~0").replace("/", "~1") for p in parts) if parts else "/"


class LeakScanner:
    """
    Collects findings, problems and what was inspected, sink by sink.

    raw_roots are folders of intentionally raw inputs (the fixture codebase):
    a path inside one is refused as a sink rather than scanned.
    """

    def __init__(self, needles, raw_roots=()):
        needles = tuple(Needle(*n) if not isinstance(n, Needle) else n for n in needles)
        ids = [n.case_id for n in needles]

        if len(set(ids)) != len(ids):
            raise ValueError("Each needle needs its own case ID.")
        for needle in needles:
            if len(needle.value) < MIN_NEEDLE_LENGTH:
                raise ValueError(f"{needle.case_id} is too short to look for reliably.")

        self.needles = needles
        self._matcher = _Matcher(needles)
        self._raw_roots = [Path(r).resolve() for r in raw_roots]
        self.findings, self.problems, self.inspections = [], [], []

    def __repr__(self):
        return f"<LeakScanner: {len(self.needles)} needles, {len(self.findings)} findings>"

    # --- recording ----------------------------------------

    def _safe(self, text):
        """A location that holds a planted value is withheld, by case."""
        cases = sorted({case for _, _, case, _, _ in self._matcher.hits(text)})
        return f"[location withheld: contains {', '.join(cases)}]" if cases else text

    def _record(self, sink, where, text, hits, seen=None):
        """Turn hits in text into findings, skipping (case, part) already in seen."""
        lines = None
        added = []

        for start, _, case_id, part, encoding in hits:
            if seen is not None and (case_id, part) in seen:
                continue
            if lines is None:
                lines = _Lines(text) if isinstance(text, str) else None
            location = f"{where}:{lines(start)}" if lines else where
            finding = Finding(case_id, sink, location, encoding, part)
            if finding not in self.findings:
                self.findings.append(finding)
                added.append(finding)
            if seen is not None:
                seen.add((case_id, part))

        return added

    def _problem(self, sink, where, kind, detail):
        problem = Problem(sink, where, kind, detail)
        if problem not in self.problems:
            self.problems.append(problem)
        return problem

    def _inspected(self, sink, where, reader, chars):
        self.inspections.append(Inspection(sink, where, reader, chars))

    def _scan_string(self, sink, where, text, seen=None):
        """Exact matches, then matches broken by whitespace, in one string."""
        found = self._record(sink, where, text, self._matcher.hits(text), seen)
        done = {(f.case_id, f.part) for f in found} | (seen or set())
        found += self._record(sink, where, text, self._matcher.split_hits(text), done)
        return found

    # --- public API ---------------------------------------

    def scan_text(self, sink, text, location="(text)"):
        """Check one piece of text (captured stdout, a callback payload ...)."""
        if not isinstance(text, str):
            self._problem(sink, location, UNREADABLE, f"expected text, got {type(text).__name__}")
            return []
        self._inspected(sink, location, "text", len(text))
        return self._scan_string(sink, location, text)

    def scan_data(self, sink, data, location="(data)"):
        """Check JSON-shaped data structurally: every key and every string."""
        found, chars = [], [0]

        def walk(value, parts):
            here = f"{location} {_pointer(parts)}"
            if isinstance(value, str):
                chars[0] += len(value)
                found.extend(self._scan_string(sink, here, value))
            elif isinstance(value, dict):
                for key, item in value.items():
                    key_text = str(key)
                    chars[0] += len(key_text)
                    found.extend(self._record(sink, f"{location} {_pointer(parts)} (key)", key_text,
                                              self._matcher.hits(key_text)))
                    walk(item, parts + [self._safe(key_text)])
            elif isinstance(value, (list, tuple)):
                for index, item in enumerate(value):
                    walk(item, parts + [index])
            elif value is None or isinstance(value, (bool, int, float)):
                pass
            else:
                self._problem(sink, here, UNPARSEABLE, f"a {type(value).__name__} is not JSON-shaped; read as text")
                text = repr(value)
                chars[0] += len(text)
                found.extend(self._scan_string(sink, here, text))

        walk(data, [])
        self._inspected(sink, location, "data", chars[0])
        return found

    def scan_file(self, sink, path, base=None):
        """Check one file, read according to its type."""
        path = Path(path)
        where = self._where(path, base)

        if self._is_raw(path):
            self._problem(sink, where, RAW_INPUT, "a raw fixture input is not an output and was not scanned")
            return []

        # A file named after a secret is a leak too.
        found = self._record(sink, f"{where} (file name)", str(path.name), self._matcher.hits(path.name))

        try:
            data = path.read_bytes()
        except OSError as exc:
            self._problem(sink, where, UNREADABLE, type(exc).__name__)
            return found

        suffix = path.suffix.lower()
        reader = (
            self._json if suffix in _JSON_SUFFIXES
            else self._markup if suffix in _MARKUP_SUFFIXES
            else self._pdf if suffix == ".pdf" or data.startswith(b"%PDF-")
            else self._png if suffix == ".png" or data.startswith(_PNG_SIGNATURE)
            else self._sqlite if suffix in _SQLITE_SUFFIXES or data.startswith(b"SQLite format 3\0")
            else self._plain
        )
        return found + reader(sink, where, path, data)

    def require_file(self, sink, path, base=None, nonempty=True):
        """scan_file() for an artifact that must exist (and hold something)."""
        path = Path(path)
        where = self._where(path, base)

        if not path.is_file():
            self._problem(sink, where, MISSING, "the artifact was not produced")
            return []
        if nonempty and path.stat().st_size == 0:
            self._problem(sink, where, EMPTY, "the artifact is empty")
        return self.scan_file(sink, path, base)

    def scan_tree(self, sink, folder, base=None, required=True, exclude=()):
        """Check every file under folder. A missing or empty folder is a problem when required."""
        folder = Path(folder)
        where = self._where(folder, base)

        if self._is_raw(folder):
            self._problem(sink, where, RAW_INPUT, "a raw fixture input is not an output and was not scanned")
            return []

        if not folder.is_dir():
            if required:
                self._problem(sink, where, MISSING, "the folder was not produced")
            return []

        files = sorted(
            p for p in folder.rglob("*")
            if p.is_file() and not any(part in exclude for part in p.relative_to(folder).parts)
        )
        if not files and required:
            self._problem(sink, where, EMPTY, "the folder holds no files")

        found = []
        for path in files:
            found += self.scan_file(sink, path, base if base is not None else folder.parent)
        return found

    def missing(self, sink, location, detail="the artifact was not produced"):
        """Record that something expected was not there to check."""
        return self._problem(sink, location, MISSING, detail)

    def contains_planted(self, text):
        """Whether text holds any planted value, without recording anything."""
        return self._matcher.contains_any(text)

    def report(self):
        return ScanReport(
            tuple(n.case_id for n in self.needles),
            tuple(self.findings),
            tuple(self.problems),
            tuple(self.inspections),
        )

    # --- readers ------------------------------------------

    def _where(self, path, base):
        try:
            text = path.relative_to(base).as_posix() if base is not None else path.as_posix()
        except ValueError:
            text = path.name
        return self._safe(text)

    def _is_raw(self, path):
        resolved = Path(path).resolve()
        return any(resolved == root or root in resolved.parents for root in self._raw_roots)

    def _decode(self, data):
        """Text from bytes, or None when the bytes are not text (UTF-16 without a BOM decodes as UTF-8 with NULs)."""
        try:
            if data.startswith((b"\xff\xfe", b"\xfe\xff")):
                return data.decode("utf-16")
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            return None
        return None if "\x00" in text else text

    def _plain(self, sink, where, path, data):
        text = self._decode(data)
        if text is None:
            return self._binary(sink, where, data)
        self._inspected(sink, where, "text", len(text))
        return self._scan_string(sink, where, text)

    def _binary(self, sink, where, data, seen=None, reader="bytes"):
        if reader:
            self._inspected(sink, where, reader, len(data))
        return self._record(sink, where, None, self._matcher.binary_hits(data), seen)

    def _json(self, sink, where, path, data):
        text = self._decode(data)
        if text is None:
            self._problem(sink, where, UNREADABLE, "not UTF-8 or UTF-16 text")
            return self._binary(sink, where, data)

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            self._problem(sink, where, UNPARSEABLE, "not valid JSON; read as text")
            self._inspected(sink, where, "text", len(text))
            return self._scan_string(sink, where, text)

        return self.scan_data(sink, parsed, where)

    def _markup(self, sink, where, path, data):
        text = self._decode(data)
        if text is None:
            self._problem(sink, where, UNREADABLE, "not UTF-8 or UTF-16 text")
            return self._binary(sink, where, data)

        self._inspected(sink, where, "markup", len(text))
        found = self._scan_string(sink, where, text)
        seen = {(f.case_id, f.part) for f in found}

        # Entities and tags hide nothing: read it again as a browser shows it.
        for label, view in (("unescaped", html.unescape(text)), ("text only", html.unescape(_TAG.sub(" ", text)))):
            found += self._scan_string(sink, f"{where} ({label})", view, seen)
            seen |= {(f.case_id, f.part) for f in found}

        for number, match in enumerate(_PLANTUML_SOURCE.finditer(text), 1):
            try:
                source = _plantuml_decode(match.group(1))
            except (ValueError, zlib.error):
                self._problem(sink, where, PARTIAL, "an embedded PlantUML source could not be decoded")
                continue
            found += self._scan_string(sink, f"{where} (embedded PlantUML source {number})", source)

        return found

    def _pdf(self, sink, where, path, data):
        try:
            page_text, doc_strings, decoded, problems = _pdf_parts(data)
        except ValueError:
            self._problem(sink, where, UNPARSEABLE, "not a readable PDF")
            return self._binary(sink, where, data)

        for kind, detail in problems:
            self._problem(sink, where, kind, detail)

        text = "\n".join(page_text)
        self._inspected(sink, where, "pdf", len(text))

        if not text.strip():
            self._problem(sink, where, NO_TEXT, "no text could be extracted from the pages")

        found = self._scan_string(sink, f"{where} (page text)", text)
        seen = {(f.case_id, f.part) for f in found}

        found += self._scan_string(sink, f"{where} (document strings)", "\n".join(doc_strings), seen)
        seen |= {(f.case_id, f.part) for f in found}

        for stream in decoded:
            found += self._binary(sink, f"{where} (stream bytes)", stream, seen, reader=None)
            seen |= {(f.case_id, f.part) for f in found}

        found += self._binary(sink, f"{where} (file bytes)", data, seen, reader=None)
        return found

    def _png(self, sink, where, path, data):
        try:
            texts = _png_texts(data)
        except (ValueError, zlib.error):
            self._problem(sink, where, UNPARSEABLE, "not a readable PNG")
            return self._binary(sink, where, data)

        self._inspected(sink, where, "png text chunks (pixels not read)", sum(len(t) for _, t in texts))
        found = []
        for key, text in texts:
            found += self._scan_string(sink, f"{where} (text chunk {self._safe(key)})", text)
        return found

    def _sqlite(self, sink, where, path, data):
        found, chars = [], 0
        uri = f"{Path(path).resolve().as_uri()}?mode=ro&immutable=1"

        try:
            connection = sqlite3.connect(uri, uri=True)
        except sqlite3.Error:
            self._problem(sink, where, UNPARSEABLE, "not a readable SQLite database")
            return self._binary(sink, where, data)

        try:
            tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
            for table in tables:
                quoted = '"' + table.replace('"', '""') + '"'
                cursor = connection.execute(f"SELECT * FROM {quoted}")
                columns = [d[0] for d in cursor.description]
                for row_number, row in enumerate(cursor, 1):
                    for column, value in zip(columns, row):
                        if isinstance(value, bytes):
                            value = value.decode("utf-8", "replace")
                        if isinstance(value, str):
                            chars += len(value)
                            found += self._scan_string(
                                sink, f"{where} table {self._safe(table)} row {row_number} column {self._safe(column)}", value,
                            )
        except sqlite3.Error:
            self._problem(sink, where, PARTIAL, "a table could not be read")
        finally:
            connection.close()

        self._inspected(sink, where, "sqlite", chars)
        seen = {(f.case_id, f.part) for f in found}
        return found + self._binary(sink, f"{where} (file bytes)", data, seen, reader=None)


def missing_controls(text, controls):
    """IDs of the nonsecret controls ({id: text}) not found verbatim in text."""
    return sorted(control_id for control_id, control in controls.items() if control not in text)


@dataclass(frozen=True)
class ScanReport:
    case_ids: tuple
    findings: tuple
    problems: tuple
    inspections: tuple

    @property
    def leaks(self):
        return len(self.findings)

    @property
    def complete(self):
        return not self.problems and bool(self.inspections)

    @property
    def ok(self):
        """No leak, no problem, and something was actually inspected."""
        return not self.findings and self.complete

    def sinks(self):
        names = [i.sink for i in self.inspections] + [f.sink for f in self.findings] + [p.sink for p in self.problems]
        return list(dict.fromkeys(names))

    def for_sink(self, sink):
        inspected = [i for i in self.inspections if i.sink == sink]
        findings = [f for f in self.findings if f.sink == sink]
        problems = [p for p in self.problems if p.sink == sink]
        return {
            "inspected": len(inspected),
            "text_chars": sum(i.chars for i in inspected),
            "leaks": len(findings),
            "cases_leaked": sorted({f.case_id for f in findings}),
            "findings": [asdict(f) for f in findings],
            "problems": [asdict(p) for p in problems],
        }

    def by_case(self):
        return {
            case_id: {
                "leaks": sum(f.case_id == case_id for f in self.findings),
                "sinks": sorted({f.sink for f in self.findings if f.case_id == case_id}),
            }
            for case_id in self.case_ids
        }

    def to_dict(self):
        return {
            "ok": self.ok,
            "complete": self.complete,
            "leaks": self.leaks,
            "problems": len(self.problems),
            "inspected": len(self.inspections),
            "readers": dict(Counter(i.reader for i in self.inspections)),
            "cases": self.by_case(),
            "sinks": {sink: self.for_sink(sink) for sink in self.sinks()},
        }
