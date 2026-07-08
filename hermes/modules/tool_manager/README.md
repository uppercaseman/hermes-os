# Hermes Tool Manager

Tool Manager is the only path from Hermes to external systems. Hermes
Commander — and every other module — never talks to an API directly;
every external service is represented by a **Tool Adapter** registered
here. This document covers the architecture, the folder layout, and how
to add a new adapter. It complements, and does not repeat, the docstrings
in each source file.

## Why it exists

Without this boundary, "call OpenAI" logic ends up scattered across
whichever module happened to need it first, each with its own retry loop,
its own rate limiting (or none), its own auth handling. Tool Manager
exists so that concern is solved exactly once, generically, and every
provider — however different — plugs into the same infrastructure.

## Architecture

```
                     ┌───────────────────────────┐
                     │        ToolManager         │
                     │  (retry, rate limit,       │
                     │   timeout, invoke/stream)   │
                     └──────────────┬─────────────┘
                                    │ registers each adapter with
                                    ▼
                     ┌───────────────────────────┐
                     │         Supervisor          │  (core/supervisor)
                     │ health_check loop + restart  │
                     └──────────────┬─────────────┘
                                    │ manages lifecycle of
        ┌────────────┬─────────────┼─────────────┬────────────┬──────────┐
        ▼            ▼             ▼             ▼            ▼          ▼
    OpenAIAdapter ClaudeAdapter MiniMaxAdapter GeminiAdapter OllamaAdapter MCPServerAdapter ObsidianAdapter PaperclipAdapter
      (production)  (production)  (production)  (production)  (production)  (production)   (production)   (stub)
```

Every adapter satisfies one Protocol: `ToolAdapter` (contracts.py), which
extends the kernel's own `Supervisable` Protocol
(`core/supervisor/contracts.py`). That inheritance is the key design
decision in this module:

- **Health monitoring and automatic restart are not reimplemented here.**
  An adapter, once registered, is enrolled with the same `Supervisor` that
  will eventually manage Memory Manager, Workflow Engine, and every other
  module. Tool Manager contributes zero health-check-loop code of its
  own — it reuses the kernel's.
- **Retries reuse `RetryPolicy`** (`core/supervisor/policy.py`) — the
  same backoff math already used for Commander's task retries and the
  Supervisor's module restarts. This is the third reuse of that one
  building block, exactly as its own docstring anticipated.
- **Authentication is folded into startup.** `adapter.authenticate()` is
  called once, immediately before `adapter.start()`, via a small internal
  shim (`_SupervisableAdapter` in service.py) that bridges the richer
  `ToolAdapter` protocol down to the plain `Supervisable` one the
  Supervisor understands. A failed `authenticate()` is handled exactly
  like a startup crash — the Supervisor's existing restart policy applies
  unchanged.

## Execution modes

| Mode | Method | Where it lives |
|---|---|---|
| Synchronous | `ToolManager.invoke()` | Awaits the full result inline; retried, rate-limited, timeout-bounded. |
| Asynchronous | `ToolManager.invoke_async()` + `get_result()` / `await_result()` | Built **on top of** `invoke()` by scheduling it as a background task and handing back a handle. No adapter implements its own job-submission protocol. |
| Streaming | `ToolManager.invoke_stream()` | Pass-through to `adapter.invoke_stream()`, gated by `capabilities.supports_streaming`. Retries only stream *establishment* (the first chunk); a mid-stream failure ends the stream with a final chunk carrying `error` set, never retried and never raised. |

Only `invoke()` is mandatory for an adapter to implement — it is the one
shape every provider can express. `invoke_stream()` only needs a real
implementation if the adapter declares `supports_streaming=True`.
"Asynchronous execution" is deliberately not part of the adapter
contract at all, for the same reason: it doesn't need to be, since Tool
Manager provides it uniformly for every adapter.

## Retries, rate limits, auth, health — all generic

None of the above is provider-specific:

- **Retries**: `ToolAdapterConfig.retry_policy` (a `RetryPolicy`), applied
  by `ToolManager.invoke()`/`invoke_stream()` around every call into an
  adapter.
- **Rate limits**: `ToolAdapterConfig.rate_limit` (a `RateLimitPolicy`)
  backs one `RateLimiter` (token bucket, `rate_limiter.py`) per registered
  adapter, acquired before every call.
- **Auth**: `ToolAdapterConfig.auth` (an `AuthConfig`) carries only a
  *reference* to where the real credential lives (an env var name, a
  secret-store key) — never the secret itself. Resolving that reference
  is the future Configuration Manager's job, consistent with the
  architecture doc's "secrets never in plain config" rule.
- **Health**: `ToolAdapterConfig.health_check_interval_seconds` configures
  how often the Supervisor polls `adapter.health_check()`.

A caller (or a future Configuration Manager) tunes all four per adapter,
without Tool Manager or any adapter needing new code.

## Folder structure

