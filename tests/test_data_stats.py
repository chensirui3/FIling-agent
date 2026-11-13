import pytest

from filing_agent.data.stats import answer_kind, compute_stats
from tests.factories import document, question


@pytest.mark.parametrize(
    ("answer", "kind"),
    [
        ("$1577.00", "number"),
        ("24.26", "number"),
        ("(1,234)", "number"),
        ("5.1%", "number"),
        ("$8.70 billion", "number"),
        ("Yes, dividends have risen every year.", "yes/no + rationale"),
        ("No. The quick ratio was 0.96.", "yes/no + rationale"),
        ("The consumer segment shrank by 0.9% organically.", "text with figures"),
        ("Operating margin is not a useful metric for a bank.", "text only"),
    ],
)
def test_answer_kind(answer: str, kind: str) -> None:
    assert answer_kind(answer) == kind


def test_compute_stats_counts() -> None:
    questions = [
        question("a", "Acme", "ACME_2020_10K", pages=(1, 2)),
        question("b", "Acme", "ACME_2020_10K", "domain-relevant", "Yes, it is."),
        question("c", "Beta", "BETA_2021_10Q", "novel-generated", "Revenue grew 4%."),
    ]
    documents = {
        "ACME_2020_10K": document("ACME_2020_10K", "Acme"),
        "BETA_2021_10Q": document("BETA_2021_10Q", "Beta", doc_type="10q", period=2021),
    }
    s = compute_stats(questions, documents)
    assert (s.n_questions, s.n_companies, s.n_docs) == (3, 2, 2)
    assert s.doc_types == {"10k": 2, "10q": 1}
    assert s.periods == {2020: 2, 2021: 1}
    assert s.evidence_items == 4
    assert s.multi_page_questions == 1
    assert s.answer_kinds_by_type["metrics-generated"] == {"number": 1}
