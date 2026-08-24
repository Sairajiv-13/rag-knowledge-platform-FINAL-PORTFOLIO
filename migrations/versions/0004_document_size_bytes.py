"""documents.size_bytes for per-tenant storage quotas

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-26

Adds size_bytes so a tenant's total stored bytes can be summed without loading
the deferred raw_content column. Backfills existing rows from the actual byte
length of their stored payload (octet_length on the bytea), so the quota is
correct from the first request after upgrade — not just for new uploads.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # server_default 0 so the NOT NULL add succeeds on existing rows.
    op.add_column(
        "documents",
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
    )
    # Backfill from the real payload length where we still have the bytes.
    op.execute(
        "UPDATE documents SET size_bytes = octet_length(raw_content) "
        "WHERE raw_content IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_column("documents", "size_bytes")
