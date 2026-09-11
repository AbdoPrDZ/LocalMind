# Synchronization

How detected significant changes (Level 2–3 from [change-detection.md](./change-detection.md)) become context updates, governed by the configured update mode.

## Mode Behavior

### automatic

1. Identify affected context.
2. Update it.
3. Verify consistency.
4. Continue/finish the task.

Do not ask the user for permission for every context update.

### ask

Ask before modifying context. The question must state:

- **What changed** in the project.
- **Which context files** may be affected.
- **Why the change matters** for future agent work.

Do not ask for insignificant changes — Level 0/1 noise never reaches the user.

### manual

Never silently modify context. Report instead:

```text
Context may need updating:
- architecture.md
- domains/orders.md
```

Include a one-line reason per file when helpful. Only update after the user explicitly requests it; then perform the full synchronization below.

## Update Only Affected Context

Never rewrite the entire context after a change. Identify the smallest set of affected files:

```text
New payment workflow
    ↓
workflows.md
payments.md
possibly architecture.md
```

Do not touch unrelated context (`database.md`, `frontend.md`, `deployment.md`) unless the change actually affects them. This keeps context stable, reviewable, and cheap to maintain.

## Update Procedure

1. Identify what project knowledge changed.
2. Identify which context files describe that knowledge.
3. Read the relevant context files.
4. Verify the new behavior against the actual project.
5. Update only the affected context.
6. Check for contradictions with other context files.
7. Update related context if necessary.
8. Update `README.md` if navigation changed.
9. Update `structure.md` if the context organization changed.
10. Keep documentation concise and useful.

## Content Change Vs Structure Change

Distinguish two kinds of updates:

**Content change** — existing structure remains, information changes:

```text
architecture.md changes because the architecture changed
```

Follow the procedure above.

**Structure change** — the organization of the context itself changes:

- A new major domain requires a new context file or `domains/` directory.
- A file becomes too large and needs splitting.
- Several related files should be grouped into a domain directory.
- A new category of project knowledge is introduced.

When structure changes:

1. Update/create the relevant context files.
2. Update `structure.md`.
3. Update `README.md` navigation if necessary.

Ensure the resulting organization still matches the configured detail level and scope ([configuration.md](./configuration.md)); the formal contract lives in [structure.md](./structure.md).

## User Overrides

Explicit instructions override detection:

- "update the context" → update regardless of level.
- "do not update context" → no updates this task unless correctness/safety requires them.
- "audit the context" → run [audit.md](./audit.md).
