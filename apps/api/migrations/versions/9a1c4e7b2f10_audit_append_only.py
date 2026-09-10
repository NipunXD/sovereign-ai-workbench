"""Make the audit log genuinely append-only.

The hash chain proves tampering *after the fact*. This migration adds the
database-level enforcement that stops it happening in the first place: a trigger
that refuses UPDATE and DELETE on audit_events, and a sequence that gives the
chain a gapless, clock-independent order.

Without the trigger, anyone holding the application's own credentials could
rewrite history and recompute the hashes to match. With it, they would have to
be a database superuser — and a superuser's actions are outside the trust
boundary this control is designed for anyway.

Revision ID: 9a1c4e7b2f10
Revises: 83d57ff9e67e
Create Date: 2026-09-10
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "9a1c4e7b2f10"
down_revision: str | None = "83d57ff9e67e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A dedicated sequence, taken inside the same transaction that reads the
    # previous hash. Ordering by timestamp would be ambiguous for two events in
    # the same millisecond, and a hash chain needs one definite order.
    op.execute("CREATE SEQUENCE IF NOT EXISTS audit_events_seq_seq AS BIGINT START 1")
    op.execute(
        "ALTER TABLE audit_events ALTER COLUMN seq SET DEFAULT nextval('audit_events_seq_seq')"
    )
    op.execute("ALTER SEQUENCE audit_events_seq_seq OWNED BY audit_events.seq")

    op.execute(
        """
        CREATE OR REPLACE FUNCTION audit_events_no_mutation()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION
                'audit_events is append-only: % on seq % was rejected',
                TG_OP, COALESCE(OLD.seq, -1)
                USING HINT = 'Audit records cannot be modified or deleted.';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_events_immutable
        BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION audit_events_no_mutation();
        """
    )

    # Full-text search over chunk text. The generated tsvector column comes from
    # the model; the GIN index is what makes it usable, and it is the sparse
    # half of hybrid retrieval — the reason no separate search engine is needed.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chunks_tsv_gin ON chunks USING GIN (tsv)"
    )
    # Trigram index for equipment-tag lookups like 'V-1201', where an embedding
    # is actively unhelpful because V-1201 and V-1202 embed almost identically.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chunks_text_trgm ON chunks USING GIN (text gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_chunks_text_trgm")
    op.execute("DROP INDEX IF EXISTS ix_chunks_tsv_gin")
    op.execute("DROP TRIGGER IF EXISTS audit_events_immutable ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS audit_events_no_mutation()")
    op.execute("ALTER TABLE audit_events ALTER COLUMN seq DROP DEFAULT")
    op.execute("DROP SEQUENCE IF EXISTS audit_events_seq_seq")
