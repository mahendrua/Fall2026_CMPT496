"""
utils/secret_redaction.py

Finds secrets in text on this machine and swaps them for placeholders before
the text is sent to an AI provider (T-077, US-034).

    password = "Fake-Pass-111!"   ->   password = "[REDACTED_PASSWORD_1]"

Detection is plain pattern matching: no AI call, no network. It is
contextual. A value counts as a secret only when the text around it says so,
e.g. it is assigned to a name ending in "password". A string is not redacted
just because it is long or contains the word "key".

Only the value is replaced. Names, quotation marks, separators, comments and
line breaks stay as they were, so the code around a secret still reads
correctly.

One SecretRedactor serves one outgoing request:

    redactor = SecretRedactor()
    redactor.scan(system_prompt, user_prompt)    # learn every secret first
    safe = [redactor.redact(system_prompt), redactor.redact(user_prompt)]
    redactor.report()                             # counts per category only

The same secret gets the same placeholder everywhere in that request. The
secret-to-placeholder mapping lives only on that object. It is never saved,
logged or shared, so two requests running at once cannot see each other's
values. Text that has already been redacted comes back unchanged.

Detected
--------
Passwords
    A quoted value assigned to, or compared with, a name that ends in
    password, passwd, pwd, passphrase or pass (DB_PASSWORD, adminPwd,
    smtp_pass, password2):
        C#       string Password = "..."; Password { get; set; } = "...";
                 @"..." verbatim strings; settings["Db:Password"] = "..."
        Python   password = "..."; password: str = "..."; connect(password="...")
        JS/TS    const password = '...'; { password: `...` }
        JSON     "password": "..."
        XML      password="..."
    An unquoted value on a config-style line:
        .env     DB_PASSWORD=...           export DB_PASSWORD=...
        YAML     password: ...             - password: ...
        INI      password = ...   (only when the value is not a bare identifier)
    Command-line flags: --password=...  --password ...

Left alone
----------
Environment-variable and config lookups (os.getenv("DB_PASSWORD"),
process.env.DB_PASSWORD, Configuration["Db:Password"]); template references
(${DB_PASSWORD}, $(DbPassword), %DB_PASSWORD%, {{ password }}); obvious
stand-ins (<password>, ********, YOUR_PASSWORD, changeme, PASSWORD_HERE);
our own [REDACTED_...] placeholders; labels ("Password", "Enter your
password"); names that only start with a password word (PasswordLength,
password_hint); and words that merely end in "pass" (bypass, compass).

Limitations
-----------
This is pattern matching, not proof that nothing sensitive remains. It
misses a secret with no telling name beside it (Login("admin", "...")), a
value passed positionally, an unquoted INI value that looks like an
identifier (password = letmein), an unquoted value containing spaces, and a
secret built by string concatenation. It can over-redact a variable in a
JavaScript object written one property per line (password: pw).
"""

import re
from collections import Counter
from dataclasses import dataclass


PASSWORD = "PASSWORD"

# [REDACTED_PASSWORD_1], [REDACTED_API_KEY_2] ...
PLACEHOLDER_RE = re.compile(r"\[REDACTED_([A-Z]+(?:_[A-Z]+)*)_(\d+)\]")

# Shorter values are noise ("", "x", "?"), not credentials.
MIN_SECRET_LENGTH = 3

# A secret found in context is also replaced where it appears on its own
# elsewhere in the same request, but only when it is long and mixed enough
# not to be an ordinary word. Otherwise password = "administrator" would
# blank out every "administrator" in the code.
MIN_PROPAGATED_LENGTH = 8


class SecretRedactionError(RuntimeError):
    """The text could not be checked for secrets, so it must not be sent."""


# ---------------------------------------------------------
# Patterns
# ---------------------------------------------------------

# Longest first: the name's ending is matched against these in order.
_PASSWORD_WORDS = ("passphrase", "password", "passwd", "pwd", "pass")

# Words that also end ordinary words (bypass, compass), so they only count
# after a separator or a camelCase hump: db_pass, smtpPass, PASS.
_SHORT_WORDS = {"pass"}

