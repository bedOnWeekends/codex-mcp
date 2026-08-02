"""Add durable multi-agent assignments.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("key", sa.String(length=40), nullable=False),
        sa.Column("role", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("instruction", sa.Text(), nullable=False),
        sa.Column(
            "depends_on", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "owned_paths", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("model_tier", sa.String(length=20), nullable=False),
        sa.Column("worktree_path", sa.Text(), nullable=True),
        sa.Column("codex_thread_id", sa.String(length=255), nullable=True),
        sa.Column("commit_sha", sa.String(length=40), nullable=True),
        sa.Column(
            "changed_files", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False),
        sa.Column(
            "estimated_cost_usd", sa.Numeric(precision=12, scale=6), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "key", name="uq_agent_assignments_run_key"),
        sa.UniqueConstraint("task_id", name="uq_agent_assignments_task_id"),
    )
    op.create_index("ix_agent_assignments_run_id", "agent_assignments", ["run_id"])
    op.create_index("ix_agent_assignments_task_id", "agent_assignments", ["task_id"])
    op.create_index("ix_agent_assignments_status", "agent_assignments", ["status"])
    op.create_index(
        "ix_agent_assignments_run_status_created_at",
        "agent_assignments",
        ["run_id", "status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_assignments_run_status_created_at",
        table_name="agent_assignments",
    )
    op.drop_index("ix_agent_assignments_status", table_name="agent_assignments")
    op.drop_index("ix_agent_assignments_task_id", table_name="agent_assignments")
    op.drop_index("ix_agent_assignments_run_id", table_name="agent_assignments")
    op.drop_table("agent_assignments")
