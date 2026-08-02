# Codex Orchestrator — Phase 5

Phase 5 adds an explicit delivery boundary after implementation and verification.
A verification-passed run waits for a second approval. The worker then reruns the
trusted verification commands and creates one local commit on the isolated run
worktree branch. It never pushes, opens a pull request, merges, deploys, or trades.

## Safety defaults

- `ORCH_CODEX_MODE=fake` remains the default, so local end-to-end tests use no Codex
  quota.
- Planning always runs read-only.
- Implementation, fixes, verification, and delivery operate only in a per-run Git
  worktree.
- Verification commands are registered locally by the administrator and execute
  without a shell.
- Delivery requires the latest run version and a Conventional Commit message.
- Verification runs again immediately before delivery.
- If verification changes the worktree, delivery fails instead of committing it.
- Delivery commits are idempotent and carry an `Orchestrator-Run` trailer.
- The orchestrator never pushes, opens a pull request, merges, deploys, or accesses
  live trading credentials.

## Phase 5 flow

```text
create_run
  -> PLAN
  -> awaiting_plan_approval
approve_plan
  -> IMPLEMENT in runtime/worktrees/<run-id>
  -> REVIEW using git diff --check + registered commands
  -> awaiting_delivery_approval
approve_delivery
  -> DELIVER
     -> rerun verification
     -> create one local commit on orchestrator/run-<run-id>
  -> completed

Verification failure before delivery:
  -> retry DELIVER within the configured attempt limit
  -> failed when attempts are exhausted
```

The delivery commit remains local. Inspect it manually and decide whether to push or
open a pull request outside the orchestrator.

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
  --verification-config verification.json
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
- `approve_delivery`
- `cancel_run`

Example delivery approval after `get_run` reports
`awaiting_delivery_approval`:

```json
{
  "run_id": "<run UUID>",
  "expected_version": 6,
  "commit_message": "feat(orchestrator): add quote lookup",
  "notes": "Verification reviewed"
}
```

Use the exact latest `version` returned by `get_run`. Supported commit types are
`feat`, `fix`, `refactor`, `test`, `docs`, `chore`, and `ci`.

With the default fake mode, planning and implementation use the deterministic fake
client. Delivery itself is a local Git operation and does not call Codex.
Set `ORCH_CODEX_MODE=live` only when intentionally performing a real Codex run.

## Validate

```powershell
ruff format --check .
ruff check .
pyright
pytest -q
```

The database stores run status, task kind, approval type, and artifact kind as strings,
so Phase 5 remains compatible with the existing schema and requires no new Alembic
migration.
