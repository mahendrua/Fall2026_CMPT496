"""!
@file secret_redaction_test.py
@brief Tests for utils/secret_redaction.py (T-077, US-034).
@details Every secret below is fake and made up for these tests. No model or
network is involved: redaction is local pattern matching.
"""

import time

import pytest

from utils.secret_redaction import (
    PLACEHOLDER_RE,
    RedactionReport,
    SecretRedactionError,
    SecretRedactor,
    redact_text,
)

FAKE = "Fake-Pass-111!"
OTHER = "Fake-Pass-222!"
P1 = "[REDACTED_PASSWORD_1]"
P2 = "[REDACTED_PASSWORD_2]"


# ---------------------------------------------------------------------------
# Passwords in the languages and formats the pipeline sends
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("snippet", [
    # C#
    f'string password = "{FAKE}";',
    f'private const string DefaultPassword = "{FAKE}";',
    f'public string Password {{ get; set; }} = "{FAKE}";',
    f'var user = new User {{ Name = "ann", Password = "{FAKE}" }};',
    f'settings["Db:Password"] = "{FAKE}";',
    f'Login(userName: "ann", password: "{FAKE}");',
    f'if (password == "{FAKE}") {{ }}',
    # Python
    f'PASSWORD = "{FAKE}"',
    f"db_password = '{FAKE}'",
    f'password: str = "{FAKE}"',
    f'connect(host="db", password="{FAKE}")',
    f'config = {{"user": "ann", "password": "{FAKE}"}}',
    f'os.environ["DB_PASSWORD"] = "{FAKE}"',
    # JavaScript / TypeScript
    f"const password = '{FAKE}';",
    f'const options = {{ user: "ann", password: "{FAKE}" }};',
    f"const smtpPass = `{FAKE}`;",
    f'let adminPassword: string = "{FAKE}";',
    # JSON
    f'{{"Database": {{"Password": "{FAKE}"}}}}',
    # YAML
    f'password: "{FAKE}"',
    f"password: {FAKE}",
    f"  db_password: {FAKE}  # rotate monthly",
    f"  - password: {FAKE}",
    # .env and shell
    f"DB_PASSWORD={FAKE}",
    f'export DB_PASSWORD="{FAKE}"',
    f"mysql --user=root --password={FAKE} shop",
    f'mysql --password "{FAKE}" shop',
    # INI / properties
    f"password = {FAKE}",
    f"spring.datasource.password={FAKE}",
    # XML attribute
    f'<database user="ann" password="{FAKE}" />',
    # Numbered names
    f'password2 = "{FAKE}"',
    f'DB_PASSWORD_1 = "{FAKE}"',
])
def test_password_value_is_replaced_and_nothing_else_changes(snippet):
    out, report = redact_text(snippet)

    assert out == snippet.replace(FAKE, P1)
    assert report.replaced == {"PASSWORD": 1}


def test_csharp_verbatim_string_is_replaced_whole():
    # "" inside @"..." is an escaped quote, part of the same value.
    snippet = 'var pwd = @"Fake""Pass\\111";'

    out, _ = redact_text(snippet)

    assert out == f'var pwd = @"{P1}";'


def test_secret_inside_an_ordinary_string_is_found():
    snippet = f"conn = \"user=ann password='{FAKE}'\""

    out, _ = redact_text(snippet)

    assert out == f"conn = \"user=ann password='{P1}'\""


def test_line_breaks_quotes_and_comments_survive():
    snippet = (
        "// settings\r\n"
        f'string password = "{FAKE}"; // do not commit\r\n'
        "\r\n"
        f"DB_PASSWORD='{OTHER}'\n"
        "password: plain   # comment kept\n"
    )

    out, report = redact_text(snippet)

    assert out == (
        "// settings\r\n"
        f'string password = "{P1}"; // do not commit\r\n'
        "\r\n"
        f"DB_PASSWORD='{P2}'\n"
        "password: [REDACTED_PASSWORD_3]   # comment kept\n"
    )
    assert report.replaced == {"PASSWORD": 3}


# ---------------------------------------------------------------------------
# Placeholders: consistent within a request, numbered, idempotent
# ---------------------------------------------------------------------------

