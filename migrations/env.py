from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.engine import Connection
from sqlalchemy.pool import NullPool

from orchestrator.db_models import Base
from orchestrator.settings import get_settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_sync_database_url() -> str:
    url = str(get_settings().database_url)
    if url.startswith("postgresql+asyncpg://"):
        return url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def configure(connection: Connection | None = None) -> None:
    kwargs = {
        "target_metadata": target_metadata,
        "compare_type": True,
    }
    if connection is None:
        context.configure(
            url=get_sync_database_url(),
            literal_binds=True,
            dialect_opts={"paramstyle": "named"},
            **kwargs,
        )
    else:
        context.configure(connection=connection, **kwargs)


def run_migrations_offline() -> None:
    configure()
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(
        get_sync_database_url(), poolclass=NullPool, pool_pre_ping=True
    )
    try:
        with engine.connect() as connection:
            configure(connection)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
