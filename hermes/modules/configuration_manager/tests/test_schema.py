import pytest
from pydantic import BaseModel

from hermes.modules.configuration_manager.errors import ConfigValidationError, UnknownNamespaceError
from hermes.modules.configuration_manager.interface import build_configuration_manager


class _ToolManagerConfig(BaseModel):
    default_timeout_seconds: float = 30.0
    max_retries: int = 3


class _StrictProviderConfig(BaseModel):
    dry_run: bool
    api_key_env_var: str


def test_register_schema_with_defaults_makes_a_validated_config_available():
    config = build_configuration_manager()

    config.register_schema(
        "tool_manager", _ToolManagerConfig, defaults={"default_timeout_seconds": 45.0, "max_retries": 5}
    )

    module_config = config.get_module_config("tool_manager")
    assert isinstance(module_config, _ToolManagerConfig)
    assert module_config.default_timeout_seconds == 45.0
    assert module_config.max_retries == 5


def test_get_module_config_raises_for_an_unregistered_namespace():
    config = build_configuration_manager()

    with pytest.raises(UnknownNamespaceError):
        config.get_module_config("never_registered")


def test_register_schema_raises_immediately_when_defaults_do_not_satisfy_it():
    config = build_configuration_manager()

    with pytest.raises(ConfigValidationError) as exc_info:
        config.register_schema("providers.openai", _StrictProviderConfig, defaults={"dry_run": True})
        # api_key_env_var is required and was never supplied -- must fail fast.

    assert exc_info.value.namespace == "providers.openai"


def test_get_module_config_reflects_overrides_on_top_of_defaults():
    config = build_configuration_manager()
    config.register_schema("tool_manager", _ToolManagerConfig, defaults={})

    import asyncio

    asyncio.run(config.set_override("tool_manager.max_retries", 9))

    assert config.get_module_config("tool_manager").max_retries == 9


def test_defaults_are_namespaced_and_do_not_leak_across_namespaces():
    config = build_configuration_manager()
    config.register_schema("tool_manager", _ToolManagerConfig, defaults={"max_retries": 7})

    assert config.get("tool_manager.max_retries") == 7
    assert config.get("providers.openai.max_retries") is None
