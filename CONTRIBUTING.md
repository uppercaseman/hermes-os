# Contributing to Hermes OS

> How to add a module, a skill, a workflow, or a demo without breaking the conventions every existing module follows. The spec is the single source of truth; this page is a checklist for keeping the code consistent with it.

## Where to start

Before any contribution:

1. Read the relevant spec page in the Obsidian vault (`../Documents/Obsidian Vault/Hermes/`).
2. Read the relevant ADR(s) — most architectural decisions are recorded with context and consequences.
3. Read [`Standards/Module Layout.md`](../Documents/Obsidian%20Vault/Hermes/Standards/Module%20Layout.md) — the four-layer split and the eight-file convention are codified and enforced.

## Adding a new module

A new module is a new directory under `hermes/core/` or `hermes/modules/`. It must follow the eight-file convention:

```
<name>/
├── __init__.py             # re-exports the public surface from interface.py
├── interface.py            # Protocol-based abstract surface; the dependency the rest of the code depends on
├── service.py              # the concrete implementation of the Protocol
├── models.py               # Pydantic models for the module's own state and I/O
├── contracts.py            # types imported across module boundaries
├── events.py               # event-type string constants this module publishes and subscribes to
├── errors.py               # module-specific exception classes (subclasses of HermesError)
├── README.md               # the public specification of the module's behaviour
└── tests/
    ├── __init__.py
    └── test_<name>.py
```

Two named exceptions apply (`core/event_bus/`, `core/state_manager/`); both drop `interface.py` and `models.py` because they are publish/subscribe and state primitives respectively. No other module is exempt.

Import edges:

- `core/` never imports `modules/`.
- `modules/` may import `core/` and other `modules/` (subject to inter-module contracts — see "Open Standards" below).
- `skills/` and `demos/` are higher layers; `core/` never imports them.

## Adding a new skill

A new skill is a new directory under `hermes/skills/<name>/` containing exactly one file:

```
hermes/skills/<name>/
└── skill.toml              # [skill] and [requirements] sections; see any existing skill for shape
```

The skill is discovered automatically by `SkillRegistry` walking `hermes/skills/`. It is **not** a Python subpackage.

## Adding a new demo

A new demo is a new directory under `hermes/demos/<name>/`. Demos are the only layer that may import from all four layers; they exist to demonstrate one end-to-end user journey. The existing `demos/research_brief/` is the canonical example.

## Adding a new ADR

ADRs live in the Obsidian vault (`../Documents/Obsidian Vault/Hermes/ADR/`). The process and template are codified in [`Standards/ADR Process.md`](../Documents/Obsidian%20Vault/Hermes/Standards/ADR%20Process.md) and [`Templates/ADR Template.md`](../Documents/Obsidian%20Vault/Hermes/Templates/ADR%20Template.md).

## The test standard

Every module ships with at least one `tests/test_<name>.py` covering its public surface (per the Protocol in `interface.py`). The full standards are in [`Standards/Testing Standard.md`](../Documents/Obsidian%20Vault/Hermes/Standards/Testing%20Standard.md). The minimum bar:

- The Protocol's every public method has at least one happy-path test.
- Every error class in `errors.py` has at least one test that triggers it.
- The test suite must pass with `pytest`.

```bash
.venv/bin/python -m pytest
```

## Open Standards (not yet codified)

The following are standards the codebase observes by convention but which are not yet captured as [[Standards/Home|Standards]] pages:

- **Inter-module contracts** — the rules for how two `modules/` packages are allowed to import each other (the audit flagged this as the next gap to close after Sprint 0; see T6 in the Sprint-0 plan).
- **Code Style Standard** — language-specific formatting/linting rules. Until codified, follow PEP 8 with a 100-character line limit.

## CI foundation (planned)

The repository does not yet have a CI pipeline. The intended foundation (to be added when the project is ready to install third-party dev tools):

- **ruff** — lint + format (`ruff check` / `ruff format`). Config in `pyproject.toml` under `[tool.ruff]`.
- **pyright** — type-check (`pyright`). Config in `pyrightconfig.json`.
- **pytest** — already configured via `pyproject.toml` under `[tool.pytest.ini_options]`.
- **pre-commit** — `.pre-commit-config.yaml` to wire the above.
- **GitHub Actions** — `.github/workflows/ci.yml` to run `ruff`, `pyright`, and `pytest` on every push and PR.

These are deliberately deferred until the project is ready to depend on them. Adding stub config files for tools that aren't installed would create dead configuration; the goal is to add all of the above together once.

## Sprint 0 status

Hermes is at Sprint 0 (reconciliation). No business features are being added yet. The right contributions at this stage are:

- Spec-vs-code drift fixes (driven by ADRs 0017–0021).
- Additional module tests.
- Documentation improvements to the spec vault.

Adding a new business feature before Sprint 0 closes is out of scope. See the [audit report](#) and the [ADRs](../Documents/Obsidian%20Vault/Hermes/ADR/Home.md) for what counts as in-scope.

## Reporting a vulnerability

See [`SECURITY.md`](SECURITY.md).