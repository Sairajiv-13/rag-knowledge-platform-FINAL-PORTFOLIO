"""Retrieval eval harness.

Ingests evals/corpus into a dedicated tenant (recreated each run) and measures
doc-level retrieval quality on evals/questions.jsonl:

- hit@1 / hit@3 / hit@5: is the expected source document among the top k
  results' files?
- MRR@5: mean reciprocal rank of the expected document.

Every reported number is measured in THIS run against THIS configuration —
the active embedding provider and reranker are printed alongside the metrics,
because numbers from the `fake` provider validate plumbing, not retrieval
quality. Run with RAG_EMBEDDING_PROVIDER=local (and optionally
RAG_RERANKER=cross_encoder) for numbers that mean something semantically.

Usage:
    python evals/run_eval.py [--top-n 5] [--modes hybrid dense keyword] [--out results.json]
"""

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from rag_platform.config import get_settings
from rag_platform.ingestion.parsers import source_type_for_filename
from rag_platform.llm.factory import build_embedding_provider, build_reranker
from rag_platform.models import Tenant
from rag_platform.retrieval.service import RetrievalService
from rag_platform.services.ingestion import IngestionService

ROOT = Path(__file__).parent
EVAL_TENANT_SLUG = "eval-harness"


async def _prepare_tenant(
    session_factory, settings, chunk_target_tokens=None  # type: ignore[no-untyped-def]
) -> "Tenant":
    """Recreate the eval tenant so every run measures a known corpus, never
    leftovers from a previous configuration (e.g. embeddings from a different
    provider, which would silently corrupt the numbers).

    chunk_target_tokens overrides the configured chunk size — used by the
    --chunk-sweep mode to measure how retrieval quality changes with it.
    """
    embedder = build_embedding_provider(settings)
    target = chunk_target_tokens or settings.chunk_target_tokens
    # keep overlap proportional and always < target (settings invariant)
    overlap = min(settings.chunk_overlap_tokens, max(target // 8, 1))
    service = IngestionService(
        embedder,
        chunk_target_tokens=target,
        chunk_overlap_tokens=overlap,
        embed_batch_size=settings.embed_batch_size,
    )
    async with session_factory() as session:
        await session.execute(delete(Tenant).where(Tenant.slug == EVAL_TENANT_SLUG))
        tenant = Tenant(name="Eval Harness", slug=EVAL_TENANT_SLUG)
        session.add(tenant)
        await session.commit()

    corpus = sorted((ROOT / "corpus").glob("*.md"))
    for path in corpus:
        async with session_factory() as session:
            await service.ingest(
                session,
                tenant_id=tenant.id,
                filename=path.name,
                raw=path.read_bytes(),
                source_type=source_type_for_filename(path.name),
            )
    print(f"ingested {len(corpus)} corpus docs into tenant '{EVAL_TENANT_SLUG}'", file=sys.stderr)
    return tenant


async def _score_modes(
    session_factory, tenant, retrieval, questions, modes, top_n  # type: ignore[no-untyped-def]
) -> dict:
    """Retrieval quality (hit@k, MRR) per mode over the question set."""
    out: dict = {}  # type: ignore[type-arg]
    for mode in modes:
        reciprocal_ranks: list[float] = []
        hits = {1: 0, 3: 0, 5: 0}
        for item in questions:
            async with session_factory() as session:
                retrieved = await retrieval.search(
                    session,
                    tenant_id=tenant.id,
                    query=item["question"],
                    mode=mode,
                    top_n=top_n,
                )
            rank = next(
                (i for i, r in enumerate(retrieved, start=1)
                 if r.filename == item["expected_file"]),
                None,
            )
            reciprocal_ranks.append(1.0 / rank if rank else 0.0)
            for k in hits:
                if rank is not None and rank <= k:
                    hits[k] += 1
        n = len(questions)
        out[mode] = {
            "hit@1": round(hits[1] / n, 3),
            "hit@3": round(hits[3] / n, 3),
            "hit@5": round(hits[5] / n, 3),
            "mrr@5": round(sum(reciprocal_ranks) / n, 3),
        }
    return out


async def _score_answer_quality(
    session_factory, tenant, retrieval, questions, top_n  # type: ignore[no-untyped-def]
) -> dict:
    """Retrieval-grounded answer quality WITHOUT an LLM:

    - faithfulness proxy: does at least one RETRIEVED chunk actually contain
      the expected fact? (can the model even ground a correct answer?)
    - This isolates the retrieval contribution to answer quality. True
      end-to-end correctness needs a real LLM; that harness is answer_eval.py,
      which requires RAG_LLM_PROVIDER=anthropic.
    """
    supported = 0
    for item in questions:
        fact = item.get("expected_fact")
        if not fact:
            continue
        async with session_factory() as session:
            retrieved = await retrieval.search(
                session, tenant_id=tenant.id, query=item["question"],
                mode="hybrid", top_n=top_n,
            )
        if any(fact.lower() in r.content.lower() for r in retrieved):
            supported += 1
    n = sum(1 for q in questions if q.get("expected_fact"))
    return {
        "questions_with_fact": n,
        "fact_in_retrieved_context": supported,
        "context_support_rate": round(supported / n, 3) if n else None,
    }


async def _evaluate(args: argparse.Namespace) -> dict:  # type: ignore[type-arg]
    settings = get_settings()
    engine = create_async_engine(str(settings.database_url), poolclass=NullPool)
    try:
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        questions = [
            json.loads(line)
            for line in (ROOT / "questions.jsonl").read_text().splitlines()
            if line.strip()
        ]

        def _service(top_n: int) -> RetrievalService:
            return RetrievalService(
                build_embedding_provider(settings),
                build_reranker(settings),
                k_dense=settings.retrieval_k_dense,
                k_keyword=settings.retrieval_k_keyword,
                rrf_k=settings.retrieval_rrf_k,
                top_n=top_n,
            )

        results: dict = {  # type: ignore[type-arg]
            "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "config": {
                "embedding_provider": settings.embedding_provider,
                "reranker": settings.reranker,
                "top_n": args.top_n,
                "questions": len(questions),
            },
        }

        if args.chunk_sweep:
            # How does retrieval quality change with chunk size? Re-ingest the
            # corpus at each size into its own tenant, score hybrid each time.
            sweep: dict = {}  # type: ignore[type-arg]
            for size in args.chunk_sweep:
                tenant = await _prepare_tenant(session_factory, settings, chunk_target_tokens=size)
                scores = await _score_modes(
                    session_factory, tenant, _service(args.top_n), questions, ["hybrid"], args.top_n
                )
                sweep[str(size)] = scores["hybrid"]
                print(f"chunk_target={size}: {scores['hybrid']}", file=sys.stderr)
            results["chunk_sweep"] = sweep
            return results

        tenant = await _prepare_tenant(session_factory, settings)
        results["modes"] = await _score_modes(
            session_factory, tenant, _service(args.top_n), questions, args.modes, args.top_n
        )
        results["answer_quality"] = await _score_answer_quality(
            session_factory, tenant, _service(args.top_n), questions, args.top_n
        )
        return results
    finally:
        await engine.dispose()


def _print_table(results: dict) -> None:  # type: ignore[type-arg]
    """Human-readable comparison table to stderr (JSON still goes to stdout)."""
    if "modes" in results:
        print("\n| mode    | hit@1 | hit@3 | hit@5 | mrr@5 |", file=sys.stderr)
        print("|---------|-------|-------|-------|-------|", file=sys.stderr)
        for mode, m in results["modes"].items():
            print(
                f"| {mode:<7} | {m['hit@1']:.3f} | {m['hit@3']:.3f} | "
                f"{m['hit@5']:.3f} | {m['mrr@5']:.3f} |",
                file=sys.stderr,
            )
    if results.get("answer_quality", {}).get("context_support_rate") is not None:
        aq = results["answer_quality"]
        print(
            f"\ncontext support (fact present in retrieved chunks): "
            f"{aq['fact_in_retrieved_context']}/{aq['questions_with_fact']} "
            f"= {aq['context_support_rate']}",
            file=sys.stderr,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument(
        "--modes", nargs="+", default=["keyword", "dense", "hybrid"],
        choices=["keyword", "dense", "hybrid"],
    )
    parser.add_argument(
        "--chunk-sweep", nargs="+", type=int, default=None,
        metavar="TOKENS", help="re-ingest+score hybrid at each chunk size, e.g. 200 400 800",
    )
    parser.add_argument("--out", type=Path, default=None, help="also write JSON here")
    args = parser.parse_args()

    results = asyncio.run(_evaluate(args))
    print(json.dumps(results, indent=2))
    _print_table(results)
    if args.out:
        args.out.write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
