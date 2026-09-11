# Relay Rules

Use these conventions for every commit, branch, and pull request.

Describe concrete changes in commits and pull requests. Do not mention development
phases or phase numbers in their titles, subjects, or descriptions.

## Commits

- One-line subject only
- No trailing period
- No conventional-commit prefixes (`feat:`, `fix:`, `chore:`)
- No AI attribution, co-author trailers, or generated-by footers

Good:

```text
Add health endpoint and local stack
Persist workflow runs in Postgres
Recover abandoned steps after lease expiry
```

Bad:

```text
feat: add health endpoint.
Add health endpoint.

Co-Authored-By: Cursor <cursoragent@cursor.com>
```

## Branches and pull requests

Name them the way a person would describe the change. Do not mention AI, Cursor, Codex, or agents. Do not use `feat/`, `fix/`, `chore/`, or other prefix schemes.

Good:

```text
health-and-local-stack
persist-workflow-runs
recover-abandoned-steps
```

```text
Add health check and local stack
Persist workflow and step runs
```

Bad:

```text
feat/phase-1-health
cursor/add-health-check
ai-implement-phase-1
add-comprehensive-health-check-and-docker-compose-infrastructure
```

```text
feat: add health endpoint
AI-generated Phase 1 implementation
```
