# Hermes Provider Ecosystem

> Sprint-4 fills in the **Provider Ecosystem** layer of Hermes: a
> production-ready adapter for every configured provider, a shared
> canonical capability matrix, a unified HTTP transport, an
> end-to-end observability surface, and a **Provider Router** that
> turns a `capability` request into a structured
> `ProviderInvocationOutcome` with full fail-over semantics.

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                       Commander (hermes/modules/commander/)        │
│             speaks capability strings; never names a provider.    │
└─────────────────────────────────┬────────────────────────────────┘
                                  │  RoutingRequest(capability=...)
                                  ▼
┌──────────────────────────────────────────────────────────────────┐
│              ProviderRouter (hermes/modules/provider_router/)     │
│  Resolves capability -> ranked candidate chain via Registry,      │
│  walks the chain on transient failure, records the full trail.    │
└─────────────────────────────────┬────────────────────────────────┘
                                  │  ToolInvocationRequest
                                  ▼
┌──────────────────────────────────────────────────────────────────┐
│       ToolManager (hermes/modules/tool_manager/service.py)        │
│   retry, rate limit, timeout, invoke/stream, async handle.         │
└─────────────────────────────────┬────────────────────────────────┘
                                  │  delegates to adapter
                                  ▼
┌──────────────────────────────────────────────────────────────────┐
│  Adapters (hermes/modules/tool_manager/adapters/)                 │
│  OpenAI / Anthropic / MiniMax / Gemini / Ollama / MCP / Obsidian  │
│   Each: dry_run=true default, env-var-only auth, observability.    │
└──────────────────────────────────────────────────────────────────┘
```

## Modules in this slice

| Module | Owns | Talks to |
| --- | --- | --- |
| `tool_manager/adapters/` | 8 production-ready ToolAdapter implementations + HTTP base + capability matrix + observability surface | Tool Manager, Configuration Manager, Event Bus |
| `tool_manager/adapters/provider_config.py` | Pydantic schemas + canonical capability matrix | Tool Manager (registrations) |
| `provider_router/` | Capability → ranked-chain resolution; fail-over; routing events | Tool Manager, Capability Registry, Event Bus |

## Canonical capability matrix

Single source of truth lives in
`hermes/modules/tool_manager/adapters/provider_config.py::SUPPORTED_CAPABILITIES_TABLE`.
Every adapter's `register_provider_capabilities()` helper reads from
this table.

| Provider | Capabilities |
| --- | --- |
| `openai` | reasoning, planning, code_generation, vision, image_generation, video_generation, voice_generation |
| `anthropic` | reasoning, planning, code_generation, vision |
| `minimax` | reasoning, planning, code_generation, vision |
| `gemini` | reasoning, planning, code_generation, vision, image_generation, video_generation |
| `ollama` | reasoning, planning, code_generation, vision |
| `mcp` | reasoning, planning, code_generation, memory, retrieval, communication, desktop_automation, browser_automation, vision |

## Configuration

Every provider is configured exclusively via `ConfigurationManager`,
under the `providers.<name>` namespace. Example:

```python
config.register_schema("providers.openai", OpenAIProviderConfig)
await config.set_override("providers.openai.api_key_env_var", "OPENAI_API_KEY")
await config.set_override("providers.openai.model_name", "gpt-4o")
await config.set_override("providers.openai.invocation_timeout_seconds", 45.0)
await config.set_override("providers.openai.dry_run", False)  # opt-in; default True

adapter = OpenAIAdapter.from_configuration_manager(name="openai", configuration_manager=config)
```

API keys are **never** constructor arguments, config-file values, or
hardcoded strings. Each adapter takes the *name* of an environment
variable; the actual value is read at `authenticate()`/`invoke()` time,
only when `dry_run=False` is explicitly requested.

## Observability

Two layers of events, both routed through the existing Event Bus:

| Layer | Vocabulary | Source |
| --- | --- | --- |
| Provider lifecycle | `tool_manager.provider.{selected, succeeded, failed, timeout, retry, token_usage, latency, estimated_cost, cancelled, health_changed}` | Each adapter via `ProviderRecorder` |
| Routing decisions | `provider_router.routing.{started, succeeded, failed, failover}` and `provider_router.provider_attempt.{started, succeeded, failed}` | Provider Router |

A replay tool reading only the event log can reconstruct both the
provider's per-invocation observability trail **and** the router's
chain-walk decisions.

## Safety posture

- `dry_run=True` is the **default** for every adapter.
- A `dry_run=False` adapter that lacks a transport raises
  `RuntimeError("no transport configured")` immediately, never silently
  falling back to a live call.
- A `dry_run=False` adapter with no API key in the configured env var
  raises the adapter's typed `AuthenticationError` from both
  `authenticate()` and `invoke()`.
- Tool Manager's own `RetryPolicy` + `RateLimitPolicy` apply on top of
  every adapter call -- retries are generic, not provider-specific.
- Cancellation: every HTTP request carries a `CancellationToken`; the
  transport checks it between read cycles.

## Module-by-module guides

- `hermes/modules/provider_router/README.md` -- routing API,
  semantics, event sequence, future considerations.
- `hermes/modules/tool_manager/README.md` -- adapter lifecycle, mode
  matrix, Configuration Manager wiring.
- `hermes/modules/capability_registry/README.md` -- how the chain is
  ranked, canonical capability constants.

## Test totals (Sprint-4)

| File | Tests | Focus |
| --- | --- | --- |
| `provider_router/tests/test_service.py` | 18 | Routing logic (no collaborators) |
| `tool_manager/tests/test_provider_ecosystem.py` | 24 | Mocked-network adapter tests + Router integration |
| `tool_manager/tests/test_adapters.py` | 51 | Parametrized smoke tests over all 8 adapters |
| `tool_manager/tests/test_openai_adapter.py` | 6 | OpenAI-specific safety + config |
| `tool_manager/tests/test_configuration_manager_wiring.py` | 11 | Configuration Manager wiring |

## Architectural decisions

Sprint-4 made these binding decisions:

1. **No second provider abstraction.** Every adapter implements the
   existing `ToolAdapter` Protocol. Provider-specific code lives only
   inside the adapter's file.
2. **Commander is provider-agnostic.** The only thing Commander sees is
   `RoutingRequest(capability=...)` and a `ProviderInvocationOutcome` or
   a stream. No provider name crosses that boundary.
3. **The router is read-mostly.** It walks the registry chain and
   invokes Tool Manager; it never writes to Memory, never invokes the
   Reflection Engine, never mutates the Capability Registry.
4. **No hard-coded configuration.** Adapter config flows exclusively
   through `ConfigurationManager`. Constructor arguments exist for
   testing only.
5. **The HTTP transport is generic.** One `Transport` Protocol + one
   `StdlibHTTPTransport` implementation; every cloud adapter reuses it.
   `httpx`/`aiohttp` are explicitly avoided to keep the dependency
   surface zero.
6. **The capability matrix is centralised.** One table in
   `provider_config.py`. Adapter registration helpers read from it.
7. **Streaming integrates with the Event Bus.** Token usage, latency,
   and cost events fire through `ProviderRecorder`; no second
   observability surface.