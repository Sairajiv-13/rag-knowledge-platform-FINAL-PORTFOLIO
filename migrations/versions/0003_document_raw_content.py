"""documents.raw_content for async ingestion

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-07

Nullable on purpose: rows ingested before this migration have no stored bytes;
the worker fails those with an actionable message rather than pretending.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("raw_content", sa.LargeBinary(), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "raw_content")
