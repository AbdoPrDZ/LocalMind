# Templates

How framework-specific context structures work without making this skill framework-specific.

## Principle

The skill is framework-agnostic. It contains **no bundled templates** and no framework-specific rules. Framework knowledge enters exclusively through the project's own contract file:

```text
Laravel Context Template
        ↓ installs / populates
.agents/context/structure.md
        ↓ consumed by
this skill's bootstrap & maintenance behavior
```

The skill reads whatever `structure.md` is present and follows it. This separation means templates can evolve independently — a future external template installer may install or populate the file, and this skill's behavior does not change.

## What A Template May Define

A template populates `structure.md` with:

- Minimal structure (files required at detail level `minimal`).
- Standard structure.
- Detailed structure.
- File responsibilities (what belongs / does not belong in each file).
- Domain organization.
- Framework-specific context categories (e.g., Eloquent models, Odoo modules, RSC boundaries).

Templates define structure only. They do not contain project content — the actual context is always generated from analysis of the real project.

## Installation Rules

1. **Never blindly overwrite existing project knowledge.** If a context already exists, preserve its information and merge the template structure carefully.
2. A newly installed template takes effect from the next bootstrap or structure change; it does not force an immediate rewrite of valid existing files.
3. After a template merge, verify the result still matches the configured detail level and scope.

## Generic Fallback

When no template matches the detected stack:

- Build a generic project-context structure based on the actual project ([bootstrap.md](./bootstrap.md)).
- Do not invent framework-specific behavior, categories, or file names that the project does not exhibit.
- If a project deviates from its framework's conventions, document the project's actual architecture — never the framework's textbook architecture.

## Status

No framework templates are shipped with this skill. Establishing the generic skill and the `structure.md` contract comes first; Laravel/Odoo/React/etc. templates are separate future work delivered as installers that populate `structure.md`.
