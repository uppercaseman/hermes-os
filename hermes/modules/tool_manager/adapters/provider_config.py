"""Pydantic configuration schemas for every supported provider.

These schemas are registered with `ConfigurationManager` under the
conventional `providers.<name>` namespace. They define the shape of the
configuration a given adapter reads when constructed via the
`from_configuration_manager(...)` alternative constructor.

**Capabilities mapping** (declared once, reused across everything):

| Provider                | reasoning | planning | code_generation | vision | memory | retrieval | communication | image | video | voice | desktop | browser |
|-------------------------|:---------:|:--------:|:---------------:|:------:|:------:|:---------:|:-------------:|:-----:|:-----:|:-----:|:-------:|:-------:|
| openai                  |     X     |    X     |        X        |   X    |   -    |     -     |       -       |   X   |   X   |   X   |    -    |    -    |
| anthropic               |     X     |    X     |        X        |   X    |   -    |     -     |       -       |   -   |   -   |   -   |    -    |    -    |
| minimax                 |     X     |    X     |        X        |   X    |   -    |     -     |       -       |   -   |   -   |   -   |    -    |    -    |
| gemini                  |     X     |    X     |        X        |   X    |   -    |     -     |       -       |   X   |   X   |   -   |    -    |    -    |
| ollama (local)          |     X     |    X     |        X        |   X    |   -    |     -     |       -       |   -   |   -   |   -   |    -    |    -    |
| mcp (server-defined)    |     *     |    *     |        *        |   *    |   *    |     *     |       *       |   *   |   *   |   *   |    *    |    *    |

`*` for MCP means "whatever capabilities the connected server declares,"
discovered at first contact. Capability Registry stays provider-agnostic;
the canonical capability names are frozen.

All schemas share the same default posture:

- `dry_run=True` is **unconditional** unless explicitly overridden.
- API keys are *references* (env var names), never values.
- Timeout and retry defaults are conservative; override with care.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ProviderConfigBase(BaseModel):
    """Common fields every provider schema declares. Concrete schemas
    subclass this and add provider-specific knobs (e.g. `base_url`)."""

    model_config = ConfigDict(extra="forbid")

    dry_run: bool = Field(
        default=True,
        description="Safe-by-default. False = make real network calls.",
    )
    model_name: str = Field(
        default="default",
        description="Provider-specific model identifier. Defaults are conservative.",
    )
    api_key_env_var: str = Field(
        default="",
        description="Environment variable holding the API key. NEVER the key itself.",
    )
    invocation_timeout_seconds: float = Field(default=30.0, gt=0, description="Per-call HTTP timeout.")
    max_retries: int = Field(default=2, ge=0, le=10, description="Retries permitted on transient failures.")
    cost_per_call: float = Field(
        default=0.0, ge=0.0, description="Provider-declared unit cost, used by Capability Registry ranking."
    )
    priority: int = Field(
        default=100, ge=0, description="Lower = preferred in selection. Pass to Capability Registry."
    )
    enabled: bool = Field(default=True, description="Kill switch independent of overall health.")


class OpenAIProviderConfig(ProviderConfigBase):
    """OpenAI (Chat Completions API)."""

    api_key_env_var: str = Field(default="OPENAI_API_KEY")
    base_url: str = Field(
        default="https://api.openai.com/v1",
        description="Override for the OpenAI-compatible endpoint (Azure, proxies).",
    )
    organization: str | None = Field(default=None, description="Optional `OpenAI-Organization` header value.")

    # Capability declarations drive the registration helper in adapter
    # files; the constants come from `capability_registry.capabilities`.
    capabilities: tuple[str, ...] = Field(
        default=("reasoning", "planning", "code_generation", "vision", "image_generation", "video_generation", "voice_generation"),
    )


class MiniMaxProviderConfig(ProviderConfigBase):
    """MiniMax chat completions (OpenAI-compatible)."""

    api_key_env_var: str = Field(default="MINIMAX_API_KEY")
    base_url: str = Field(default="https://api.minimax.chat/v1")
    group_id: str | None = Field(default=None, description="X-Global-GroupId header value (if applicable).")
    capabilities: tuple[str, ...] = Field(
        default=("reasoning", "planning", "code_generation", "vision"),
    )


class AnthropicProviderConfig(ProviderConfigBase):
    """Anthropic Messages API."""

    api_key_env_var: str = Field(default="ANTHROPIC_API_KEY")
    base_url: str = Field(default="https://api.anthropic.com", description="Override for proxies.")
    anthropic_version: str = Field(default="2023-06-01")
    max_tokens: int = Field(default=4096, ge=1, le=200_000)
    capabilities: tuple[str, ...] = Field(
        default=("reasoning", "planning", "code_generation", "vision"),
    )


class GeminiProviderConfig(ProviderConfigBase):
    """Google Generative Language (Gemini) API."""

    api_key_env_var: str = Field(default="GEMINI_API_KEY")
    base_url: str = Field(default="https://generativelanguage.googleapis.com")
    api_version: str = Field(default="v1beta")
    capabilities: tuple[str, ...] = Field(
        default=("reasoning", "planning", "code_generation", "vision", "image_generation", "video_generation"),
    )


class OllamaProviderConfig(ProviderConfigBase):
    """Local Ollama-compatible server (also covers LM Studio, vLLM, llama.cpp with the same HTTP API)."""

    model_name: str = Field(default="llama3.1")
    api_key_env_var: str = Field(default="")
    base_url: str = Field(
        default="http://localhost:11434",
        description="Ollama root endpoint; `http://localhost:1234` for LM Studio, etc.",
    )
    keep_alive_minutes: int = Field(default=5, ge=0)
    capabilities: tuple[str, ...] = Field(
        default=("reasoning", "planning", "code_generation", "vision"),
    )


class MCPProviderConfig(ProviderConfigBase):
    """MCP server connection. The server declares its own capabilities;
    we declare a default capability set here that the registry can use
    until first contact confirms/refines it."""

    api_key_env_var: str = Field(default="")
    server_command: str = Field(
        default="",
        description="Shell command to spawn the MCP server, e.g. `npx @modelcontextprotocol/server-filesystem`.",
    )
    server_args: tuple[str, ...] = Field(default=())
    server_env: dict[str, str] = Field(default_factory=dict)
    capabilities: tuple[str, ...] = Field(
        default=(
            "reasoning",
            "planning",
            "code_generation",
            "memory",
            "retrieval",
            "communication",
            "desktop_automation",
            "browser_automation",
            "vision",
        ),
        description="Default capability set; refined by `discover_capabilities()` once connected.",
    )


def provider_config_for(name: str) -> type[ProviderConfigBase]:
    """Returns the Pydantic schema class for a named provider. Used by
    default `Adapter.from_configuration_manager` constructors and by
    tests; the configuration registration itself can pass any
    `BaseModel` subclass."""
    table: dict[str, type[ProviderConfigBase]] = {
        "openai": OpenAIProviderConfig,
        "anthropic": AnthropicProviderConfig,
        "minimax": MiniMaxProviderConfig,
        "gemini": GeminiProviderConfig,
        "ollama": OllamaProviderConfig,
        "mcp": MCPProviderConfig,
    }
    return table[name]


# Cost estimation primitives ----------------------------------------------------


class TokenUsage(BaseModel):
    """Carrier returned in `output["usage"]` for any model provider,
    used by Capability Registry for cost tracking."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


