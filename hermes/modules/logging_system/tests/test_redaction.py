from hermes.modules.logging_system.redaction import REDACTED, default_redactor


def test_redacts_keys_matching_sensitive_names():
    payload = {"api_key": "sk-realvalue", "password": "hunter2", "token": "abc", "normal": "keep me"}

    redacted = default_redactor(payload)

    assert redacted["api_key"] == REDACTED
    assert redacted["password"] == REDACTED
    assert redacted["token"] == REDACTED
    assert redacted["normal"] == "keep me"


def test_key_matching_is_case_insensitive_and_substring():
    payload = {"OPENAI_API_KEY": "x", "UserSecretValue": "y", "credentialsBlob": "z"}

    redacted = default_redactor(payload)

    assert redacted["OPENAI_API_KEY"] == REDACTED
    assert redacted["UserSecretValue"] == REDACTED
    assert redacted["credentialsBlob"] == REDACTED


def test_redacts_nested_dicts_and_lists():
    payload = {"outer": {"inner_secret": "x", "fine": "y"}, "items": [{"token": "z"}, {"ok": "w"}]}

    redacted = default_redactor(payload)

    assert redacted["outer"]["inner_secret"] == REDACTED
    assert redacted["outer"]["fine"] == "y"
    assert redacted["items"][0]["token"] == REDACTED
    assert redacted["items"][1]["ok"] == "w"


def test_redacts_bare_string_values_matching_api_key_prefixes():
    payload = {"message": "sk-abcdefghijklmnopqrst", "other": "just a normal sentence"}

    redacted = default_redactor(payload)

    assert redacted["message"] == REDACTED
    assert redacted["other"] == "just a normal sentence"


def test_does_not_redact_short_or_unrelated_strings():
    payload = {"note": "sk-short", "id": "not-a-secret-just-an-id"}

    redacted = default_redactor(payload)

    assert redacted["note"] == "sk-short"  # too short to match the pattern
    assert redacted["id"] == "not-a-secret-just-an-id"


def test_original_payload_is_not_mutated():
    payload = {"api_key": "secret"}

    default_redactor(payload)

    assert payload["api_key"] == "secret"
