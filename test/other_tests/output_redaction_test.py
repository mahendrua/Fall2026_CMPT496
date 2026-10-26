"""!
@file output_redaction_test.py
@brief Tests for utils/output_redaction.py and KnownSecrets (T-078, US-034).
@details What the app shows or saves must not carry a secret the shared
detector finds, including one repeated without the context that identified
it earlier in the same run. Every secret here is fake, and no model or
network is involved. Checks for a planted value go through absent() and
kept(), which fail with a count, never the value: pytest would otherwise
print it in the assertion diff.
"""

import asyncio
import json
import threading

import pytest
from langchain_core.messages import HumanMessage

import utils.output_redaction as output_redaction
import utils.secret_redaction as secret_redaction
from agent.request_redaction import redact_messages
from agent.structured_output.BR_output import ValidatedRule
from agent.structured_output.file_summary_output import FileSummaryOutput
from utils.output_redaction import (
    WITHHELD,
    OutputRedactionError,
    safe_text,
    sanitize_data,
    sanitize_text,
    save_json,
    save_text,
    secret_scope,
)
from utils.secret_redaction import KnownSecrets, SecretRedactor

FAKE = "Fake-Pass-111!"
OTHER = "Fake-Pass-222!"
THIRD = "Fake-Pass-333!"
# Assembled at run time so the repository never holds a real-format token.
GITHUB = "ghp" + "_" + "A1b2C3d4E5f6G7h8I9j0" * 2
P1 = "[REDACTED_PASSWORD_1]"
P2 = "[REDACTED_PASSWORD_2]"

PLANTED = (FAKE, OTHER, THIRD, GITHUB)


def absent(text, *secrets):
    """Fail, without quoting them, if any planted secret is in text."""
    leaked = sum(secret in text for secret in secrets or PLANTED)
    if leaked:
        raise AssertionError(f"{leaked} planted secret(s) found in the output")


def kept(text, secret):
    """Fail, without quoting it, unless secret is still in text (a documented limitation)."""
    if secret not in text:
        raise AssertionError("the value was expected to be left in place")


def learn_from_request(text):
    """What T-077 does with a request: clean it, and tell the open scope what it found."""
    redact_messages([HumanMessage(content=text)])


def keys_of(value):
    """Every key path in nested data, to show the shape survived."""
    if isinstance(value, dict):
        return {(k,) + rest for k, v in value.items() for rest in keys_of(v) | {()}}
    if isinstance(value, list):
        return {(i,) + rest for i, v in enumerate(value) for rest in keys_of(v) | {()}}
    return set()


def non_text(value):
    """Every number, boolean and None in nested data, in order."""
    if isinstance(value, dict):
        return [x for v in value.values() for x in non_text(v)]
    if isinstance(value, list):
        return [x for v in value for x in non_text(v)]
    return [] if isinstance(value, str) else [value]


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------

def test_secret_in_text_is_replaced_and_the_rest_kept():
    out, report = sanitize_text(f'Login failed: password = "{FAKE}" for ann')

    absent(out)
    assert out == f'Login failed: password = "{P1}" for ann'
    assert report.replaced == {"PASSWORD": 1}


@pytest.mark.parametrize("line", [
    "Summarizing: C:/code/Shop/Auth/PasswordPolicy.cs",
    "Validation pass complete: 4 valid, 1 discarded, 0 need more context.",
    "The password is required and must be 12 characters long.",
    'password = os.getenv("DB_PASSWORD")',
    "Google rate limit reached, waiting 31 s before retrying...",
    "",
])
def test_text_without_secrets_is_unchanged(line):
    out, report = sanitize_text(line)

    assert out == line
    assert report.total == 0


def test_vendor_token_in_an_error_message_is_replaced():
    out, report = sanitize_text(f"401 Unauthorized: bad credentials for token {GITHUB}")

    absent(out)
    assert out == "401 Unauthorized: bad credentials for token [REDACTED_TOKEN_1]"
    assert report.replaced == {"TOKEN": 1}


def test_cleaning_twice_changes_nothing():
    text = f'password = "{FAKE}"\nAuthorization: Bearer {GITHUB}'
    once, _ = sanitize_text(text)
    twice, report = sanitize_text(once)

    absent(once)
    assert twice == once
    assert report.total == 0


