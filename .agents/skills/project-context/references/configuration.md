# Configuration

The configuration model persisted in `.agents/context/config.md`. Three axes: update mode, detail level, scope. Optionally the template in use.

## File Format

```text
# Context Configuration

Update Mode: automatic

Detail Level: standard

Scope: project + domains

Template: laravel
```

If the user chooses different terminology for a value (e.g., "auto-update" for `automatic`), map it to the standard value and persist the standard name.

## Update Mode

Answers: **who decides when context changes?** Behavior when significant (Level 2–3) changes are detected:

| Mode | Behavior |
|---|---|
| `automatic` | Update affected context immediately; verify consistency; continue the task. No per-change permission requests. |
| `ask` | Detect and ask before modifying context. The question states what changed, which files are affected, why it matters. |
| `manual` | Never modify automatically. Report that context may need updating; update only on explicit request. |

Detailed runtime behavior per mode: [synchronization.md](./synchronization.md).

## Detail Level

Answers: **how much knowledge is captured?** It controls the amount of useful knowledge — never accuracy, and never a license to document every file, function, or class.

### Minimal

Only what another AI critically needs to understand the project:

- Project purpose.
- Main technologies.
- High-level architecture.
- Critical conventions.
- Major domains.
- Critical workflows/integrations.

Avoid implementation details, minor decisions, detailed domain internals.

### Standard

Important knowledge across the project:

- Architecture.
- Conventions.
- Database concepts.
- APIs.
- Workflows.
- Integrations.
- Domains.
- Important decisions.

Create only files relevant to the actual project.

### Detailed

Everything from Standard plus relevant:

- Domain relationships.
- Business rules.
- Complex workflows.
- Architectural decisions.
- Infrastructure.
- Important implementation patterns.
- Complex integrations.
- Domain-specific architecture.

Still never duplicate the source code.

## Scope

Answers: **how wide does knowledge reach?**

| Scope | Coverage |
|---|---|
| `project` | Project-wide knowledge only. |
| `project + domains` | Plus important business/technical domains (recommended default). |
| `deep` | Additionally important implementation-level knowledge. |

Even at `deep`, do not document every source file. The objective remains useful AI context, not a Markdown duplicate of the codebase.

## Extensibility Note

Value names may become configurable in the future; `automatic/ask/manual` and `minimal/standard/detailed` are the initial standard and must remain internally consistent with [change-detection.md](./change-detection.md) and [structure.md](./structure.md).
