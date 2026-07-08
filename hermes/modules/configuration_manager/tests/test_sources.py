import json
import os
import tempfile
from pathlib import Path

from hermes.modules.configuration_manager.sources import flatten, load_env_values, load_file_values


def _with_env(values: dict[str, str]):
    """Sets `values` in the real environment for the duration of the
    `with` block, restoring whatever was there before (or absence)
    afterward. A tiny stand-in for pytest's `monkeypatch.setenv`, since
    this environment never has pytest installed."""

    class _Ctx:
        def __enter__(self):
            self._previous = {k: os.environ.get(k) for k in values}
            os.environ.update(values)
            return self

        def __exit__(self, *exc):
            for k, old in self._previous.items():
                if old is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = old

    return _Ctx()


def test_env_var_maps_double_underscore_segments_to_a_dotted_path():
    with _with_env({"HERMES_TOOL_MANAGER__DEFAULT_TIMEOUT_SECONDS": "30"}):
        values = load_env_values(prefix="HERMES")

    assert values["tool_manager.default_timeout_seconds"] == 30


def test_env_var_without_the_segment_separator_is_ignored():
    with _with_env({"HERMES_JUSTONESEGMENT": "x"}):
        values = load_env_values(prefix="HERMES")

    assert "justonesegment" not in values
    assert not any(k.startswith("justonesegment") for k in values)


def test_env_var_outside_the_prefix_is_ignored():
    with _with_env({"NOT_HERMES__TOOL_MANAGER__X": "1"}):
        values = load_env_values(prefix="HERMES")

    assert not any("tool_manager" in k for k in values)


def test_env_coercion_recognises_bools_ints_floats_and_json():
    with _with_env(
        {
            "HERMES_X__A": "true",
            "HERMES_X__B": "false",
            "HERMES_X__C": "42",
            "HERMES_X__D": "3.14",
            "HERMES_X__E": "plain string",
            "HERMES_X__F": '["a", "b"]',
        }
    ):
        values = load_env_values(prefix="HERMES")

    assert values["x.a"] is True
    assert values["x.b"] is False
    assert values["x.c"] == 42
    assert values["x.d"] == 3.14
    assert values["x.e"] == "plain string"
    assert values["x.f"] == ["a", "b"]


def test_flatten_nests_dicts_into_dotted_paths():
    nested = {"providers": {"openai": {"dry_run": True, "api_key_env_var": "OPENAI_API_KEY"}}}

    assert flatten(nested) == {
        "providers.openai.dry_run": True,
        "providers.openai.api_key_env_var": "OPENAI_API_KEY",
    }


def test_flatten_keeps_lists_as_leaf_values():
    assert flatten({"a": {"b": [1, 2, 3]}}) == {"a.b": [1, 2, 3]}


def test_load_file_values_missing_file_returns_empty_dict():
    assert load_file_values("/nonexistent/path/should/not/exist.json") == {}


def test_load_file_values_reads_json():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "config.json"
        path.write_text(json.dumps({"feature_flags": {"new_ui": True}}))

        assert load_file_values(path) == {"feature_flags.new_ui": True}


def test_load_file_values_reads_toml():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "config.toml"
        path.write_text('[providers.openai]\ndry_run = true\n')

        assert load_file_values(path) == {"providers.openai.dry_run": True}


def test_load_file_values_rejects_unsupported_extension():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "config.yaml"
        path.write_text("a: 1")

        try:
            load_file_values(path)
        except ValueError as exc:
            assert "yaml" in str(exc)
        else:
            raise AssertionError("expected ValueError for an unsupported extension")
