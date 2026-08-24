"""Grounded answers end-to-end: citations, SSE sequence, usage metering."""

DOC = (
    b"# Billing\n\n## Invoices\n\nInvoices are issued on the first business day of each month.\n\n"
    b"## Refunds\n\nRefunds are processed within five business days of approval.\n"
)


async def seeded(client, auth):
    resp = await client.post(
        "/v1/documents", headers=auth["headers"], files={"file": ("billing.md", DOC)}
    )
    assert resp.status_code == 202


async def test_answer_returns_citations_resolving_to_sources(client, auth):
    await seeded(client, auth)
    body = (
        await client.post(
            "/v1/answers",
            headers=auth["headers"],
            json={"query": "when are invoices issued?", "top_n": 3},
        )
    ).json()
    assert body["answer"].startswith("FAKE_ANSWER[1]")  # fake echoes context markers
    assert body["model"] == "fake-llm"
    assert body["usage"]["input_tokens"] > 0
    assert body["cost_usd"] is None  # prices unconfigured -> no invented cost
    markers = [c["marker"] for c in body["citations"]]
    assert markers == sorted(markers) and 1 in markers
    assert all(c["filename"] == "billing.md" for c in body["citations"])


async def test_answer_without_context_short_circuits_llm(client, auth):
    body = (
        await client.post("/v1/answers", headers=auth["headers"], json={"query": "zebra xylophone"})
    ).json()
    assert body["citations"] == [] and body["model"] is None and body["usage"] is None
    usage = (await client.get("/v1/usage", headers=auth["headers"])).json()
    assert usage["total_calls"] == 0  # no LLM spend happened


async def test_streaming_emits_citations_then_deltas_then_done(client, auth):
    await seeded(client, auth)
    async with client.stream(
        "POST",
        "/v1/answers",
        headers=auth["headers"],
        json={"query": "refund timeline?", "stream": True},
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = "".join([part async for part in resp.aiter_text()])
    i_cit, i_delta, i_done = (body.index(f"event: {e}") for e in ("citations", "delta", "done"))
    assert i_cit < i_delta < i_done
    assert body.count("event: done") == 1


async def test_usage_rollup_counts_stream_and_nonstream(client, auth):
    await seeded(client, auth)
    await client.post("/v1/answers", headers=auth["headers"], json={"query": "invoices?"})
    async with client.stream(
        "POST",
        "/v1/answers",
        headers=auth["headers"],
        json={"query": "refunds?", "stream": True},
    ) as resp:
        async for _ in resp.aiter_text():
            pass
    usage = (await client.get("/v1/usage?days=7", headers=auth["headers"])).json()
    assert usage["total_calls"] == 2
    assert usage["total_input_tokens"] > 0 and usage["total_output_tokens"] > 0
    assert usage["total_cost_usd"] is None  # honest: prices not configured
    assert usage["by_model"][0]["model"] == "fake-llm"
