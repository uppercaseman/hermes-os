# Hermes Capability Registry

Hermes never requests a provider. It requests a **capability** —
`reasoning`, `planning`, `code_generation`, `image_generation`,
`video_generation`, `voice_generation`, `vision`, `memory`,
`retrieval`, `communication`, `desktop_automation`,
`browser_automation` (the canonical twelve-capability taxonomy; see
`capabilities.py`) — and the Capability Registry decides which
registered Tool Adapter should serve it.

This module makes **no calls to Tool Manager, the Supervisor, or any
external API**. It is a pure selection framework: given the capability
registrations and health/override state it's been told about, it picks
the best available provider and hands back the full fallback chain.

### Canonical capability matrix (single source of truth)

Sprint-4 centralised the capability-per-provider matrix in
`tool_manager/adapters/provider_config.py::supported_capabilities()`.
Each adapter's registration helper (`register_provider_capabilities`)
reads from this single source so the matrix in code, READMEs, and the
engineering report never drifts.

| Provider | Capabilities |
| --- | --- |
| `openai` | reasoning, planning, code_generation, vision, image_generation, video_generation, voice_generation |
| `anthropic` | reasoning, planning, code_generation, vision |
| `minimax` | reasoning, planning, code_generation, vision |
| `gemini` | reasoning, planning, code_generation, vision, image_generation, video_generation |
| `ollama` | reasoning, planning, code_generation, vision |
| `mcp` | reasoning, planning, code_generation, memory, retrieval, communication, desktop_automation, browser_automation, vision |

## Where it sits

```
   caller asks for a capability, e.g. "reasoning"
                    │
                    ▼
        ┌───────────────────────────┐
        │     CapabilityRegistry      │  <- THIS MODULE (selection only)
        │  registrations + health +    │
        │  overrides -> SelectionStrategy │
        └──────────────┬───────────────┘
                        │ returns a tool_name (e.g. "claude")
                        ▼
        ┌───────────────────────────┐
        │        ToolManager           │  <- invokes the chosen adapter
        └───────────────────────────┘
```

The registry never invokes anything itself — it only answers "which
`tool_name` should handle this capability right now," which a caller then
hands to Tool Manager's `invoke()`. Wiring that hand-off (and Commander's
planning phase asking for capabilities instead of tool names) is a
follow-on integration step, deliberately not done in this task.

## How each requirement is met

| Requirement | Mechanism |
|---|---|
| Priority ordering | `CapabilityProviderRegistration.priority` (lower preferred) |
| Fallback providers | `select()` returns the entire ranked `chain`, not just the winner; `resolve_chain()` exposes it directly |
| Provider health | `ProviderHealth.state`; updated explicitly via `update_health()`, or automatically if `start()` was called with an event bus (see below) |
| Provider cost | `CapabilityProviderRegistration.cost_per_call`, a ranking tiebreaker |
| Provider latency | `declared_latency_ms` (config) overridden by `record_latency()`'s rolling average once real samples exist |
| Manual override | `set_override()` pins a capability to one provider; `set_provider_enabled()` is an independent kill switch |
| Future automatic optimisation | Ranking is delegated to a pluggable `SelectionStrategy` (contracts.py) — the default (`strategies.py`) is deterministic; a learning/adaptive strategy can be swapped in later with no change to `service.py` |

## Automatic health tracking (optional)

If constructed with an event bus (`build_capability_registry(event_bus=...)`)
and started (`await registry.start()`), the registry subscribes to every
event and reacts to the Supervisor's `supervisor.unit.*` lifecycle events
— the exact same events already used to supervise Tool Adapters:

| Supervisor event | Resulting health state |
|---|---|
| `unit.started` | `healthy` |
| `unit.unhealthy` | `degraded` |
| `unit.crashed`, `unit.restarting`, `unit.restart_exhausted`, `unit.restart_skipped`, `unit.stopped` | `unavailable` |

This means a provider that goes down (and gets auto-restarted by the
Supervisor, or gives up after exhausting retries) is automatically
excluded from selection without anything else in the system telling the
registry directly — the same event log that drives debugging also drives
failover. Without an event bus, health is tracked purely through explicit
`update_health()` calls; both modes are fully supported and tested.

## Selection algorithm (default strategy)

1. If an override pins the capability to a provider: use it, unless that
   provider is manually disabled (in which case: no selection, with a
   `reason` explaining the conflict).
2. Otherwise, filter registered providers to those not manually disabled
   and not `unavailable`.
3. Rank the rest by: healthy-before-degraded, then `priority` ascending,
   then `cost_per_call` ascending, then `latency_ms` ascending (observed
   if available, else declared).
4. Return the top candidate as `selected`, and the full ranked list as
   `chain` — the fallback order a caller can walk manually.
5. If nothing survives step 2: `selected=None` with a `reason`, never a
   raised exception (that's reserved for asking about a capability that
   was never registered at all — `UnknownCapabilityError`).

## Folder structure

```
hermes/modules/capability_registry/
├── README.md
├── capabilities.py     <- named capability string constants
├── models.py             <- registrations, health, candidates, selection result
├── contracts.py           <- SelectionStrategy protocol
├── strategies.py            <- PriorityCostLatencyStrategy (the default/only one so far)
├── errors.py                  <- UnknownCapabilityError, UnknownProviderError
├── events.py                    <- capability_registry.* event constants
├── service.py                     <- CapabilityRegistry itself
├── interface.py                     <- public entry point (build_capability_registry)
└── tests/
    ├── conftest.py
    ├── test_models.py
    ├── test_strategies.py
    └── test_service.py
```
