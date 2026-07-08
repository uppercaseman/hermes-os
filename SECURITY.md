# Security Policy

> How to report a vulnerability in Hermes OS, and what we commit to in return. The reference implementation handles user goals, runs tools, and reaches external providers; the security model is taken seriously even at the Sprint-0 stage.

## Supported versions

Hermes is in **Sprint 0** (architectural reconciliation). The current development version is the only supported version. There are no production deployments and no prior releases; security advisories, when issued, will describe current-code mitigations only.

## Design-level guarantees

The following are properties the architecture enforces rather than properties the implementation happens to have today; they are recorded here so a contributor adding a module knows what is mandatory:

- **Credentials are loaded from environment variables at the point of use only.** Per Design Principle D9, no module may read, print, or persist a real credential value. The `AuthConfig.credential_ref` field on a provider adapter holds the *name* of the environment variable, never its value.
- **Live-provider adapters default to dry-run mode.** Per Design Principle D8, every adapter capable of a live external action ships with `dry_run=True` as its unconditional default; a real network call requires explicit, deliberate opt-out.
- **One subscriber's exception must never crash another subscriber or the publisher.** Per-handler fault isolation is a kernel-level invariant of the Event Bus.
- **Credential references are additive, never required.** A module may optionally supply an adapter's `dry_run` default and credential-variable *name* via the [[Configuration Manager]] — but the Configuration Manager is never on the critical path of authentication.

## Reporting a vulnerability

For anything that looks like a security issue — a credential leak, an unbounded provider call, an Event Bus fault-isolation bug, anything that would let one mission influence another without authorisation — please report privately **before** filing a public issue.

Channel: **email the maintainer** at the address in the git log (placeholder until the project is hosted). Use a descriptive subject line (`SECURITY:` followed by a short noun-phrase). Do not include a proof-of-concept exploit in the initial report.

When filing, please include:

- The affected module(s) and file path(s).
- A minimal reproduction (without a live exploit if the issue is exploitable).
- Whether the issue is reachable in dry-run mode or only with a configured live provider.
- Your assessment of severity (information disclosure, credential exposure, denial of service, privilege escalation, etc.).

## What you can expect

- Acknowledgement within **3 working days**.
- A triage decision (accepted / needs-more-info / declined) within **10 working days**.
- A coordinated disclosure timeline that respects your preference; defaults are a fix within 30 days for high-severity and 90 days for medium-severity.
- Public credit in the relevant ADR's "Reported by" section when the fix lands, if you would like it.
- We will not pursue legal action against security research conducted in good faith on this codebase.

## Out of scope

- Vulnerabilities in third-party providers (Anthropic, OpenAI, MiniMax, etc.) — report those to the upstream provider.
- Theoretical attacks requiring a configured credential the attacker does not already possess.
- Issues only reachable through disabling dry-run mode on a live adapter without the operator's knowledge (an attacker who controls the operator's config has already crossed the trust boundary).

## Disclosure

Once a fix is merged, a brief security note will be appended to [`CHANGELOG.md`](CHANGELOG.md) under a "Security" heading, describing the class of issue, the affected versions, and the fix — sufficient for downstream users to understand what changed without a working exploit.