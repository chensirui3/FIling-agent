from __future__ import annotations

import pytest

from filing_agent.data.financebench import Document, Evidence, Question
from filing_agent.eval import retrieval as rv
from tests.search_fixtures import CORPUS, build_index, parsed

PASSAGE = "purchases of property plant and equipment (1,577) (1,373)"


def _question(qid: str, pages: list[int], text: str = PASSAGE) -> Question:
    return Question(
        id=qid,
        company="Acme",
        doc_name="ACME_2018_10K",
        question_type="metrics-generated",
        question="What is Acme's FY2018 capex (purchases of property plant and equipment)?",
        answer="$1577.00",
        evidence=[
            Evidence(doc_name="ACME_2018_10K", page=p, text=text, full_page_text="") for p in pages
        ],
    )


def test_acceptable_pages_add_equally_good_pages_in_the_same_filing() -> None:
    doc = parsed("ACME_2018_10K")
    doc.pages[2].text += " " + PASSAGE  # the passage now appears on two pages
    assert rv.acceptable_pages(_question("q", [1]), doc) == (frozenset({1, 2}),)


def test_aggregate_hit_full_and_mrr() -> None:
    rows = [
        {"ranks": [1]},
        {"ranks": [3, 12]},  # second evidence item only at rank 12
        {"ranks": [None]},
    ]
    metrics = rv.aggregate(rows, ks=(1, 5, 20))
    assert metrics["hit@1"] == pytest.approx(1 / 3)
    assert metrics["hit@5"] == pytest.approx(2 / 3)
    assert metrics["full@5"] == pytest.approx(1 / 3)
    assert metrics["full@20"] == pytest.approx(2 / 3)
    assert metrics["mrr"] == pytest.approx((1 + 1 / 3 + 0) / 3)


def test_run_scores_only_pages_of_the_gold_filing() -> None:
    documents = {
        name: Document(
            doc_name=name, company=info.company, doc_type=info.doc_type, doc_period=info.doc_period
        )
        for name, (info, _) in CORPUS.items()
    }
    cases = rv.build_cases([_question("q1", [1])], documents, parsed)
    rows = rv.run(build_index(), cases, modes=["bm25", "hybrid"])
    assert {(r["setting"], r["mode"]) for r in rows} == {
        (s, m) for s in rv.SETTINGS for m in ("bm25", "hybrid")
    }
    gold_only = [r for r in rows if r["setting"] == "gold_filing"]
    assert all(r["ranks"] == [1] for r in gold_only)
    summary = rv.summarize(rows, groups={"dev": {"q1"}})
    assert {row["part"] for row in summary} == {"all", "dev"}
