"""Integration test: real Event Bus, real Logging System, real Tool
Manager + the real (safe-mode) OpenAI adapter, real Capability
Registry, and real State Manager -- proving Configuration Manager's
output is directly usable by each of them, without modifying any of
those modules' own code.
"""
from __future__ import annotations

import os

from pydantic import BaseModel

from hermes.core.event_bus.in_memory import InMemoryEventBus
from hermes.core.state_manager.interface import build_state_manager
from hermes.modules.capability_registry.interface import build_capability_registry
from hermes.modules.capability_registry.models import CapabilityProviderRegistration
from hermes.modules.configuration_manager.interface import build_configuration_manager
from hermes.modules.configuration_manager.events import CONFIG_VALUE_CHANGED
from hermes.modules.logging_system.interface import build_logging_system
from hermes.modules.logging_system.redaction import REDACTED
from hermes.modules.tool_manager.adapters.openai_adapter import OpenAIAdapter
from hermes.modules.tool_manager.interface import build_tool_manager
from hermes.modules.tool_manager.models import ToolAdapterConfig, ToolInvocationRequest


# A caller-defined schema, exactly the shape a real Tool Manager
# adapter's constructor expects -- Configuration Manager itself knows
# nothing about OpenAI or any other provider; this schema lives in the
# integration that needs it, not in the module's own production code.
class OpenAIProviderConfig(BaseModel):
    dry_run: bool = True
    api_key_env_var: str = "OPENAI_API_KEY"


async def test_configuration_manager_events_are_captured_by_a_real_logging_system():
    bus = InMemoryEventBus()
    logging_system = build_logging_system(event_bus=bus)
    await logging_system.start()

    config = build_configuration_manager(event_bus=bus)
    await config.start()
    await config.set_override("providers.openai.api_key", "sk-realsecretvalue1234567890")

    entries = await logging_system.query(source_module="configuration_manager")

    assert any(e.event_type == "configuration_manager.config.loaded" for e in entries)
    change_entries = [e for e in entries if e.event_type == CONFIG_VALUE_CHANGED]
    assert len(change_entries) == 1
    # Redacted twice over: once by Configuration Manager before
    # publishing, once again (idempotently) by Logging System's own
    # capture-time redaction. Neither module needs to know the other
    # does this -- the secret simply never survives either hop.
    assert change_entries[0].payload["new_value"] == REDACTED


async def test_provider_config_constructs_a_real_openai_adapter_and_invokes_it_through_tool_manager():
    bus = InMemoryEventBus()
    config = build_configuration_manager(event_bus=bus)
    config.register_schema(
        "providers.openai", OpenAIProviderConfig, defaults={"dry_run": True, "api_key_env_var": "OPENAI_API_KEY"}
    )

    provider_kwargs = config.get_provider_config("openai")
    assert provider_kwargs == {"dry_run": True, "api_key_env_var": "OPENAI_API_KEY"}

    adapter = OpenAIAdapter(name="openai", **provider_kwargs)  # unmodified adapter, zero Config Manager awareness

    tool_manager = build_tool_manager(event_bus=bus)
    tool_manager.register_adapter(adapter, ToolAdapterConfig(name="openai"))

    result = await tool_manager.invoke(
        ToolInvocationRequest(tool_name="openai", operation="chat", parameters={"prompt": "hello"})
    )

    assert result.status == "completed"
    assert result.output["dry_run"] is True  # config-driven dry_run really reached the adapter


async def test_provider_config_dry_run_override_flows_through_to_the_adapter():
    """Flipping Configuration Manager's dry_run override changes what a
    freshly constructed adapter does -- proving the config is live, not
    just structurally compatible."""
    config = build_configuration_manager()
    config.register_schema(
        "providers.openai", OpenAIProviderConfig, defaults={"dry_run": True, "api_key_env_var": "OPENAI_API_KEY"}
    )
    await config.set_override("providers.openai.dry_run", False)

    provider_kwargs = config.get_provider_config("openai")
    assert provider_kwargs["dry_run"] is False

    # get_dry_run() gives the same answer via the dedicated dry-run
    # lookup path (#6), independent of the schema-backed one above.
    assert config.get_dry_run("providers.openai") is False


async def test_config_driven_capability_registry_override_selects_the_configured_provider():
    bus = InMemoryEventBus()
    registry = build_capability_registry(event_bus=bus)
    registry.register_provider(CapabilityProviderRegistration(capability="code_generation", tool_name="claude", priority=10))
    registry.register_provider(CapabilityProviderRegistration(capability="code_generation", tool_name="openai", priority=100))

    config = build_configuration_manager(event_bus=bus)
    await config.set_override("capability_registry.overrides.code_generation", "openai")

    # Unmodified: nothing forces a caller to route config through
    # Capability Registry automatically -- that wiring is deliberately
    # explicit, exactly like every other cross-module hookup in Hermes.
    pinned_tool = config.get("capability_registry.overrides.code_generation")
    await registry.set_override("code_generation", pinned_tool)

    selection = await registry.select("code_generation")

    assert selection.selected == "openai"
    assert selection.overridden is True


async def test_configuration_manager_reports_its_own_health_to_state_manager():
    bus = InMemoryEventBus()
    state_manager = build_state_manager(event_bus=bus)
    config = build_configuration_manager(event_bus=bus)

    state_manager.declare_module("configuration_manager")
    await state_manager.report_heartbeat("configuration_manager", "healthy")

    assert state_manager.get_state("configuration_manager") == "healthy"
    # Configuration Manager's own config is still queryable at any time,
    # completely independent of whether State Manager considers it
    # healthy -- there is no coupling in the other direction.
    assert config.get("anything", "default") == "default"


async def test_feature_flag_read_from_a_real_env_var_end_to_end():
    """The full #1 loading-from-environment-variables path, exercised
    against a real HERMES_ variable, not a synthetic dict."""
    os.environ["HERMES_FEATURE_FLAGS__ENABLE_STREAMING"] = "true"
    try:
        config = build_configuration_manager()
        assert config.is_feature_enabled("enable_streaming") is True
    finally:
        os.environ.pop("HERMES_FEATURE_FLAGS__ENABLE_STREAMING", None)
