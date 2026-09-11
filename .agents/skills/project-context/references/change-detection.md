# Change Detection

After completing a meaningful coding task, determine whether the change affects **persistent project knowledge** — architecture, domains, rules, workflows, integrations, decisions. The unit of classification is knowledge impact, not lines of code touched.

## Decision Question

> Did this change alter what another AI needs to know to understand and safely modify this project?

- No → Level 0. Stop.
- Unsure → treat as Level 1 and evaluate honestly.
- Yes → classify Level 2 or 3.

## Level 0 — Ignore

Implementation details, not persistent knowledge:

- Formatting.
- Typo fixes.
- Variable renaming.
- Minor CSS changes.
- Small internal refactors.
- Simple implementation bug fixes that do not alter architecture or behavior.
- Tests that do not introduce project-level knowledge.
- Temporary/debugging changes (diagnostic code, scratch scripts, local experiments).

## Level 1 — Evaluate

Consider updating; only do so if another AI would **materially benefit** from knowing:

- New helper abstractions.
- New reusable components.
- Minor dependency changes.
- New implementation patterns.
- Small convention changes.

## Level 2 — Significant

Normally update context:

- New modules.
- New major components.
- New APIs.
- New database entities.
- New external integrations.
- New business workflows.
- Authentication/authorization changes.
- Queue/background-processing architecture.
- Significant domain changes.
- Significant dependency changes.

## Level 3 — Mandatory

Always consider context synchronization:

- Architecture changes.
- Database architecture changes.
- Authentication architecture changes.
- Authorization architecture changes.
- Major business-rule changes.
- Major workflow/state-machine changes.
- Deployment architecture changes.
- Framework/technology changes.
- Major system additions/removals.
- Major domain restructuring.

## Applying The Classification

Level 2–3 changes are handled according to the configured update mode — see [synchronization.md](./synchronization.md). Level 0 produces no writes in every mode; this is what keeps the context stable, reviewable, and free of documentation churn.

Temporary/debugging changes deserve emphasis: diagnostic code that is later removed never touches context at either insertion or removal time.
