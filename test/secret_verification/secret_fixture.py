"""
@file secret_fixture.py
@brief The T-079 sample codebase: ten planted fake secrets, S01-S10, and the
nonsecret controls written around them (US-034).
@details build_fixture() writes LedgerLite, a small C# project with Python,
JavaScript, shell and Markdown files -- file types the pipeline reads -- and
returns where every secret and every control was written. Those positions
come from the templates below, not from the app's detector, so they can be
used to judge it.

Every value is fake. They are generated at run time from a seed, so the
repository never holds a complete token in a vendor's format (Stripe, AWS,
GitHub) or a private key; the same seed always gives the same values.

detector_samples() holds the same secrets in file types the pipeline does
not read (.json, .env, YAML, XML, INI, PEM), plus a few documented misses.
Those only exercise the detector and are reported apart from the pipeline.

Nothing here prints a value: the repr of every object leaves it out.
"""

import hashlib
import random
import re
import string
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_SEED = 79
CODEBASE_NAME = "LedgerLite"

# The placeholder category the app is expected to give each secret
# (README, "Placeholders"). Used to count and report, never to detect.
PASSWORD = "PASSWORD"
API_KEY = "API_KEY"
TOKEN = "TOKEN"
AUTHORIZATION = "AUTHORIZATION"
PRIVATE_KEY = "PRIVATE_KEY"

# How an occurrence is marked as a secret where it is written.
LABELLED = "labelled"      # a name, header or connection-string key says so
PROSE = "prose"            # "the default admin password is ..."
QUERY = "query"            # ?refresh_token=...
VENDOR = "vendor"          # only the token's own format says so
ECHO = "echo"              # repeated with no context, in the same file
PLANTUML = "plantuml"      # a PlantUML private field: -password : string = "..."

# Reported gaps, reproduced here. A was fixed in T-079; occurrences keep the
# label so a regression is attributed to it.
KNOWN_GAPS = {
    "A": "PlantUML private field (-password : string = \"...\") was not detected (fixed in T-079)",
}

# Multi-line values (the private key) are also looked for line by line, so a
# partial leak is found. Shorter lines are too common to mean anything.
MIN_FRAGMENT_LENGTH = 16


class FixtureError(RuntimeError):
    """The templates and the positions recorded for them disagree."""


@dataclass(frozen=True)
class PlantedSecret:
    case_id: str
    category: str
    kind: str
    value: str = field(repr=False)

    @property
    def fragments(self):
        """Each line of a multi-line value long enough to identify it."""
        lines = [line.strip() for line in self.value.splitlines()]
        if len(lines) < 2:
            return ()
        return tuple(line for line in lines if len(line) >= MIN_FRAGMENT_LENGTH)


@dataclass(frozen=True)
class Occurrence:
    """Where one secret was written: path relative to the fixture root, character offsets, 1-based line."""

    case_id: str
    path: str
    start: int
    end: int
    line: int
    context: str
    known_gap: str = ""


@dataclass(frozen=True)
class Control:
    """Nonsecret text that must come through unchanged. Safe to print."""

    control_id: str
    kind: str
    text: str
    path: str
    start: int
    end: int
    line: int


# ---------------------------------------------------------------------------
# The ten values
# ---------------------------------------------------------------------------

_ALNUM = string.ascii_letters + string.digits
_UPPER_DIGITS = string.ascii_uppercase + string.digits
_BASE64 = string.ascii_letters + string.digits + "+/"


def _pick(rng, alphabet, n):
    return "".join(rng.choice(alphabet) for _ in range(n))


def _mixed(rng, n):
    """n letters and digits with at least one upper case, lower case and digit."""
    while True:
        text = _pick(rng, _ALNUM, n)
        if any(c.isupper() for c in text) and any(c.islower() for c in text) and any(c.isdigit() for c in text):
            return text


def _aws_id(rng):
    while True:
        tail = _pick(rng, _UPPER_DIGITS, 16)
        if any(c.isdigit() for c in tail) and any(c.isalpha() for c in tail):
            # Split so no complete key ID is ever written in this file.
            return "AK" + "IA" + tail


