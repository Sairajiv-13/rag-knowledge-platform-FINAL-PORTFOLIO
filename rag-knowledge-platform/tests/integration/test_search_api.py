"""Retrieval against real pgvector + tsvector, fake embeddings."""

GUIDE = (
    b"# Ops Guide\n\n## Recall tuning\n\n"
    # NB: standalone token on purpose — Postgres FTS lexes "hnsw.ef_search"
    # into 'hnsw.ef'+'search', which the phrase query 'ef<->search' can't
    # match (documented FTS limitation in the README).
    b"Set ef_search higher to trade latency for recall in vector search.\n\n"
    b"## Backups\n\nNightly base backups plus WAL archiving protect the data.\n"
)


async def seeded(client, auth):
    resp = await client.post(
        "/v1/documents", headers=auth["headers"], files={"file": ("guide.md", GUIDE)}
    )
    assert resp.status_code == 202
    return resp.json()["id"]


async def test_keyword_mode_finds_exact_term_with_location(client, auth):
    await seeded(client, auth)
    results = (
        await client.post(
            "/v1/search",
            headers=auth["headers"],
            json={"query": "ef_search", "mode": "keyword"},
        )
    ).json()["results"]
    assert results, "exact term must be found by tsvector search"
    top = results[0]
    assert "ef_search" in top["content"]
    assert top["filename"] == "guide.md"
    assert "keyword_ts_rank" in top["scores"]
    assert top["location"].startswith("§ Ops Guide")


async def test_hybrid_mode_reports_all_score_components(client, auth):
    await seeded(client, auth)
    results = (
        await client.post(
            "/v1/search",
            headers=auth["headers"],
            json={"query": "tuning recall for vector search", "mode": "hybrid", "top_n": 3},
        )
    ).json()["results"]
    assert results
    assert "rrf" in results[0]["scores"] and "rerank" in results[0]["scores"]


async def test_validation_rejects_bad_requests(client, auth):
    assert (
        await client.post("/v1/search", headers=auth["headers"], json={"query": ""})
    ).status_code == 422
    assert (
        await client.post(
            "/v1/search", headers=auth["headers"], json={"query": "x", "mode": "psychic"}
        )
    ).status_code == 422
    assert (
        await client.post("/v1/search", headers=auth["headers"], json={"query": "x", "top_n": 999})
    ).status_code == 422
