# Domains

Business-domain knowledge for `llm-ccp`, one file per meaningful domain.

## Current domains

None yet. This project is an **LLM tool platform** (see `architecture.md`); the
`projects`/`tasks` models are example scaffolding, not a business domain, so
they do not get a domain file.

## When to add a domain file

Create a file here only when a real, meaningful domain emerges (e.g. users,
projects-as-product data, documents). Rule of thumb: a second agent should be
able to learn the domain's rules from this file without reading the code.

A domain file typically contains: purpose, important entities, business rules,
relationships, workflows, important constraints, project-specific knowledge.

Do not create one file per model or per module.