def _key_body(rng):
    lines = ["MIIEvQIBADANBgkqhkiG9w0BAQEFAASC" + _pick(rng, _BASE64, 32)]
    lines += [_pick(rng, _BASE64, 64) for _ in range(3)]
    lines.append(_pick(rng, _BASE64, 38) + "==")
    return "\n".join(lines)


# (case_id, category, kind, make(rng)). The order is the generation order, so
# changing it changes every value for a seed.
_SECRETS = (
    ("S01", PASSWORD, "C# constant password, also in prose, a comment and a PlantUML field",
     lambda rng: "Ldg" + _mixed(rng, 6) + "-Adm!" + _pick(rng, string.digits, 3)),
    ("S02", API_KEY, "Python named API key",
     lambda rng: "rpt_" + _mixed(rng, 28)),
    ("S03", TOKEN, "JavaScript refresh token, also in a query string",
     lambda rng: "lrt" + _mixed(rng, 37)),
    ("S04", API_KEY, "Stripe-format secret key (vendor format only)",
     lambda rng: "sk" + "_live_" + _mixed(rng, 24)),
    ("S05", API_KEY, "AWS-format access key ID in a shell export",
     _aws_id),
    ("S06", TOKEN, "GitHub-format token in a shell variable",
     lambda rng: "gh" + "p_" + _mixed(rng, 36)),
    ("S07", AUTHORIZATION, "Bearer credential in C# and a curl header",
     lambda rng: "lbt" + _mixed(rng, 37)),
    ("S08", PASSWORD, "SQL Server connection-string password",
     lambda rng: "Db" + _mixed(rng, 8) + "#" + _pick(rng, string.digits, 4)),
    ("S09", PASSWORD, "Password in a PostgreSQL URL",
     lambda rng: "Rp" + _mixed(rng, 10) + "!" + _pick(rng, string.digits, 2)),
    ("S10", PRIVATE_KEY, "PEM private key body in Markdown",
     _key_body),
)

CASE_IDS = tuple(case_id for case_id, *_ in _SECRETS)


def make_secrets(seed=DEFAULT_SEED):
    """The ten planted secrets for seed, the same every time."""
    rng = random.Random(f"T-079/{seed}")
    return tuple(
        PlantedSecret(case_id, category, kind, make(rng))
        for case_id, category, kind, make in _SECRETS
    )


# ---------------------------------------------------------------------------
# Controls: nonsecret text the pipeline must leave alone
# ---------------------------------------------------------------------------

# Built at run time like the private key, for the same reason.
PEM_BEGIN = "-----BEGIN " + "PRIVATE KEY-----"
PEM_END = "-----END " + "PRIVATE KEY-----"

CONTROLS = {
    "C01": ("business threshold", "ApprovalThreshold = 5000m"),
    "C02": ("name starting with a secret word", "PasswordMinLength = 12"),
    "C03": ("cache key", 'CacheKey = "invoice-cache-v2"'),
    "C04": ("environment lookup", 'GetEnvironmentVariable("LEDGER_DB_PASSWORD")'),
    "C05": ("primary key", 'var primaryKey = "InvoiceId";'),
    "C06": ("ordinary URL", "https://docs.example.com/ledger/api"),
    "C07": ("business threshold", "amount >= 10000m"),
    "C08": ("environment lookup", 'os.getenv("REPORTING_API_KEY")'),
    "C09": ("Windows path", "C:\\LedgerLite\\exports"),
    "C10": ("cache key", 'cache_key = "monthly-report"'),
    "C11": ("existing placeholder", "token=[REDACTED_TOKEN_1]"),
    "C12": ("nonsecret query parameter", "client_id=ledger-web"),
    "C13": ("name starting with a secret word", 'tokenType: "Bearer"'),
    "C14": ("primary key", 'const primaryKey = "ledgerId";'),
    "C15": ("environment lookup", "process.env.LEDGER_API_KEY"),
    "C16": ("log path", "LOG_PATH=/var/log/ledger/deploy.log"),
    "C17": ("plain setting", "RETRY_LIMIT=3"),
    "C18": ("environment reference in a header", '"Authorization: Bearer $GITHUB_TOKEN"'),
    "C19": ("ordinary URL", "https://api.github.com/repos/acme/ledger-lite/releases/latest"),
    "C20": ("business rule", "Invoices above 5000 CAD need manager approval"),
    "C21": ("stand-in value", "REPORTING_API_KEY=YOUR_API_KEY"),
    "C22": ("PEM armour line", PEM_BEGIN),
    "C23": ("PEM armour line", PEM_END),
    "C24": ("PlantUML nonsecret field", "-approvalThreshold : decimal = 5000"),
    "C25": ("PlantUML visibility syntax", "+ApproveInvoice(invoice : Invoice) : bool"),
    "C26": ("PlantUML nonsecret field", "-cacheKey : string = \"invoice-cache-v2\""),
}


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------
# <<S01>> writes a secret, <<S01|echo>> writes it with the given context
# (labelled when none is given), <<C01>> writes a control. Paths are relative
# to the codebase root.