# ---------------------------------------------------------------------------
# Structured outputs
# ---------------------------------------------------------------------------

def summary_output():
    return FileSummaryOutput(
        path="Shop/Data/Db.cs",
        summary=f'Opens the database with the hard-coded password "{FAKE}".',
        dependencies=["System.Data"],
        types=[{
            "name": "Db",
            "kind": "class",
            "description": "Database access.",
            "properties": [{"name": "Password", "type": "string", "description": "Login.",
                            "visibility": "private", "is_static": True}],
            "methods": [{"name": "Open", "description": "Opens it.", "visibility": "public", "parameters": [
                {"name": "timeout", "type": "int", "direction": "in"}]}],
            "plantuml": "class Db {\n  {static} +Password : string\n}",
        }],
        business_rules=[{"rule": "The database login is fixed.", "source_file": "Shop/Data/Db.cs"}],
    ).model_dump(mode="json")


def validated_rule():
    return ValidatedRule(
        id=7,
        rule="Only administrators may reset passwords.",
        source_directory="Shop/Auth",
        source_file_paths=["Shop/Auth/Reset.cs", "Shop/Auth/Roles.cs"],
        explanation={
            "evidence": {"Shop/Auth/Reset.cs": [f'if (password == "{FAKE}") return true;', "RequireRole(Admin);"]},
            "reasoning": "Reset checks the role first.",
        },
        provenance=[{"origin": "file", "source_file_paths": ["Shop/Auth/Reset.cs"]}],
    ).model_dump(mode="json")


@pytest.mark.parametrize("make, model", [
    (summary_output, FileSummaryOutput),
    (validated_rule, ValidatedRule),
], ids=["file-summary", "validated-rule"])
def test_structured_output_keeps_its_shape_references_and_schema(make, model):
    data = make()
    out, report = sanitize_data(data)
    text = json.dumps(out)

    absent(text)
    assert report.replaced == {"PASSWORD": 1}
    assert keys_of(out) == keys_of(data)
    assert non_text(out) == non_text(data)
    model.model_validate(out)

    if model is ValidatedRule:
        assert out["id"] == 7
        assert out["source_file_paths"] == data["source_file_paths"]
        assert list(out["explanation"]["evidence"]) == ["Shop/Auth/Reset.cs"]
        assert out["explanation"]["evidence"]["Shop/Auth/Reset.cs"][1] == "RequireRole(Admin);"
    else:
        assert out["path"] == "Shop/Data/Db.cs"
        assert out["business_rules"] == data["business_rules"]
        assert out["types"] == data["types"]


def test_input_data_is_not_changed():
    data = validated_rule()
    before = json.dumps(data)

    sanitize_data(data)

    assert json.dumps(data) == before


def test_a_field_name_marks_its_value_a_secret():
    out, report = sanitize_data({
        "db_password": "hunter2-Zz9",
        "apiKey": "abcd1234efgh5678",
        "note": "The same hunter2-Zz9 appears here without a label.",
        "comment": "nothing to see",
    })

    absent(json.dumps(out), "hunter2-Zz9", "abcd1234efgh5678")
    assert out["db_password"] == P1
    assert out["apiKey"] == "[REDACTED_API_KEY_1]"
    assert out["note"] == f"The same {P1} appears here without a label."
    assert out["comment"] == "nothing to see"
    assert report.replaced == {"API_KEY": 1, "PASSWORD": 2}


def test_reference_fields_and_keys_are_copied_unchanged():
    data = {
        "id": "Db.Password",
        "symbol": "Db.Password",
        "content_hash": "3f2a9c",
        "source_file_paths": ["Auth/password.txt"],
        # Limitation: reference values are not scanned, so links between
        # outputs never break -- even if a file name held a secret.
        "candidates": [f"Auth/{FAKE}.cs", "Auth/Other.cs"],
        # A key is never rewritten, even one that reads like a secret's name.
        "password": {"password": "Pw-Not-A-Key-1"},
    }
    out, _ = sanitize_data(data)

    assert out["id"] == out["symbol"] == "Db.Password"
    assert out["content_hash"] == "3f2a9c"
    assert out["source_file_paths"] == ["Auth/password.txt"]
    kept(out["candidates"][0], FAKE)
    assert list(out["password"]) == ["password"]
    assert out["password"]["password"] == P1


