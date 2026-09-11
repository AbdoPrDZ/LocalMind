# Bootstrap

How to create the context system when a project has none.

## Trigger Conditions

Bootstrap when **all** of these hold:

- `.agents/context/` does not exist.
- The user asks for coding work on the project.
- No existing context system is present under another name.

Do not require the user to manually explain the context system. Bootstrap it according to this skill.

If `.agents/context/` already exists, do not bootstrap and do not recreate anything — read it, respect its established structure, improve incomplete files only when necessary.

## Procedure

1. **Detect the project technology/framework** from manifests, config, and source (e.g., Laravel, React, Flutter, Odoo, Django, Node.js, generic/custom).
2. **Check whether a suitable context structure/template is available** — an installed template's `structure.md`, or fall back to generic structure (see Fallback below).
3. **Ask the user for preferences**, unless already configured:
   - Update mode (`automatic` / `ask` / `manual`).
   - Detail level (`minimal` / `standard` / `detailed`).
   - Scope (`project` / `project + domains` / `deep`).
4. **Create `.agents/context/`.**
5. **Create the core files:** `README.md`, `config.md`, `structure.md`.
6. **Analyze the project at a high level:** project type, framework, main technologies, architecture, major domains, important integrations, existing conventions.
7. **Generate appropriate context files according to `structure.md`** at the configured detail level and scope.
8. **Stop before documenting the entire codebase.** The bootstrap output is the minimal reliable layer, not an inventory.

## Persistence

Persist the chosen configuration in `config.md` before generating content:

```text
# Context Configuration

Update Mode: automatic

Detail Level: standard

Scope: project + domains

Template: laravel
```

Semantics of each axis: [configuration.md](./configuration.md).

## Generic Fallback

When no framework-specific structure matches the detected stack:

- Build a generic project-context structure based on the **actual project**.
- Do not invent framework-specific rules or categories that the project does not exhibit.
- Document the architecture the project actually uses, even if it deviates from framework conventions.

Template handling details: [templates.md](./templates.md). The formal contract for what gets written into `structure.md`: [structure.md](./structure.md).