# Shell variables that hold a directory, not a password.
_NOT_PASSWORD_NAMES = {"PWD", "OLDPWD"}


def _alternation(words):
    return "|".join(words)


def _name(words):
    """A name ending in one of words, optionally numbered: DB_PASSWORD, adminPwd, pwd2."""
    return (
        r"(?:[A-Za-z_$@][\w$@.\-]*?)?"
        rf"(?:{_alternation(words)})"
        r"(?:[_\-]?\d{1,3})?"
    )


# A quoted value. The group that matched says how it was quoted.
_LITERAL = r"""
    (?:
        (?P<vpre>\$@|@\$|@)"(?P<verbatim>(?:[^"]|"")*)"
      | (?P<dpre>[rbuf$]{0,2})"(?P<dq>(?:[^"\\\r\n]|\\.)*)"
      | (?P<spre>[rbuf]{0,2})'(?P<sq>(?:[^'\\\r\n]|\\.)*)'
      | `(?P<bq>(?:[^`\\]|\\.)*)`
    )
"""

_LITERAL_GROUPS = ("verbatim", "dq", "sq", "bq")


def _assignment_re(words):
    """
    name = "value", name: 'value', "name": "value", ["name"] = @"value" ...

    The lookbehind keeps a match from starting in the middle of a longer
    name. Without it every character of a long run like a-b-c-... would
    start a scan to the end of the run, which is quadratic.
    """
    name = _name(words)
    return re.compile(
        rf"""
        (?:
            (?<![\w$@.\-])(?P<name>{name})
          | (?P<kq>["'])(?P<qname>[^"'\r\n]{{0,200}}?(?:{_alternation(words)})(?:[_\-]?\d{{1,3}})?)(?P=kq)\]?
        )
        (?:[ \t]*\{{[ \t]*get;[^}}\r\n]*\}})?
        (?:[ \t]*:[ \t]*[A-Za-z_][\w.]*(?:\[[\w., ]*\])?\??(?=[ \t]*=[^=>]))?
        [ \t]*(?P<op>===?|!==?|:=|=>|=|:)\s*
        {_LITERAL}
        """,
        re.VERBOSE | re.IGNORECASE,
    )


def _config_line_re(words):
    """
    KEY=value, KEY: value or KEY = value as a whole line (.env, YAML, INI).

    The value runs to the end of the line. Comments and code-shaped values
    are dealt with in _config_line(), not here: a lazy match up to an
    optional comment would backtrack quadratically on a long minified line.
    """
    return re.compile(
        rf"""
        ^[ \t]*(?:export[ \t]+|-[ \t]+)?
        (?P<name>{_name(words)})
        (?:(?P<tight>=)|[ \t]*(?P<sep>[:=])[ \t]+)
        (?P<value>[^\s"'`=>\#][^\r\n]*)
        """,
        re.VERBOSE | re.IGNORECASE | re.MULTILINE,
    )


_PASSWORD_ASSIGNMENT = _assignment_re(_PASSWORD_WORDS)
_PASSWORD_CONFIG_LINE = _config_line_re(_PASSWORD_WORDS)

