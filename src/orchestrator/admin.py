from __future__ import annotations

import asyncio
from pathlib import Path

import typer
from pydantic import TypeAdapter, ValidationError

from .database import create_database
from .errors import OrchestratorError
from .phase4_store import Phase4Store
from .schemas import RepositoryCreate, VerificationCommandSpec
from .settings import get_settings

app = typer.Typer(no_args_is_help=True, help="Local orchestrator administration.")
repository_app = typer.Typer(
    no_args_is_help=True,
    help="Manage registered repositories.",
)
app.add_typer(repository_app, name="repository")
_VERIFICATION_ADAPTER = TypeAdapter(list[VerificationCommandSpec])


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
        raise typer.BadParameter(f"Register the Git root instead: {repository_root}")
    return resolved


def _load_verification_config(path: Path | None) -> list[VerificationCommandSpec]:
    if path is None:
        return []
    try:
        return _VERIFICATION_ADAPTER.validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise typer.BadParameter(f"Invalid verification config {path}: {exc}") from exc


async def _add_repository(
    *,
    name: str,
    path: Path,
    default_branch: str,
    verification_config: list[VerificationCommandSpec],
) -> None:
    root = await _validate_git_repository(path)
    database = create_database(get_settings())
    try:
        repository = await Phase4Store(database.session_factory).create_repository(
            RepositoryCreate(
                name=name,
                root_path=root,
                default_branch=default_branch,
                verification_config=verification_config,
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
    verification_config: Path | None = typer.Option(
        None,
        "--verification-config",
        exists=True,
        dir_okay=False,
        help="JSON file containing trusted verification command specifications.",
    ),
) -> None:
    """Register a local Git repository for MCP-run creation."""
    asyncio.run(
        _add_repository(
            name=name,
            path=path,
            default_branch=default_branch,
            verification_config=_load_verification_config(verification_config),
        )
    )


async def _set_verification(
    *,
    name: str,
    commands: list[VerificationCommandSpec],
) -> None:
    database = create_database(get_settings())
    try:
        repository = await Phase4Store(
            database.session_factory
        ).update_repository_verification_config(name, commands)
        names = ", ".join(item.name for item in commands) or "git diff --check only"
        typer.echo(f"Updated {repository.name} verification commands: {names}")
    except OrchestratorError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    finally:
        await database.close()


@repository_app.command("set-verification")
def set_verification(
    name: str = typer.Option(..., "--name"),
    config: Path = typer.Option(..., "--config", exists=True, dir_okay=False),
) -> None:
    """Replace trusted verification commands for a registered repository."""
    asyncio.run(
        _set_verification(name=name, commands=_load_verification_config(config))
    )


async def _list_repositories() -> None:
    database = create_database(get_settings())
    try:
        repositories = await Phase4Store(database.session_factory).list_repositories()
        if not repositories:
            typer.echo("No repositories registered.")
            return
        for repository in repositories:
            command_names = (
                ",".join(item.name for item in repository.verification_config)
                or "git-diff-check"
            )
            typer.echo(
                f"{repository.name}\t{repository.default_branch}\t"
                f"{repository.root_path}\t{command_names}"
            )
    finally:
        await database.close()


@repository_app.command("list")
def list_repositories() -> None:
    """List locally registered repositories."""
    asyncio.run(_list_repositories())


if __name__ == "__main__":
    app()