def estimate_cost_usd(
    *,
    cost_per_call: float,
    input_tokens: int,
    output_tokens: int,
) -> float:
    """Estimate USD cost for a model invocation. The model registry /
    configuration provides `cost_per_call` as a flat per-call rate; this
    helper combines it with observed token counts when available so a
    future dashboard can show per-call cost vs per-token cost. Today
    it's still proportional to `cost_per_call` per invocation — the
    granular per-token arithmetic is left to the future price-update
    pipeline."""
    return max(0.0, float(cost_per_call))


# Capability helpers -------------------------------------------------------------


def capability_labels(constants_module: Any) -> tuple[str, ...]:
    """Build a tuple of capability constant strings for a provider config
    from a constants module that exposes them as snake_case attributes.
    Kept loose (not strict-typed) because the source of truth lives in
    `capability_registry.capabilities` and evolves with the canonical
    taxonomy."""
    return tuple(
        v
        for k, v in vars(constants_module).items()
        if not k.startswith("_") and isinstance(v, str) and v.islower()
    )


def supported_capabilities(provider: str) -> tuple[str, ...]:
    """Returns the frozen canonical capability tuple supported by a
    given provider. Used by registration helpers across every adapter
    so the capability matrix in the engineering report and READMEs is
    always derived from one place, not duplicated per-file."""
    from hermes.modules.tool_manager.adapters.provider_config import (  # lazy import avoids cycles
        AnthropicProviderConfig,
        GeminiProviderConfig,
        MCPProviderConfig,
        OllamaProviderConfig,
        OpenAIProviderConfig,
    )

    table: dict[str, type[ProviderConfigBase]] = {
        "openai": OpenAIProviderConfig,
        "anthropic": AnthropicProviderConfig,
        "minimax": MiniMaxProviderConfig,
        "gemini": GeminiProviderConfig,
        "ollama": OllamaProviderConfig,
        "mcp": MCPProviderConfig,
    }
    cfg = table[provider]
    return tuple(cfg.model_fields["capabilities"].default)


def provider_names() -> tuple[str, ...]:
    """Returns the canonical provider-name tuple used everywhere in the
    registry/registration helpers."""
    return ("openai", "anthropic", "minimax", "gemini", "ollama", "mcp")


LiteralProvider = Literal["openai", "anthropic", "minimax", "gemini", "ollama", "mcp"]
