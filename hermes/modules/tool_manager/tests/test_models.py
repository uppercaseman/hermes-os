import pytest
from pydantic import ValidationError

from hermes.modules.tool_manager.models import (
    RateLimitPolicy,
    ToolAdapterConfig,
    ToolCapabilities,
)


def test_tool_capabilities_defaults_to_sync_only_and_requires_auth():
    capabilities = ToolCapabilities()

    assert capabilities.supports_sync is True
    assert capabilities.supports_streaming is False
    assert capabilities.requires_auth is True


def test_rate_limit_policy_rejects_non_positive_values():
    with pytest.raises(ValidationError):
        RateLimitPolicy(max_calls=0, per_seconds=1.0)
    with pytest.raises(ValidationError):
        RateLimitPolicy(max_calls=10, per_seconds=0)


def test_tool_adapter_config_has_sensible_defaults():
    config = ToolAdapterConfig(name="my-tool")

    assert config.name == "my-tool"
    assert config.retry_policy.max_attempts == 3
    assert config.invocation_timeout_seconds == 30.0
    assert config.auth.auth_type == "api_key"