_CSPROJ = """<Project Sdk="Microsoft.NET.Sdk">

  <PropertyGroup>
    <TargetFramework>net10.0</TargetFramework>
    <ImplicitUsings>enable</ImplicitUsings>
    <Nullable>enable</Nullable>
  </PropertyGroup>

</Project>
"""

_INVOICE_SERVICE = """using System;

namespace LedgerLite.Billing
{
    public class InvoiceService
    {
        public const decimal <<C01>>;
        public const int <<C02>>;
        private const string <<C03>>;
        private const string AdminPassword = "<<S01>>";
        // The legacy approval portal still accepts <<S01|echo>> until it is retired.

        public string ConnectionString { get; } = "Server=db.ledger.internal;Database=Ledger;User Id=ledger_app;Password=<<S08>>;";

        public string DatabasePasswordFromEnvironment() => Environment.<<C04>> ?? string.Empty;

        public bool NeedsApproval(decimal amount) => amount > ApprovalThreshold;

        public bool CanApprove(string input) => input == AdminPassword;

        public string PrimaryKeyFor(Invoice invoice)
        {
            <<C05>>
            return primaryKey + ":" + invoice.Id;
        }
    }

    public class Invoice
    {
        public int Id { get; set; }

        public decimal Amount { get; set; }
    }
}
"""

_PAYMENT_CLIENT = """using System.Net.Http;
using System.Net.Http.Headers;

namespace LedgerLite.Integrations
{
    public class PaymentClient
    {
        private static readonly string StripeKey = "<<S04|vendor>>";
        public const string DocsUrl = "<<C06>>";
        private readonly HttpClient _http = new HttpClient();

        public PaymentClient()
        {
            _http.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", "<<S07>>");
        }

        public string ApiKeyForCharges() => StripeKey;

        public bool IsLargePayment(decimal amount) => <<C07>>;
    }
}
"""

_REPORT_EXPORT = '''"""Monthly report export for LedgerLite."""

import os

REPORTING_API_KEY = "<<S02>>"
DATABASE_URL = "postgresql://report_user:<<S09>>@reports.ledger.internal:5432/ledger"
EXPORT_DIR = r"<<C09>>"
MAX_ROWS = 10000
<<C10>>

# A logged request looks like: GET /reports?<<C11>>


def api_key():
    return <<C08>> or REPORTING_API_KEY


def export_headers():
    return {"X-Api-Key": api_key(), "Accept": "application/json"}
'''

_SESSION_JS = """// Session handling for the LedgerLite web client.
const REFRESH_URL = "https://auth.example.com/oauth/refresh?<<C12>>&refresh_token=<<S03|query>>";

const session = {
  refreshToken: "<<S03>>",
  <<C13>>,
  expiresIn: 3600,
};

<<C14>>

function apiKey() {
  return <<C15>>;
}

function refreshUrl() {
  return REFRESH_URL;
}

module.exports = { session, primaryKey, apiKey, refreshUrl };
"""