# mysql --password=... / --password "..." / openssl -pass ...
_PASSWORD_FLAG = re.compile(
    rf"""
    (?<![\w-])--?(?P<name>{_alternation(_PASSWORD_WORDS)})
    (?:=|[ \t]+)
    (?:
        "(?P<dq>[^"\r\n]*)"
      | '(?P<sq>[^'\r\n]*)'
      | (?P<bare>[^\s"'`\-][^\s"'`]*)
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)


# ---------------------------------------------------------
# What is not a secret
# ---------------------------------------------------------

# Looked up, templated or parameterised: the real value is somewhere else.
_REFERENCE_RE = re.compile(
    r"""
    ^(?:
        \$[A-Za-z_]\w*              # $DB_PASSWORD
      | %[A-Za-z_][\w.]*%           # %DB_PASSWORD%
      | __[A-Za-z][\w.]*__          # __DbPassword__ (pipeline token replace)
      | <[^<>]+>                    # <your-password>
      | \*[\w\-]+                   # YAML alias *default_password
      | \{\d*\}                     # {0}
      | %[sdr]                      # %s
      | \?                          # SQL parameter
      | [:@][A-Za-z_]\w*            # :password, @Password SQL parameters
    )$
    """,
    re.VERBOSE,
)

# ${...}, ${{ secrets.X }}, $(...), {{ x }}, #{...}
_TEMPLATE_MARKERS = ("${", "{{", "$(", "#{")

# ********, xxxxxx, ......
_MASK_RE = re.compile(r"^[*xX•·.\-_#?]+$")

# YOUR_PASSWORD, your-api-key, PUT_PASSWORD_HERE, changeme
_STAND_IN_RE = re.compile(
    r"^(?:your[\w\- ]*|[\w\- ]*[_\- ]here|(?:change|replace)[_\- ]?me|placeholder|redacted)$",
    re.IGNORECASE,
)

_NULLISH = {"null", "none", "nil", "undefined", "true", "false", "yes", "no", "on", "off", "~"}

# A type annotation, not a value: password: str, Password: SecureString.
_TYPE_NAMES = {
    "str", "string", "int", "bool", "bytes", "any", "object", "number",
    "boolean", "char", "secretstr", "securestring", "var", "dynamic",
}

# "Password", "Enter your password", "/account/reset-password"
_LABEL_RE = re.compile(r"^[A-Za-z/][A-Za-z \-_/.:]*$")
_PASSWORD_LABEL_WORDS = re.compile(r"passphrase|password|passwd|pwd", re.IGNORECASE)

# What a "pwd" holds when it is the working directory.
_PATH_RE = re.compile(r"^(?:[/~.]|[A-Za-z]:[\\/]|\\\\)")

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_$][\w$]*$")
_DOTTED_RE = re.compile(r"^[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+$")
_PASCAL_WORD_RE = re.compile(r"^[A-Z][A-Za-z]*$")

_CODE_CHARACTERS = set("()[]{}\"'`")


def _is_stand_in(value):
    """A reference, template, mask or placeholder rather than a real value."""
    return bool(
        PLACEHOLDER_RE.fullmatch(value)
        or any(marker in value for marker in _TEMPLATE_MARKERS)
        or _REFERENCE_RE.match(value)
        or _MASK_RE.match(value)
        or _STAND_IN_RE.match(value)
    )


def _password_word(name):
    """
    The password word name ends with, or None.

    A trailing number is ignored (password2, DB_PASSWORD_1). "pass" only
    counts as a word of its own: db_pass and smtpPass, not bypass.
    """
    if name in _NOT_PASSWORD_NAMES:
        return None

    base = re.sub(r"[_\-]?\d{1,3}$", "", name)
    lower = base.lower()

    for word in _PASSWORD_WORDS:
        if not lower.endswith(word):
            continue

        prefix = base[: len(base) - len(word)]

        if word in _SHORT_WORDS and prefix:
            hump = base[len(prefix)].isupper() and (prefix[-1].islower() or prefix[-1].isdigit())
            if prefix[-1] not in "_-.$@: /" and not hump:
                return None

        return word

    return None


def _password_value_ok(value, word):
    value = value.strip()

    if len(value) < MIN_SECRET_LENGTH or _is_stand_in(value):
        return False

    if value.lower() in _NULLISH:
        return False

    if _LABEL_RE.match(value) and _PASSWORD_LABEL_WORDS.search(value):
        return False

    if word == "pwd" and _PATH_RE.match(value):
        return False

    return True


# ---------------------------------------------------------
# Rules: each turns a match into (start, end, value), or None
# ---------------------------------------------------------

def _literal_group(match):
    for group in _LITERAL_GROUPS:
        if match.group(group) is not None:
            return group
    return None


def _password_assignment(match):
    word = _password_word(match.group("name") or match.group("qname"))
    group = _literal_group(match)

    if word is None or group is None:
        return None

    value = match.group(group)
    prefix = (match.group("vpre") or match.group("dpre") or match.group("spre") or "").lower()

    # $"..." and f"..." with {braces} build the value from code.
    if ("$" in prefix or "f" in prefix) and "{" in value:
        return None

    if not _password_value_ok(value, word):
        return None

    return match.start(group), match.end(group), value


def _config_line(match):
    word = _password_word(match.group("name"))
    if word is None:
        return None

    raw = match.group("value")

    # "value  # comment" in YAML and most .env readers.
    comment = re.search(r"[ \t]#", raw)
    value = (raw[: comment.start()] if comment else raw).rstrip()

    # Prose ("Password: see the vault") or a code expression.
    if re.search(r"\s", value) or _CODE_CHARACTERS & set(value):
        return None

    # A statement or an object member: Password = pw; / password: pw,
    if value.endswith((";", ",")) or _DOTTED_RE.match(value):
        return None

    if value.lower() in _TYPE_NAMES:
        return None

    if _IDENTIFIER_RE.match(value):
        # password: userPassword / password=password -- a variable.
        if _PASSWORD_LABEL_WORDS.search(value):
            return None

        # password = pw is code; INI writers rarely use bare words.
        if match.group("sep") == "=":
            return None

        # DB_PASSWORD=letmein is a .env line; password=pw is a keyword argument.
        if match.group("tight") and not match.group("name").isupper():
            return None

        # password: SecretStr is a type annotation.
        if match.group("sep") == ":" and _PASCAL_WORD_RE.match(value):
            return None

    if not _password_value_ok(value, word):
        return None

    start = match.start("value")
    return start, start + len(value), value


def _password_flag(match):
    group = next(g for g in ("dq", "sq", "bare") if match.group(g) is not None)
    value = match.group(group)
    word = _password_word(match.group("name"))

    if word is None or not _password_value_ok(value, word):
        return None

    return match.start(group), match.end(group), value


# (category, pattern, extract)
_RULES = (
    (PASSWORD, _PASSWORD_ASSIGNMENT, _password_assignment),
    (PASSWORD, _PASSWORD_CONFIG_LINE, _config_line),
    (PASSWORD, _PASSWORD_FLAG, _password_flag),
)


def _search(pattern, text, extract):
    """
    Yield (start, end, value) for each match that extract() accepts.

    A rejected match is searched again one character on, so a secret nested
    inside a string that was itself rejected is still found.
    """
    pos = 0

    while True:
        match = pattern.search(text, pos)
        if match is None:
            return

        found = extract(match)

        if found is None:
            pos = match.start() + 1
        else:
            yield found
            pos = match.end()


def _find(text):
    """
    Every secret in text as non-overlapping (start, end, category, value),
    in text order, skipping anything inside an existing placeholder.
    """
    taken = [m.span() for m in PLACEHOLDER_RE.finditer(text)]

    hits = [
        (start, end, category, value)
        for category, pattern, extract in _RULES
        for start, end, value in _search(pattern, text, extract)
    ]

    # Earliest first, then the longest of those starting together.
    hits.sort(key=lambda hit: (hit[0], hit[0] - hit[1]))

    kept, i = [], 0
    for hit in hits:
        start, end = hit[0], hit[1]

        # taken[i] becomes the first placeholder that ends after this hit
        # starts; both lists are in text order, so i only moves forward.
        while i < len(taken) and taken[i][1] <= start:
            i += 1

        if i < len(taken) and taken[i][0] < end:
            continue

        if kept and start < kept[-1][1]:
            continue

        kept.append(hit)

    return kept


def _propagates(value):
    """Long and mixed enough to replace wherever it appears: letters plus digits or symbols."""
    if len(value) < MIN_PROPAGATED_LENGTH:
        return False

    kinds = (
        any(c.isalpha() for c in value),
        any(c.isdigit() for c in value),
        any(not c.isalnum() for c in value),
    )
    return sum(kinds) >= 2


def _require_text(text):
    if not isinstance(text, str):
        raise SecretRedactionError(
            f"Cannot check a {type(text).__name__} for secrets; expected text."
        )
    return text


# ---------------------------------------------------------
# Public API
# ---------------------------------------------------------

@dataclass(frozen=True)
class RedactionReport:
    """
    What one request had replaced, by category. Never holds the values or
    which placeholder stands for which value, so it is safe to log.
    """

    replaced: dict
    distinct: dict

    @property
    def total(self):
        return sum(self.replaced.values())

    def __str__(self):
        if not self.total:
            return "no secrets found"

        parts = ", ".join(
            f"{category} x{count}" for category, count in self.replaced.items()
        )
        return f"{self.total} secret value(s) replaced ({parts})"


class SecretRedactor:
    """
    Replaces secrets with placeholders for one request.

    Create one per request and drop it afterwards. It is not thread-safe and
    is not meant to be shared: the whole point is that its mapping dies with
    the request.
    """

    __slots__ = ("_placeholders", "_numbers", "_replaced", "_distinct", "_known", "_known_count")

    def __init__(self):
        # secret -> (category, placeholder). Never leaves this object.
        self._placeholders = {}
        # Highest placeholder number used so far, per category.
        self._numbers = Counter()
        self._replaced = Counter()
        self._distinct = {}
        self._known = None
        self._known_count = 0

    def __repr__(self):
        return f"<SecretRedactor: {self.report()}>"

    def scan(self, *texts):
        """
        Learn the secrets in every text without changing anything.

        Scan all parts of a request before redacting any of them, so a secret
        found in a later part is also replaced in an earlier one, and new
        placeholders are numbered after any already in the request.
        """
        texts = [_require_text(text) for text in texts]

        for text in texts:
            for match in PLACEHOLDER_RE.finditer(text):
                category, number = match.group(1), int(match.group(2))
                self._numbers[category] = max(self._numbers[category], number)

        for text in texts:
            for _, _, category, value in _find(text):
                self._placeholder_for(value, category)

    def redact(self, text):
        """text with every secret replaced by its placeholder."""
        self.scan(text)

        out, pos = [], 0

        for start, end, _, value in _find(text):
            out.append(self._propagate(text[pos:start]))
            out.append(self._swap(value))
            pos = end

        out.append(self._propagate(text[pos:]))
        return "".join(out)

    def report(self):
        """Counts by category of what this redactor replaced so far."""
        return RedactionReport(
            replaced=dict(sorted(self._replaced.items())),
            distinct={
                category: len(placeholders)
                for category, placeholders in sorted(self._distinct.items())
            },
        )

    # --- internals ----------------------------------------

    def _placeholder_for(self, value, category):
        if value not in self._placeholders:
            self._numbers[category] += 1
            placeholder = f"[REDACTED_{category}_{self._numbers[category]}]"
            self._placeholders[value] = (category, placeholder)

        return self._placeholders[value][1]

    def _swap(self, value):
        category, placeholder = self._placeholders[value]
        self._replaced[category] += 1
        self._distinct.setdefault(category, set()).add(placeholder)
        return placeholder

    def _known_pattern(self):
        """Every learned secret that may be replaced without context, longest first."""
        if self._known_count != len(self._placeholders):
            values = sorted(
                (v for v in self._placeholders if _propagates(v)), key=len, reverse=True
            )
            self._known = (
                re.compile(
                    r"(?<!\w)(?:" + "|".join(map(re.escape, values)) + r")(?!\w)"
                )
                if values else None
            )
            self._known_count = len(self._placeholders)

        return self._known

    def _propagate(self, piece):
        """piece with any already-learned secret replaced, placeholders untouched."""
        pattern = self._known_pattern()

        if pattern is None or not piece:
            return piece

        out, pos = [], 0

        for match in PLACEHOLDER_RE.finditer(piece):
            out.append(pattern.sub(lambda m: self._swap(m.group(0)), piece[pos:match.start()]))
            out.append(match.group(0))
            pos = match.end()

        out.append(pattern.sub(lambda m: self._swap(m.group(0)), piece[pos:]))
        return "".join(out)


def redact_text(text):
    """
    One piece of text on its own, with a fresh redactor.

    Returns (redacted_text, RedactionReport).
    """
    redactor = SecretRedactor()
    return redactor.redact(text), redactor.report()
