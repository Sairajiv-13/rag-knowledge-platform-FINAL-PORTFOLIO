"""ORM models — the source of truth for the schema.

Migrations under migrations/versions/ are written against these models;
`alembic check` in CI keeps the two from drifting apart.

Multi-tenancy (ADR 0002): shared tables, every row carries tenant_id, and the
repository layer (stage 3) requires tenant_id on every query. chunks.tenant_id
is deliberately denormalized from documents so the retrieval hot path filters
by tenant without a join.
"""

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Computed,
    DateTime,
    Enum,
    ForeignKey,
    Identity,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Fixed by the default embedding model, bge-small-en-v1.5 (ADR 0001).
# pgvector columns have a hard dimension, so changing the embedding model
# means a migration + full re-embed of all chunks.
EMBEDDING_DIM = 384

# Deterministic constraint/index names: without these, autogenerate diffs are
# noisy and downgrades can't reliably drop unnamed constraints.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class DocumentSourceType(enum.StrEnum):
    PDF = "pdf"
    MARKDOWN = "markdown"
    HTML = "html"


class DocumentStatus(enum.StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


def _enum(e: type[enum.Enum], name: str) -> Enum:
    # native_enum=False -> VARCHAR + CHECK constraint instead of a Postgres
    # enum type: adding a value later is a cheap constraint swap, not the
    # ALTER TYPE ... ADD VALUE dance (which can't run inside a transaction).
    return Enum(
        e, name=name, native_enum=False, length=20, values_callable=lambda x: [m.value for m in x]
    )


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # lazy="raise": in async SQLAlchemy an implicit lazy load blows up mid-await
    # anyway; failing loudly forces every access to use an explicit eager load.
    documents: Mapped[list["Document"]] = relationship(back_populates="tenant", lazy="raise")


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        # Same file uploaded twice by the same tenant is a no-op (dedup),
        # but two tenants may upload identical files independently.
        UniqueConstraint("tenant_id", "content_sha256"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String(512))
    source_type: Mapped[DocumentSourceType] = mapped_column(
        _enum(DocumentSourceType, "document_source_type")
    )
    content_sha256: Mapped[str] = mapped_column(String(64))
    # Uploaded payload size, recorded at register() so per-tenant storage can
    # be summed without loading the (deferred, possibly large) raw_content.
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    status: Mapped[DocumentStatus] = mapped_column(
        _enum(DocumentStatus, "document_status"), server_default=DocumentStatus.PENDING.value
    )
    # Original upload bytes: the worker's input, and what makes re-embedding
    # (e.g. an embedding-model migration) possible without re-uploads. BYTEA in
    # Postgres is a deliberate trade at a 10MB cap (ADR 0005): one datastore,
    # transactional with the row; object storage is the documented move if
    # uploads grow. deferred=True so list/get endpoints never drag megabytes
    # off disk just to show metadata.
    raw_content: Mapped[bytes | None] = mapped_column(LargeBinary, default=None, deferred=True)
    # Populated only when status == FAILED; kept on the row (not just in logs)
    # so the API can tell the uploader *why* ingestion failed.
    error_message: Mapped[str | None] = mapped_column(Text, default=None)
    chunk_count: Mapped[int | None] = mapped_column(Integer, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # onupdate is ORM-side, not a DB trigger: fine here because all writes go
    # through the ORM; raw-SQL writers would need to set it themselves.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    tenant: Mapped[Tenant] = relationship(back_populates="documents", lazy="raise")
    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="document", lazy="raise", passive_deletes=True
    )


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index"),
        # HNSW over cosine distance: default index for the dense half of hybrid
        # search. NOTE: tenant filtering happens *after* the ANN scan inside
        # pgvector, which degrades recall for small tenants at large scale —
        # called out in ADR 0002 / SCALABILITY.md rather than solved now.
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        # GIN over the generated tsvector: the BM25-ish keyword half.
        Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
    )

    # BigInteger identity, not UUID: chunks is the largest, hottest table and
    # an 8-byte key keeps its several indexes small. Chunk ids are only ever
    # exposed tenant-scoped, so id enumeration leaks nothing.
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(
        # No separate index: the (document_id, chunk_index) unique constraint
        # already serves document-scoped lookups.
        ForeignKey("documents.id", ondelete="CASCADE")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer)
    embedding: Mapped[Any] = mapped_column(Vector(EMBEDDING_DIM))
    # Generated column: Postgres keeps the tsvector in sync on every write,
    # so keyword search can never see stale text. 'english' config only —
    # documented limitation until language detection is worth its complexity.
    tsv: Mapped[Any] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', content)", persisted=True)
    )
    # Page number / heading path etc. — shape varies by source type, hence JSONB.
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    document: Mapped[Document] = relationship(back_populates="chunks", lazy="raise")


class ApiCredential(Base):
    """OAuth2 client-credentials pair for a tenant (ADR 0004).

    Only a SHA-256 of the secret is stored: the secret is 32 bytes of real
    entropy, so a KDF like bcrypt would add cost without adding security —
    KDFs exist to protect low-entropy human passwords from brute force.
    """

    __tablename__ = "api_credentials"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    client_id: Mapped[str] = mapped_column(String(64), unique=True)
    secret_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Touched on token issuance only, NOT on every API request — a per-request
    # UPDATE would put write amplification on the hottest path for a vanity metric.
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class UsageRecord(Base):
    """One row per billable LLM call. Token counts come straight from the
    provider response; cost is computed only if prices are configured, else
    NULL — never a made-up number."""

    __tablename__ = "usage_records"
    __table_args__ = (
        # Serves both "usage for tenant X" and "usage for tenant X since T".
        Index("ix_usage_records_tenant_id_created_at", "tenant_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    operation: Mapped[str] = mapped_column(String(32))  # e.g. "answer"
    model: Mapped[str] = mapped_column(String(100))
    input_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