def test_repeated_secret_gets_the_same_placeholder():
    snippet = (
        f'string password = "{FAKE}";\n'
        f'string backupPassword = "{OTHER}";\n'
        f'string adminPwd = "{FAKE}";\n'
    )

    out, report = redact_text(snippet)

    assert out == (
        f'string password = "{P1}";\n'
        f'string backupPassword = "{P2}";\n'
        f'string adminPwd = "{P1}";\n'
    )
    assert report.replaced == {"PASSWORD": 3}
    assert report.distinct == {"PASSWORD": 2}


def test_secret_is_replaced_consistently_across_texts_of_one_request():
    system = "You review C# code."
    code = f'string password = "{FAKE}";'
    # A second message mentions the same value with no telling name beside it.
    notes = f'Login("ann", "{FAKE}") fails in the test.'

    redactor = SecretRedactor()
    redactor.scan(system, notes, code)

    assert redactor.redact(notes) == f'Login("ann", "{P1}") fails in the test.'
    assert redactor.redact(code) == f'string password = "{P1}";'
    assert redactor.redact(system) == system


def test_secret_found_later_is_replaced_earlier_only_after_scanning_both():
    first = f'Login("ann", "{FAKE}");'
    second = f'password = "{FAKE}"'

    unscanned = SecretRedactor()
    assert unscanned.redact(first) == first

    scanned = SecretRedactor()
    scanned.scan(first, second)
    assert scanned.redact(first) == f'Login("ann", "{P1}");'


def test_short_or_plain_word_secret_is_not_replaced_without_context():
    # Replacing "administrator" everywhere would wreck ordinary code.
    snippet = 'password = "administrator"\nrole = "administrator"\n'

    out, _ = redact_text(snippet)

    assert out == f'password = "{P1}"\nrole = "administrator"\n'


def test_redacting_twice_changes_nothing():
    snippet = (
        f'string password = "{FAKE}";\n'
        f"DB_PASSWORD={OTHER}\n"
        f'Login("ann", "{FAKE}");\n'
    )

    once, _ = redact_text(snippet)

    # A fresh redactor, as on a retried request.
    again, report = redact_text(once)
    assert again == once
    assert report.total == 0

    # The same redactor, as when one request is checked twice.
    redactor = SecretRedactor()
    first = redactor.redact(snippet)
    assert redactor.redact(first) == first
    assert redactor.report().replaced == {"PASSWORD": 3}


def test_existing_placeholders_are_kept_and_new_ones_numbered_after_them():
    snippet = f'password = "{P1}"\nother_pwd = "{OTHER}"\n'

    out, report = redact_text(snippet)

    assert out == f'password = "{P1}"\nother_pwd = "{P2}"\n'
    assert report.replaced == {"PASSWORD": 1}


def test_numbering_accounts_for_placeholders_in_every_scanned_text():
    old = f"Earlier answer used {P2}."
    new = f'password = "{FAKE}"'

    redactor = SecretRedactor()
    redactor.scan(new, old)

    assert redactor.redact(new) == 'password = "[REDACTED_PASSWORD_3]"'
    assert redactor.redact(old) == old


def test_same_input_gives_same_output():
    snippet = f'password = "{FAKE}"\nDB_PASSWORD={OTHER}\n'

    assert redact_text(snippet) == redact_text(snippet)


def test_input_is_not_changed():
    snippet = f'password = "{FAKE}"'
    texts = [snippet, "second"]
    before = list(texts)

    redactor = SecretRedactor()
    redactor.scan(*texts)
    redactor.redact(texts[0])

    assert texts == before
    assert snippet == f'password = "{FAKE}"'


def test_redactors_do_not_share_secrets():
    first = SecretRedactor()
    first.redact(f'password = "{FAKE}"')

    # A concurrent request that only mentions the value without context.
    second = SecretRedactor()
    text = f'Login("ann", "{FAKE}")'

    assert second.redact(text) == text
    assert second.report().total == 0


