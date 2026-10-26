"""
utils/secret_redaction.py

Finds secrets in text on this machine and swaps them for placeholders before
the text is sent to an AI provider (T-077, US-034).

    password = "Fake-Pass-111!"   ->   password = "[REDACTED_PASSWORD_1]"

Detection is plain pattern matching: no AI call, no network. It is
contextual. A value counts as a secret only when the text around it says so
(the name it is assigned to, the header it is sent in, the connection string
it sits in) or when it has a vendor's unmistakable token format. A string is
not redacted just because it is long or contains the word "key".

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

Categories say what the secret is, not where it was found: a password in a
connection string is a PASSWORD.

Detected
--------
PASSWORD
    A quoted value assigned to, compared with, or used as the default of a
    name ending in password, passwd, pwd, passphrase or pass (DB_PASSWORD,
    adminPwd, smtp_pass, password2):
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
    Command-line flags (--password=..., --password ...), curl -u user:...,
    and credential pairs: new NetworkCredential("user", "..."),
    HTTPBasicAuth("user", "..."), auth=("user", "...").

API_KEY, TOKEN
    The same formats, for names ending in api key; access, secret, private,
    subscription, signing, encryption, master, account or hmac key; secret;
    or token (API_KEY=..., "apiKey": "...", clientSecret = "...",
    access_token: ...). A bare "token" or "secret", and parser state such as
    nextToken, only count when the value looks generated (letters and
    digits). Names that merely contain "key" (cacheKey, primaryKey, sortKey)
    never count.
    Recognisable formats wherever they appear: AWS access key IDs (AKIA...),
    Google API keys (AIza...), Stripe secret keys (sk_live_..., rk_test_...),
    OpenAI and Anthropic style keys (sk-...), SendGrid keys (SG....),
    GitHub (ghp_..., github_pat_...), GitLab (glpat-...), Slack (xoxb-...),
    npm (npm_...) and Hugging Face (hf_...) tokens, and JSON Web Tokens.
    Credential headers: X-Api-Key, X-Auth-Token, Ocp-Apim-Subscription-Key.

AUTHORIZATION
    Authorization and Proxy-Authorization header values (Bearer, Basic,
    Token ...) in HTTP text, curl -H, header dictionaries and
    DefaultRequestHeaders.Add(...); new AuthenticationHeaderValue("Bearer", "...").

Connection strings
    Password/Pwd (PASSWORD), AccountKey and SharedAccessKey (API_KEY) and
    SharedAccessSignature (TOKEN) in key=value; connection strings (SQL
    Server, ODBC, Npgsql, MySQL, Azure Storage, Service Bus), including
    {braced} and 'quoted' values. It only counts as a connection string when
    the same line also has ';' and a key such as Server=, Host= or
    AccountName=. Passwords in URLs (postgres://user:...@host) and secrets in
    query strings (?api_key=..., &sig=...).

PRIVATE_KEY
    The body of a -----BEGIN ... PRIVATE KEY----- block. The BEGIN and END
    lines stay.

Left alone
----------
Environment-variable and config lookups (os.getenv("DB_PASSWORD"),
process.env.API_KEY, Configuration["Db:Password"]); template and format
references (${API_KEY}, $(DbPassword), %DB_PASSWORD%, {{ token }}, {0},
{password}); interpolated and concatenated values ($"...{pwd}...",
"Password=" + pwd); obvious stand-ins (<api-key>, ********, YOUR_API_KEY,
changeme, PASSWORD_HERE, user:pass@host); our own [REDACTED_...]
placeholders; labels ("Password", "X-Api-Key"); one- or two-word values under
key names (api_key="lm-studio"); names that only start with a secret word
(PasswordLength, tokenType, apiKeyHeader); words that merely end in "pass"
(bypass, compass) and "PASS" as a test status; ternary branches
(ok ? "PASS" : "FAIL"); header names used in code (authorization:
Optional[str], {"api-key": self.api_key}); file paths under key names
(privateKey = "keys/app.pem"); publishable Stripe keys (pk_...); and
connection strings that carry no credential (Integrated Security=true).

Checked against this repository, its target codebases and about 11 MB of
third-party Python (openai, anthropic, langchain, httpx, requests, google-genai):
the only redactions left there are documentation passwords in URLs, test
fixture keys, and one pagination token.

Limitations
-----------
This is pattern matching, not proof that nothing sensitive remains. It
misses:
- a secret with no telling name beside it (Login("admin", "...")) or passed
  positionally;
- a bare "Key" name (the Jwt:Key signing key in appsettings.json), since
  most keys are not secrets;
- vendor formats not listed above;
- a token with no digits under a bare "token" or "secret" name, and a key
  of under 21 characters made only of letters, dots, dashes and underscores;
- an unquoted INI value that looks like an identifier (password = letmein);
- a key, token or connection-string value that is built by concatenation,
  or a key or token value containing spaces (authToken = "Bearer ...").
It can over-redact a variable in a JavaScript object written one property
per line (password: pw), and documentation examples written in a real
vendor format.
"""

