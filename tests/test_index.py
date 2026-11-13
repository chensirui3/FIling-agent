from __future__ import annotations

from pathlib import Path

import pytest

from filing_agent.corpus.index import FilingIndex, Filters, bm25_tokens
from tests.search_fixtures import HashEmbedder, build_index

QUERY = "purchases of property plant and equipment cash flow 2018"


def test_bm25_tokens_join_thousands_and_drop_stopwords() -> None:
    assert bm25_tokens("The capex of $1,577.5 in 2018") == ["capex", "1577.5", "2018"]


@pytest.mark.parametrize("mode", ["bm25", "dense", "hybrid"])
def test_search_returns_one_hit_per_page(mode: str) -> None:
    hits = build_index().search(QUERY, mode=mode, top_k=10)  # type: ignore[arg-type]
    keys = [(h.doc_name, h.page) for h in hits]
    assert len(keys) == len(set(keys))
    assert ("ACME_2018_10K", 1) in keys[:3]


def test_filters_restrict_the_candidate_filings() -> None:
    index = build_index()
    only_beta = index.search(QUERY, mode="hybrid", filters=Filters(companies=frozenset({"Beta"})))
    assert {h.doc_name for h in only_beta} == {"BETA_2018_10Q"}
    acme_2019 = index.search(
        QUERY,
        mode="bm25",
        filters=Filters(companies=frozenset({"Acme"}), periods=frozenset({2019})),
    )
    assert {h.doc_name for h in acme_2019} == {"ACME_2019_10K"}
    gold = index.search(
        QUERY, mode="dense", filters=Filters(doc_names=frozenset({"ACME_2018_10K"}))
    )
    assert gold and all(h.doc_name == "ACME_2018_10K" for h in gold)


def test_number_queries_match_through_bm25() -> None:
    hits = build_index().search("1,699", mode="bm25", top_k=3)
    assert [(h.doc_name, h.page) for h in hits] == [("ACME_2019_10K", 1)]


def test_hybrid_falls_back_to_bm25_without_embeddings() -> None:
    index = build_index(dense=False)
    assert not index.has_dense
    assert index.search(QUERY, mode="hybrid", top_k=3) == index.search(QUERY, mode="bm25", top_k=3)
    with pytest.raises(ValueError, match="no dense"):
        index.search(QUERY, mode="dense")


def test_headers_are_searchable_when_enabled() -> None:
    query = "Beta 10-Q 2018 page 1"
    with_headers = build_index(headers=True).search(query, mode="bm25", top_k=1)
    assert (with_headers[0].doc_name, with_headers[0].page) == ("BETA_2018_10Q", 1)


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    index = build_index()
    index.save(tmp_path / "test")
    loaded = FilingIndex.load(tmp_path / "test", embedder=HashEmbedder())
    for mode in ("bm25", "dense", "hybrid"):
        assert loaded.search(QUERY, mode=mode) == index.search(QUERY, mode=mode)  # type: ignore[arg-type]
    assert loaded.spec == index.spec


def test_loading_a_missing_index_explains_what_to_do(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="fa index build"):
        FilingIndex.load(tmp_path / "nope")