_DEPLOY_SH = """#!/usr/bin/env bash
set -euo pipefail

export AWS_ACCESS_KEY_ID=<<S05>>
GITHUB_TOKEN=<<S06>>
<<C16>>
<<C17>>

curl -fsS -H <<C18>> <<C19>>
curl -fsS -H "Authorization: Bearer <<S07>>" https://ledger.example.com/api/health
"""

_SETUP_MD = """# LedgerLite setup

<<C20>> before they are paid.

The default admin password is <<S01|prose>>. Change it after the first login.

Set `<<C21>>` in your shell; the app reads it with `os.getenv("REPORTING_API_KEY")`.
API reference: https://docs.example.com/ledger/api. Exports are written to `C:\\LedgerLite\\exports`.

The webhook signing key:

```text
<<C22>>
<<S10>>
<<C23>>
```
"""

_ARCHITECTURE_MD = """# Architecture

```plantuml
@startuml
class InvoiceService {
  -password : string = "<<S01|plantuml>>"
  <<C24>>
  <<C26>>
  <<C25>>
}
@enduml
```
"""

PIPELINE_TEMPLATES = {
    f"{CODEBASE_NAME}.csproj": _CSPROJ,
    "Billing/InvoiceService.cs": _INVOICE_SERVICE,
    "Integrations/PaymentClient.cs": _PAYMENT_CLIENT,
    "tools/report_export.py": _REPORT_EXPORT,
    "web/session.js": _SESSION_JS,
    "scripts/deploy.sh": _DEPLOY_SH,
    "docs/SETUP.md": _SETUP_MD,
    "docs/architecture.md": _ARCHITECTURE_MD,
}

_GAP_OF_CONTEXT = {PLANTUML: "A"}

_MARKER = re.compile(r"<<(?P<id>[SCD]\d{2})(?:\|(?P<context>\w+))?>>")


def _render(path, template, secrets, controls):
    """(text, occurrences, controls) for one template."""
    out, size, line = [], 0, 1
    occurrences, written = [], []
    pos = 0

    def add(piece):
        nonlocal size, line
        out.append(piece)
        size += len(piece)
        line += piece.count("\n")

    for match in _MARKER.finditer(template):
        add(template[pos:match.start()])
        pos = match.end()
        item = match.group("id")
        start, start_line = size, line

        if item.startswith("S"):
            context = match.group("context") or LABELLED
            add(secrets[item].value)
            occurrences.append(Occurrence(
                item, path, start, size, start_line, context, _GAP_OF_CONTEXT.get(context, ""),
            ))
        else:
            kind, text = controls[item]
            add(text)
            written.append(Control(item, kind, text, path, start, size, start_line))

    add(template[pos:])
    return "".join(out), occurrences, written


# ---------------------------------------------------------------------------
# The fixture
# ---------------------------------------------------------------------------

def _digest(data):
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class Fixture:
    root: Path
    seed: int
    secrets: tuple = field(repr=False)
    occurrences: tuple = field(repr=False)
    controls: tuple = field(repr=False)
    digests: dict = field(repr=False)

    def __repr__(self):
        return f"<Fixture {self.root.name}: {len(self.secrets)} secrets, {len(self.occurrences)} occurrences>"

    def secret(self, case_id):
        return next(s for s in self.secrets if s.case_id == case_id)

    @property
    def files(self):
        return tuple(sorted(self.digests))

    def path(self, relative):
        return self.root / relative

    def text(self, relative):
        return self.path(relative).read_text(encoding="utf-8")

    def occurrences_in(self, relative, include_gaps=True):
        return [
            o for o in self.occurrences
            if o.path == relative and (include_gaps or not o.known_gap)
        ]

    def controls_in(self, relative):
        return [c for c in self.controls if c.path == relative]

    def expected_counts(self, relative, include_gaps=True):
        """{category: occurrences} a correct redaction of this one file replaces."""
        return dict(sorted(Counter(
            self.secret(o.case_id).category for o in self.occurrences_in(relative, include_gaps)
        ).items()))

    def expected_distinct(self, relative, include_gaps=True):
        """{category: distinct secrets} in this one file."""
        cases = {o.case_id for o in self.occurrences_in(relative, include_gaps)}
        return dict(sorted(Counter(self.secret(c).category for c in cases).items()))

    def changed_files(self):
        """Files whose bytes differ from what was written, or that are gone."""
        changed = []
        for relative, digest in self.digests.items():
            path = self.path(relative)
            if not path.is_file() or _digest(path.read_bytes()) != digest:
                changed.append(relative)
        return changed

    def needles(self):
        """(case_id, value, fragments) for every secret, for leak_scanner.Needle."""
        return [(s.case_id, s.value, s.fragments) for s in self.secrets]