import re
from collections import Counter
from dataclasses import dataclass


PASSWORD = "PASSWORD"
API_KEY = "API_KEY"
TOKEN = "TOKEN"
AUTHORIZATION = "AUTHORIZATION"
PRIVATE_KEY = "PRIVATE_KEY"

# [REDACTED_PASSWORD_1], [REDACTED_API_KEY_2] ...
PLACEHOLDER_RE = re.compile(r"\[REDACTED_([A-Z]+(?:_[A-Z]+)*)_(\d+)\]")

# Shorter values are noise ("", "x", "?"), not credentials.
MIN_SECRET_LENGTH = 3

# Generated keys and tokens are long. Shorter values under a key or token
# name are ids, flags and test stubs.
MIN_KEY_LENGTH = 8

# A secret found in context is also replaced where it appears on its own
# elsewhere in the same request, but only when it is long and mixed enough
# not to be an ordinary word. Otherwise password = "administrator" would
# blank out every "administrator" in the code.
MIN_PROPAGATED_LENGTH = 8


class SecretRedactionError(RuntimeError):
    """The text could not be checked for secrets, so it must not be sent."""


# ---------------------------------------------------------
# Names that mark a value as a secret
# ---------------------------------------------------------

_PASSWORD_WORDS = ("passphrase", "password", "passwd", "pwd", "pass")

# Only these kinds of key are secrets. cacheKey, primaryKey and sortKey are not.
_KEY_KINDS = (
    "api", "access", "secret", "private", "subscription", "signing",
    "encryption", "master", "account", "hmac",
)

_API_KEY_WORDS = (
    rf"(?:{'|'.join(_KEY_KINDS)})[_\-.]?key(?:[_\-.]?id)?",
    "secret",
)

_TOKEN_WORDS = ("token",)

_SECRET_WORDS = _PASSWORD_WORDS + _API_KEY_WORDS + _TOKEN_WORDS

# What comes before "token" when the token is a credential: accessToken,
# GITHUB_TOKEN, x-auth-token. nextToken and endToken are parser state.
_CREDENTIAL_TOKEN_KINDS = (
    "access", "refresh", "auth", "bearer", "api", "id", "session", "csrf",
    "xsrf", "github", "gh", "gitlab", "slack", "discord", "telegram", "bot",
    "oauth", "jwt", "personal", "pat", "security", "sas", "client", "app",
    "user", "service", "deploy", "hf", "huggingface", "npm", "pypi", "sentry",
    "webhook", "secret", "private",
)

# Words that also end ordinary words (bypass, compass), so they only count
# after a separator or a camelCase hump: db_pass, smtpPass, PASS.
_SHORT_WORDS = {"pass"}

# Shell variables that hold a directory, not a password.
_NOT_SECRET_NAMES = {"PWD", "OLDPWD"}


def _alternation(words):
    return "|".join(words)


