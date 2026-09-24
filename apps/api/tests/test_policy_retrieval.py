from app.rag.compiler import REQUIRED_RULES, compile_policy_rules
from app.rag.documents import load_policy_chunks
from tests.conftest import ROOT


def test_corpus_is_chunked_by_policy_id():
    chunks = load_policy_chunks(ROOT / "documents")
    ids = {c.policy_id for c in chunks}
    assert {"IROP-001", "IROP-002", "MCT-002", "MCT-003", "VIP-001", "FARE-002", "SSR-001"} <= ids
    mct = next(c for c in chunks if c.policy_id == "MCT-002")
    assert mct.source_document == "connection-policy.md"
    assert mct.params == {"rule": "mct", "airport": "NRT", "mct_minutes": 90}
    assert "policy-params" not in mct.text


async def test_search_returns_provenance(client):
    body = (await client.get("/api/policies/search", params={"q": "minimum connection time NRT transfer"})).json()
    top = body["hits"][0]
    assert top["policy_id"] == "MCT-002"
    for key in ("policy_id", "source_document", "section", "text", "score", "retriever"):
        assert top[key] not in (None, "")
    assert body["provider"] == "lexical-bm25" and body["nvidia"] is False


async def test_vip_and_interline_queries(client):
    vip = (await client.get("/api/policies/search", params={"q": "VIP priority re-accommodation"})).json()
    assert vip["hits"][0]["policy_id"] == "VIP-001"
    inter = (await client.get("/api/policies/search", params={"q": "interline partner carrier agreement"})).json()
    assert inter["hits"][0]["policy_id"] == "IROP-002"
    assert inter["hits"][0]["params"]["interline_partners"] == ["OZ"]


def test_compiler_applies_only_retrieved_policies():
    chunks = load_policy_chunks(ROOT / "documents")
    from app.domain.models import PolicyHit

    hits = [
        PolicyHit(
            policy_id=c.policy_id,
            title=c.title,
            source_document=c.source_document,
            section=c.section,
            text=c.text,
            score=1.0,
            params=c.params,
            retriever="t",
        )
        for c in chunks
    ]
    full = compile_policy_rules(hits)
    assert full.missing == []
    assert full.mct_minutes == 90 and full.interline_partners == ["OZ"] and full.max_delay_hours == 12
    assert full.applied["mct"] == "MCT-002"

    partial = compile_policy_rules([h for h in hits if h.policy_id in {"IROP-001", "VIP-001"}])
    assert set(partial.missing) == set(REQUIRED_RULES) - {"own_carrier_first"}
    assert partial.allow_interline is False  # conservative default, not LLM memory
    assert partial.mct_minutes == 120
