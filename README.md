# Codex Orchestrator — Phase 7

Phase 7 replaces the single implementation task with a durable multi-agent workflow.
After plan approval, a read-only supervisor produces a validated dependency DAG. Ready
agents execute in parallel through the existing PostgreSQL queue, each with an
independent Codex thread, Git worktree, role contract, and file-ownership boundary.
The integrator collects implementer commits without creating the final commit, then
reuses the existing verification, delivery, and Draft PR publication boundaries.

## Safety defaults

- `ORCH_CODEX_MODE=fake` remains the default. The deterministic fake supervisor creates
  a four-agent DAG without consuming Codex quota.
- `ORCH_GITHUB_PUBLISH_MODE=fake` remains the default and performs no remote side
  effects.
- The supervisor, explorers, and reviewers always run read-only.
- Implementers may modify only their declared non-overlapping `owned_paths`.
- Every agent uses its own worktree and branch.
- Dependency commits are applied only inside dependent agent worktrees.
- Integration conflicts and ownership violations fail instead of being auto-resolved.
- Agent retries amend one cumulative agent commit rather than creating an ambiguous
  commit chain.
- Final integration stages all implementer changes with `cherry-pick --no-commit`.
- The existing REVIEW and DELIVERY stages verify and create one final run commit.
- Publication still requires a separate approval and creates only a Draft PR.
- The orchestrator never force-pushes, marks a PR ready, merges, deploys, or trades.

## Phase 7 flow

```text
create_run
  -> PLAN (read-only)
  -> awaiting_plan_approval
approve_plan
  -> SUPERVISE (read-only)
     -> validate roles, DAG, dependencies, and path ownership
  -> EXECUTING
     -> EXPLORER agents (read-only)
     -> ready IMPLEMENTER agents in parallel
     -> REVIEWER agents after all implementers
  -> INTEGRATING
     -> stage implementer commits in topological order
  -> REVIEW
     -> administrator-registered verification commands
  -> awaiting_delivery_approval
approve_delivery
  -> rerun verification
  -> create one final local run commit
  -> awaiting_publish_approval
finish_run or approve_publish
  -> completed
```

The global `ORCH_MAX_PARALLEL_WORKERS` setting bounds actual concurrency. A run may
contain at most `ORCH_MAX_AGENTS_PER_RUN` assignments, currently restricted to 3–8.

## Durable agent contract

Each row in `agent_assignments` records:

- stable assignment key and role
- dependency keys and owned path prefixes
- task and worktree identity
- independent Codex thread
- status, token usage, and estimated cost
- changed files and local agent commit SHA

A supervisor plan is rejected when it contains cycles, unknown dependencies,
overlapping implementer ownership, read-only path ownership, or a reviewer that does
not depend on every implementer.

## Worktree layout

```text
runtime/worktrees/
├─ <run-id>/
│  └─ final integration and delivery worktree
└─ agents/
   └─ <run-id>/
      ├─ explore-codebase/
      ├─ implement-source/
      ├─ implement-tests/
      └─ review-integration/
```

Agent branches use:

```text
orchestrator/run-<run-id>/agent-<assignment-key>
```

The final run branch remains:

```text
orchestrator/run-<run-id>
```

## Setup and migration

```powershell
Copy-Item .env.example .env
python -m pip install -e ".[dev]"
docker compose up -d postgres
alembic upgrade head
```

Phase 7 adds migration `0002_agent_assignments.py`. Existing Phase 6 databases must run
`alembic upgrade head` before starting the server or worker.

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

Public MCP tools remain:

- `list_repositories`
- `create_run`
- `get_run`
- `approve_plan`
- `approve_delivery`
- `approve_publish`
- `finish_run`
- `cancel_run`

`get_run` now includes an `agents` array with dependency, ownership, worktree, thread,
commit, usage, and status details.

## Zero-cost multi-agent check

Keep both external modes fake:

```env
ORCH_CODEX_MODE=fake
ORCH_GITHUB_PUBLISH_MODE=fake
ORCH_MAX_PARALLEL_WORKERS=3
ORCH_MAX_AGENTS_PER_RUN=8
```

The fake supervisor creates:

```text
explore-codebase
├─ implement-source  (owns src)
└─ implement-tests   (owns tests)
   └─ review-integration depends on both implementers
```

The two implementers become queue-ready together and can be claimed by separate
workers. Fake agents make no files, so the run completes through a verified no-op
delivery without modifying the target repository or contacting GitHub.

## Live modes

Live Codex execution remains opt-in:

```env
ORCH_CODEX_MODE=live
```

Live GitHub Draft PR publication is independently opt-in:

```env
ORCH_GITHUB_PUBLISH_MODE=live
ORCH_GITHUB_TOKEN=<secret token>
ORCH_GITHUB_REMOTE_NAME=origin
ORCH_GITHUB_API_URL=https://api.github.com
ORCH_GITHUB_API_VERSION=2026-03-10
```

Do not enable either live mode during the Phase 7 local quality check.

## Validate

```powershell
python -m pip install -e ".[dev]"
alembic upgrade head
ruff format --check .
ruff check .
pyright
pytest -q
```