_ENDINGS = (
    (PASSWORD, re.compile(rf"(?:{_alternation(_PASSWORD_WORDS)})$", re.IGNORECASE)),
    (API_KEY, re.compile(rf"(?:{_alternation(_API_KEY_WORDS)})$", re.IGNORECASE)),
    (TOKEN, re.compile(rf"(?:{_alternation(_TOKEN_WORDS)})$", re.IGNORECASE)),
)


def _separate(prefix, first):
    """Whether a short word starts a word of its own: db_pass, smtpPass, not bypass."""
    last = prefix[-1]
    return last in "_-.$@: /" or (first.isupper() and (last.islower() or last.isdigit()))


def _classify(name):
    """
    (category, word, strong) when name ends in a secret word, else None.

    A trailing number is ignored (password2, API_KEY_1). strong means the
    name alone is convincing (api_key, accessToken). A bare "token" or
    "secret", or parser state like nextToken, is weak: the value must also
    look generated.
    """
    if name in _NOT_SECRET_NAMES:
        return None

    base = re.sub(r"[_\-]?\d{1,3}$", "", name)

    for category, ending in _ENDINGS:
        match = ending.search(base)
        if match is None:
            continue

        prefix, word = base[: match.start()], match.group(0).lower()

        if category == PASSWORD:
            if word in _SHORT_WORDS and prefix and not _separate(prefix, base[match.start()]):
                return None
            return PASSWORD, word, True

        if category == API_KEY:
            return API_KEY, word, word != "secret" or bool(prefix.strip("_-.$@: "))

        stem = re.sub(r"[^a-z]", "", prefix.lower())
        return TOKEN, word, stem.endswith(_CREDENTIAL_TOKEN_KINDS)

    return None


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
      | \{[A-Za-z_][\w.]*\}         # {password}
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
_MASK_RE = re.compile(r"^[*xX\u2022\u00b7.\-_#?]+$")

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

# "Password", "Enter your password", "/account/reset-password", "X-Api-Key"
_LABEL_RE = re.compile(r"^[A-Za-z/][A-Za-z \-_/.:]*$")

_LABEL_WORDS = {
    PASSWORD: re.compile(r"passphrase|password|passwd|pwd", re.IGNORECASE),
    API_KEY: re.compile(r"key|secret", re.IGNORECASE),
    TOKEN: re.compile(r"token", re.IGNORECASE),
    AUTHORIZATION: re.compile(r"auth|bearer|basic|token|key", re.IGNORECASE),
}

_PLAIN_WORDS_RE = re.compile(r"^[A-Za-z][A-Za-z_\-.]{0,19}$")

# What a "pwd" holds when it is the working directory.
_PATH_RE = re.compile(r"^(?:[/~.]|[A-Za-z]:[\\/]|\\\\)")

# What a privateKey or secretFile holds when it names a file.
_FILE_PATH_RE = re.compile(
    r"^(?:[A-Za-z]:[\\/]|\\\\|~/|\.{1,2}[\\/])"
    r"|^/?[\w.\-]+(?:[\\/][\w.\-]+)*\.(?:pem|key|p12|pfx|json|crt|cer|der|txt|env|ppk|jks|keystore)$"
    r"|^/[\w.\-]+(?:/[\w.\-]+)+$",
    re.IGNORECASE,
)

# What sits between two string literals: "Password=" + pwd + ";" leaves
# " + pwd + " looking like a quoted value.
_CONCATENATION_RE = re.compile(r"^\s*[+&.]\s|\s[+&.]\s*$")

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


def _looks_generated(value):
    """Letters and digits together, as generated keys have and IDENTIFIER or Optional do not."""
    return any(c.isalpha() for c in value) and any(c.isdigit() for c in value)


def _password_value_ok(value, word="password"):
    value = value.strip()

    if len(value) < MIN_SECRET_LENGTH or _is_stand_in(value):
        return False

    # user:pass@host and password = "pass" are documentation, not passwords.
    if value.lower() in _NULLISH or value.lower() in _SHORT_WORDS:
        return False

    if _LABEL_RE.match(value) and _LABEL_WORDS[PASSWORD].search(value):
        return False

    if word == "pwd" and _PATH_RE.match(value):
        return False

    return True