def _check(text_of, secrets, occurrences, controls):
    """The recorded positions must describe the written text exactly."""
    values = [s.value for s in secrets]

    if len(set(values)) != len(values):
        raise FixtureError("Two cases have the same value.")

    for a in secrets:
        for b in secrets:
            if a is not b and a.value in b.value:
                raise FixtureError(f"{a.case_id} is part of {b.case_id}, so a finding could not be attributed.")

    by_case = {s.case_id: s for s in secrets}

    for o in occurrences:
        if text_of[o.path][o.start:o.end] != by_case[o.case_id].value:
            raise FixtureError(f"{o.case_id} is not where it was recorded in {o.path}.")

    for c in controls:
        if text_of[c.path][c.start:c.end] != c.text:
            raise FixtureError(f"{c.control_id} is not where it was recorded in {c.path}.")
        if any(v in c.text for v in values):
            raise FixtureError(f"{c.control_id} contains a planted value.")
        for o in occurrences:
            if o.path == c.path and o.start < c.end and c.start < o.end:
                raise FixtureError(f"{c.control_id} overlaps {o.case_id} in {c.path}.")

    # Every appearance of a value is a recorded occurrence, so nothing is
    # planted by accident (in a control, or by one template echoing another).
    for path, text in text_of.items():
        for s in secrets:
            recorded = sum(1 for o in occurrences if o.path == path and o.case_id == s.case_id)
            if text.count(s.value) != recorded:
                raise FixtureError(f"{s.case_id} appears in {path} where it was not recorded.")

    missing = set(by_case) - {o.case_id for o in occurrences}
    if missing:
        raise FixtureError(f"Never written: {', '.join(sorted(missing))}.")


def build_fixture(parent, seed=DEFAULT_SEED, name=CODEBASE_NAME):
    """
    Write the LedgerLite codebase into parent/name and return its Fixture.

    The folder must not exist yet: a fixture is never written over anything.
    """
    root = Path(parent) / name
    root.mkdir(parents=True, exist_ok=False)

    secrets = make_secrets(seed)
    by_case = {s.case_id: s for s in secrets}

    text_of, occurrences, controls = {}, [], []

    for relative, template in PIPELINE_TEMPLATES.items():
        text, found, written = _render(relative, template, by_case, CONTROLS)
        text_of[relative] = text
        occurrences += found
        controls += written

    _check(text_of, secrets, occurrences, controls)

    digests = {}
    for relative, text in text_of.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        digests[relative] = _digest(path.read_bytes())

    return Fixture(root, seed, secrets, tuple(occurrences), tuple(controls), digests)


# ---------------------------------------------------------------------------
# Detector-only samples
# ---------------------------------------------------------------------------
# File types the pipeline does not read, and documented misses. They are
# never written into the pipeline's codebase.

DETECTED = "detected"
KNOWN_MISS = "known_miss"

DETECTOR_CONTROLS = {
    "D01": ("primary key", '"PrimaryKey": "InvoiceId"'),
    "D02": ("plain setting", "LOG_LEVEL=info"),
    "D03": ("host name", "DATABASE_HOST=reports.ledger.internal"),
    "D04": ("plain setting", "retry_limit = 3"),
    "D05": ("ordinary URL", '"BaseUrl": "https://reports.example.com/api"'),
    "D06": ("e-mail address", 'from="billing@example.com"'),
}

