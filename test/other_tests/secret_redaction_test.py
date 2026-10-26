"""!
@file secret_redaction_test.py
@brief Tests for utils/secret_redaction.py (T-077, US-034).
@details Every secret below is fake and made up for these tests. No model or
network is involved: redaction is local pattern matching.
"""

import time

import pytest

from utils.secret_redaction import (
    API_KEY,
    AUTHORIZATION,
    PASSWORD,
    PLACEHOLDER_RE,
    PRIVATE_KEY,
    TOKEN,
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
    # Words that end in "pass", and PASS as a test status
    'bypass = "Fake-Pass-111!"',
    'compass = "north-north-west"',
    'console.log(ok ? "PASS" : "FAIL");',
    'var colour = status switch { "PASS" => "green", _ => "red" };',
    'label = (isAdmin ? "password" : "username")',
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


# ---------------------------------------------------------------------------
# API keys, tokens, authorization, connection strings, private keys
# ---------------------------------------------------------------------------

KEY = "Fake-Key-123456789"
TOKEN_VALUE = "Fake-Token-123456789"
BASIC = "ZmFrZXVzZXI6ZmFrZXBhc3M="  # base64 of fakeuser:fakepass

# Vendor-format fakes are pieced together at run time so this file never
# contains a string in a real token format: GitHub push protection would
# refuse the commit, and scanners would report the repository.
GITHUB = "gh" + "p_" + "Fa1ke" * 8
GITHUB_PAT = "github" + "_pat_" + "Fa1ke" * 5
GITLAB = "gl" + "pat-" + "Fa1ke" * 4
SLACK = "xo" + "xb-" + "1234-5678-Fa1ke"
NPM = "np" + "m_" + "Fa1ke" * 7 + "x"
HUGGING_FACE = "h" + "f_" + "Fa1ke" * 7
JWT = "ey" + "JhbGciOiJIUzI1NiJ9." + "ey" + "JzdWIiOiJmYWtlIn0." + "Fa1keSignature1"
AWS = "AK" + "IA" + "FAKEFAKEFAKE1234"
GOOGLE = "AI" + "za" + "Fa1ke" * 7
STRIPE = "sk" + "_live_" + "Fa1ke" * 4
STRIPE_PUBLISHABLE = "pk" + "_live_" + "Fa1ke" * 4
OPENAI = "sk" + "-proj-" + "Fa1ke" * 4
ANTHROPIC = "sk" + "-ant-api03-" + "Fa1ke" * 4
SENDGRID = "S" + "G." + "Fa1ke" * 4 + "." + "Fa1ke" * 8


def placeholder(category, number=1):
    return f"[REDACTED_{category}_{number}]"


@pytest.mark.parametrize("snippet, secret, category", [
    # C#
    (f'public const string ApiKey = "{KEY}";', KEY, API_KEY),
    (f'private static readonly string ClientSecret = @"{KEY}";', KEY, API_KEY),
    (f'options.SigningKey = "{KEY}";', KEY, API_KEY),
    (f'var accessToken = "{TOKEN_VALUE}";', TOKEN_VALUE, TOKEN),
    # Python
    (f'API_KEY = "{KEY}"', KEY, API_KEY),
    (f'SECRET_KEY = "{KEY}"', KEY, API_KEY),
    (f"client = Client(api_key='{KEY}')", KEY, API_KEY),
    (f'refresh_token: str = "{TOKEN_VALUE}"', TOKEN_VALUE, TOKEN),
    (f'api_key = os.getenv("API_KEY", "{KEY}")', KEY, API_KEY),
    # JavaScript / TypeScript
    (f"const stripeSecretKey = '{KEY}';", KEY, API_KEY),
    (f'const config = {{ authToken: "{TOKEN_VALUE}" }};', TOKEN_VALUE, TOKEN),
    (f'const key = process.env.API_KEY || "{KEY}";', KEY, API_KEY),
    # JSON
    (f'{{"Stripe": {{"SecretKey": "{KEY}"}}}}', KEY, API_KEY),
    (f'{{"access_token": "{TOKEN_VALUE}", "token_type": "bearer"}}', TOKEN_VALUE, TOKEN),
    # YAML
    (f"api_key: {KEY}", KEY, API_KEY),
    (f"  client_secret: '{KEY}'", KEY, API_KEY),
    (f"token: {TOKEN_VALUE}", TOKEN_VALUE, TOKEN),
    # .env
    (f"OPENAI_API_KEY={KEY}", KEY, API_KEY),
    (f"AWS_SECRET_ACCESS_KEY={KEY}", KEY, API_KEY),
    (f"export GITHUB_TOKEN={TOKEN_VALUE}", TOKEN_VALUE, TOKEN),
    # Bare "token" and "secret" count when the value looks generated.
    (f'token = "{TOKEN_VALUE}"', TOKEN_VALUE, TOKEN),
    (f'secret = "{KEY}"', KEY, API_KEY),
])
def test_api_key_and_token_assignments(snippet, secret, category):
    out, report = redact_text(snippet)

    assert out == snippet.replace(secret, placeholder(category))
    assert report.replaced == {category: 1}


@pytest.mark.parametrize("secret, category", [
    (GITHUB, TOKEN),
    (GITHUB_PAT, TOKEN),
    (GITLAB, TOKEN),
    (SLACK, TOKEN),
    (NPM, TOKEN),
    (HUGGING_FACE, TOKEN),
    (JWT, TOKEN),
    (AWS, API_KEY),
    (GOOGLE, API_KEY),
    (STRIPE, API_KEY),
    (OPENAI, API_KEY),
    (ANTHROPIC, API_KEY),
    (SENDGRID, API_KEY),
], ids=[
    "github", "github-pat", "gitlab", "slack", "npm", "hugging-face", "jwt",
    "aws", "google", "stripe", "openai", "anthropic", "sendgrid",
])
def test_vendor_formats_are_found_without_a_telling_name(secret, category):
    # In a comment, a list and a URL: nothing nearby says "key" or "token".
    snippet = (
        f"// left over from debugging: {secret}\n"
        f'var values = new[] {{ "{secret}" }};\n'
        f"https://example.com/hook/{secret}\n"
    )

    out, report = redact_text(snippet)

    assert out == snippet.replace(secret, placeholder(category))
    assert report.replaced == {category: 3}
    assert report.distinct == {category: 1}


@pytest.mark.parametrize("snippet, secret, category", [
    (f"Authorization: Bearer {TOKEN_VALUE}", TOKEN_VALUE, AUTHORIZATION),
    (f"Proxy-Authorization: Basic {BASIC}", BASIC, AUTHORIZATION),
    (f'curl -H "Authorization: Token {TOKEN_VALUE}" https://api.example.com', TOKEN_VALUE, AUTHORIZATION),
    (f'client.DefaultRequestHeaders.Add("Authorization", "Bearer {TOKEN_VALUE}");', TOKEN_VALUE, AUTHORIZATION),
    (f'headers = {{"Authorization": "Basic {BASIC}"}}', BASIC, AUTHORIZATION),
    (f"headers['Authorization'] = 'Bearer {TOKEN_VALUE}'", TOKEN_VALUE, AUTHORIZATION),
    (f'new AuthenticationHeaderValue("Bearer", "{TOKEN_VALUE}")', TOKEN_VALUE, AUTHORIZATION),
    (f'curl -H "X-Api-Key: {KEY}" https://api.example.com', KEY, API_KEY),
    (f'request.Headers.Add("Ocp-Apim-Subscription-Key", "{KEY}");', KEY, API_KEY),
    (f"X-Auth-Token: {TOKEN_VALUE}", TOKEN_VALUE, TOKEN),
    (f'var credential = new NetworkCredential("ann", "{FAKE}");', FAKE, PASSWORD),
    (f'requests.get(url, auth=("ann", "{FAKE}"))', FAKE, PASSWORD),
    (f"session.auth = HTTPBasicAuth('ann', '{FAKE}')", FAKE, PASSWORD),
    (f"curl -u ann:{FAKE} https://api.example.com", FAKE, PASSWORD),
])
def test_authorization_credentials(snippet, secret, category):
    out, report = redact_text(snippet)

    assert out == snippet.replace(secret, placeholder(category))
    assert report.replaced == {category: 1}


def test_aws_credential_pair_redacts_both_parts():
    snippet = f'new BasicAWSCredentials("{AWS}", "{KEY}")'

    out, report = redact_text(snippet)

    assert out == (
        f'new BasicAWSCredentials("{placeholder(API_KEY, 1)}", "{placeholder(API_KEY, 2)}")'
    )
    assert report.distinct == {API_KEY: 2}


@pytest.mark.parametrize("snippet, secret, category", [
    # SQL Server
    (f'"Server=db;Database=Shop;User Id=sa;Password={FAKE};"', FAKE, PASSWORD),
    (f'"Data Source=db;Initial Catalog=Shop;User ID=sa;Password={FAKE}"', FAKE, PASSWORD),
    # ODBC with a braced value that contains ';'
    ('"Driver={SQL Server};Server=db;Uid=sa;Pwd={Fake;Pass111};"', "Fake;Pass111", PASSWORD),
    # Npgsql with a quoted value
    ("\"Host=db;Port=5432;Username=app;Password='Fake;Pass111'\"", "Fake;Pass111", PASSWORD),
    # MySQL, in appsettings.json
    (f'{{"ConnectionStrings": {{"Default": "server=db;uid=app;pwd={FAKE};database=shop"}}}}', FAKE, PASSWORD),
    # Azure Storage and Service Bus
    (f'"DefaultEndpointsProtocol=https;AccountName=fakeacct;AccountKey={BASIC};EndpointSuffix=core.windows.net"', BASIC, API_KEY),
    (f'"Endpoint=sb://fake.servicebus.windows.net/;SharedAccessKeyName=Root;SharedAccessKey={BASIC}"', BASIC, API_KEY),
    # Passwords in URLs
    (f"DATABASE_URL=postgres://app:{FAKE}@db:5432/shop", FAKE, PASSWORD),
    (f'client = MongoClient("mongodb+srv://app:{FAKE}@cluster0.example.net/shop")', FAKE, PASSWORD),
    (f"redis://:{FAKE}@cache:6379/0", FAKE, PASSWORD),
    # Secrets in query strings
    (f"https://api.example.com/v1/orders?user=ann&api_key={KEY}&page=2", KEY, API_KEY),
    (f"https://example.com/callback?access_token={TOKEN_VALUE}", TOKEN_VALUE, TOKEN),
    (f"https://acct.blob.core.windows.net/c/f.txt?sv=2022-11-02&sig={BASIC}", BASIC, TOKEN),
])
def test_connection_string_credentials(snippet, secret, category):
    out, report = redact_text(snippet)

    assert out == snippet.replace(secret, placeholder(category), 1)
    assert report.replaced == {category: 1}


def test_private_key_body_is_replaced_and_markers_kept():
    body = "MIIEFAKEFAKEFAKEFAKE\nFAKEFAKEFAKEFAKE==\n"
    pem = f"-----BEGIN RSA PRIVATE KEY-----\n{body}-----END RSA PRIVATE KEY-----\n"

    out, report = redact_text(pem)

    assert out == (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        f"{placeholder(PRIVATE_KEY)}\n"
        "-----END RSA PRIVATE KEY-----\n"
    )
    assert report.replaced == {PRIVATE_KEY: 1}


def test_private_key_in_a_json_string_with_escaped_newlines():
    # A cloud service-account file: the key is one JSON string with \n escapes.
    snippet = (
        r'{"type": "service_account", "private_key": "-----BEGIN PRIVATE KEY-----'
        r'\nMIIEFAKEFAKEFAKEFAKE\nFAKEFAKE==\n-----END PRIVATE KEY-----\n"}'
    )

    out, _ = redact_text(snippet)

    assert out == (
        r'{"type": "service_account", "private_key": "-----BEGIN PRIVATE KEY-----'
        + r"\n" + placeholder(PRIVATE_KEY) + r'\n-----END PRIVATE KEY-----\n"}'
    )


def test_same_secret_keeps_one_placeholder_across_kinds_of_context():
    snippet = (
        f"DB_PASSWORD={FAKE}\n"
        f'"Server=db;User Id=sa;Password={FAKE};"\n'
        f"postgres://app:{FAKE}@db/shop\n"
    )

    out, report = redact_text(snippet)

    assert out == snippet.replace(FAKE, P1)
    assert report.replaced == {PASSWORD: 3}
    assert report.distinct == {PASSWORD: 1}


def test_report_counts_every_category_without_values():
    snippet = (
        f'string password = "{FAKE}";\n'
        f'string apiKey = "{KEY}";\n'
        f"// {GITHUB}\n"
        f"Authorization: Bearer {TOKEN_VALUE}\n"
        "-----BEGIN PRIVATE KEY-----\nMIIEFAKEFAKEFAKEFAKE\n-----END PRIVATE KEY-----\n"
        f'"Server=db;Uid=sa;Pwd={OTHER};"\n'
        f'string sameKeyAgain = "{KEY}";\n'
    )

    redactor = SecretRedactor()
    out = redactor.redact(snippet)
    report = redactor.report()

    assert report.replaced == {
        API_KEY: 2, AUTHORIZATION: 1, PASSWORD: 2, PRIVATE_KEY: 1, TOKEN: 1,
    }
    assert report.distinct == {
        API_KEY: 1, AUTHORIZATION: 1, PASSWORD: 2, PRIVATE_KEY: 1, TOKEN: 1,
    }
    assert report.total == 7
    assert str(report) == (
        "7 secret value(s) replaced "
        "(API_KEY x2, AUTHORIZATION x1, PASSWORD x2, PRIVATE_KEY x1, TOKEN x1)"
    )

    for secret in (FAKE, OTHER, KEY, GITHUB, TOKEN_VALUE, "MIIEFAKE"):
        assert secret not in out
        assert secret not in str(report)
        assert secret not in repr(report)
        assert secret not in repr(redactor)


@pytest.mark.parametrize("snippet", [
    # "key" alone is not a secret
    'string cacheKey = "user:42:profile";',
    'var primaryKey = "OrderId";',
    'sortKey = "createdAt123"',
    'var key = "orders:2024:summary";',
    'Configuration["Jwt:Key"]',
    # Labels and header names
    'const apiKeyHeader = "X-Api-Key";',
    'ApiKey = "X-Api-Key"',
    'tokenType = "Bearer"',
    # Parser and framework tokens
    "CancellationToken token = default;",
    'token = "IDENTIFIER"',
    'nextToken = "END_OF_FILE"',
    'currentToken = "("',
    "token: string;",
    "accessToken: AccessToken",
    # Secret names and flags that are not secrets
    'secretName = "db-creds"',
    "secret: my-db-secret",
    'isSecret = "false"',
    # File paths under key names
    'privateKey = "keys/app.pem"',
    'var signingKey = File.ReadAllText(@"C:\\keys\\signing.key");',
    # Lookups, templates and stand-ins
    'api_key = os.environ["API_KEY"]',
    "const apiKey = process.env.API_KEY;",
    'api_key = "${API_KEY}"',
    "API_KEY=$API_KEY",
    'apiKey: "<your-api-key>"',
    "API_KEY=YOUR_API_KEY",
    'token: "{{ github_token }}"',
    "GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}",
    "api_key=api_key,",
    # Connection strings without a credential, or with one built in code
    '"Server=db;Database=Shop;Integrated Security=true;"',
    '"Server=db;Password=" + pwd + ";"',
    "conn = 'Server=db;Uid=sa;Pwd=' + pw + ';'",
    '$"Server={host};Database=Shop;Password={pwd};"',
    'f"Host={host};Password={settings.password};"',
    'string.Format("Server={0};Password={1};", host, pwd)',
    "password=pw;",
    # URLs without credentials, or with a documentation stand-in
    "https://example.com:8080/path?page=2&sort=name",
    "postgres://user:password@localhost/db",
    "git@github.com:org/repo.git",
    "https://example.com/search?token=abc",
    # Authorization with no literal credential
    "Authorization: Bearer ${TOKEN}",
    "Authorization: Bearer <token>",
    'Authorization = "Bearer " + token;',
    'headers["Authorization"] = $"Bearer {token}";',
    "The Authorization header is required.",
    "Authorization: required",
    '[Authorize(Roles = "Admin")]',
    # Header names in code: annotations, variables, lists of names
    "authorization: Optional[str] = None",
    'auth_headers = {"api-key": self.api_key}',
    'SENSITIVE_HEADERS = {"api-key", "authorization"}',
    'client = OpenAI(base_url=url, api_key="lm-studio")',
    'client_args={"proxy": "socks5://user:pass@host:port"}',
    # Look a little like keys, but are not secrets
    'class="sk-fading-circle-spinner-small"',
    f'stripe.publishableKey = "{STRIPE_PUBLISHABLE}"',
    'var id = Guid.Parse("3f2504e0-4f89-11d3-9a0c-0305e82c3301");',
    'digest = "sha256:3f2504e04f8911d39a0c0305e82c3301"',
    'src = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="',
])
def test_non_secret_near_secret_words_is_left_alone(snippet):
    out, report = redact_text(snippet)

    assert out == snippet
    assert report.total == 0
