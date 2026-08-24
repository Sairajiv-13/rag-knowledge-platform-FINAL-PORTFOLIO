"""Developer CLI: exercise the domain layer end-to-end before the API exists.

    python -m rag_platform.cli create-tenant --name "Acme" --slug acme
    python -m rag_platform.cli ingest --tenant acme docs/guide.md
    python -m rag_platform.cli search --tenant acme --query "how do I deploy?"

Stays after stage 4 too: it's the fastest way to debug retrieval without HTTP
in the way. argparse over click/typer: three subcommands don't earn a dependency.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rag_platform.config import Settings, get_settings
from rag_platform.db import create_engine, create_session_factory
from rag_platform.exceptions import NotFoundError, RagPlatformError
from rag_platform.ingestion.parsers import source_type_for_filename
from rag_platform.llm.factory import build_embedding_provider, build_reranker
from rag_platform.logging import configure_logging
from rag_platform.models import ApiCredential, Tenant
from rag_platform.retrieval.service import RetrievalService, SearchMode
from rag_platform.services.ingestion import IngestionService


async def _resolve_tenant(
    session_factory: async_sessionmaker[AsyncSession], slug: str
) -> Tenant:
    async with session_factory() as session:
        tenant = (
            await session.execute(select(Tenant).where(Tenant.slug == slug))
        ).scalar_one_or_none()
    if tenant is None:
        raise SystemExit(f"error: no tenant with slug {slug!r} (create-tenant first)")
    return tenant


async def _cmd_create_tenant(settings: Settings, args: argparse.Namespace) -> None:
    engine = create_engine(str(settings.database_url))
    try:
        async with create_session_factory(engine)() as session:
            tenant = Tenant(name=args.name, slug=args.slug)
            session.add(tenant)
            await session.commit()
            print(json.dumps({"id": str(tenant.id), "slug": tenant.slug}))
    finally:
        await engine.dispose()


async def _cmd_ingest(settings: Settings, args: argparse.Namespace) -> None:
    engine = create_engine(str(settings.database_url))
    try:
        session_factory = create_session_factory(engine)
        tenant = await _resolve_tenant(session_factory, args.tenant)
        service = IngestionService(
            build_embedding_provider(settings),
            chunk_target_tokens=settings.chunk_target_tokens,
            chunk_overlap_tokens=settings.chunk_overlap_tokens,
            embed_batch_size=settings.embed_batch_size,
        )
        for path_str in args.paths:
            path = Path(path_str)
            async with session_factory() as session:
                document = await service.ingest(
                    session,
                    tenant_id=tenant.id,
                    filename=path.name,
                    raw=path.read_bytes(),
                    source_type=source_type_for_filename(path.name),
                )
            print(
                json.dumps(
                    {
                        "document_id": str(document.id),
                        "filename": document.filename,
                        "status": document.status,
                        "chunks": document.chunk_count,
                    }
                )
            )
    finally:
        await engine.dispose()


async def _cmd_search(settings: Settings, args: argparse.Namespace) -> None:
    engine = create_engine(str(settings.database_url))
    try:
        session_factory = create_session_factory(engine)
        tenant = await _resolve_tenant(session_factory, args.tenant)
        service = RetrievalService(
            build_embedding_provider(settings),
            build_reranker(settings),
            k_dense=settings.retrieval_k_dense,
            k_keyword=settings.retrieval_k_keyword,
            rrf_k=settings.retrieval_rrf_k,
            top_n=settings.retrieval_top_n,
        )
        mode: SearchMode = args.mode
        async with session_factory() as session:
            results = await service.search(
                session, tenant_id=tenant.id, query=args.query, mode=mode, top_n=args.top_n
            )
        for r in results:
            print(
                json.dumps(
                    {
                        "chunk_id": r.chunk_id,
                        "file": r.filename,
                        "chunk_index": r.chunk_index,
                        "scores": {k: round(v, 4) for k, v in r.scores.items()},
                        "meta": r.meta,
                        "snippet": r.content[:160],
                    }
                )
            )
        if not results:
            print("no results", file=sys.stderr)
    finally:
        await engine.dispose()


async def _cmd_create_credential(settings: Settings, args: argparse.Namespace) -> None:
    from rag_platform import security

    engine = create_engine(str(settings.database_url))
    try:
        session_factory = create_session_factory(engine)
        tenant = await _resolve_tenant(session_factory, args.tenant)
        client_id = security.generate_client_id()
        client_secret = security.generate_client_secret()
        async with session_factory() as session:
            session.add(
                ApiCredential(
                    tenant_id=tenant.id,
                    name=args.name,
                    client_id=client_id,
                    secret_hash=security.hash_secret(client_secret),
                )
            )
            await session.commit()
        # The secret is printed exactly once and only its hash is stored —
        # losing it means issuing a new credential, same as any sane API vendor.
        print(json.dumps({"client_id": client_id, "client_secret": client_secret}))
        print("store the client_secret now; it cannot be shown again", file=sys.stderr)
    finally:
        await engine.dispose()


async def _cmd_revoke_credential(settings: Settings, args: argparse.Namespace) -> None:
    from datetime import UTC, datetime

    engine = create_engine(str(settings.database_url))
    try:
        async with create_session_factory(engine)() as session:
            cred = (
                await session.execute(
                    select(ApiCredential).where(ApiCredential.client_id == args.client_id)
                )
            ).scalar_one_or_none()
            if cred is None:
                raise NotFoundError(f"no credential with client_id {args.client_id!r}")
            cred.revoked_at = cred.revoked_at or datetime.now(UTC)
            await session.commit()
            print(json.dumps({"client_id": cred.client_id, "revoked_at": str(cred.revoked_at)}))
    finally:
        await engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rag_platform.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("create-tenant", help="create a tenant")
    p.add_argument("--name", required=True)
    p.add_argument("--slug", required=True)
    p.set_defaults(func=_cmd_create_tenant)

    p = sub.add_parser("create-credential", help="issue OAuth2 client credentials for a tenant")
    p.add_argument("--tenant", required=True, help="tenant slug")
    p.add_argument("--name", required=True, help="label, e.g. 'ci-pipeline'")
    p.set_defaults(func=_cmd_create_credential)

    p = sub.add_parser("revoke-credential", help="revoke a credential immediately")
    p.add_argument("--client-id", required=True)
    p.set_defaults(func=_cmd_revoke_credential)

    p = sub.add_parser("ingest", help="ingest local files for a tenant")
    p.add_argument("--tenant", required=True, help="tenant slug")
    p.add_argument("paths", nargs="+")
    p.set_defaults(func=_cmd_ingest)

    p = sub.add_parser("search", help="search a tenant's chunks")
    p.add_argument("--tenant", required=True, help="tenant slug")
    p.add_argument("--query", required=True)
    p.add_argument("--mode", choices=["hybrid", "dense", "keyword"], default="hybrid")
    p.add_argument("--top-n", type=int, default=None)
    p.set_defaults(func=_cmd_search)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level)  # same JSON pipeline as the API
    try:
        asyncio.run(args.func(settings, args))
    except RagPlatformError as exc:
        # Domain errors are expected outcomes: message + nonzero exit,
        # no traceback wall.
        raise SystemExit(f"error: {exc.detail}") from exc


if __name__ == "__main__":
    main()