```
hermes/modules/tool_manager/
├── README.md            <- this file
├── contracts.py          <- ToolAdapter protocol (extends Supervisable)
├── models.py              <- ToolInvocationRequest/Result, capabilities, configs
├── events.py               <- tool_manager.* event constants
├── errors.py                <- UnsupportedCapabilityError, UnknownHandleError
├── rate_limiter.py            <- generic token-bucket RateLimiter
├── service.py                   <- ToolManager itself
├── interface.py                   <- public entry point (build_tool_manager)
├── adapters/
│   ├── __init__.py                     <- public surface
│   ├── base.py                          <- BaseToolAdapter common scaffolding
│   ├── http_base.py                      <- Transport Protocol + StdlibHTTPTransport
│   ├── provider_config.py                 <- canonical capability matrix + Pydantic schemas
│   ├── provider_events.py                  <- ProviderRecorder + provider.* event vocabulary
│   ├── capability_registration.py           <- shared register_provider_capabilities() helper
│   ├── openai_adapter.py                    <- production OpenAI Chat Completions
│   ├── claude_adapter.py                     <- production Anthropic Messages API
│   ├── minimax_adapter.py                     <- production MiniMax OpenAI-compatible
│   ├── gemini_adapter.py                      <- production Google Gemini
│   ├── ollama_adapter.py                      <- production Ollama-compatible (NDJSON)
│   ├── mcp_server_adapter.py                   <- production MCP JSON-RPC 2.0 over stdio
│   ├── obsidian_adapter.py                     <- production Obsidian vault reads
│   └── paperclip_adapter.py                     <- stub (dry_run only)
└── tests/
    ├── conftest.py, fakes.py                        <- ScriptedToolAdapter test double
    ├── test_models.py
    ├── test_rate_limiter.py
    ├── test_service.py
    ├── test_adapters.py                              <- parametrized smoke tests over all 8 adapters
    ├── test_openai_adapter.py                         <- OpenAI-specific
    ├── test_configuration_manager_wiring.py            <- Configuration Manager wiring
    └── test_provider_ecosystem.py                       <- mocked-network + Router integration
```

## What is, and is not, implemented

Sprint-4 shipped production-ready adapters for every configured cloud, local, and protocol provider. Six are real integrations; two are utility adapters (Obsidian for the local vault, Paperclip as a deterministic stub). Every adapter:

- implements the full `ToolAdapter` Protocol: `authenticate`, `start`, `stop`, `health_check`, `invoke`, `invoke_stream`
- defaults to `dry_run=True` so it is safe to construct and invoke with no credentials and no network access
- reads API keys **only from environment variables by name** — never from constructor arguments
- publishes a structured set of `tool_manager.provider.*` observability events (`succeeded`, `failed`, `timeout`, `token_usage`, `latency`, `estimated_cost`, `cancelled`, `health_changed`)
- supports cancellation via the `CancellationToken` baked into `HTTPRequest`
- is fully unit-tested with a mocked `Transport` — no adapter requires a live API key for CI

## OpenAI Adapter

`OpenAIAdapter` is the production reference adapter. It is the template
the other cloud adapters (`ClaudeAdapter`, `MiniMaxAdapter`,
`GeminiAdapter`) follow. In dry_run mode (the default) it returns a
structured result with no network access; in production mode it
authenticates via `OPENAI_API_KEY`, posts to
`{base_url}/chat/completions`, parses the JSON response, and surfaces
both completion text and token usage.

### Configuration

| Setting | Default | How to set it |
|---|---|---|
| API key | none | Environment variable `OPENAI_API_KEY` (or a custom name via `api_key_env_var=`) — **never** a constructor argument, a config file, or a hardcoded string. `test_api_key_is_never_a_constructor_argument` in the test suite guards against this changing by accident. |
| `dry_run` | `True` | `OpenAIAdapter(name=..., dry_run=False)` to disable. |
| `base_url` | `https://api.openai.com/v1` | Override to point at an OpenAI-compatible proxy (e.g. LM Studio, vLLM). |
| `model_name` | `gpt-4o-mini` | Per-call override available via `parameters["model"]`. |
| `invocation_timeout_seconds` | `30.0` | |
| `max_retries` | `2` | Backoff/retry budget on top of Tool Manager's own `RetryPolicy`. |
| `cost_per_call` | `0.0` | Used by `ProviderRecorder.estimated_cost` event. |

### Wiring it to the Capability Registry

`OpenAIAdapter` itself has no dependency on Capability Registry — that
separation is preserved. A thin, optional helper does the wiring:

```python
from hermes.modules.tool_manager.adapters import OpenAIAdapter, register_with_capability_registry

adapter = OpenAIAdapter(name="openai")  # dry_run=True by default
tool_manager.register_adapter(adapter, ToolAdapterConfig(name="openai"))
register_with_capability_registry(capability_registry, tool_name="openai")
# capability_registry.select("reasoning") / .select("code_generation")
# can now resolve to "openai"
```

### The safety guarantee, precisely

