"""
Tests for citation following (app/services/llm/rag/citations.py).

The unit tests run on a fake label index; the last one uses the real store and
the local embedding model (no LLM call).

Run:
    python tests/test_citations.py
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.llm.rag import citations as C

PGOUM, FAQ, CTE = "PGOUM Madrid 1997", "Preguntas de interés", "CTE DB-SI"
LP, TH = "The London Plan 2021", "Tower Hamlets Local Plan"
_FAKE = [(d, a, C._segments(a)) for d, a in [
    (PGOUM, "TÍTULO 7 > 7.3 > 7.3.4"), (PGOUM, "TÍTULO 7 > 7.3 > 7.3.4 > 1"), (PGOUM, "TÍTULO 7 > 7.3 > 7.3.4 > 2"),
    (PGOUM, "TÍTULO 7 > 7.3 > 7.3.11 > 2"), (PGOUM, "TÍTULO 7 > 7.3 > 7.3.11 > 3"),
    (FAQ, "20 > b"), (CTE, "Artículo 11 > 25"), (CTE, "Artículo 12"),
    (LP, "Policy H15 (part 1)"), (LP, "Policy H16"), (LP, "Policy D6 > A"),
    (TH, "3.1"), (TH, "Policy H16"),
]]


def _with_fake(fn):
    real = C._refs
    C._refs = lambda country: _FAKE
    try:
        fn()
    finally:
        C._refs = real


def test_citations_in_three_languages():
    assert C.citations("cumplirán las condiciones de los artículos 7.3.3, 7.3.4 y 7.3.5 de las NNUU") == \
        [("art", "7.3.3"), ("art", "7.3.4"), ("art", "7.3.5")]
    assert C.citations("ai sensi dell'art. 77 e dell'articolo 3-bis") == [("art", "77"), ("art", "3-bis")]
    assert C.citations("see Policy H16 and Table 3.1") == [("policy", "h16"), ("table", "3.1")]
    assert C.citations("no citation here, only 40 m2 and 7.3 metres") == []


def test_paragraph_citation_selects_the_paragraph_in_another_document():
    def run():
        hit = {"doc_name": FAQ, "article_ref": "20 > b"}
        assert C.resolve(("art", "7.3.11.2"), hit, "ES", [FAQ, PGOUM]) == (True, [(PGOUM, "TÍTULO 7 > 7.3 > 7.3.11 > 2")])
        assert C.resolve(("art", "7.3.4.2.c"), hit, "ES", [FAQ]) == (True, [(PGOUM, "TÍTULO 7 > 7.3 > 7.3.4 > 2")])
        specific, refs = C.resolve(("art", "7.3.4.d"), hit, "ES", [FAQ])      # no paragraph 'd': the article
        assert not specific and (PGOUM, "TÍTULO 7 > 7.3 > 7.3.4 > 1") in refs and len(refs) == 3
    _with_fake(run)


def test_named_citations_stay_in_the_citing_document():
    def run():
        lp = {"doc_name": LP, "article_ref": "Policy H15 (part 1)"}
        assert C.resolve(("policy", "h16"), lp, "UK", [LP, TH]) == (False, [(LP, "Policy H16")])
        assert C.resolve(("table", "3.1"), lp, "UK", [LP, TH]) == (False, [])   # not TH's section 3.1
        cte = {"doc_name": CTE, "article_ref": "Artículo 11 > 25"}
        assert C.resolve(("art", "11"), cte, "ES", [CTE]) == (False, [])        # an article citing itself
        assert C.resolve(("art", "12"), cte, "ES", [CTE]) == (False, [(CTE, "Artículo 12")])
    _with_fake(run)


def test_follow_on_the_store_adds_the_cited_rule_and_programme():
    from app.services.llm.rag import config
    from app.services.llm.rag.retriever import _format, search
    q = "Busco un edificio para una residencia de estudiantes con 40 habitaciones individuales."
    base = search(q, country="ES", use_case="student_housing", mode="rag", quantitative_only=True,
                  follow_citations=False)
    full = search(q, country="ES", use_case="student_housing", mode="rag", quantitative_only=True,
                  follow_citations=True)
    assert full[:len(base)] == base                                    # the top-k is untouched
    added = full[len(base):]
    refs = {h["article_ref"] for h in added}
    assert {"TÍTULO 7 > 7.3 > 7.3.11 > 2", "TÍTULO 7 > 7.3 > 7.3.4 > 2"} <= refs
    assert 0 < len(added) <= config.CITATION_MAX and all(h["cited_by"] for h in added)
    assert "cited by " in _format(added)[0]


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)}/{len(tests)} tests passed.")
