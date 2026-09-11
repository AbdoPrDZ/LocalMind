# Context Structure Specification

The file layout contract for `.agents/context/`. Files are instances of these
rules; change this file only when the context *organization* changes.

## Available files and their purpose

| File | Purpose |
|---|---|
| `README.md` | Entry point. What exists, how to use the context, navigation by task type. |
| `config.md` | How the AI maintains the context (update mode, detail level, scope, template). |
| `structure.md` | This contract. |
| `architecture.md` | Layered architecture and data flow of the whole system. |
| `database.md` | Database setup, model registry, dynamically generated CRUD tools. |
| `api.md` | The shared LLM interface: `Chat` service, `Agent`, `Tool`. The contract every app uses to reach the model. |
| `workflows.md` | Launch flows, chat pipeline, tool-calling loop. |
| `conventions.md` | Code style and structural conventions to follow when touching code. |
| `domains/` | Business-domain knowledge, one file per meaningful domain. |

## What belongs / does not belong

- `architecture.md` — layers, module responsibilities, data flow. No per-file inventories.
- `database.md` — engine wiring, registry mechanics, tool generation rules. No column-by-column listings unless a domain needs them.
- `api.md` — the public contract between apps and the LLM. Not framework docs.
- `workflows.md` — end-to-end flows (launch, chat, tool calls). Not function listings.
- `conventions.md` — rules enforced across the codebase.
- `domains/*.md` — purpose, entities, business rules, workflows of a domain.
- Never: per-class/per-function/per-endpoint/per-column inventories, generic framework documentation, or unstable implementation details.

## Detail levels

- **minimal**: `README.md`, `config.md`, `structure.md`, `architecture.md`
- **standard** (current): adds `database.md`, `api.md`, `workflows.md`, `conventions.md`, `domains/`
- **detailed**: adds `decisions.md`, `infrastructure.md`, `integrations.md`

Files outside the current level must not exist; files inside it stay within
their declared responsibilities.

## Domains

Rule: create a domain file only for meaningful domains — never one per module
or table. A domain file contains: purpose, important entities, business rules,
relationships, workflows, important constraints, project-specific knowledge.

`domains/README.md` lists domains and when to add a new one.