def _key_value_ok(value, category, strong):
    """For API keys, tokens and authorization values."""
    value = value.strip()

    # Generated credentials never contain spaces; prose and expressions do.
    if len(value) < MIN_KEY_LENGTH or re.search(r"\s", value):
        return False

    if _is_stand_in(value) or value.lower() in _NULLISH:
        return False

    if _LABEL_RE.match(value) and _LABEL_WORDS[category].search(value):
        return False

    # A word or two ("lm-studio", "authorization", "development"), not a
    # generated key. Long letter-only keys still count.
    if _PLAIN_WORDS_RE.match(value):
        return False

    if _FILE_PATH_RE.match(value):
        return False

    return strong or _looks_generated(value)


def _value_ok(category, word, strong, value):
    if category == PASSWORD:
        return _password_value_ok(value, word)
    return _key_value_ok(value, category, strong)


# ---------------------------------------------------------
# Patterns
# ---------------------------------------------------------

def _name(words):
    """A name ending in one of words, optionally numbered: DB_PASSWORD, apiKey, token2."""
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


# name = "value", name: 'value', "name": "value", ["name"] = @"value" ...
#
# The lookbehind keeps a match from starting in the middle of a longer name.
# Without it every character of a long run like a-b-c-... would start a scan
# to the end of the run, which is quadratic. A quoted "name" straight after
# ? is a ternary branch (ok ? "PASS" : "FAIL"), not a key.
_ASSIGNMENT = re.compile(
    rf"""
    (?:
        (?<![\w$@.\-])(?P<name>{_name(_SECRET_WORDS)})
      | (?<!\?)(?<!\?[ ])(?P<kq>["'])(?P<qname>[^"'\r\n]{{0,200}}?(?:{_alternation(_SECRET_WORDS)})(?:[_\-]?\d{{1,3}})?)(?P=kq)\]?
    )
    (?:[ \t]*\{{[ \t]*get;[^}}\r\n]*\}})?
    (?:[ \t]*:[ \t]*[A-Za-z_][\w.]*(?:\[[\w., ]*\])?\??(?=[ \t]*=[^=>]))?
    [ \t]*(?P<op>===?|!==?|:=|=>|=|:)\s*
    {_LITERAL}
    """,
    re.VERBOSE | re.IGNORECASE,
)

# The default in a lookup is a hard-coded secret too:
# os.getenv("API_KEY", "..."), config.get("password", "..."),
# GetEnvironmentVariable("TOKEN") ?? "...", process.env.API_KEY || "..."
_LOOKUP_DEFAULT = re.compile(
    rf"""
    (?:
        (?:getenv|\.get|GetValue(?:<[\w.]+>)?|GetEnvironmentVariable)
        \(\s*(?P<kq>["'])(?P<qname>[^"'\r\n]{{1,200}})(?P=kq)\s*
        (?:,|\)\s*(?:\?\?|\|\||or\b))
      | process\.env\.(?P<name>[\w$]+)\s*(?:\|\||\?\?)
    )
    \s*
    {_LITERAL}
    """,
    re.VERBOSE | re.IGNORECASE,
)

# KEY=value, KEY: value or KEY = value as a whole line (.env, YAML, INI).
#
# The value runs to the end of the line. Comments and code-shaped values are
# dealt with in _config_line(), not here: a lazy match up to an optional
# comment would backtrack quadratically on a long minified line.
_CONFIG_LINE = re.compile(
    rf"""
    ^[ \t]*(?:export[ \t]+|-[ \t]+)?
    (?P<name>{_name(_SECRET_WORDS)})
    (?:(?P<tight>=)|[ \t]*(?P<sep>[:=])[ \t]+)
    (?P<value>[^\s"'`=>\#][^\r\n]*)
    """,
    re.VERBOSE | re.IGNORECASE | re.MULTILINE,
)

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

