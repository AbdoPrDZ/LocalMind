# Audit

An explicit, on-request comparison of the maintained context against the actual project. Triggered by phrases like "audit the context" or "audit the project context".

## Procedure

1. Read `README.md`.
2. Read `config.md`.
3. Read `structure.md`.
4. Compare context against the actual project (inspect the relevant architecture).
5. Find outdated information.
6. Find missing important information.
7. Find contradictions.
8. Find unnecessary/outdated context files.
9. Report findings.
10. Update according to the configured update mode or explicit user request.

## Scope Rule

An audit does not inspect every source file unless necessary. Focus on the architecture and knowledge areas described by the existing context; sample-verify where coverage is broad.

## Applying Findings

- **Outdated** → correct it; the actual project is authoritative.
- **Missing** → add only what the configured detail level and scope justify ([configuration.md](./configuration.md)).
- **Contradictory** → resolve in favor of the actual project, then fix whichever source produced the contradiction.
- **Unnecessary files** → propose removal; follow the update mode before deleting.

If findings require reorganizing the context (splitting files, creating domains), treat that as a structure change per [synchronization.md](./synchronization.md) and update `structure.md` accordingly.
