# Codex Orchestrator — Phase 6

Phase 6 adds an explicit publication boundary after verified local delivery. A run
never pushes automatically after implementation or delivery. It first waits in
`awaiting_publish_approval`, where the operator can either finish locally or approve
publication of the isolated run branch as a GitHub Draft Pull Request.

## Safety defaults

- `ORCH_CODEX_MODE=fake` remains the default, so planning and implementation use no
  Codex quota.
- `ORCH_GITHUB_PUBLISH_MODE=fake` is also the default. Fake publication performs no
  Git push and no GitHub API request.
- Planning is read-only.
- Implementation, fixes, verification, delivery, and publication use a per-run Git
  worktree and branch.
- Delivery reruns administrator-registered verification commands before committing.
- Publication requires another explicit approval using the latest run version.
- Live publication accepts only a clean orchestrator run branch whose HEAD equals the
  approved delivery commit and contains the matching `Orchestrator-Run` trailer.
- Live publication pushes only the isolated run branch without force-push.
- Phase 6 creates or reuses only a GitHub Draft Pull Request.
- The orchestrator never marks a PR ready, merges, deploys, or trades.
- `finish_run` completes a delivered run while keeping its branch local.

## Phase 6 flow

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
  -> awaiting_publish_approval

Option A: finish_run
  -> completed with no push and no pull request

Option B: approve_publish
  -> PUBLISH
     -> fake mode: record a simulated publication
     -> live mode: push the run branch and create or reuse a Draft PR
  -> completed
```

A failed publish task follows the existing bounded retry policy. A retry re-pushes the
same branch and reuses an existing open Draft PR when one already exists.

## Setup

```powershell
Copy-Item .env.example .env
python -m pip install -e ".[dev]"
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

Verification commands are argv arrays. Shell strings, pipes, redirects, and command
chaining are intentionally rejected.

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
- `approve_publish`
- `finish_run`
- `cancel_run`

## Zero-side-effect publication test

Keep both modes fake:

```env
ORCH_CODEX_MODE=fake
ORCH_GITHUB_PUBLISH_MODE=fake
```

After `get_run` reports `awaiting_publish_approval`, call:

```json
{
  "run_id": "<run UUID>",
  "expected_version": 8,
  "title": "test(orchestrator): simulate draft publication",
  "body": "Phase 6 fake publication test",
  "draft": true,
  "notes": "No remote side effects"
}
```

Use the exact latest `version` returned by `get_run`. The fake publisher records a
successful simulated publication without reading Git credentials or contacting GitHub.

## Live GitHub publication

Live publication is opt-in:

```env
ORCH_GITHUB_PUBLISH_MODE=live
ORCH_GITHUB_TOKEN=<secret token>
ORCH_GITHUB_REMOTE_NAME=origin
ORCH_GITHUB_API_URL=https://api.github.com
ORCH_GITHUB_API_VERSION=2026-03-10
```

The token is used only for GitHub REST API requests and is stored as a Pydantic
`SecretStr`. The configured Git remote must separately have permission to push the run
branch, for example through the existing Git Credential Manager or SSH configuration.
The REST token must be able to create pull requests in the target repository.

Phase 6 supports only `github.com` HTTPS and SSH remote formats. Credential-bearing
HTTPS remote URLs are rejected. Live publication requires a real delivery commit;
fake-mode no-op deliveries cannot be published live.

Example approval:

```json
{
  "run_id": "<run UUID>",
  "expected_version": 8,
  "title": "feat(trading): add quote lookup",
  "body": "## Summary\n\nAdd verified quote lookup support.",
  "draft": true,
  "notes": "Reviewed local delivery"
}
```

Supported title types are `feat`, `fix`, `refactor`, `test`, `docs`, `chore`, and
`ci`. The `draft` field is fixed to `true`; requesting a non-draft PR is rejected.

## Validate

```powershell
ruff format --check .
ruff check .
pyright
pytest -q
```

Run status, task kind, approval type, and artifact kind remain string columns, so Phase
6 requires no new Alembic migration.