# (name, file type, expectation, note, template)
_DETECTOR_SAMPLES = (
    ("appsettings.json", "JSON", DETECTED, "connection string and named API key",
     """{
  "ConnectionStrings": {
    "Ledger": "Server=db.ledger.internal;Database=Ledger;User Id=ledger_app;Password=<<S08>>;"
  },
  "Reporting": {
    "ApiKey": "<<S02>>",
    <<D05>>
  },
  "Cache": { <<D01>> }
}
"""),
    (".env", "dotenv", DETECTED, "vendor key, vendor token and a password",
     """AWS_ACCESS_KEY_ID=<<S05>>
GITHUB_TOKEN=<<S06>>
LEDGER_DB_PASSWORD=<<S01>>
<<D02>>
"""),
    ("docker-compose.yml", "YAML", DETECTED, "list-style environment entries",
     """services:
  reports:
    image: ledger/reports:1.4
    environment:
      - POSTGRES_PASSWORD=<<S09>>
      - REFRESH_TOKEN=<<S03>>
      - <<D03>>
"""),
    ("web.config", "XML", DETECTED, "connection-string attribute and password attribute",
     """<configuration>
  <connectionStrings>
    <add name="Ledger" connectionString="Server=db.ledger.internal;Database=Ledger;User Id=ledger_app;Password=<<S08>>;" />
  </connectionStrings>
  <system.net>
    <mailSettings>
      <smtp <<D06>>>
        <network host="smtp.example.com" userName="billing" password="<<S01>>" />
      </smtp>
    </mailSettings>
  </system.net>
</configuration>
"""),
    ("payments.ini", "INI", DETECTED, "vendor-format key under a secret name",
     """[payments]
secret_key = <<S04>>
<<D04>>
"""),
    ("signing.pem", "PEM", DETECTED, "private key file",
     f"""{PEM_BEGIN}
<<S10>>
{PEM_END}
"""),
    ("LegacyLogin.cs", "C#", KNOWN_MISS, "password passed positionally, with no telling name",
     """public class LegacyLogin
{
    public bool Run(Portal portal) => portal.Login("admin", "<<S01>>");
}
"""),
    ("jwt.json", "JSON", KNOWN_MISS, "a bare \"Key\" name (the Jwt:Key signing key)",
     """{ "Jwt": { "Key": "<<S02>>", "Issuer": "ledger" } }
"""),
    ("ReplayClient.cs", "C#", KNOWN_MISS, "a value repeated without context in a separate request",
     """public class ReplayClient
{
    public string Replay() => Send("<<S07>>");
}
"""),
)


@dataclass(frozen=True)
class DetectorSample:
    name: str
    file_type: str
    expectation: str
    note: str
    text: str = field(repr=False)
    occurrences: tuple = field(repr=False)
    controls: tuple = field(repr=False)


def detector_samples(seed=DEFAULT_SEED):
    """The detector-only samples for seed, positions checked like the fixture's."""
    secrets = make_secrets(seed)
    by_case = {s.case_id: s for s in secrets}
    samples = []

    for name, file_type, expectation, note, template in _DETECTOR_SAMPLES:
        text, found, written = _render(name, template, by_case, DETECTOR_CONTROLS)
        used = tuple(s for s in secrets if any(o.case_id == s.case_id for o in found))
        _check({name: text}, used, found, written)
        samples.append(DetectorSample(name, file_type, expectation, note, text, tuple(found), tuple(written)))

    return tuple(samples)


def write_detector_samples(folder, seed=DEFAULT_SEED):
    """Write the detector-only samples into folder (which must not exist) and return them."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=False)
    samples = detector_samples(seed)
    for sample in samples:
        (folder / sample.name).write_text(sample.text, encoding="utf-8", newline="\n")
    return samples
