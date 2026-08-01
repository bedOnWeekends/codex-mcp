# Codex Orchestrator — Phase 2

Phase 2 exposes the persistent core through a Streamable HTTP MCP control plane.
It still does **not** invoke Codex; it creates and manages durable Run/Task state
that phase 3 workers will consume.

## Included

- FastMCP Streamable HTTP endpoint at `/mcp`
- `list_repositories`, `create_run`, `get_run`, `cancel_run`
- structured Pydantic tool input/output
- optimistic concurrency through `expected_version`
- atomic Run + initial planning Task creation
- transactional cancellation of active tasks
- liveness and PostgreSQL readiness routes
- local-only repository administration CLI

## Public MCP tools

| Tool | Effect |
|---|---|
| `list_repositories` | Read registered repository names |
| `create_run` | Create a Run and queue its planning Task |
| `get_run` | Read Run state, version, budget, and Task summaries |
| `cancel_run` | Cancel a non-terminal Run using optimistic locking |

Repository paths are never accepted from MCP callers. A local administrator must
register each Git repository first.

## Setup

```powershell
Copy-Item .env.example .env
pip install -e . --no-deps --no-build-isolation
docker compose up -d postgres
alembic upgrade head
```

Register a local Git repository:

```powershell
orchestrator-admin repository add `
  --name toss-trader `
  --path C:\dev\toss-trader `
  --default-branch main
```

List registrations:

```powershell
orchestrator-admin repository list
```

## Run the MCP server

```powershell
orchestrator-server
```

Endpoints:

```text
http://127.0.0.1:8000/mcp
http://127.0.0.1:8000/health/live
http://127.0.0.1:8000/health/ready
```

## MCP Inspector

```powershell
npx @modelcontextprotocol/inspector
```

Connect with Streamable HTTP to:

```text
http://127.0.0.1:8000/mcp
```

Suggested manual sequence:

1. `list_repositories`
2. `create_run`
3. `get_run`
4. call `cancel_run` with the exact `version` returned by `get_run`

## Validate

```powershell
ruff format --check .
ruff check .
pyright
pytest -q
```

## Phase-2 boundaries

- no Codex SDK invocation
- no scheduler or worker loop
- no plan approval tool yet
- no Git worktree creation
- no automatic execution after a planning Task is queued
- PostgreSQL remains the authoritative state store
