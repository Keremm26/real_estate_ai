"""
Tests for area-scoped retrieval (no LLM calls: the router's model is stubbed).

Some documents apply to only part of a city slice (Tower Hamlets' own HMO
standards inside London). The manifest declares ``scope_area``, every chunk
stores it ("all" otherwise), the area router places the request in one of the
slice's scoped areas or in none, and the cascade keeps scoped documents only for
their own area. Slices without scoped documents make no router call.

Run:
    python tests/test_retrieval_scope.py
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import app.services.llm.langchain_client as lc
from app.services.llm.agents import regulatory_agent
from app.services.llm.rag import router
from app.services.llm.rag.manifest import MANIFESTS, scoped_areas
from app.services.llm.rag.retriever import _cascade_where, _format


class _Reply:
    def __init__(self, content):
        self.content = content


def _stub_llm(answer=None, error=None):
    calls = []

    class _LLM:
        def invoke(self, messages):
            calls.append(messages)
            if error:
                raise error
            return _Reply(answer)

    lc.get_llm = lambda **kw: _LLM()
    router._route_area_cached.cache_clear()
    return calls


def test_manifest_scopes():
    assert scoped_areas("UK") == ["Tower Hamlets"]
    assert scoped_areas("IT") == [] and scoped_areas("ES") == []
    th = [s["doc_key"] for s in MANIFESTS["london"] if s.get("scope_area")]
    assert sorted(th) == ["th_article4_class_e", "th_local_plan_2031", "th_prs_property_standards"]


def test_cascade_area_clause():
    clauses = lambda where: [c for c in where["$and"] if "scope_area" in c]
    assert clauses(_cascade_where("UK", "general")) == []                     # None: no filter (evals)
    assert clauses(_cascade_where("UK", "general", area="")) == [{"scope_area": "all"}]
    assert clauses(_cascade_where("UK", "general", area="Tower Hamlets")) == [
        {"scope_area": {"$in": ["all", "Tower Hamlets"]}}]


def test_scope_shown_in_context_header():
    text, _ = _format([{"doc_name": "TH standards", "article_ref": "1", "jurisdiction_level": "municipal",
                        "scope_area": "Tower Hamlets", "text": "8.5 m2"},
                       {"doc_name": "HMO Regs", "article_ref": "1A", "jurisdiction_level": "national",
                        "scope_area": "all", "text": "6.51 m2"}])
    assert "(municipal; applies only in Tower Hamlets)" in text
    assert "applies only in all" not in text


def test_route_area_answers():
    _stub_llm('{"area": "Tower Hamlets", "reason": "Mile End"}')
    assert router.route_area("HMO near Mile End", ["Tower Hamlets"])[0] == "Tower Hamlets"
    _stub_llm('{"area": "none", "reason": "Camden"}')
    area, trace = router.route_area("HMO near Camden Town", ["Tower Hamlets"])
    assert area == "" and trace["area"] == "none" and "error" not in trace
    _stub_llm('{"area": "Hackney"}')                                          # outside the vocabulary
    area, trace = router.route_area("HMO in Hackney", ["Tower Hamlets"])
    assert area == "" and "outside vocabulary" in trace["error"]
    _stub_llm(error=ConnectionError("server down"))                           # degrade, don't crash
    area, trace = router.route_area("HMO near Whitechapel", ["Tower Hamlets"])
    assert area == "" and "server down" in trace["error"]


def test_slices_without_scoped_documents_make_no_router_call():
    calls = _stub_llm('{"area": "none"}')
    captured = {}
    original = regulatory_agent.retrieve_with_trace
    regulatory_agent.retrieve_with_trace = lambda q, **kw: (captured.update(kw) or ("docs", [], {}))
    try:
        regulatory_agent.load_regulatory_context("residenza per 60 studenti", retrieval_mode="rag",
                                                 country="IT", use_case="student_housing")
        assert calls == [] and captured["area"] is None
        _, _, _, trace = regulatory_agent.load_regulatory_context("HMO near Mile End", retrieval_mode="rag",
                                                                  country="UK", use_case="general")
        assert len(calls) == 1 and captured["area"] == "" and trace["area_routing"]["area"] == "none"
    finally:
        regulatory_agent.retrieve_with_trace = original


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} tests passed.")
