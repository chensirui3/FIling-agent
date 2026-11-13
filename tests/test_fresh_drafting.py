from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from filing_agent.corpus.parse import ParsedDoc, ParsedPage
from filing_agent.fresh.drafting import (
    TextCandidate,
    check_candidate,
    choose_candidate,
    discussion_pages,
    draft_all,
    draft_filing,
    failures,
    load_drafts,
    save_drafts,
    text_question,
)
from filing_agent.fresh.filings import FreshFiling

MDNA = (
    "Net sales increased 8% to $1.2 billion, primarily due to higher volume in Asia, partially "
    "offset by unfavorable currency. Operating margin improved, driven by pricing. Costs "
    "attributable to the restructuring fell as a result of lower severance, reflecting the "
    "completed program."
)
FILING = FreshFiling(
    doc_name="ACME_2026Q3_10Q",
    company="Acme",
    cik=1,
    form="10-Q",
    accession="0000000001-26-000002",
    filing_date=date(2026, 10, 30),
    report_date=date(2026, 9, 30),
    fiscal_year=2026,
    fiscal_period="Q3",
    url="https://www.sec.gov/acme.htm",
    size=1,
    sha256="0" * 64,
)


def _doc() -> ParsedDoc:
    explain = "primarily due to x. driven by y. partially offset by z. as a result of w."
    texts: list[tuple[str, str | None]] = [
        (f"Cover {explain} {explain}", None),  # cover pages never qualify
        ("Table of contents", None),
        (f"Statements of Income {explain} {explain}", "income_statement"),
        (MDNA, None),  # 6 explanations
        (explain, None),  # 4
        ("primarily due to x. driven by y.", None),  # 2: below the threshold
    ]
    pages = [
        ParsedPage(page=i, text=t, layout=t, statement=s)  # type: ignore[arg-type]
        for i, (t, s) in enumerate(texts)
    ]
    return ParsedDoc(
        doc_name=FILING.doc_name,
        parser_version="test",
        pdfplumber_version="test",
        source_size=1,
        n_pages=len(pages),
        seconds=0.0,
        pages=pages,
    )


GOOD = {
    "question": "What drove Acme's net sales change in the three months ended September 30, 2026?",
    "answer": "Net sales increased 8% to $1.2 billion, primarily due to higher volume in Asia, "
    "partially offset by unfavorable currency.",
    "evidence_quote": "Net sales increased 8% to $1.2 billion, primarily due to higher volume in "
    "Asia, partially offset by unfavorable currency.",
    "key_figures": ["8%", "$1.2 billion"],
    "topic": "sales drivers",
}


def test_discussion_pages_rank_explanatory_text_outside_statements() -> None:
    assert discussion_pages(_doc()) == [3, 4]


def test_a_good_candidate_passes_every_check() -> None:
    checked = check_candidate(TextCandidate(**GOOD), MDNA, FILING)
    assert checked.valid, checked.problems


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ({"evidence_quote": "Net sales increased 9% to $1.2 billion."}, "quote not found"),
        ({"key_figures": ["8%", "$1.3 billion"]}, "figures not in the quote"),
        (
            {"question": "Why did Acme's net sales rise 8% in the three months ended "
             "September 30, 2026?"},
            "gives away",
        ),
        ({"question": "What drove net sales in the three months ended September 30, 2026?"},
         "does not name the company"),
        ({"question": "What drove Acme's net sales change in the third quarter?"},
         "does not name the period"),
    ],
)  # fmt: skip
def test_checks_catch_unusable_candidates(change: dict[str, Any], problem: str) -> None:
    checked = check_candidate(TextCandidate(**(GOOD | change)), MDNA, FILING)
    assert any(problem in p for p in checked.problems), checked.problems


def test_quotes_match_across_typographic_differences() -> None:
    page = "Revenue grew — primarily due to Acme’s new “Pro” plan."
    quote = 'Revenue grew - primarily due to Acme\'s new "Pro" plan.'
    candidate = TextCandidate(**(GOOD | {"evidence_quote": quote, "key_figures": []}))
    assert "quote not found on the page" not in check_candidate(candidate, page, FILING).problems


