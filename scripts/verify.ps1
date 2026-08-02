param(
    [switch]$SkipInstall,
    [switch]$KeepPostgres
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$env:ORCH_ENVIRONMENT = "test"
$env:ORCH_DATABASE_URL = "postgresql+asyncpg://orchestrator:orchestrator@127.0.0.1:5433/orchestrator"
$env:ORCH_RUNTIME_DIR = Join-Path $repoRoot ".local-verify-runtime"
$env:ORCH_CODEX_MODE = "fake"
$env:ORCH_GITHUB_PUBLISH_MODE = "fake"
$env:ORCH_API_ENABLED = "false"

Write-Host "== Python =="
python --version

if (-not $SkipInstall) {
    Write-Host "== Install package and development dependencies =="
    python -m pip install -e ".[dev]"
}

Write-Host "== Start PostgreSQL =="
docker compose up -d postgres

try {
    Write-Host "== Wait for PostgreSQL =="
    $ready = $false
    foreach ($attempt in 1..30) {
        docker compose exec -T postgres pg_isready -U orchestrator -d orchestrator | Out-Null
        if ($LASTEXITCODE -eq 0) {
            $ready = $true
            break
        }
        Start-Sleep -Seconds 2
    }
    if (-not $ready) {
        throw "PostgreSQL did not become ready within 60 seconds."
    }

    Write-Host "== Apply migrations =="
    python -m alembic upgrade head

    Write-Host "== Ruff format check =="
    python -m ruff format --check .

    Write-Host "== Ruff lint =="
    python -m ruff check .

    Write-Host "== Pyright =="
    python -m pyright

    Write-Host "== Pytest =="
    python -m pytest -q

    Write-Host "All local verification checks passed."
}
finally {
    if (-not $KeepPostgres) {
        Write-Host "== Stop PostgreSQL =="
        docker compose down
    }
}