# curl -u user:password / --user "user:password"
_CURL_USER = re.compile(
    r"""\bcurl\b[^\r\n]*?[ \t](?:-u|--user)[ \t]+(?P<q>["']?)[^\s:"']*:(?P<value>[^\s"']+)(?P=q)"""
)

# new NetworkCredential("user", "..."), HTTPBasicAuth("user", "..."),
# auth=("user", "..."), new BasicAWSCredentials("AKIA...", "...")
_CREDENTIAL_CALL = re.compile(
    r"""
    (?P<fn>NetworkCredential|HTTPBasicAuth|HTTPDigestAuth|HTTPProxyAuth|SetBasicAuth
      |BasicAuth|UsernamePasswordCredentials?|PasswordAuthentication
      |BasicAWSCredentials|auth[ \t]*=[ \t]*)
    \(\s*
    (?:@?"[^"\r\n]*"|'[^'\r\n]*'|[\w.]+)
    \s*,\s*
    (?:@?"(?P<dq>(?:[^"\\\r\n]|\\.)*)"|'(?P<sq>(?:[^'\\\r\n]|\\.)*)')
    """,
    re.VERBOSE,
)

_AUTH_SCHEMES = (
    "bearer", "basic", "token", "digest", "apikey", "api-key", "key", "sso-key",
    "otp", "negotiate", "ntlm", "hmac", "sharedkey",
)

# Authorization: Bearer ... in HTTP text, curl -H, header dictionaries and
# Headers.Add("Authorization", "..."); hyphenated credential headers such as
# X-Api-Key and X-Auth-Token.
_HEADER = re.compile(
    rf"""
    (?<![\w\-])(?P<name>(?:proxy-)?authorization|(?:[A-Za-z0-9]+-)+(?:key|token|secret))
    (?P<nq>["']?)\]?[ \t]*[:=,][ \t]*(?P<vq>["'`]?)
    (?:(?P<scheme>{_alternation(_AUTH_SCHEMES)})[ \t]+)?
    (?P<value>[^\s"'`,;<>()\[\]{{}}]+)
    """,
    re.VERBOSE | re.IGNORECASE,
)

# After a header value, these mean it was code: Optional[str], get_key(), List<string>.
_CODE_FOLLOWERS = set("[(<{")

# new AuthenticationHeaderValue("Bearer", "...")
_AUTH_HEADER_VALUE = re.compile(
    r"""AuthenticationHeaderValue\(\s*@?"[^"\r\n]*"\s*,\s*@?"(?P<value>[^"\r\n]+)"\s*\)""",
    re.IGNORECASE,
)

# The credential keys of a connection string, and the category of each.
_CONNECTION_KEYS = {
    "password": PASSWORD,
    "pwd": PASSWORD,
    "userpassword": PASSWORD,
    "databasepassword": PASSWORD,
    "accountkey": API_KEY,
    "sharedaccesskey": API_KEY,
    "sharedaccesssignature": TOKEN,
}

_CONNECTION_CREDENTIAL = re.compile(
    r"""
    (?<![\w.\-])
    (?P<key>password|pwd|user[ ]password|(?:jet[ ]oledb:)?database[ ]password
      |account[ ]?key|shared[ ]?access[ ]?key|shared[ ]?access[ ]?signature)
    [ \t]*=[ \t]*
    (?:
        \{(?P<braced>[^}\r\n]*)\}
      | '(?P<sq>[^'\r\n]*)'
      | (?P<bare>[^;'"\s{][^;'"\r\n]*)
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Another key that shows the line really is a connection string.
_CONNECTION_COMPANION = re.compile(
    r"""
    (?<![\w.\-])
    (?:server|data[ ]source|host|hostname|address|addr|network[ ]address|database
      |initial[ ]catalog|user[ ]?id|uid|user|user[ ]?name|accountname|endpoint
      |defaultendpointsprotocol|provider|driver|dsn|port|integrated[ ]security
      |trusted_connection|sslmode|endpointsuffix|blobendpoint)
    [ \t]*=
    """,
    re.VERBOSE | re.IGNORECASE,
)

# How far either side of a credential to look for the rest of its connection string.
_CONNECTION_WINDOW = 300

# postgres://user:password@host, redis://:password@host
_URL_CREDENTIALS = re.compile(
    r"""\b[a-z][a-z0-9+.\-]{1,30}://[^\s:/@"'`<>]*:(?P<value>[^\s/@"'`<>]+)@(?=[\w\[%])""",
    re.IGNORECASE,
)

