"""Add durable authenticated API automation runs.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "automation_runs",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("execution_mode", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("commit_message", sa.String(length=100), nullable=False),
        sa.Column("pull_request_title", sa.String(length=256), nullable=False),
        sa.Column("pull_request_body", sa.Text(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("commit_sha", sa.String(length=40), nullable=True),
        sa.Column("branch", sa.String(length=255), nullable=True),
        sa.Column("pull_request_url", sa.Text(), nullable=True),
        sa.Column("pull_request_number", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index(
        "ix_automation_runs_status_created_at",
        "automation_runs",
        ["status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_automation_runs_status_created_at",
        table_name="automation_runs",
    )
    op.drop_table("automation_runs")
