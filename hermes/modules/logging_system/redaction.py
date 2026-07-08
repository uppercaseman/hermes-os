"""Redaction hooks for secrets/API keys.

Applied to EVERY captured event's payload before it's stored. This
exists because it's a real risk, not a theoretical one: a genuine
project-scoped OpenAI API key was found sitting in a development
environment's own `OPENAI_API_KEY` env var during an earlier task in
this codebase's history. Logging System must never persist a real
secret, even if some future adapter's error message happened to embed
one in a plain string rather than a cleanly-named field.
"""
from __future__ import annotations

import re
from typing import Any, Callable

REDACTED = "***REDACTED***"

# Name-based: low false-positive rate, catches the overwhelming majority
# of real cases (api_key, apiKey, secret, token, password, credential,
# authorization -- case-insensitive substring match).
_SENSITIVE_KEY_PATTERN = re.compile(r"(key|secret|token|password|credential|authorization)", re.IGNORECASE)

# Value-based: a best-effort supplementary net for common API key
# prefixes (OpenAI's sk-/pk-, generic rk- style tokens) appearing in a
# string that wasn't caught by the key check -- e.g. embedded in a
# free-text error message. Not exhaustive; the name-based check above is
# the reliable mechanism.
_SENSITIVE_VALUE_PATTERN = re.compile(r"^(sk|pk|rk)-[A-Za-z0-9_-]{10,}$")

RedactionHook = Callable[[dict[str, Any]], dict[str, Any]]


def default_redactor(payload: dict[str, Any]) -> dict[str, Any]:
    """Recursively redacts dict values whose key looks sensitive, plus
    any bare string value matching a common API-key prefix pattern."""
    return _redact_value(payload)


def _redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (REDACTED if _SENSITIVE_KEY_PATTERN.search(key) else _redact_value(val))
            for key, val in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, str) and _SENSITIVE_VALUE_PATTERN.match(value):
        return REDACTED
    return value
