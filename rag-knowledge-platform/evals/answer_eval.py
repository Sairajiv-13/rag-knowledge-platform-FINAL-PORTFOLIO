"""End-to-end answer quality eval (best with a REAL LLM).

Retrieval quality is measured in run_eval.py without an LLM. This harness
measures the generated ANSWER:

- correctness: does the answer text contain the expected fact?
- faithfulness: is that fact present in a chunk the answer actually CITED?
  (a correct-looking answer whose citation doesn't support it is the exact
  hallucination mode RAG must avoid)

Real numbers require RAG_LLM_PROVIDER=anthropic and a real embedding provider.
With the fake LLM the harness still runs end to end, but the fake echoes
context instead of reasoning, so treat those as a PLUMBING check only. One
command for real numbers:

    RAG_LLM_PROVIDER=anthropic RAG_ANTHROPIC_API_KEY=sk-... \
    RAG_EMBEDDING_PROVIDER=local python evals/answer_eval.py
"""

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
from rag_platform.llm.factory import build_embedding_provider, build_llm_provider, build_reranker
from rag_platform.models import Tenant
from rag_platform.retrieval.service import RetrievalService
from rag_platform.services.answering import AnswerService
from rag_platform.services.ingestion import IngestionService

ROOT = Path(__file__).parent
EVAL_TENANT_SLUG = "answer-eval-harness"


async def _prepare_corpus(session_factory, settings):  # type: ignore[no-untyped-def]
    embedder = build_embedding_provider(settings)
    ingestion = IngestionService(
        embedder,
        chunk_target_tokens=settings.chunk_target_tokens,
        chunk_overlap_tokens=settings.chunk_overlap_tokens,
        embed_batch_size=settings.embed_batch_size,
    )
    async with session_factory() as session:
        await session.execute(delete(Tenant).where(Tenant.slug == EVAL_TENANT_SLUG))
        tenant = Tenant(name="Answer Eval", slug=EVAL_TENANT_SLUG)
        session.add(tenant)
        await session.commit()
    for path in sorted((ROOT / "corpus").glob("*.md")):
        async with session_factory() as session:
            await ingestion.ingest(
                session, tenant_id=tenant.id, filename=path.name,
                raw=path.read_bytes(), source_type=source_type_for_filename(path.name),
            )
    return tenant


async def run() -> dict:  # type: ignore[type-arg]
    settings = get_settings()
    engine = create_async_engine(str(settings.database_url), poolclass=NullPool)
    try:
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        tenant = await _prepare_corpus(session_factory, settings)
        retrieval = RetrievalService(
            build_embedding_provider(settings), build_reranker(settings),
            k_dense=settings.retrieval_k_dense, k_keyword=settings.retrieval_k_keyword,
            rrf_k=settings.retrieval_rrf_k, top_n=settings.retrieval_top_n,
        )
        answering = AnswerService(
            retrieval, build_llm_provider(settings), session_factory,
            max_tokens=settings.answer_max_tokens,
            price_input_per_mtok=settings.price_input_per_mtok,
            price_output_per_mtok=settings.price_output_per_mtok,
        )
        questions = [
            json.loads(line)
            for line in (ROOT / "questions.jsonl").read_text().splitlines() if line.strip()
        ]
        correct = faithful = scored = 0
        for item in questions:
            fact = item.get("expected_fact")
            if not fact:
                continue
            scored += 1
            async with session_factory() as session:
                prepared = await answering.prepare(
                    session, tenant_id=tenant.id, query=item["question"],
                    top_n=settings.retrieval_top_n,
                )
                if prepared is None:
                    continue
                result = await answering.complete(session, prepared, tenant_id=tenant.id)
            if fact.lower() in result.answer.lower():
                correct += 1
            cited = " ".join(c.snippet.lower() for c in result.citations)
            if fact.lower() in cited:
                faithful += 1
        await engine.dispose()
        return {
            "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "llm_provider": settings.llm_provider,
            "embedding_provider": settings.embedding_provider,
            "scored_questions": scored,
            "correctness": round(correct / scored, 3) if scored else None,
            "faithfulness": round(faithful / scored, 3) if scored else None,
            "plumbing_only": settings.llm_provider == "fake",
        }
    finally:
        await engine.dispose()


def main() -> None:
    result = asyncio.run(run())
    print(json.dumps(result, indent=2))
    if result.get("plumbing_only"):
        print(
            "\nNOTE: RAG_LLM_PROVIDER=fake — plumbing numbers, not a real answer-quality "
            "measurement. Re-run with RAG_LLM_PROVIDER=anthropic for real numbers.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
