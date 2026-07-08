# Hermes OS — Reference Implementation

> The reference implementation of the Hermes Agent Operating System. Mission-driven, capability-routed, kernel-supervised, locally specifiable. Built so any new module can be added by following the layout already in use.

## What this is

Hermes is a mission-driven AI operating system. A user expresses a goal; Hermes translates that goal into a mission, assembles a temporary team of specialist roles, executes a workflow against the available providers, captures everything that happens in a log, and folds what was learned into memory.

This repository is the **reference implementation**. The single source of truth for what Hermes is, what its modules do, and why each architectural decision was made, lives in the Obsidian specification vault (`../Documents/Obsidian Vault/Hermes/`). This README is a map from "the spec" to "the code."

## Repository layout

The codebase follows the four-layer layout codified in [`Standards/Module Layout.md`](../Documents/Obsidian%20Vault/Hermes/Standards/Module%20Layout.md) (see ADR 0020):

```
hermes/
├── core/           # kernel-level primitives (commander, event_bus, state_manager, supervisor)
├── modules/        # capability-bearing subsystems (mission_system, tool_manager, capability_registry, ...)
├── skills/         # reusable capability bundles (code_review, document_analysis, ...) described by skill.toml
├── demos/          # vertical slices that wire several modules together (research_brief)
└── tests/          # cross-layer integration tests
```

Each subpackage under `core/`, `modules/`, and the `skills/` framework follows the eight-file convention (`__init__.py`, `interface.py`, `service.py`, `models.py`, `contracts.py`, `events.py`, `errors.py`, `README.md`, `tests/`).

## Quick start

```bash
# 1. Create a virtual environment and install (development) extras.
python3.13 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"

# 2. Run the test suite.
.venv/bin/python -m pytest

# 3. Run the research-brief demo end-to-end.
.venv/bin/python -m hermes.demos.research_brief.cli
```

The demo runs in safe (dry-run) mode by default and does not make any live API calls. See `demos/research_brief/README.md` for details.

## Where to read more

- The Specification — the authoritative description of every module, with Purpose, Responsibilities, Relationships, and Design Decisions.
- The ADRs (`ADR/Home.md`) — every architectural decision and why it was made.
- The Standards (`Standards/Home.md`) — the rules every module and every document follows.
- The Code Repository Reality Audit — the baseline Sprint 0 reconciliation; the ADRs 0017–0021 are its direct resolutions.

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the module-adding checklist, the test standard, and the CI foundation plan. See [`SECURITY.md`](SECURITY.md) for how to report a vulnerability.

## Status

Hermes is at **Sprint 0** — reconciliation of architectural drift between the specification and the code. The reference implementation is production-shaped for the kernel but no provider is wired to a real network endpoint; the OpenAI adapter is a dry-run-by-default skeleton, every other adapter is a placeholder.

## License

TBD — see `LICENSE` once chosen.