# ---------------------------------------------------------------------------
# Not secrets: left exactly as they are
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("snippet", [
    # Environment and config lookups
    'password = os.getenv("DB_PASSWORD")',
    'password = os.environ["DB_PASSWORD"]',
    "const password = process.env.DB_PASSWORD;",
    'var password = Environment.GetEnvironmentVariable("DB_PASSWORD");',
    'var password = Configuration["Db:Password"];',
    'password = settings.DbPassword',
    # Templates and references
    'password = "${DB_PASSWORD}"',
    "DB_PASSWORD=$SECRET_FROM_VAULT",
    'password: "${{ secrets.DB_PASSWORD }}"',
    '<add password="$(DbPassword)" />',
    'password = "%DB_PASSWORD%"',
    "password: {{ vault_db_password }}",
    'password = f"{prefix}{suffix}"',
    'var password = $"{user}-{year}";',
    'cmd.Parameters.AddWithValue("@Password", password);',
    "password: *default_password",
    # Obvious stand-ins and our own placeholders
    'password = "<your-password>"',
    'password = "********"',
    'password = "YOUR_PASSWORD"',
    'password = "PUT_PASSWORD_HERE"',
    'password = "changeme"',
    f'password = "{P1}"',
    f"DB_PASSWORD={P1}",
    # Labels and prose
    '[Display(Name = "Password")]',
    'password = "Password"',
    'passwordPrompt = "Enter your password"',
    'resetPassword = "/account/reset-password"',
    "Password: see the team vault",
    # Names that start with, but do not end in, a password word
    "PasswordMinLength = 12;",
    'password_hint = "first pet"',
    'passwordHash = "not-checked-here"',
    # Words that end in "pass"
    'bypass = "Fake-Pass-111!"',
    'compass = "north-north-west"',
    # Code, not values
    "password: str",
    "password: SecretStr",
    "password: Optional[str] = None",
    "password: string;",
    "password = pw",
    "self.password = password",
    "    password=password,",
    "password=pw_var",
    "password=None",
    "Password = newPassword;",
    "password = getpass.getpass()",
    'if (password == "") return;',
    # pwd as a working directory
    "PWD=/home/ann/project",
    'pwd = "/home/ann/project"',
])
def test_non_secret_is_left_alone(snippet):
    out, report = redact_text(snippet)

    assert out == snippet
    assert report.total == 0


# ---------------------------------------------------------------------------
# Safe reporting and failure
# ---------------------------------------------------------------------------

def test_report_gives_counts_and_categories_never_values():
    redactor = SecretRedactor()
    redactor.redact(f'password = "{FAKE}"\nDB_PASSWORD={OTHER}\nsmtpPass = "{FAKE}"')

    report = redactor.report()

    assert isinstance(report, RedactionReport)
    assert report.replaced == {"PASSWORD": 3}
    assert report.distinct == {"PASSWORD": 2}
    assert report.total == 3

    for shown in (str(report), repr(report), repr(redactor)):
        assert FAKE not in shown
        assert OTHER not in shown
        assert "[REDACTED_" not in shown


def test_report_with_nothing_found():
    _, report = redact_text("int total = a + b;")

    assert report.total == 0
    assert report.replaced == {}
    assert str(report) == "no secrets found"


@pytest.mark.parametrize("bad", [None, b"password = 'x'", 42, ["password"]])
def test_non_text_is_refused_with_a_safe_error(bad):
    redactor = SecretRedactor()

    with pytest.raises(SecretRedactionError) as error:
        redactor.redact(bad)

    assert "password" not in str(error.value)

    with pytest.raises(SecretRedactionError):
        redactor.scan("fine", bad)


def test_placeholder_pattern_matches_what_redaction_produces():
    out, _ = redact_text(f'password = "{FAKE}"')

    match = PLACEHOLDER_RE.search(out)
    assert match.group(0) == P1
    assert match.group(1) == "PASSWORD"
    assert match.group(2) == "1"


# ---------------------------------------------------------------------------
# Long input stays linear
# ---------------------------------------------------------------------------

LONG_INPUTS = {
    "identifier": "a" * 200_000,
    "dashed": "x-" * 100_000,
    "password-words": "password" * 25_000,
    "quoted-keys": '"password' * 25_000,
    "prose-line": "password: " + "ab " * 60_000,
    "unclosed-backtick": "password = `" + "a\\" * 60_000,
}


# Short ids: pytest puts the id in an environment variable, and Windows
# refuses one longer than 32767 characters.
@pytest.mark.parametrize("text", LONG_INPUTS.values(), ids=LONG_INPUTS.keys())
def test_long_input_finishes_quickly(text):
    # These were quadratic before the name lookbehind covered "-"; a
    # regression takes minutes, not seconds.
    started = time.perf_counter()
    redact_text(text)
    assert time.perf_counter() - started < 10
