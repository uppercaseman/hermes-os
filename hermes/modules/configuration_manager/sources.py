"""Loading configuration from environment variables and local config
files, both reduced to the same shape: a flat `dict[str, Any]` keyed by
dotted path (`"providers.openai.dry_run"`), so `service.py` never needs
to know which source a value came from once it's loaded.

No new dependency was added for file support: `.json` uses the
stdlib `json` module, `.toml` uses `tomllib` (stdlib since Python
3.11, read-only -- exactly what a config *reader* needs).
"""
from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path
from typing import Any

# Env vars are matched as `<PREFIX>_<SEGMENT>__<SEGMENT>__...`: a single
# underscore separates the prefix from the path, a double underscore
# separates path segments (so a segment itself may contain single
# underscores, e.g. "TOOL_MANAGER", without being split into two
# segments). Example: HERMES_PROVIDERS__OPENAI__DRY_RUN=true ->
# path "providers.openai.dry_run", value True.
_SEGMENT_SEPARATOR = "__"


def load_env_values(*, prefix: str = "HERMES") -> dict[str, Any]:
    """Scans `os.environ` for every variable matching the
    `<prefix>_<SEGMENT>__<SEGMENT>...` shape and returns them as a flat
    dotted-path dict with coerced (non-string where possible) values.
    Variables under the prefix that don't contain the segment separator
    are silently ignored -- there is no path to map them to."""
    marker = f"{prefix}_"
    result: dict[str, Any] = {}
    for raw_key, raw_value in os.environ.items():
        if not raw_key.startswith(marker):
            continue
        remainder = raw_key[len(marker):]
        if _SEGMENT_SEPARATOR not in remainder:
            continue
        path = ".".join(segment.lower() for segment in remainder.split(_SEGMENT_SEPARATOR))
        result[path] = _coerce(raw_value)
    return result


def _coerce(raw: str) -> Any:
    """Environment variables are always strings on the wire; this
    recovers the type a caller almost certainly meant, in the same
    order a human would guess it: bool keywords, then int, then float,
    then JSON (for list/dict-shaped values), falling back to the raw
    string unchanged."""
    lowered = raw.strip().lower()
    if lowered in ("true", "yes", "1", "on"):
        return True
    if lowered in ("false", "no", "0", "off"):
        return False
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    if raw.strip().startswith(("{", "[")):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
    return raw


def load_file_values(path: str | Path) -> dict[str, Any]:
    """Loads a `.json` or `.toml` config file and flattens it into a
    dotted-path dict. A missing file is treated as "no file-sourced
    config" (an empty dict), not an error -- a config file is always
    optional, since env vars and defaults can fully cover a deployment
    on their own."""
    file_path = Path(path)
    if not file_path.exists():
        return {}
    if file_path.suffix == ".json":
        raw = json.loads(file_path.read_text())
    elif file_path.suffix == ".toml":
        with file_path.open("rb") as handle:
            raw = tomllib.load(handle)
    else:
        raise ValueError(f"unsupported config file extension {file_path.suffix!r} (expected .json or .toml)")
    return flatten(raw)


def flatten(nested: dict[str, Any], *, prefix: str = "") -> dict[str, Any]:
    """`{"providers": {"openai": {"dry_run": True}}}` ->
    `{"providers.openai.dry_run": True}`. Non-dict values (including
    lists) are kept as-is, not recursed into further."""
    result: dict[str, Any] = {}
    for key, value in nested.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            result.update(flatten(value, prefix=path))
        else:
            result[path] = value
    return result
