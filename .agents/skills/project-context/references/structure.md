# Structure Contract

The formal specification for `.agents/context/structure.md` — the file this skill both consumes and maintains.

## What structure.md Is

`structure.md` is the **context structure specification**, not a listing of current files. It defines the rules the context must follow; the other files are instances of those rules.

It is itself part of the maintained context: when the context organization changes, `structure.md` changes with it (see [synchronization.md](./synchronization.md)).

## Required Contents

A complete `structure.md` describes:

1. **Available context files/directories** — every file and directory that may exist, including optional ones.
2. **Purpose of each** — one clear responsibility per file; no overlapping homes for the same knowledge.
3. **What information belongs in each** — content expectations.
4. **What information does not belong** — explicit exclusions to prevent duplication across files.
5. **Conditions for creating files** — when a file comes into existence (e.g., "create `payments.md` when a second payment provider appears"), so files are created deliberately, not speculatively.
6. **Minimal structure** — files required at detail level `minimal`.
7. **Standard structure** — files required at detail level `standard`.
8. **Detailed structure** — additions at detail level `detailed`.
9. **Domain organization where applicable** — rules for the `domains/` directory.

## Core File Responsibilities

The three core files always exist; their responsibilities are fixed:

### README.md

Entry point. Explains:

- What the context is.
- How an AI agent should use it.
- Which context files exist.
- How to navigate the context (what to read per task type).

Concise. Must not duplicate the entire context — it routes, it does not contain.

### config.md

Defines how the AI should maintain the context:

- Update Mode.
- Detail Level.
- Scope.
- Template.

Semantics defined in [configuration.md](./configuration.md).

### structure.md

This contract. Changes only when the organization of the context changes, never on mere content updates.

## Domain Organization

For multi-domain projects:

```text
.agents/context/
└── domains/
    ├── README.md
    ├── authentication.md
    ├── orders.md
    ├── payments.md
    └── notifications.md
```

Rules:

- Create domain context only for meaningful domains — never one file per module or table.
- A domain file normally contains: purpose, important entities, business rules, relationships, workflows, important integrations, important constraints, project-specific implementation knowledge.
- Exact contents follow what `structure.md` specifies for domain files.
- Promote files into `domains/` only via a deliberate structure change ([synchronization.md](./synchronization.md)).

## Per-Detail-Level Structures

`structure.md` must make detail levels operational by stating which files exist at each level. Guidance for what belongs at each level: [configuration.md](./configuration.md).

Example shape (generic project):

```text
minimal:   README.md, config.md, structure.md, architecture.md
standard:  + database.md, api.md, workflows.md, conventions.md
detailed:  + decisions.md, infrastructure.md, integrations.md, domains/
```

Files outside the current level's set must not exist; files inside it must stay within their declared responsibilities.

## Anti-Goals

- No per-class, per-function, per-endpoint, or per-column inventories.
- No generic framework documentation.
- No temporary implementation details — unstable knowledge does not belong in the specification-driven layer.