def test_list_items_are_text_not_fields_named_after_their_list():
    # The snippets under a file name are code, not values of a field called
    # "password_reset" -- only what the code itself shows counts.
    out, report = sanitize_data({"password_reset": ["ResetAsync(user);", "Notify(user);"]})

    assert out == {"password_reset": ["ResetAsync(user);", "Notify(user);"]}
    assert report.total == 0


@pytest.mark.parametrize("value", [object(), {1, 2}, b"bytes", FileSummaryOutput(path="a", summary="b")])
def test_anything_that_is_not_json_shaped_is_refused(value):
    with pytest.raises(OutputRedactionError) as caught:
        sanitize_data({"summary": f"password = {FAKE}", "extra": value})

    assert str(caught.value) == f"Cannot check a {type(value).__name__} for secrets."


# ---------------------------------------------------------------------------
# Secrets repeated without their context
# ---------------------------------------------------------------------------

def test_an_answer_repeating_a_request_secret_is_cleaned():
    with secret_scope() as known:
        learn_from_request(f'private const string AdminPassword = "{FAKE}";')
        out, report = sanitize_text(f"The login succeeds when the input equals {FAKE}.")

    absent(out)
    assert out == f"The login succeeds when the input equals {P1}."
    assert report.replaced == {"PASSWORD": 1}
    assert len(known) == 1


def test_an_output_teaches_the_scope_too():
    with secret_scope():
        sanitize_text(f'Retrying with password = "{FAKE}"')
        out, _ = sanitize_text(f"Connection refused for {FAKE}")

    absent(out)


def test_structured_echoes_are_cleaned_and_numbered_after_existing_placeholders():
    with secret_scope():
        for secret in (FAKE, OTHER, THIRD):
            learn_from_request(f'password = "{secret}"')

        # The model saw P1 for one of them; it echoes another without context.
        out, report = sanitize_data({"summary": f"Compares with {P1}, then falls back to {OTHER}."})

    absent(json.dumps(out))
    # Numbered after P1, with no gaps for the known secrets that did not appear.
    assert out == {"summary": f"Compares with {P1}, then falls back to {P2}."}
    assert report.replaced == {"PASSWORD": 1}


def test_echoes_are_not_recognised_outside_a_scope():
    # Limitation: with no run open, nothing is remembered between pieces.
    learn_from_request(f'password = "{FAKE}"')
    out, _ = sanitize_text(f"Connection refused for {FAKE}")

    kept(out, FAKE)


def test_short_or_plain_secrets_are_only_replaced_in_context():
    # Limitation, as in T-077: replacing "Pw-12" or "letmein" wherever it
    # appears would also blank out ordinary text.
    with secret_scope() as known:
        learn_from_request('password = "Pw-12"\nsmtp_pass = "letmein"')
        out, _ = sanitize_text("Tried Pw-12 and letmein.")

    kept(out, "Pw-12")
    kept(out, "letmein")
    assert len(known) == 0


def test_scopes_nest_and_forget_when_closed():
    with secret_scope() as outer:
        with secret_scope() as inner:
            learn_from_request(f'password = "{FAKE}"')
        assert inner is outer
        assert len(outer) == 1

    with secret_scope() as fresh:
        out, _ = sanitize_text(f"Connection refused for {FAKE}")

    assert len(fresh) == 0
    kept(out, FAKE)


