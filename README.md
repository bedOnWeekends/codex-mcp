# Codex Orchestrator — Phase 4

Phase 4 turns an approved plan into an isolated implementation and verification
workflow. The public MCP control plane can approve a plan, queue implementation,
run registered verification commands, and request bounded fixes without merging or
pushing code automatically.

## Safety defaults

- `ORCH_CODEX_MODE=fake` is the default, so local end-to-end tests use no Codex quota.
- Planning always runs read-only.
- Implementation and fixes run only inside a per-run Git worktree.
- Verification commands are registered locally by the administrator and execute
  without a shell.
- The orchestrator never commits, merges, pushes, deploys, or accesses live trading
  credentials.
- A run completes after verification succeeds; the resulting worktree remains for
  manual inspection.

## Phase 4 flow

```text
create_run
  -> PLAN
  -> awaiting_plan_approval
approve_plan
  -> IMPLEMENT in runtime/worktrees/<run-id>
  -> REVIEW using git diff --check + registered commands
  -> COMPLETED
     or FIX -> REVIEW (bounded by ORCH_MAX_FIX_CYCLES)
```

## Setup

```powershell
Copy-Item .env.example .env
python -m pip install -e .
docker compose up -d postgres
alembic upgrade head
```

Register the target repository and optional verification commands:

```powershell
orchestrator-admin repository add `
  --name toss-trader `
  --path "C:\Users\dbals\Documents\toss-trader" `
  --default-branch main `
  --verification-file verification.json
```

Example `verification.json`:

```json
[
  {
    "name": "tests",
    "command": ["python", "-m", "pytest", "-q"],
    "timeout_seconds": 300
  }
]
```

Commands are argv arrays. Shell strings such as `"pytest -q"`, pipes, redirects,
and command chaining are intentionally rejected.

## Run locally

Terminal 1:

```powershell
orchestrator-server
```

Terminal 2:

```powershell
orchestrator-worker
```

Inspector URL:

```text
http://127.0.0.1:8000/mcp
```

Public MCP tools:

- `list_repositories`
- `create_run`
- `get_run`
- `approve_plan`
- `cancel_run`

With the default fake mode, the complete state transition can be tested without an
external model call. Set `ORCH_CODEX_MODE=live` only when intentionally performing
a real Codex run.

## Validate

```powershell
ruff format --check .
ruff check .
pyright
pytest -q
```

The repository schema remains compatible with Phase 3; no new Alembic migration is
required for this phase.
