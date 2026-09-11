"""run trace

The replayable event stream of a run, so a saved conversation keeps its
evidence and not just its answer.

Revision ID: 7c2e91b4d0aa
Revises: 5637a8ff9a5d
Create Date: 2026-09-11 15:10:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "7c2e91b4d0aa"
down_revision: str | None = "5637a8ff9a5d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column(
            "trace",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_runs", "trace")