def test_concurrent_runs_do_not_share_secrets():
    both_learned = threading.Barrier(2, timeout=10)
    outputs, errors = {}, []

    def run(name, mine):
        try:
            with secret_scope():
                learn_from_request(f'password = "{mine}"')
                both_learned.wait()
                outputs[name] = sanitize_text(f"Saw {FAKE} and {OTHER}.")[0]
        except Exception as exc:  # reported below, without the values
            errors.append(type(exc).__name__)

    threads = [threading.Thread(target=run, args=args) for args in (("a", FAKE), ("b", OTHER))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    absent(outputs["a"], FAKE)
    kept(outputs["a"], OTHER)
    absent(outputs["b"], OTHER)
    kept(outputs["b"], FAKE)


def test_concurrent_requests_within_one_run_share_the_scope():
    # The agents' asyncio.gather batches: one call's secret protects another's output.
    async def batch():
        learned = asyncio.Event()

        async def request():
            learn_from_request(f'password = "{FAKE}"')
            learned.set()

        async def answer():
            await learned.wait()
            return sanitize_text(f"Echo: {FAKE}")[0]

        _, out = await asyncio.gather(request(), answer())
        return out

    with secret_scope():
        out = asyncio.run(batch())

    absent(out)


def test_nothing_here_reveals_a_value():
    known = KnownSecrets()
    redactor = SecretRedactor()
    redactor.redact(f'password = "{FAKE}"')
    known.learn(redactor)

    _, report = sanitize_text(f'password = "{FAKE}"')

    for shown in (repr(known), str(report), repr(report), repr(redactor)):
        absent(shown)
    assert not hasattr(known, "__dict__")


# ---------------------------------------------------------------------------
# When text cannot be checked
# ---------------------------------------------------------------------------

@pytest.fixture
def broken_detector(monkeypatch):
    """The detector fails, quoting what it was reading, as a bug might."""
    def fail(text):
        raise RuntimeError(f"could not read {text!r}")

    monkeypatch.setattr(secret_redaction, "_find", fail)


def test_a_detector_failure_never_passes_text_on(broken_detector):
    with pytest.raises(OutputRedactionError) as caught:
        sanitize_text(f'password = "{FAKE}"')

    absent(str(caught.value))
    # Nothing chained either, so a traceback cannot show the failure's text.
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    assert safe_text(f'password = "{FAKE}"') == WITHHELD
    assert safe_text(f'password = "{FAKE}"', fallback="[progress withheld]") == "[progress withheld]"


def test_safe_text_withholds_what_is_not_text():
    assert safe_text(None) == WITHHELD
    assert safe_text(b"password") == WITHHELD


@pytest.mark.parametrize("save, first, second", [
    (save_json, {"summary": "first"}, {"summary": f'password = "{FAKE}"'}),
    (save_text, "first\n", f'password = "{FAKE}"\n'),
], ids=["json", "text"])
def test_a_failed_save_leaves_the_previous_file(tmp_path, monkeypatch, save, first, second):
    path = tmp_path / "out" / "summary.json"
    save(path, first)
    before = path.read_text(encoding="utf-8")

    monkeypatch.setattr(secret_redaction, "_find", lambda text: 1 / 0)
    with pytest.raises(OutputRedactionError) as caught:
        save(path, second)

    assert str(caught.value) == "summary.json was not saved: The text could not be checked for secrets."
    assert path.read_text(encoding="utf-8") == before
    assert sorted(p.name for p in path.parent.iterdir()) == ["summary.json"]


def test_data_that_cannot_be_checked_is_not_saved(tmp_path):
    path = tmp_path / "rules.json"

    with pytest.raises(OutputRedactionError) as caught:
        save_json(path, {"rules": {"a", "b"}})

    assert str(caught.value) == "rules.json was not saved: Cannot check a set for secrets."
    assert not path.exists()
    assert list(tmp_path.iterdir()) == []


def test_saves_write_cleaned_content_and_leave_no_temporary_file(tmp_path):
    data = validated_rule()
    report = save_json(tmp_path / "validated_rules.json", [data], ensure_ascii=False)
    text_report = save_text(tmp_path / "unit_tests.txt", f'var pwd = "{FAKE}"; // café\n')

    saved = (tmp_path / "validated_rules.json").read_text(encoding="utf-8")
    saved_text = (tmp_path / "unit_tests.txt").read_text(encoding="utf-8")
    absent(saved)
    absent(saved_text)
    assert json.loads(saved) == [sanitize_data(data)[0]]
    assert saved_text == f'var pwd = "{P1}"; // café\n'
    assert report.replaced == text_report.replaced == {"PASSWORD": 1}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["unit_tests.txt", "validated_rules.json"]


def test_a_failed_write_removes_its_temporary_file(tmp_path, monkeypatch):
    path = tmp_path / "summary.json"
    save_json(path, {"summary": "first"})

    def refuse(src, dst):
        raise PermissionError("in use")

    monkeypatch.setattr(output_redaction.os, "replace", refuse)
    with pytest.raises(PermissionError):
        save_json(path, {"summary": "second"})

    assert json.loads(path.read_text(encoding="utf-8")) == {"summary": "first"}
    assert [p.name for p in tmp_path.iterdir()] == ["summary.json"]