# ?api_key=...&sig=...
_QUERY_PARAMETER = re.compile(
    r"""[?&](?P<name>[A-Za-z_][\w\-.]{0,60})=(?P<value>[^&#\s"'`<>;)]+)"""
)

# Query parameters that are credentials without a secret word in the name:
# an Azure SAS or AWS presigned-URL signature.
_SIGNATURE_PARAMETERS = {"sig", "signature", "x-amz-signature"}

_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN (?P<label>(?:[A-Z0-9]+ )*)PRIVATE KEY(?P<block>(?: BLOCK)?)-----"
    r"(?P<body>[\s\S]{1,20000}?)"
    r"-----END (?P=label)PRIVATE KEY(?P=block)-----"
)


def _strong_random(value):
    """Upper case, lower case and digits, as generated keys have and sk-spinner-fade does not."""
    return (
        any(c.isupper() for c in value)
        and any(c.islower() for c in value)
        and any(c.isdigit() for c in value)
    )


# Vendor formats specific enough to redact with no context.
# (category, pattern, extra check)
_FORMATS = tuple(
    (category, re.compile(pattern), check)
    for category, pattern, check in (
        # AWS access key ID
        (API_KEY, r"\b(?:AKIA|ASIA|ABIA|ACCA)[0-9A-Z]{16}\b", None),
        # Google API key
        (API_KEY, r"\bAIza[0-9A-Za-z_\-]{35}(?![\w\-])", None),
        # Stripe secret and restricted keys (pk_ publishable keys are public)
        (API_KEY, r"\b[rs]k_(?:live|test)_[0-9A-Za-z]{16,}\b", None),
        # OpenAI and Anthropic style
        (API_KEY, r"(?<![\w\-])sk-[A-Za-z0-9_\-]{20,}(?![\w\-])", _strong_random),
        # SendGrid
        (API_KEY, r"\bSG\.[A-Za-z0-9_\-]{16,32}\.[A-Za-z0-9_\-]{16,64}(?![\w\-])", None),
        # GitHub
        (TOKEN, r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,255}\b", None),
        (TOKEN, r"\bgithub_pat_[A-Za-z0-9_]{22,255}\b", None),
        # GitLab
        (TOKEN, r"\bglpat-[A-Za-z0-9_\-]{20,}(?![\w\-])", None),
        # Slack
        (TOKEN, r"\bxox[abposr]-[A-Za-z0-9\-]{10,}(?![\w\-])", None),
        # npm
        (TOKEN, r"\bnpm_[A-Za-z0-9]{36}\b", None),
        # Hugging Face
        (TOKEN, r"\bhf_[A-Za-z0-9]{30,}\b", _strong_random),
        # JSON Web Token: header.payload.signature
        (TOKEN, r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}(?![\w\-])", None),
    )
)


# ---------------------------------------------------------
# Rules: each turns a match into (start, end, value, category), or None
# ---------------------------------------------------------

def _literal_group(match):
    for group in _LITERAL_GROUPS:
        if match.group(group) is not None:
            return group
    return None


def _first_group(match, groups):
    return next((g for g in groups if match.group(g) is not None), None)


