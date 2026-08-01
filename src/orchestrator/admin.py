from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from .database import create_database
from .errors import OrchestratorError
from .schemas import RepositoryCreate
from .settings import get_settings
from .store import Store

app = typer.Typer(no_args_is_help=True, help="Local orchestrator administration.")
repository_app = typer.Typer(
    no_args_is_help=True,
    help="Manage registered repositories.",
)
app.add_typer(repository_app, name="repository")


async def _validate_git_repository(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_dir():
        raise typer.BadParameter(f"Directory does not exist: {resolved}")
    process = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        str(resolved),
        "rev-parse",
        "--show-toplevel",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        detail = stderr.decode(errors="replace").strip()
        raise typer.BadParameter(detail or f"Not a Git repository: {resolved}")
    repository_root = Path(stdout.decode().strip()).resolve()
    if repository_root != resolved:
        raise typer.BadParameter(
            f"Register the Git root instead: {repository_root}"
        )
    return resolved


async def _add_repository(
    *,
    name: str,
    path: Path,
    default_branch: str,
) -> None:
    root = await _validate_git_repository(path)
    settings = get_settings()
    database = create_database(settings)
    try:
        store = Store(database.session_factory)
        repository = await store.create_repository(
            RepositoryCreate(
                name=name,
                root_path=root,
                default_branch=default_branch,
            )
        )
        typer.echo(f"Registered {repository.name}: {repository.root_path}")
    except OrchestratorError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    finally:
        await database.close()


@repository_app.command("add")
def add_repository(
    name: str = typer.Option(..., "--name"),
    path: Path = typer.Option(..., "--path", exists=True, file_okay=False),
    default_branch: str = typer.Option("main", "--default-branch"),
) -> None:
    """Register a local Git repository for MCP-run creation."""
    asyncio.run(
        _add_repository(name=name, path=path, default_branch=default_branch)
    )


async def _list_repositories() -> None:
    settings = get_settings()
    database = create_database(settings)
    try:
        repositories = await Store(database.session_factory).list_repositories()
        if not repositories:
            typer.echo("No repositories registered.")
            return
        for repository in repositories:
            typer.echo(
                f"{repository.name}\t{repository.default_branch}\t"
                f"{repository.root_path}"
            )
    finally:
        await database.close()


@repository_app.command("list")
def list_repositories() -> None:
    """List locally registered repositories."""
    asyncio.run(_list_repositories())


if __name__ == "__main__":
    app()
