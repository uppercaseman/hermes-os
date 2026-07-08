# Changelog

> All notable changes to Hermes OS, by version. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres to [Semantic Versioning](https://semver.org/) once a public release exists.

## [Unreleased] — Sprint 0

The reference implementation is undergoing architectural reconciliation. No version has been released; this section records the Sprint-0 changes as they land.

### Reconciliation ADRs (closed)

- **ADR 0017** — Mission Lifecycle reconciliation. The seven-state runtime vocabulary (`draft`, `team_assigned`, `awaiting_approval`, `active`, `completed`, `failed`, `dissolved`) is now a subset of the canonical thirteen-state machine from the spec. The Literal in `hermes/modules/mission_system/models.py` was expanded additively to sixteen values (thirteen canonical + three implementation-nicknamed). No existing call site or test changed.
- **ADR 0018** — Provider Manager realised by `tool_manager` + `capability_registry`. ADR-only decision; the spec's normative description of "Provider Manager" is realised by the two modules that already fulfilled it. A pointer was added to the [[Specification/01 - Architecture/Provider Manager|Provider Manager spec page]]. No code change.
- **ADR 0019** — Capability Taxonomy reconciliation. `hermes/modules/capability_registry/capabilities.py` was expanded to the canonical twelve-capability set from ADR 0016. The legacy constants `MEMORY_SEARCH`, `FILE_STORAGE`, `SPEECH` are kept exported as deprecated aliases. `roles.py` migrates to the canonical `MEMORY`. Tests updated.
- **ADR 0020** — Adopt `core / modules / skills / demos` as the canonical repository structure. The eight-file per-module convention is codified as [`Standards/Module Layout.md`](../Documents/Obsidian%20Vault/Hermes/Standards/Module%20Layout.md). No code change.
- **ADR 0021** — Promote [[Specification/01 - Architecture/Supervisor|Supervisor]], [[Specification/01 - Architecture/Intent Router|Intent Router]], and [[Specification/01 - Architecture/Skills|Skills]] to first-class specification modules. Three new spec pages; the [[Specification/01 - Architecture/Home|01 - Architecture]] index is updated. No code change.

### Module changes

- `hermes/core/supervisor/` — no code change; promoted to a first-class spec module.
- `hermes/modules/intent_router/` — no code change; promoted to a first-class spec module.
- `hermes/modules/mission_system/models.py` — `MissionStatus` Literal expanded from seven values to sixteen (additive).
- `hermes/modules/mission_system/roles.py` — Research Specialist's trigger/default capabilities migrated from `MEMORY_SEARCH` to canonical `MEMORY`.
- `hermes/modules/mission_system/tests/test_roles.py` — assertion updated to canonical `memory`.
- `hermes/modules/capability_registry/capabilities.py` — six new canonical constants (`PLANNING`, `DESKTOP_AUTOMATION`, `VOICE_GENERATION`, `MEMORY`, `RETRIEVAL`, `COMMUNICATION`); three legacy aliases marked deprecated.
- `hermes/modules/reflection_engine/` — **new module** (ADR-0015). Seven-phase reflection pipeline: Harvest → Candidate Generation → Scoring & Routing → Quality Gates → Human Approval → Commit → Transition. Single writer to User DNA / Skill Memory / Experience Memory / Project Memory. Subscribes to `mission_system.mission.completed` and `mission_system.mission.failed`; publishes the full event vocabulary (`reflection.started`, `reflection.completed`, `memory.candidate.created`, `memory.promoted`, `memory.rejected`, `memory.superseded`, `memory.approval_granted`, `memory.approval_denied`, `reflection.failed`). Files: `__init__.py`, `interface.py`, `service.py`, `models.py`, `contracts.py`, `events.py`, `errors.py`, `README.md`, `tests/test_service.py`. 36 new tests, ~0.4s.

### Repository hygiene

- `README.md` — created.
- `.gitignore` — created.
- `CONTRIBUTING.md` — created.
- `SECURITY.md` — created.
- `CHANGELOG.md` — created (this file).

### Specification vault changes

- `ADR/0017 - Reconcile the Mission Lifecycle Vocabulary with the Canonical 13-State Machine.md` — created.
- `ADR/0018 - Provider Manager Realised by Tool Manager + Capability Registry.md` — created.
- `ADR/0019 - Reconcile the Capability Constants with the Canonical 12-Capability Taxonomy.md` — created.
- `ADR/0020 - Adopt core modules skills demos as the Canonical Repository Structure.md` — created.
- `ADR/0021 - Promote Supervisor, Intent Router, and Skills to First-Class Specification Modules.md` — created.
- `Standards/Module Layout.md` — created.
- `Standards/Home.md` — index updated.
- `Specification/01 - Architecture/Supervisor.md` — created.
- `Specification/01 - Architecture/Intent Router.md` — created.
- `Specification/01 - Architecture/Skills.md` — created.
- `Specification/01 - Architecture/Home.md` — three new modules added to the index, mermaid diagram, and Children list. Removed the "Intent Router is deliberately not a top-level page" decision.
- `Specification/01 - Architecture/Kernel.md` — wikilink updated to [[Supervisor]]; `related_adrs` includes 0021.
- `Specification/01 - Architecture/Provider Manager.md` — "Realisation" subsection added per ADR 0018.

### Validation

- `pytest` test suite passes — 546 tests, 0 failures (456 pre-existing + 36 new for the Reflection Engine + 54 new for the Cognitive Memory Architecture), excluding the pre-existing asynchronous polling bug in `hermes/modules/tool_manager/tests/test_service.py`, which is out of scope and documented as a known baseline condition (TD-I4).
- No live API calls were made; every adapter ships with `dry_run=True` as the unconditional default.
- Sprint-1's C1 conflict (Memory Manager has no per-destination type fields) is now resolved by the Sprint-2 first-class typed-memory layer. C2 (Mission System publishes no `cancelled` event) remains a recommended ADR.

## Sprint 2 — Cognitive Memory Architecture

The Sprint-1 Reflection Engine shipped with a temporary `scope`+`tags` compatibility encoding for its four destination memory types. Sprint-2 promotes those types (plus the rest of the Memory Galaxy) to first-class fields on `MemoryEntry`, gives the engine a typed write path through `MemoryManager.record_typed`, and migrates existing reflection-engine entries losslessly.

### Module changes

- `hermes/modules/memory_manager/typed.py` — **new**. `MemoryType` Literal (six canonical cognitive memory types: `user_dna`, `working_memory`, `mission_memory`, `project_memory`, `skill_memory`, `experience_memory`). `Provenance`, `MemoryRelationship`, `GraphPath`, `MemoryRelationshipType` constants, tag helpers, `is_memory_type` validator.
- `hermes/modules/memory_manager/migration.py` — **new**. `migrate_memory_galaxy(memory_manager)` — one-shot, idempotent, lossless. Lifts legacy `scope="persistent"` + `reflection_engine:managed` + `reflection:<destination>` entries into typed fields; preserves the legacy tag encoding alongside.
- `hermes/modules/memory_manager/models.py` — `MemoryEntry` gains six additive, optional typed fields: `memory_type`, `confidence`, `importance`, `provenance`, `superseded_by`, `relationships`. Forward references resolved with Pydantic `model_rebuild`.
- `hermes/modules/memory_manager/service.py` — new public surfaces `record_typed`, `mark_superseded` (additive-only supersession primitive), `find_relationships`, `find_path` (Knowledge Graph BFS traversal). `query(memory_type=...)`, `query(include_superseded=...)` filter additions. Existing `save` / `get` / `get_by_key` / `delete` / `record_decision` / `record_error` / `get_*_history` / `search_similar` / `sweep_expired` / `grant_permission` / `revoke_permission` signatures unchanged.
- `hermes/modules/memory_manager/events.py` — three new event constants: `ENTRY_TYPED_RECORDED`, `ENTRY_SUPERSEDED`, `MEMORY_GALAXY_MIGRATED`.
- `hermes/modules/memory_manager/__init__.py`, `interface.py` — re-export the new typed symbols (`MemoryType`, `Provenance`, `MemoryRelationship`, `GraphPath`, `MemoryRelationshipType`, `migrate_memory_galaxy`, …).
- `hermes/modules/reflection_engine/contracts.py` — `MemoryWriter` Protocol gains `record_typed` as a structural addition. The legacy `record` method is preserved.
- `hermes/modules/reflection_engine/service.py` — `_commit_candidate` writes through `MemoryManager.record_typed`. Engine vocabulary (`user_dna` / `skill` / `experience` / `project`) maps to canonical `MemoryType` values via a new `_DESTINATION_TO_MEMORY_TYPE` table. Provenance is passed as a list of dicts at the protocol boundary so the engine's `Provenance` and the memory manager's `Provenance` (structurally identical Pydantic models) coexist.
- `hermes/modules/reflection_engine/tests/test_integration_memory.py` — **new**. 6 integration tests verifying the engine writes typed memory end-to-end against a real `MemoryManager` (no fakes).
- `hermes/modules/memory_manager/tests/test_typed.py` — **new**. 48 tests covering `record_typed`, `mark_superseded`, `find_relationships`, `find_path`, the migration shim, and Sprint-2 backwards-compatibility (legacy `save` calls, scope-rejection semantics, etc.).

### Backwards compatibility

- All 492 pre-existing tests continue to pass unchanged.
- Every existing `MemoryManager` API surface keeps its signature.
- External callers (`workflow_engine/contracts.py` `MemoryStore`, `mission_system/team_builder.py` `MemoryPermissionGranter`, `demos/research_brief/runner.py`) keep working without changes.
- The legacy `reflection_engine:managed` / `reflection:<destination>` tag encoding is preserved alongside typed fields so any consumer that hasn't migrated yet still finds engine-written entries.

## [0.0.0] — Pre-Sprint-0 baseline

The pre-Sprint-0 baseline of the reference implementation: 13 modules under `hermes/` (4 in `core/`, 9 in `modules/`), 5 skill descriptors under `hermes/skills/`, 1 demo under `hermes/demos/`, and the architectural drift reconciled by Sprint 0. No version number was assigned. With the Reflection Engine landing in Sprint 1, the count becomes 14 modules under `hermes/` (4 in `core/`, 10 in `modules/`).

## Sprint 3 — Knowledge & Reasoning Layer

The Sprint-2 typed Memory Architecture is frozen. Sprint-3 adds the runtime Knowledge Graph, the Context Builder, and the Reasoning Engine — three read-only modules that turn the typed substrate into a structured `ReasoningContext` payload Commander (and a future Provider Ecosystem layer) can dispatch on.

### Module changes

- `hermes/modules/knowledge_graph/` — **new**. Runtime layer over Memory Manager's typed `relationships` field, `backlinks`, and `tags`. **No separate storage engine.** Provides `neighbourhood(...)` (BFS from a seed), `expansion(...)` (1-hop structural + tag-overlap fan-out), `influence_score(...)` (clamped total of weight × confidence / (1 + age_in_days)), and `propagated_confidence(...)` (confidence × edge-weight product along the shortest typed path). Performance budget: 10,000-edge BFS under 200 ms.
- `hermes/modules/context_builder/` — **new**. Assembles the most relevant memories for a `ContextRequest` (seed set + k + min_confidence + max_hops). Combines Knowledge Graph traversal, expansion, and confidence propagation into one weighted per-entry score: `0.5 × propagated_confidence + 0.3 × path_score + 0.2 × entry.confidence`. Returns an `AssembledContext` with a per-entry scoring trace.
- `hermes/modules/reasoning_engine/` — **new**. Prepares structured `ReasoningContext` payloads for Commander. **Read-only** over the Context Builder's output. **Does not call AI models or perform provider reasoning in Sprint-3** — a guard rail (`ProviderReasoningUnavailableError`) makes this loud. Exposes `build_default_memory_resolver(...)` so Commander's `MemoryResolver` Protocol slot can bind to the Engine without an interface change.
- Spec pages: `Specification/02 - Cognitive Architecture/Context Builder.md` and `Specification/02 - Cognitive Architecture/Reasoning Engine.md` — **new**.
- `MemoryManager`, `ReflectionEngine`, and Commander service internals — **untouched**. The Sprint-3 binding is implemented as a factory helper in `reasoning_engine/interface.py`.

### Backwards compatibility

- All 546 pre-existing tests continue to pass unchanged.
- No `MemoryManager` API surface change (Sprint-2 typed extensions preserved).
- No `ReflectionEngine` API surface change (Sprint-2's `MemoryWriter.record_typed` preserved).
- No Commander service internals change. `MemoryResolver` Protocol binding is via the factory helper.
- New event constants (`knowledge_graph.*`, `context_builder.*`, `reasoning_engine.*`) are additive — no existing event renamed or repurposed.

---

For the rationale behind any change, see the corresponding ADR in the specification vault.

## Sprint 4 — Provider Ecosystem

The Sprint-2 typed Memory + Sprint-3 Knowledge & Reasoning layers are frozen. Sprint-4 fills in the **Provider Ecosystem**: production-ready adapters for every configured provider, a shared canonical capability matrix, a unified HTTP transport, an end-to-end observability surface, and a **Provider Router** that turns a `capability` request into a structured `ProviderInvocationOutcome` with full fail-over semantics. Commander remains provider-agnostic throughout.

### Module changes

- `hermes/modules/provider_router/` — **new**. Capability-driven fail-over routing. Resolves a `capability` to the Capability Registry's ranked candidate chain, invokes each candidate through Tool Manager (so Tool Manager's existing retry / rate-limit / timeout infrastructure is reused), and on transient failure walks to the next candidate up to `failover_max_attempts`. Emits `provider_router.routing.{started, succeeded, failed, failover}` and `provider_router.provider_attempt.{started, succeeded, failed}` for every decision. Files: `__init__.py`, `interface.py`, `service.py`, `models.py`, `contracts.py`, `events.py`, `errors.py`, `README.md`, `tests/test_service.py`. 18 new tests.
- `hermes/modules/tool_manager/adapters/openai_adapter.py` — **rewritten**. Production OpenAI Chat Completions adapter. Sync + SSE streaming, `from_configuration_manager()` alternative constructor, `health_check()` via `GET /models`, `OpenAIAuthenticationError` only when `dry_run=False` and no API key is set, `ProviderRecorder` emits `token_usage`, `latency`, `estimated_cost`, `succeeded`/`failed`/`timeout` events. Backwards-compatible class name `OpenAIAdapter` and env-var constant `OPENAI_API_KEY_ENV_VAR` preserved.
- `hermes/modules/tool_manager/adapters/claude_adapter.py` — **rewritten**. Production Anthropic Messages API adapter. `x-api-key` + `anthropic-version` headers, SSE event types (`message_start`, `content_block_delta`, `message_delta`, `message_stop`) handled. Provider label is the canonical `anthropic`; class name preserved as `ClaudeAdapter`.
- `hermes/modules/tool_manager/adapters/minimax_adapter.py` — **rewritten**. Production MiniMax OpenAI-compatible adapter, `text/chatcompletion_v2` endpoint. Backwards-compatible class name + env var preserved.
- `hermes/modules/tool_manager/adapters/gemini_adapter.py` — **new**. Production Google Gemini Generative Language API adapter. `:generateContent` (sync) and `:streamGenerateContent?alt=sse` (stream). Hermes `{messages}` → Gemini `contents` with `systemInstruction` for system prompts.
- `hermes/modules/tool_manager/adapters/ollama_adapter.py` — **new**. Production Ollama-compatible local-model adapter (covers LM Studio, vLLM, llama.cpp, anything serving `/api/chat`). NDJSON streaming protocol.
- `hermes/modules/tool_manager/adapters/mcp_server_adapter.py` — **rewritten**. Production MCP (Model Context Protocol) stdio adapter. JSON-RPC 2.0 over subprocess pipes; operations: `initialize`, `list_tools`, `call_tool`. `discover_capabilities()` returns server-declared capabilities post-handshake. Backwards-compatible constructor preserved.
- `hermes/modules/tool_manager/adapters/paperclip_adapter.py` — stub kept current with the canonical interface.
- `hermes/modules/tool_manager/adapters/obsidian_adapter.py` — **rewritten**. Production Obsidian vault adapter (local filesystem reads). Operations: `list_notes`, `read_note`, `search_notes`. Non-streaming; capabilities: retrieval, memory.
- `hermes/modules/tool_manager/adapters/http_base.py` — **new**. Generic HTTP transport reused by every cloud provider adapter. Pure stdlib (`asyncio.open_connection`, hand-rolled HTTP/1.1, SSE parsing). Defines the `Transport` Protocol, `HTTPRequest`/`HTTPResponse`, `CancellationToken`, and the `HTTPTransportError` family (`HTTPConnectionError`, `HTTPTimeoutError`, `HTTPCancelledError`, `HTTPStatusError`).
- `hermes/modules/tool_manager/adapters/provider_config.py` — **new**. Pydantic config schemas for every provider (`OpenAIProviderConfig`, `AnthropicProviderConfig`, `MiniMaxProviderConfig`, `GeminiProviderConfig`, `OllamaProviderConfig`, `MCPProviderConfig`). Single source of truth for the canonical capability matrix via `supported_capabilities(provider)` and `provider_names()`. Plus `TokenUsage`, `estimate_cost_usd(...)`, and `provider_config_for(...)`.
- `hermes/modules/tool_manager/adapters/provider_events.py` — **new**. Centralised provider observability event vocabulary and `ProviderRecorder` helper. Nine event constants: `provider.selected`, `.succeeded`, `.failed`, `.timeout`, `.retry`, `.token_usage`, `.latency`, `.estimated_cost`, `.cancelled`, `.health_changed`.
- `hermes/modules/tool_manager/adapters/capability_registration.py` — **new**. Shared `register_provider_capabilities()` helper; reads the canonical matrix from `provider_config.supported_capabilities()` so the registration step in every adapter never drifts.
- `hermes/modules/tool_manager/adapters/__init__.py` — **rewritten**. Public surface for all eight adapters + the new helpers.
- `hermes/modules/tool_manager/tests/test_adapters.py` — **rewritten**. Parametrized smoke tests over all eight adapters (dry-run path, capability matrix, auth-error contract, env-var safety). 51 tests pass.
- `hermes/modules/tool_manager/tests/test_provider_ecosystem.py` — **new**. Real adapter tests with mocked network transport: bearer-token headers, Anthropic `x-api-key`, MiniMax v2 endpoint, Gemini `key=` query + `systemInstruction` propagation, Ollama NDJSON streaming, OpenAI SSE delta parsing, Anthropic SSE event types. Plus Provider Router integration tests (single success, fail-over to second candidate, exhaustion, registry-chain order, Configuration Manager loading). Plus capability-matrix stability. 24 tests pass.

### Capabilities

The canonical capability matrix is centralised in `provider_config.SUPPORTED_CAPABILITIES_TABLE`:

| Provider | Capabilities |
| --- | --- |
| `openai` | reasoning, planning, code_generation, vision, image_generation, video_generation, voice_generation |
| `anthropic` | reasoning, planning, code_generation, vision |
| `minimax` | reasoning, planning, code_generation, vision |
| `gemini` | reasoning, planning, code_generation, vision, image_generation, video_generation |
| `ollama` | reasoning, planning, code_generation, vision |
| `mcp` | reasoning, planning, code_generation, memory, retrieval, communication, desktop_automation, browser_automation, vision |

### Architecture constraints (preserved)

- Commander is **provider-agnostic** — it asks the Provider Router for a capability, never names a provider.
- No new provider abstraction: every adapter implements the existing `ToolAdapter` Protocol.
- No live API keys required for CI; every adapter accepts an injected `Transport` (HTTP providers) or mocks its subprocess pipe (MCP).
- API keys are read from environment by **name only**; they never appear as constructor arguments or in logs.
- The `dry_run=True` default is preserved across every adapter; switching it off requires a configured environment variable per provider.
- Streaming integrates with the Event Bus via the existing `tool_manager.provider.*` event vocabulary (no second observability stack).

### Backwards compatibility

- All 546 pre-Sprint-4 tests continue to pass unchanged.
- No `MemoryManager`, `Reflection Engine`, `Knowledge Graph`, `Context Builder`, or `Reasoning Engine` surface change.
- No Commander service internals change.
- Backwards-compatible adapter class names (`OpenAIAdapter`, `ClaudeAdapter`, `MiniMaxAdapter`, `MCPServerAdapter`) and env-var constants preserved.
- Provider labels in the canonical matrix use `anthropic` (not `claude`); tests asserting the historical label should look at `adapter.__class__.__name__` instead.
- `tool_manager/test_service.py` async hang (TD-I4) remains excluded per the technical debt register.

### Sprint-4 test totals

| Suite | New tests |
| --- | --- |
| `provider_router/tests/test_service.py` | 18 |
| `tool_manager/tests/test_provider_ecosystem.py` | 24 |
| `tool_manager/tests/test_adapters.py` (rewrite, same count) | (51) |
| `tool_manager/tests/test_openai_adapter.py` (updated, same count) | (6) |
| **Sprint-4 net new** | **42** |
| **Pre-Sprint-4 baseline** | **704** |
| **Total non-TD tests** | **746** |

---