def _assignment(match):
    found = _classify(match.group("name") or match.group("qname"))
    group = _literal_group(match)

    if found is None or group is None:
        return None

    # A quoted "PASS" is a test status ("PASS" => ..., case "PASS":), not a key.
    if match.group("qname") and match.group("qname").lower() in _SHORT_WORDS:
        return None

    category, word, strong = found
    value = match.group(group)
    prefix = (match.group("vpre") or match.group("dpre") or match.group("spre") or "").lower()

    # $"..." and f"..." with {braces} build the value from code.
    if ("$" in prefix or "f" in prefix) and "{" in value:
        return None

    if _CONCATENATION_RE.search(value) or not _value_ok(category, word, strong, value):
        return None

    return match.start(group), match.end(group), value, category


def _config_line(match):
    found = _classify(match.group("name"))
    if found is None:
        return None

    category, word, strong = found
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
        # password: userPassword / api_key=api_key -- a variable. A generated
        # value can contain the word too (FakeKey123), so only plain words.
        if _LABEL_WORDS[category].search(value) and not _looks_generated(value):
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

    if not _value_ok(category, word, strong, value):
        return None

    start = match.start("value")
    return start, start + len(value), value, category


def _password_flag(match):
    group = _first_group(match, ("dq", "sq", "bare"))
    value = match.group(group)

    if not _password_value_ok(value):
        return None

    return match.start(group), match.end(group), value, PASSWORD


def _curl_user(match):
    value = match.group("value")

    if not _password_value_ok(value):
        return None

    return match.start("value"), match.end("value"), value, PASSWORD


def _credential_call(match):
    group = _first_group(match, ("dq", "sq"))
    value = match.group(group)

    if match.group("fn") == "BasicAWSCredentials":
        ok, category = _key_value_ok(value, API_KEY, True), API_KEY
    else:
        ok, category = _password_value_ok(value), PASSWORD

    if not ok:
        return None

    return match.start(group), match.end(group), value, category


def _header(match):
    name, value = match.group("name"), match.group("value")
    text, end = match.string, match.end("value")

    # authorization: Optional[str] = None is a type annotation.
    if end < len(text) and text[end] in _CODE_FOLLOWERS:
        return None

    # {"api-key": self.api_key}: a quoted name with an unquoted value is code.
    if match.group("nq") and not match.group("vq"):
        return None

    if name.lower().endswith("authorization"):
        category, strong = AUTHORIZATION, match.group("scheme") is not None
    else:
        found = _classify(name)
        if found is None:
            return None
        category, _, strong = found

    # "Authorization: Bearer" with the token added in code.
    if value.lower() in _AUTH_SCHEMES or not _key_value_ok(value, category, strong):
        return None

    return match.start("value"), match.end("value"), value, category


def _auth_header_value(match):
    value = match.group("value")

    if not _key_value_ok(value, AUTHORIZATION, True):
        return None

    return match.start("value"), match.end("value"), value, AUTHORIZATION


def _in_connection_string(text, start, end):
    """Whether the credential at start..end sits in a key=value; connection string."""
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end < 0:
        line_end = len(text)

    around = text[max(line_start, start - _CONNECTION_WINDOW): min(line_end, end + _CONNECTION_WINDOW)]
    return ";" in around and _CONNECTION_COMPANION.search(around) is not None


def _connection_credential(match):
    key = re.sub(r"^jet oledb:|[ ]", "", match.group("key").lower())
    category = _CONNECTION_KEYS[key]

    group = _first_group(match, ("braced", "sq", "bare"))
    value = match.group(group)

    if group == "bare":
        value = value.rstrip()

    # Password={pwd} is C# or Python interpolation, not an ODBC {value}.
    if group == "braced" and (_IDENTIFIER_RE.match(value) or _DOTTED_RE.match(value)):
        return None

    # 'Password=' + pwd + ';' is concatenation, not a 'quoted' value.
    if group == "sq" and _CONCATENATION_RE.search(value):
        return None

    if not _in_connection_string(match.string, match.start(), match.end()):
        return None

    if category == PASSWORD:
        ok = _password_value_ok(value)
    else:
        ok = _key_value_ok(value, category, True)

    if not ok:
        return None

    start = match.start(group)
    return start, start + len(value), value, category


