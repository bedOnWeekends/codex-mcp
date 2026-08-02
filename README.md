# Codex Orchestrator — Phase 7

Phase 7 implements a token-efficient adaptive coding-agent workflow. The target is to
approach Sol medium-to-high reliability while avoiding Sol-scale token and price usage
on every step. After plan approval, a short Luna scout trajectory chooses the smallest
reliable execution shape. One Terra implementer is the default. Two or three Terra
implementers run in parallel only when repository evidence proves independent,
non-overlapping path groups. Sol is reserved for low-confidence review, high-risk code,
retries, and verification-driven fixes.

## Quality-per-token policy

```text
PLAN
  -> Luna-low SCOUT
     -> single: one Terra-high implementer                 [default]
     -> parallel: two or three independent Terra-high implementers
     -> optional Sol-medium reviewer                       [conditional]
  -> budget admission
     -> keep plan when projected tokens and cost fit
     -> collapse parallel scopes into one implementer when they do not fit
     -> reject instead of removing a required Sol quality gate
  -> deterministic integration
  -> deterministic verification
  -> Sol fix only after verification failure or escalation
  -> verified delivery and optional Draft PR publication
```

The controller, not the supervisor model, owns model selection, reviewer retention,
budget admission, and escalation. The supervisor cannot request extra agents or a more
expensive model directly.

### Deterministic routing

| Work | Model profile |
|---|---|
| Scout and topology selection | Luna, low effort |
| Normal plan | Terra, medium effort |
| Normal implementation | Terra, high effort |
| Low-confidence or high-risk review | Sol, medium effort |
| Critical implementation or repeated failure | Sol, high effort |
| Deterministic verification, integration, delivery | no model call |

Single-agent execution is preferred because coordination is not free. Parallel mode is
valid only for two or three independent implementers with non-overlapping ownership.
A separate Explorer agent is not created: the bounded Luna scout performs localization
and routing once. A reviewer is added when risk is high or critical, scout confidence
is below the configured threshold, or a three-way parallel plan needs an additional
integration check.

When the configured agent ceiling cannot hold both a parallel fan-out and a required
reviewer, the implementer scopes are combined so the Sol review slot is preserved.
When the projected token or dollar budget cannot hold a parallel plan, it is similarly
collapsed to one implementer. If the resulting single implementation plus required
review still does not fit, execution stops instead of silently reducing quality.

## Token and cost controls

- `ORCH_MAX_AGENTS_PER_RUN` accepts 2–4 and is a hard ceiling, not a target. A normal
  run still uses only one implementer.
- `ORCH_MAX_TOKENS_PER_RUN` is checked as `used + projected call tokens`, not only after
  the limit has already been reached.
- The complete Agent plan is admitted before fan-out using projected tokens and cost.
- Tier-specific projected token defaults are configurable with
  `ORCH_PROJECTED_CALL_TOKENS_CHEAP`, `DEFAULT`, and `CRITICAL`.
- Each live call also reserves a tier-specific projected dollar cost before invocation.
- Actual cost is computed from uncached input, cached-read input, prompt-cache-write
  input, and output tokens.
- Prompt-cache writes use the configurable
  `ORCH_CODEX_CACHE_WRITE_MULTIPLIER`, defaulting to 1.25× uncached input price.
- A task completion transaction is rejected when accumulated adaptive-agent cost would
  exceed the run's `max_cost_usd`.
- Dependency handoffs are schema-constrained JSON and clipped before reuse.
- Full diffs remain artifacts; downstream agents receive only compact summaries,
  changed-file manifests, commit references, material risks, and focused checks.
- Fix prompts include only the latest bounded verification tail.
- Codex structured outputs are used for Scout plans and Agent handoffs.

Pricing and projection defaults in `.env.example` are configurable operating estimates,
not permanent provider constants. Update them when model pricing or observed token use
changes. The database records task and agent input/output usage plus estimated billed
cost used by the budget gate.

