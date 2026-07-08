# Hermes Skills

A top-level directory reserved for **Skills**: reusable capabilities
shared across agents and workflows. This document covers what a Skill is
(and isn't), the manifest format, and how the five example skills here
fit into the rest of the architecture.

## Where this sits, and what I deliberately didn't change

You proposed a flat top-level tree (`hermes/commander/`, `hermes/tool_manager/`,
etc.). What's actually built uses a `core/` vs `modules/` split instead —
`core/` holds the kernel Commander depends on directly (Commander, event
bus, Supervisor, State Manager); `modules/` holds peer modules Commander
resolves requirements against (Tool Manager, Capability Registry). That
split has been consistent across every module built so far, each already
has real, tested code, and reorganizing it wasn't what this task asked
for -- so I left it alone and added `skills/` as a new top-level sibling,
matching your tree literally for the one thing actually requested:

```
hermes/
├── core/                    <- commander, event_bus, supervisor, state_manager
├── modules/                 <- tool_manager, capability_registry
└── skills/                  <- NEW: this directory
    ├── web_search/
    ├── code_review/
    ├── email_drafting/
    ├── image_generation/
    └── document_analysis/
```

`agents/` and `plugins/`, the other two names in your suggested tree,
already have a planned home: the original architecture doc's Section 10
covers a future plugin loader with `plugins/tools/`, `plugins/agents/`,
and `plugins/backends/` subdirectories. Nothing new was created for those
in this task -- say the word if you want that reserved too.

## What a Skill is (and isn't)

A Skill is a reusable, *composed* capability -- typically built by
combining one or more Capability Registry selections and Tool Manager
invocations into a single named unit of work. "web_search," for example,
would resolve the `browser_automation` and `reasoning` capabilities and
combine their results; it is not itself a provider or a tool adapter.

- A **Tool Adapter** (`modules/tool_manager/adapters/`) wraps one
  external system (OpenAI, Obsidian, ...).
- A **Capability** (`modules/capability_registry/capabilities.py`) is a
  named category of function ("reasoning," "vision") the Capability
  Registry resolves to a specific tool.
- A **Skill** is one level up: a named, reusable procedure an agent or
  workflow invokes, which itself may use several capabilities internally.

## The manifest (`skill.toml`)

Every skill directory has exactly one file so far: a declarative
manifest, never runtime code.

```toml
[skill]
name = "web_search"
version = "0.1.0"
description = "Searches the web and returns relevant results/snippets for a query."
entrypoint = "hermes.skills.web_search.skill:WebSearchSkill"

[requirements]
capabilities = ["browser_automation", "reasoning"]
tools = []
```

`entrypoint` is a `"package.module:ClassName"` reference to where the
real implementation *would* live -- `SkillRegistry` never imports it.
Loading a manifest is pure validation; it proves the file is well-formed,
nothing more. Every example here declares `capabilities`, never `tools`
-- the same "never request a specific provider" principle the Capability
Registry enforces applies to skills too: a skill should say what it
needs, not who should provide it. (`required_tools` exists on the model
for the rare case a skill genuinely needs a named tool, but nothing in
this codebase uses it.)

## What's infrastructure vs. what's a placeholder

`SkillRegistry` (service.py) is real, working infrastructure:
`register_manifest`/`register_skill` for in-process registration,
`load_manifest`/`discover` for finding and validating `skill.toml` files
on disk. The five example skills are placeholders -- manifest only, no
`skill.py`, no logic -- exactly as scoped: this task reserves the
directory and its convention, it doesn't implement web search, code
review, or any of the others.

## How to add a real skill later

1. Write `hermes/skills/<name>/skill.toml` declaring its capabilities.
2. Implement a class satisfying the `Skill` protocol (contracts.py) at
   the path `entrypoint` names.
3. Register an instance with `SkillRegistry.register_skill(...)`.

Nothing in `service.py` needs to change — the whole point, same as every
other extension point built so far (Tool Manager's adapters, Capability
Registry's strategies), is that adding one is a change entirely local to
the new skill's own files.

## Folder structure

```
hermes/skills/
├── README.md
├── models.py            <- SkillManifest, SkillRequest, SkillResult
├── contracts.py           <- Skill protocol
├── errors.py                <- InvalidManifestError, DuplicateSkillError, UnknownSkillError
├── service.py                  <- SkillRegistry
├── interface.py                  <- public entry point (build_skill_registry)
├── web_search/skill.toml
├── code_review/skill.toml
├── email_drafting/skill.toml
├── image_generation/skill.toml
├── document_analysis/skill.toml
└── tests/
    ├── conftest.py, fakes.py
    ├── test_models.py
    └── test_service.py
```
