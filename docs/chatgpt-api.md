# ChatGPT auto-PR API

The REST control plane exposes one high-level operation: create a bounded orchestration
run that advances through plan approval, verified delivery, branch publication, and a
GitHub **Draft** pull request. It never marks the pull request ready, merges, force
pushes, deploys, or trades.

The API is disabled by default. When it is enabled, the same bearer key also protects
the `/mcp` endpoint so a Cloudflare Tunnel cannot expose unauthenticated approval tools.

## Enable the local API

Generate a random bearer key with at least 32 characters and set the following values
in `.env`:

```env
ORCH_API_ENABLED=true
ORCH_API_KEY=replace-with-a-long-random-secret
ORCH_API_PREFIX=/api/v1
ORCH_API_DOCS_ENABLED=true
ORCH_CODEX_MODE=live
ORCH_GITHUB_PUBLISH_MODE=live
ORCH_GITHUB_TOKEN=replace-with-a-fine-grained-token
```

Run the migration before restarting the server:

```powershell
alembic upgrade head
orchestrator-server
```

The OpenAPI document is then available at:

```text
http://127.0.0.1:8000/api/v1/openapi.json
```

A separate `orchestrator-worker` process must be running because model, verification,
delivery, and publication tasks are executed from the durable PostgreSQL queue.

## API operations

### Start an auto-PR run

```http
POST /api/v1/runs
Authorization: Bearer <ORCH_API_KEY>
Idempotency-Key: <unique-request-id>
Content-Type: application/json
```

```json
{
  "repository": "toss-trader",
  "goal": "Add validated paper-trading order request models.",
  "constraints": [
    "Do not enable live trading.",
    "Run pytest, Ruff, and Pyright."
  ],
  "risk_level": "normal",
  "execution_mode": "auto_pr",
  "max_cost_usd": "3.00",
  "commit_message": "feat(orders): add paper order models",
  "pull_request_title": "feat(orders): add paper order models",
  "pull_request_body": "Adds validated paper-only order request models and tests."
}
```

Only repositories registered with `orchestrator-admin repository add` can be selected.
The API accepts only `low` and `normal` risk runs. A repeated idempotency key returns the
original run when the request body is identical and returns HTTP 409 when it differs.

### Read status

```http
GET /api/v1/runs/{run_id}
Authorization: Bearer <ORCH_API_KEY>
```

The response includes the authoritative run state, tasks, agents, automation status,
commit, branch, and Draft PR URL when publication completes.

### Cancel a run

```http
POST /api/v1/runs/{run_id}/cancel
Authorization: Bearer <ORCH_API_KEY>
Content-Type: application/json
```

```json
{
  "expected_version": 7,
  "reason": "Requirements changed."
}
```

The version must be the latest value returned by the status endpoint.

## Cloudflare Tunnel

Keep the API bound to loopback and publish it through a named Cloudflare Tunnel:

```powershell
cloudflared tunnel login
cloudflared tunnel create codex-orchestrator
cloudflared tunnel route dns codex-orchestrator codex-api.example.com
```

Example `config.yml`:

```yaml
tunnel: <tunnel-id>
credentials-file: C:\Users\you\.cloudflared\<tunnel-id>.json
ingress:
  - hostname: codex-api.example.com
    service: http://127.0.0.1:8000
  - service: http_status:404
```

Start it with:

```powershell
cloudflared tunnel run codex-orchestrator
```

Register `https://codex-api.example.com/api/v1/openapi.json` as the Custom GPT Action
schema and configure the same bearer key in the Action authentication settings.

## Safety boundary

- REST callers cannot provide a filesystem path or arbitrary shell command.
- Repository access is restricted to the administrator-maintained repository registry.
- Automatic approval stops on `awaiting_revision`, `failed`, or `canceled` states.
- Delivery verification still runs before the final local commit.
- Publication is always a Draft PR and never a merge.
- API credentials and GitHub tokens remain environment secrets and must not be committed.