## Safety defaults

- `ORCH_CODEX_MODE=fake` remains the default and performs no model calls.
- `ORCH_GITHUB_PUBLISH_MODE=fake` remains the default and performs no remote side
  effects.
- Every implementer uses an independent worktree and branch.
- Implementers may change only declared non-overlapping `owned_paths`.
- Reviewers are read-only.
- Ownership violations and integration conflicts fail instead of being auto-resolved.
- Integration stages implementer commits with `cherry-pick --no-commit`.
- Registered verification commands run after integration and again before delivery.
- Publication still requires a separate approval and creates only a Draft PR.
- The orchestrator never force-pushes, marks a PR ready, merges, deploys, or trades.

## Adaptive plan contract

The Luna scout returns one structured plan with:

- `mode`: `single` or `parallel`
- `confidence`: 0–1
- a short rationale
- one to three independent implementers with precise path ownership
- an optional reviewer declaration

The deterministic policy then adds or removes the reviewer based on risk and
confidence, preserves a required reviewer by collapsing implementers when necessary,
and performs full-plan budget admission. Plans are rejected for cycles, unknown
dependencies, sequential implementers, overlapping ownership, unsafe paths, or more
than four total agents.

## Durable execution

Each `agent_assignments` row records role, dependencies, ownership, worktree, Codex
thread, changed files, commit SHA, token usage, and estimated cost. Ready tasks are
claimed through PostgreSQL `FOR UPDATE SKIP LOCKED`. Each implementer produces one
cumulative local commit; retries amend that commit rather than creating an ambiguous
chain. The integration branch contains staged combined changes, and the existing
DELIVERY stage creates the one final verified run commit.

## Worktree layout

```text
runtime/worktrees/
├─ <run-id>/                         final integration and delivery worktree
└─ agents/<run-id>/<assignment-key>/ independent agent worktrees
```

Agent branches use `orchestrator/run-<run-id>/agent-<assignment-key>`. The final run
branch remains `orchestrator/run-<run-id>`.

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

## Run locally

```powershell
# terminal 1
orchestrator-server

# terminal 2
orchestrator-worker
```

MCP endpoint: `http://127.0.0.1:8000/mcp`

Public tools remain `list_repositories`, `create_run`, `get_run`, `approve_plan`,
`approve_delivery`, `approve_publish`, `finish_run`, and `cancel_run`. `get_run`
includes agent ownership, dependency, thread, commit, token, cost, and status data.

## Zero-cost integration check

```env
ORCH_CODEX_MODE=fake
ORCH_GITHUB_PUBLISH_MODE=fake
ORCH_MAX_PARALLEL_WORKERS=3
ORCH_MAX_AGENTS_PER_RUN=4
```

Fake mode selects the single-agent path and produces no file changes, model calls,
pushes, or GitHub API requests. The workflow still exercises durable supervision,
agent execution, integration, deterministic verification, delivery approval, and the
verified no-op delivery path.

## Live execution

Live Codex is opt-in:

```env
ORCH_CODEX_MODE=live
ORCH_CODEX_MODEL_CHEAP=gpt-5.6-luna
ORCH_CODEX_MODEL_DEFAULT=gpt-5.6-terra
ORCH_CODEX_MODEL_CRITICAL=gpt-5.6-sol
ORCH_PROJECTED_CALL_TOKENS_CHEAP=12000
ORCH_PROJECTED_CALL_TOKENS_DEFAULT=60000
ORCH_PROJECTED_CALL_TOKENS_CRITICAL=100000
```

The model IDs, effort levels, prices, cache-write multiplier, projected call reserves,
maximum tokens, and confidence threshold are explicit environment settings. Keep
GitHub publication fake unless remote Draft PR creation is intentionally approved.

## Validate

```powershell
python -m pip install -e ".[dev]"
alembic upgrade head
ruff format --check .
ruff check .
pyright
pytest -q
```
