import pytest
from pydantic import ValidationError

from hermes.modules.capability_registry.models import (
    CapabilityProviderRegistration,
    CapabilitySelection,
    ProviderHealth,
)


def test_registration_defaults_to_low_priority_and_zero_cost():
    registration = CapabilityProviderRegistration(capability="reasoning", tool_name="openai")

    assert registration.priority == 100
    assert registration.cost_per_call == 0.0
    assert registration.declared_latency_ms == 0.0


def test_registration_rejects_negative_priority():
    with pytest.raises(ValidationError):
        CapabilityProviderRegistration(capability="reasoning", tool_name="openai", priority=-1)


def test_provider_health_defaults_to_unknown():
    health = ProviderHealth(tool_name="openai")

    assert health.state == "unknown"
    assert health.observed_latency_ms is None
    assert health.sample_count == 0


def test_capability_selection_defaults_to_empty_chain():
    selection = CapabilitySelection(capability="reasoning", selected=None)

    assert selection.chain == []
    assert selection.overridden is False
