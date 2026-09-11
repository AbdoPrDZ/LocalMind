# project-context Specification

## Intent

`project-context` is a `discipline`-class skill (an engineering practice not tied to one stack). It gives AI coding agents persistent, structured project understanding through a maintained knowledge layer in `.agents/context/`, so each session starts from reliable memory instead of full-codebase exploration.

This document is the design specification. Operational instructions live in `SKILL.md` and `references/`; they are not duplicated here.

## Why This Skill Exists

AI coding agents lose project understanding between sessions. Without persistent memory, every agent re-explores the codebase, re-derives the same architecture knowledge, and makes the same mistakes about conventions and constraints. Long-form code documentation rots because nobody maintains it.

The skill solves this by treating project context as a **maintained system** with an owner (the agent), a configuration (`config.md`), a contract (`structure.md`), and defined maintenance behavior — instead of ad-hoc markdown files that drift from reality.

## Philosophy

> Give a new AI coding agent the smallest amount of reliable information required to understand and safely modify the project.

Consequences:

- The skill optimizes for **fast AI onboarding**, not documentation completeness.
- Knowledge quality beats knowledge quantity: accuracy, discoverability, stability, conciseness, project-specificity, low maintenance overhead.
- Anything the compiler/test suite already enforces does not belong in context.
- If more documentation were the goal, the skill would be failing at its actual goal: better AI memory.

## Context Versus Source Of Truth

Context is **derived knowledge**. The source-of-truth priority is fixed:

```text
Actual project > context configuration > existing context > framework defaults
```

The actual project means: source code, database/schema, configuration, infrastructure/deployment configuration, tests, and other executable artifacts. When any context file contradicts them, the project wins and the context must be corrected — never the reverse. This rule exists because derived documentation inevitably drifts; the drift must resolve toward reality, and agents must never "fix" code to match stale notes.

## Lifecycle

```text
Bootstrap → Understand → Maintain → Synchronize → Audit
```

1. **Bootstrap** — first contact with a project lacking `.agents/context/`: analyze, configure, generate core files. Once per project.
2. **Understand** — read `README.md → config.md → structure.md → relevant files` before substantial work. Every session.
3. **Maintain** — after meaningful tasks, classify changes against persistent-knowledge criteria.
4. **Synchronize** — apply updates to the smallest affected set, governed by the configured update mode.
5. **Audit** — on request, compare the whole context against reality and repair drift.

## Configuration Model

Three axes persisted in `.agents/context/config.md`:

| Axis | Question it answers | Values |
|---|---|---|
| Update mode | Who decides when context changes? | automatic / ask / manual |
| Detail level | How much knowledge is captured? | minimal / standard / detailed |
| Scope | How wide does knowledge reach? | project / project + domains / deep |

Detail level controls the **amount of useful knowledge captured**, never accuracy, and never licenses documenting every file, function, or class. Value names may become configurable in the future; these three are the initial standard.

## Update Modes

The modes distribute authority differently:

- `automatic` trusts the change-detection model and keeps context current without friction.
- `ask` treats the user as reviewer of context edits — used when context shapes team-facing documentation.
- `manual` makes the agent a reporter only; nothing changes without explicit instruction.

In all modes the agent detects significant changes the same way; modes differ only in what happens next.

## Change Significance Model

Not every commit changes project knowledge. The four-level model (0 ignore / 1 evaluate / 2 significant / 3 mandatory) filters implementation noise out of the context system so the layer stays stable, reviewable, and cheap to maintain. The unit of classification is *persistent project knowledge* — architecture, domains, rules, workflows, integrations — not lines of code touched.

## Template Architecture

The skill itself is framework-agnostic. Framework-specific structure arrives through the project's own contract file:

```text
Laravel Context Template
        ↓ installs / populates
.agents/context/structure.md
        ↓ consumed by
this skill's bootstrap & maintenance behavior
```

A future external template installer may install or populate `structure.md`. The skill consumes whatever contract is present; if none matches the stack, it falls back to a generic structure derived from the actual project. No framework templates are bundled with this skill yet — establishing the generic skill and the `structure.md` contract comes first.

## Trigger Context

Should trigger:

- Starting work on a project where `.agents/context/` does not exist (bootstrap).
- Starting substantial work in a project where it does exist (read-first discipline).
- Completing changes to architecture, domains, APIs, data models, auth, queues, deployment, or infrastructure.
- "update the context", "audit the context", "initialize/audit the project context".

Should not trigger:

- Level 0 work: formatting, typo fixes, renames, small internal refactors.
- Tasks fully answerable from source without project-memory needs.
- Generic "explain this framework" questions unrelated to the specific project.

## Evidence Model

Authoritative sources:

- `SKILL.md` — runtime instructions and routing.
- `references/*.md` — detailed procedures (bootstrap, configuration, change detection, synchronization, structure contract, templates, audit).
- Target-project artifacts — always authoritative over anything this skill maintains.

Data that must never enter any maintained context file:

- Secrets, credentials, tokens.
- Machine-specific filesystem paths.
- Private URLs, customer data, unredacted personal information.

## Evaluation Gates

Before shipping changes to this skill:

- `SKILL.md` ≤ ~1K tokens preferred, 2K hard cap; critical rules front-loaded.
- Procedures referenced, not duplicated, across `SKILL.md` and `references/`.
- All internal links resolve.
- Configuration model internally consistent: modes ↔ synchronization behavior; levels ↔ detail expectations.
- Change-detection rules do not encourage documentation churn (Level 0 examples produce no writes).
- A fresh agent reading only `README.md → config.md → structure.md` of a bootstrapped context can locate relevant files for a task without directory-wide exploration.

## Future Extensibility

- Configurable names for detail levels and update modes.
- External template installers writing `structure.md` per framework.
- Domain template packs (payments, auth, notifications) as composable fragments.
- Team-shared contexts with review flows on top of the `manual`/`ask` modes.
- Tooling for drift detection (context-vs-code diffing) feeding the audit procedure.

## Known Limitations

<!-- Fill over time as drift surfaces: e.g., manual-mode contexts silently rotting
     when reports are ignored; multi-repo monorepo scope edge cases. -->

## Maintenance Notes

- Update `SPEC.md` when intent, philosophy, lifecycle, or extensibility change — not for operational tweaks.
- Update `SKILL.md` when runtime behavior or routing changes.
- Grow `references/` rather than letting `SKILL.md` exceed its token cap.
- Keep trigger phrasings synchronized between this spec and the frontmatter description.
