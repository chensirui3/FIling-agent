from __future__ import annotations

from filing_agent.corpus.parse import ParsedDoc, ParsedPage
from filing_agent.data.financebench import Evidence, Question
from filing_agent.data.verify import align_evidence, containment, summarize_alignment, tokens

PAGES = [
    "Acme annual report cover page",
    "Cash flows from investing activities Purchases of property, plant and equipment "
    "(1,577) (1,373) (1,420) Proceeds from sale of businesses 846 1,065",
    "Balance sheet total assets 36,500 37,987 total liabilities 26,652 26,365",
    "Segment results industrial safety graphics 12,345 6,789 4,321",
]
CASH_FLOW_PASSAGE = "Purchases of property, plant and equipment (1,577) (1,373) (1,420)"


def _doc() -> ParsedDoc:
    return ParsedDoc(
        doc_name="ACME_2018_10K",
        parser_version="test",
        pdfplumber_version="test",
        source_size=1,
        n_pages=len(PAGES),
        seconds=0.0,
        pages=[ParsedPage(page=i, text=t, layout=t) for i, t in enumerate(PAGES)],
    )


def _question(qid: str, page: int, text: str, doc_name: str = "ACME_2018_10K") -> Question:
    return Question(
        id=qid,
        company="Acme",
        doc_name=doc_name,
        question_type="metrics-generated",
        question="q",
        answer="a",
        evidence=[Evidence(doc_name=doc_name, page=page, text=text, full_page_text="")],
    )


def test_tokens_normalize_numbers_and_drop_short_words() -> None:
    assert tokens("Net PP&E of $1,577.5 in 2018") == {"net", "1577.5", "2018"}


def test_containment_weights_numbers() -> None:
    page = tokens(PAGES[1])
    assert containment(tokens(CASH_FLOW_PASSAGE), page) == 1.0
    assert containment(tokens("purchases 9,999"), page) == 0.25  # word 1 of 1+3


def test_alignment_statuses() -> None:
    questions = [
        _question("on", 1, CASH_FLOW_PASSAGE),
        _question("off1", 2, CASH_FLOW_PASSAGE),
        _question("far", 3, CASH_FLOW_PASSAGE),
        _question("none", 1, "Goodwill impairment 77,777 88,888 99,999 not anywhere"),
        _question("nodoc", 0, CASH_FLOW_PASSAGE, doc_name="MISSING_2018_10K"),
    ]
    docs = {"ACME_2018_10K": _doc()}
    records = align_evidence(questions, docs.get)
    status = {r.question_id: r.status for r in records}
    assert status == {
        "on": "on_page",
        "off1": "off_by_one",
        "far": "elsewhere",
        "none": "not_found",
        "nodoc": "missing_doc",
    }
    far = next(r for r in records if r.question_id == "far")
    assert (far.best_page, far.offset) == (1, -2)

    summary = summarize_alignment(records)
    assert summary["evidence_items"] == 5
    assert summary["on_page_rate"] == 0.2
    assert summary["offsets"] == {-2: 1, -1: 1}
