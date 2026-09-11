---
name: project-context
class: discipline
description: >-
  Maintain an AI-readable knowledge layer for the project inside .agents/context/
  so agents understand the codebase without re-exploring it every session. Use
  when starting work on a project with no .agents/context (bootstrap), before
  substantial project work (read context first), after significant changes to
  architecture, domains, APIs, data models, auth, or infrastructure, or when the
  user asks to update, audit, or initialize the project context.
---

# Project Context

Maintain a compact, reliable knowledge layer in `.agents/context/`. The purpose is NOT to document the codebase — it is to capture the smallest amount of accurate information a new AI agent needs to understand the project and safely modify it without unnecessary exploration.

## Source Of Truth

Context is **derived knowledge**, never authoritative:

```text
Actual project > context configuration > existing context > framework defaults
```

If context conflicts with the actual project: verify the project, trust the project, correct the context, continue the task. Never modify source code to match outdated context. Never treat context as correct merely because it exists.

## When This Skill Applies

At session start and before substantial work, check whether `.agents/context/` exists.

- **Exists** → it is the project's memory; follow the reading workflow below.
- **Missing** → bootstrap it per [bootstrap.md](./references/bootstrap.md). Detect technology, check for a template, ask for preferences, then create the core files. Never make the user explain the system manually.
- Existing context is **never recreated** — preserve it and improve only when necessary.

## Context Layout

```text
.agents/context/
├── README.md      # entry point: what exists, how to use it, navigation
├── config.md      # update mode, detail level, scope, template
├── structure.md   # specification of which context files exist and why
└── domains/       # optional per-domain files (authentication.md, orders.md, ...)
```

The three core files have distinct responsibilities:

- `README.md` — concise entry point and navigation; never duplicates the rest of the context.
- `config.md` — defines how the AI must maintain the context.
- `structure.md` — the **context structure specification**: available files, their purpose, what belongs in each, when to create them, per-detail-level structures. Full contract: [structure.md](./references/structure.md).

## Reading Workflow

Before substantial coding work:

```text
README.md → config.md → structure.md → relevant context files → relevant source code
```

Do not blindly read every file. Use `structure.md` to select what is relevant (database task → database/architecture/domain files; API task → api/architecture; UI task → frontend conventions). Context reduces exploration; it never replaces verifying actual source code.

## Change Significance

After every meaningful coding task, ask: **did this change alter persistent project knowledge?**

| Level | Typical changes | Action |
|---|---|---|
| 0 | Formatting, typos, renames, minor CSS, internal refactors, temp/debug changes | Ignore |
| 1 | Helpers, reusable components, patterns, minor deps/conventions | Evaluate |
| 2 | Modules, APIs, DB entities, integrations, workflows, auth changes | Update |
| 3 | Architecture, DB/auth/deployment architecture, major rules/domains/systems | Mandatory |

Classification rules and examples: [change-detection.md](./references/change-detection.md).

## Update Modes

Configured in `config.md`; governs handling of detected Level 2–3 changes:

- **automatic** — update affected context immediately, verify consistency, continue the task.
- **ask** — report what changed, which files are affected, why it matters; wait for approval.
- **manual** — never modify automatically; report that context may need updating; act only on explicit request.

Update procedure: [synchronization.md](./references/synchronization.md).

Always update **only the smallest set of affected files** — never rewrite the whole context for a local change.

## User Overrides

Explicit instructions override detection:

- "update the context" → always update.
- "do not update context" → skip for this task unless correctness requires it.
- "audit the context" / "audit the project context" → run [audit.md](./references/audit.md).

## Anti-Patterns

| Anti-pattern | Do instead |
|---|---|
| Documentation dump — Markdown copy of the code | Capture why/what/rules/workflows, not inventories of classes or columns |
| Context worship — trusting without checking | Verify against source; correct context when it is wrong |
| Excessive updates on insignificant changes | Ignore Level 0 implementation details |
| Full rewrites after local changes | Touch only the affected files |
| Generic framework documentation | Document how THIS project uses the framework, not the framework itself |
| Leaving stale context uncorrected | Fix it whenever the configured update mode permits |

## Configuration

Update mode, detail level (`minimal` / `standard` / `detailed`), and scope are persisted in `config.md`. Semantics: [configuration.md](./references/configuration.md).

Framework-specific structures arrive as templates that populate `structure.md` — none are bundled with this skill: [templates.md](./references/templates.md).

## Principle

Better AI memory, not more documentation.