No code path in this file constructs or sends an HTTP request. Every
test in `test_openai_adapter.py` passes without network access, and
`test_non_dry_run_invoke_never_makes_a_live_call_even_with_a_key_present`
exists specifically to prove that setting `dry_run=False` *and* supplying
a (fake, in tests) key still doesn't reach a network call — it reaches
`NotImplementedError` instead.

## Consuming Configuration Manager (optional, additive)

Both `ToolManager` and `OpenAIAdapter` can optionally source their
configuration from a `ConfigurationManager` instead of their own
hardcoded defaults. Neither requires it, and omitting it reproduces
every prior behavior of this module exactly — this was proven with a
regression run of the entire pre-existing test suite plus a new,
dedicated test file (`test_configuration_manager_wiring.py`) before
this feature was considered done.

### `ToolManager.default_adapter_config()`

```python
tool_manager = build_tool_manager(event_bus=bus, configuration_manager=config)
config.register_schema("tool_manager", ...)  # optional -- see Configuration Manager's own docs
adapter_config = tool_manager.default_adapter_config("openai")  # invocation_timeout_seconds / health_check_interval_seconds sourced from config, if set
tool_manager.register_adapter(OpenAIAdapter(name="openai"), adapter_config)
```

With no `configuration_manager` given (or nothing set under
`tool_manager.*`), `default_adapter_config(name)` returns exactly
`ToolAdapterConfig(name=name)` — the same object every existing caller
already builds directly. Only `invocation_timeout_seconds` and
`health_check_interval_seconds` are sourced this way today;
`rate_limit`/`retry_policy`/`auth` are nested models this method
deliberately does not reach into yet (see "Known gaps" below) —
callers needing those still build a `ToolAdapterConfig` directly, same
as before this change.

### `OpenAIAdapter.from_configuration_manager()`

```python
adapter = OpenAIAdapter.from_configuration_manager(name="openai", configuration_manager=config)
```

An alternative constructor, additive alongside the original `__init__`
(completely unchanged). Sources exactly two values:

| Field | Source | Fallback |
|---|---|---|
| `dry_run` | `configuration_manager.get_dry_run("providers.openai")` | `global.dry_run_default`, then `True` |
| `api_key_env_var` | `configuration_manager.get_provider_config("openai")["api_key_env_var"]` | `OPENAI_API_KEY_ENV_VAR` (`"OPENAI_API_KEY"`) |

Neither of these reads the actual API key *value* — only its env var
*name* ever passes through Configuration Manager, as a plain string.
`test_construction_never_reads_the_actual_api_key_value` proves this
directly: it spies on `os.environ.get` during construction and asserts
the real key is never even looked up, let alone read or logged, unless
`authenticate()`/`invoke()` are later called with `dry_run=False`
explicitly requested (unchanged, pre-existing behavior). `dry_run`
stays `True` even with a real-looking key sitting in the environment
and nothing configured for `providers.openai` — only an explicit
config override or constructor argument can turn it off.

### Known gaps in this wiring

- `ToolManager.default_adapter_config()` only sources two scalar
  `ToolAdapterConfig` fields from Configuration Manager. The nested
  `rate_limit`/`retry_policy`/`auth` models are not wired — a natural
  next step, not done here to keep this change small and reviewable.
- Nothing calls `default_adapter_config()`/`from_configuration_manager()`
  automatically — a caller (e.g. a future startup/bootstrap module)
  has to choose to use them. `register_adapter()` itself is completely
  unmodified and still takes a caller-built `ToolAdapterConfig`.
- Only the OpenAI adapter has this wiring; the other five (still pure
  placeholders or the generic MCP adapter) do not, since building new
  providers was explicitly out of scope for this change.

## How to add a real adapter later

1. Subclass `BaseToolAdapter` (or implement `ToolAdapter` directly).
2. Set `provider` and `capabilities` as class attributes.
3. Override `invoke()` — and `invoke_stream()` if `supports_streaming` is
   `True` — with real provider logic.
4. Override `authenticate()`/`health_check()` if the provider needs a real
   handshake/liveness check beyond the no-op defaults.
5. Register an instance with `ToolManager.register_adapter(adapter,
   ToolAdapterConfig(name=..., ...))`.

Nothing in `service.py` changes. That is the whole point of the
architecture: swapping, adding, or removing a provider is a change
entirely local to its adapter file.

## How an MCP server fits in

`adapters/mcp_server_adapter.py` is the concrete proof that "support
future MCP servers" needed no special-casing: `MCPServerAdapter` is
just another `ToolAdapter`, distinguished only by carrying a
`server_command` instead of an API base URL. A real implementation would
hold an MCP client session inside `invoke()`/`invoke_stream()`; from Tool
Manager's point of view it is indistinguishable from any other adapter.

## Relationship to Commander

Commander's existing `ToolResolver` protocol
(`core/commander/contracts.py`) only asks "which tools does this plan
need" — it does not invoke anything. Tool Manager's registry-query
surface is a natural, structurally-compatible backing for that protocol
once wired up, but that integration is intentionally **not** done in this
task; Commander still runs against its test fakes for `ToolResolver`
until that wiring is explicitly requested.