class FakeClient:
    def __init__(self, text: str, *, model: str = "claude-opus-5-5", stop: str = "end_turn"):
        self.calls: list[dict[str, Any]] = []
        response = SimpleNamespace(
            model=model,
            stop_reason=stop,
            usage=SimpleNamespace(input_tokens=1_000, output_tokens=500),
            content=[SimpleNamespace(type="text", text=text)],
        )

        def create(**kwargs: Any) -> SimpleNamespace:
            self.calls.append(kwargs)
            return response

        self.messages = SimpleNamespace(create=create)


def _output(*candidates: dict[str, Any]) -> str:
    return json.dumps({"candidates": list(candidates)})


def test_draft_filing_checks_candidates_and_prices_the_call() -> None:
    bad = GOOD | {"evidence_quote": "invented text"}
    client = FakeClient(_output(bad, GOOD))
    draft = draft_filing(client, FILING, _doc())
    assert draft.page in (3, 4) and draft.error is None
    assert draft.cost_usd == pytest.approx((1_000 * 4 + 500 * 20) / 1e6)
    assert [c.valid for c in draft.candidates] == [False, True]
    assert draft.chosen() == (2, draft.candidates[1])
    assert draft.chosen(1) == (1, draft.candidates[0])  # a reviewer may pick another one
    (call,) = client.calls
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert f"Page {draft.page} of the filing" in call["messages"][0]["content"]


@pytest.mark.parametrize(
    ("client", "error"),
    [
        (FakeClient(_output(GOOD), stop="max_tokens"), "stopped with max_tokens"),
        (FakeClient(_output(GOOD), model="claude-sonnet-5-5"), "served by claude-sonnet-5-5"),
        (FakeClient("not json"), "did not match the schema"),
    ],
)
def test_draft_filing_records_errors(client: FakeClient, error: str) -> None:
    draft = draft_filing(client, FILING, _doc())
    assert draft.error is not None and error in draft.error
    assert draft.chosen() is None
    assert FILING.doc_name in failures([draft])


def test_draft_all_respects_the_cost_cap_and_round_trips(tmp_path: Path) -> None:
    work = [(FILING, _doc())]
    assert draft_all(FakeClient(_output(GOOD)), work, max_cost_usd=0.0) == []
    drafts = draft_all(FakeClient(_output(GOOD)), work, max_cost_usd=1.0, workers=1)
    assert len(drafts) == 1 and failures(drafts) == {}
    path = tmp_path / "drafts.jsonl"
    save_drafts(path, drafts)
    assert load_drafts(path) == {FILING.doc_name: drafts[0]}


def test_choose_candidate_rechecks_and_honours_a_reviewers_pick() -> None:
    draft = draft_filing(
        FakeClient(_output(GOOD | {"evidence_quote": "invented"}, GOOD)), FILING, _doc()
    )
    page = _doc().pages[draft.page].text
    number, chosen = choose_candidate(draft, page, FILING) or (0, None)
    assert number == 2 and chosen is not None and chosen.valid
    number, picked = choose_candidate(draft, page, FILING, 1) or (0, None)
    assert number == 1 and picked is not None and not picked.valid  # kept: a reviewer chose it
    assert choose_candidate(draft, "a different page", FILING) is None  # checks run again


def test_text_question_carries_quote_page_and_draft_details() -> None:
    draft = draft_filing(FakeClient(_output(GOOD)), FILING, _doc())
    page = _doc().pages[draft.page].text
    number, candidate = choose_candidate(draft, page, FILING) or (0, None)
    assert candidate is not None
    question = text_question(FILING, draft, number, candidate, page)
    assert question.id == "fresh_ACME_2026Q3_10Q_text"
    assert question.question_type == "novel-generated"
    (evidence,) = question.evidence
    assert (evidence.page, evidence.text, evidence.full_page_text) == (
        draft.page,
        GOOD["evidence_quote"],
        page,
    )
    assert question.meta is not None and question.meta["kind"] == "text"
    assert question.meta["candidate"] == 1 and question.meta["model"] == "claude-opus-5-5"


def test_answer_numbers_must_be_on_the_page() -> None:
    invented = GOOD | {"answer": GOOD["answer"] + " Margins rose to 41.7%."}
    problems = check_candidate(TextCandidate(**invented), MDNA, FILING).problems
    assert any("answer numbers not on the page: ['41.7']" in p for p in problems)
