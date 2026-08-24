"""ADR 0002's promise, asserted against the real schema and API."""

MD_A = b"# Rotation\n\nAcme rotates credentials quarterly per the security policy.\n"


async def test_cross_tenant_document_access_is_404(client, make_auth):
    acme, beta = await make_auth("acme"), await make_auth("beta")
    doc_id = (
        await client.post("/v1/documents", headers=acme["headers"], files={"file": ("r.md", MD_A)})
    ).json()["id"]
    assert (await client.get(f"/v1/documents/{doc_id}", headers=beta["headers"])).status_code == 404
    assert (
        await client.delete(f"/v1/documents/{doc_id}", headers=beta["headers"])
    ).status_code == 404
    assert (await client.get(f"/v1/documents/{doc_id}", headers=acme["headers"])).status_code == 200


async def test_search_never_leaks_other_tenants_chunks(client, make_auth):
    acme, beta = await make_auth("acme"), await make_auth("beta")
    await client.post("/v1/documents", headers=acme["headers"], files={"file": ("r.md", MD_A)})
    hits = (
        await client.post(
            "/v1/search",
            headers=beta["headers"],
            json={"query": "credentials rotation security policy", "mode": "hybrid"},
        )
    ).json()["results"]
    assert hits == []


async def test_same_content_different_tenants_is_not_a_duplicate(client, make_auth):
    acme, beta = await make_auth("acme"), await make_auth("beta")
    r1 = await client.post("/v1/documents", headers=acme["headers"], files={"file": ("r.md", MD_A)})
    r2 = await client.post("/v1/documents", headers=beta["headers"], files={"file": ("r.md", MD_A)})
    assert r1.status_code == 202 and r2.status_code == 202  # dedup is per-tenant by design


async def test_document_list_is_tenant_scoped(client, make_auth):
    acme, beta = await make_auth("acme"), await make_auth("beta")
    await client.post("/v1/documents", headers=acme["headers"], files={"file": ("r.md", MD_A)})
    assert (await client.get("/v1/documents", headers=beta["headers"])).json()["total"] == 0
