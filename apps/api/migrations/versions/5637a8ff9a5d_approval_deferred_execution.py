"""approval deferred execution

Lets an approval be acted on after the run that requested it has gone. The
bound arguments travel with the approval, and `executed_at` makes carrying
them out happen exactly once.

Revision ID: 5637a8ff9a5d
Revises: 9a1c4e7b2f10
Create Date: 2026-09-11 03:47:43.399211
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# Custom column types (UTCDateTime) render fully qualified.
import workbench.db.base  # noqa: F401

revision: str = "5637a8ff9a5d"
down_revision: str | None = "9a1c4e7b2f10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Autogenerate also proposed dropping ix_chunks_text_trgm and ix_chunks_tsv_gin.
# They are deliberately not here. Both are created in raw SQL by the initial
# migration because neither a GIN trigram index nor an index on a generated
# tsvector column can be expressed in the model, so autogenerate cannot see
# them and reports them as removed on every run. Applying that would drop the
# indexes sparse retrieval depends on, silently turning every keyword search
# into a sequential scan.


def upgrade() -> None:
    op.add_column(
        "approvals",
        sa.Column(
            "deferred",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
    )
    op.add_column(
        "approvals",
        sa.Column("executed_at", workbench.db.base.UTCDateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "approvals",
        sa.Column(
            "execution_result",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("approvals", "execution_result")
    op.drop_column("approvals", "executed_at")
    op.drop_column("approvals", "deferred")