def _url_credentials(match):
    value = match.group("value")

    if not _password_value_ok(value):
        return None

    return match.start("value"), match.end("value"), value, PASSWORD


def _query_parameter(match):
    name, value = match.group("name"), match.group("value")

    if name.lower() in _SIGNATURE_PARAMETERS:
        category, word, strong = TOKEN, name.lower(), True
    else:
        found = _classify(name)
        if found is None:
            return None
        category, word, strong = found

    if not _value_ok(category, word, strong, value):
        return None

    return match.start("value"), match.end("value"), value, category


# Whitespace or an escaped line break around a private key's body.
_PADDING = re.compile(r"\s|\\[rn]")


def _private_key(match):
    text = match.string
    start, end = match.span("body")

    while start < end and (m := _PADDING.match(text, start, end)):
        start = m.end()

    while start < end:
        if text[end - 1].isspace():
            end -= 1
        elif end - start >= 2 and text[end - 2] == "\\" and text[end - 1] in "rn":
            end -= 2
        else:
            break

    value = text[start:end]

    if len(value) < MIN_KEY_LENGTH or _is_stand_in(value):
        return None

    return start, end, value, PRIVATE_KEY


def _format_rule(category, check):
    def extract(match):
        value = match.group(0)
        if check is not None and not check(value):
            return None
        return match.start(), match.end(), value, category
    return extract


# (pattern, extract). When two rules find the same span, the earlier one
# here names the category, so vendor formats come first.
_RULES = (
    *((pattern, _format_rule(category, check)) for category, pattern, check in _FORMATS),
    (_PRIVATE_KEY_BLOCK, _private_key),
    (_HEADER, _header),
    (_AUTH_HEADER_VALUE, _auth_header_value),
    (_CREDENTIAL_CALL, _credential_call),
    (_CURL_USER, _curl_user),
    (_URL_CREDENTIALS, _url_credentials),
    (_CONNECTION_CREDENTIAL, _connection_credential),
    (_QUERY_PARAMETER, _query_parameter),
    (_LOOKUP_DEFAULT, _assignment),
    (_ASSIGNMENT, _assignment),
    (_CONFIG_LINE, _config_line),
    (_PASSWORD_FLAG, _password_flag),
)


def _search(pattern, text, extract):
    """
    Yield (start, end, value, category) for each match extract() accepts.

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
        for pattern, extract in _RULES
        for start, end, value, category in _search(pattern, text, extract)
    ]

    # Earliest first, then the longest of those starting together. The sort
    # is stable, so rule order breaks the remaining ties.
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


# Symbols a password may mix in. Brackets, dots and commas are left out:
# with letters they spell code (Optional[str], self.key), not passwords.
_PASSWORD_SYMBOLS = set("!#$%&*+-=?@^_~/|")


def _propagates(value):
    """
    Long and mixed enough to replace wherever it appears: letters plus digits
    or password symbols, and nothing that reads as code or prose.
    """
    if len(value) < MIN_PROPAGATED_LENGTH or re.search(r"\s", value):
        return False

    if _CODE_CHARACTERS & set(value) or set(value) & set("<>,;"):
        return False

    has_letter = any(c.isalpha() for c in value)
    has_digit = any(c.isdigit() for c in value)
    has_symbol = bool(_PASSWORD_SYMBOLS & set(value))
    return has_letter and (has_digit or has_symbol)


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
            self._reserve_numbers(text)

        for text in texts:
            self._learn(_find(text))

    def redact(self, text):
        """text with every secret replaced by its placeholder."""
        text = _require_text(text)
        self._reserve_numbers(text)

        hits = _find(text)
        self._learn(hits)

        out, pos = [], 0

        for start, end, _, value in hits:
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

    def _reserve_numbers(self, text):
        """Number new placeholders after any already in text."""
        for match in PLACEHOLDER_RE.finditer(text):
            category, number = match.group(1), int(match.group(2))
            self._numbers[category] = max(self._numbers[category], number)

    def _learn(self, hits):
        for _, _, category, value in hits:
            self._placeholder_for(value, category)

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
