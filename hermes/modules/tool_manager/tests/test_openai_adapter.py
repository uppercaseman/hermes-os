"""Tests for the OpenAI safe-mode skeleton adapter.

None of these tests make a live API call. Every "with a key" case uses
an obviously-fake placeholder string (never a real credential), and the
one code path that would eventually make a real call (`dry_run=False`
with a key present) is asserted to raise `NotImplementedError` --
proving no network request is attempted, not just that we didn't choose
to run one.
"""
import pytest

from hermes.modules.capability_registry.interface import build_capability_registry
from hermes.modules.tool_manager.adapters.openai_adapter import (
    OPENAI_API_KEY_ENV_VAR,
    OpenAIAdapter,
    OpenAIAuthenticationError,
    register_with_capability_registry,
)
from hermes.modules.tool_manager.models import ToolInvocationRequest

FAKE_KEY = "sk-test-obviously-fake-not-a-real-key"


def test_dry_run_is_the_default():
    adapter = OpenAIAdapter(name="openai")

    assert adapter.dry_run is True


def test_default_env_var_name():
    assert OPENAI_API_KEY_ENV_VAR == "OPENAI_API_KEY"


async def test_dry_run_invoke_completes_without_any_api_key_present(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    adapter = OpenAIAdapter(name="openai")  # dry_run=True by default
    request = ToolInvocationRequest(tool_name="openai", operation="chat", parameters={"prompt": "hello"})

    result = await adapter.invoke(request)

    assert result.status == "completed"
    assert result.output["dry_run"] is True
    assert result.output["echo_parameters"] == {"prompt": "hello"}


async def test_dry_run_authenticate_succeeds_without_any_api_key_present(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    adapter = OpenAIAdapter(name="openai")

    await adapter.authenticate()  # must not raise


async def test_non_dry_run_authenticate_raises_without_an_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    adapter = OpenAIAdapter(name="openai", dry_run=False)

    with pytest.raises(OpenAIAuthenticationError):
        await adapter.authenticate()


async def test_non_dry_run_authenticate_succeeds_with_a_key_present(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    adapter = OpenAIAdapter(name="openai", dry_run=False)

    await adapter.authenticate()  # must not raise -- and makes no network call either


async def test_non_dry_run_invoke_never_makes_a_live_call_without_a_transport(monkeypatch):
    """The critical safety assertion: even with dry_run explicitly
    disabled AND a key present, invoke() cannot make a network call
    unless a transport is configured. The adapter raises a clear error
    instead -- which proves no HTTP request could be constructed or
    sent, not just that we chose not to run one."""
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    adapter = OpenAIAdapter(name="openai", dry_run=False)  # no transport
    request = ToolInvocationRequest(tool_name="openai", operation="chat", parameters={})

    with pytest.raises(RuntimeError, match="no transport configured"):
        await adapter.invoke(request)


async def test_non_dry_run_invoke_raises_authentication_error_before_any_other_error(monkeypatch):
    """Auth is checked first -- a missing key fails with a clear,
    specific error rather than the generic "no transport" one."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    adapter = OpenAIAdapter(name="openai", dry_run=False)
    request = ToolInvocationRequest(tool_name="openai", operation="chat", parameters={})

    with pytest.raises(OpenAIAuthenticationError):
        await adapter.invoke(request)


def test_custom_api_key_env_var_name(monkeypatch):
    monkeypatch.setenv("MY_CUSTOM_OPENAI_KEY", FAKE_KEY)
    adapter = OpenAIAdapter(name="openai", api_key_env_var="MY_CUSTOM_OPENAI_KEY")

    assert adapter._load_api_key() == FAKE_KEY


def test_api_key_is_never_a_constructor_argument():
    """There is no way to pass a literal key into this class -- it can
    only ever come from the environment. This test exists so a future
    edit that adds an `api_key=` parameter gets caught."""
    import inspect

    signature = inspect.signature(OpenAIAdapter.__init__)
    assert "api_key" not in signature.parameters


async def test_register_with_capability_registry_registers_both_capabilities():
    registry = build_capability_registry()

    register_with_capability_registry(registry, tool_name="openai")

    reasoning_selection = await registry.select("reasoning")
    code_gen_selection = await registry.select("code_generation")
    assert reasoning_selection.selected == "openai"
    assert code_gen_selection.selected == "openai"
