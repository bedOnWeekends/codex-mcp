# Codex Orchestrator — Phase 3.1 (Windows/PostgreSQL 18 corrected)

Phase 3 adds a durable Codex worker that claims queued tasks with PostgreSQL
`FOR UPDATE SKIP LOCKED`, builds repository context, runs/resumes a Codex thread,
and persists the planning result. PLAN completion moves the Run to
`awaiting_plan_approval`.

## Corrections included

- PostgreSQL 18 volume mounted at `/var/lib/postgresql`
- host port `5433` mapped to container port `5432`
- `PGDATA=/var/lib/postgresql/18/docker`
- Alembic `prepend_sys_path=%(here)s/src`
- Alembic uses synchronous `psycopg`; application remains on `asyncpg`
- `psycopg[binary]` declared as a project dependency

## Upgrade from Phase 2

Do not delete the PostgreSQL volume. Back up your local `.env`, then overlay the
Phase 3 files. Keep this database URL:

```env
ORCH_DATABASE_URL=postgresql+asyncpg://orchestrator:orchestrator@127.0.0.1:5433/orchestrator
```

```powershell
python -m pip install -e .
docker compose up -d postgres
alembic upgrade head
orchestrator-server
```

Run the worker in another terminal:

```powershell
orchestrator-worker
```

The Codex adapter imports the installed `openai-codex` SDK lazily. Model values
are optional and can be configured through `ORCH_CODEX_MODEL_CHEAP`,
`ORCH_CODEX_MODEL_DEFAULT`, and `ORCH_CODEX_MODEL_CRITICAL`.
