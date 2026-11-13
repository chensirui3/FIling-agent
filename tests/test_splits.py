from __future__ import annotations

from pathlib import Path

from filing_agent.data.financebench import QUESTION_TYPES, Document, Question
from filing_agent.data.splits import load_split, make_company_split, save_split, summarize
from tests.factories import document, question


def _corpus() -> tuple[list[Question], dict[str, Document]]:
    questions: list[Question] = []
    documents: dict[str, Document] = {}
    sizes = [9, 7, 6, 6, 5, 5, 4, 4, 3, 3, 3, 2, 2, 1]
    n = 0
    for c, size in enumerate(sizes):
        company = f"Company{c:02d}"
        for k in range(size):
            doc_type = "10q" if k % 4 == 3 else "10k"
            doc_name = f"{company}_{2020 + k % 3}_{doc_type.upper()}"
            documents.setdefault(doc_name, document(doc_name, company, doc_type=doc_type))
            questions.append(question(f"q{n:03d}", company, doc_name, QUESTION_TYPES[n % 3]))
            n += 1
    return questions, documents


def test_split_is_company_grouped_and_complete() -> None:
    questions, documents = _corpus()
    split = make_company_split(questions, documents, dev_size=20, seed=1, trials=2_000)
    by_id = {q.id: q for q in questions}
    dev_companies = {by_id[i].company for i in split.dev}
    test_companies = {by_id[i].company for i in split.test}
    assert dev_companies.isdisjoint(test_companies)
    assert set(split.dev) | set(split.test) == set(by_id)
    assert set(split.dev).isdisjoint(split.test)
    assert dev_companies == set(split.dev_companies)
    assert abs(len(split.dev) - 20) <= 2


def test_split_is_deterministic_for_a_seed() -> None:
    questions, documents = _corpus()
    a = make_company_split(questions, documents, dev_size=20, seed=7, trials=1_000)
    b = make_company_split(questions, documents, dev_size=20, seed=7, trials=1_000)
    assert a == b


def test_split_roughly_preserves_question_type_mix() -> None:
    questions, documents = _corpus()
    split = make_company_split(questions, documents, dev_size=21, seed=3, trials=5_000)
    dev_types = summarize(split, questions, documents)["dev"]["question_type"]
    assert all(abs(dev_types.get(t, 0) - 7) <= 2 for t in QUESTION_TYPES)


def test_split_round_trips_through_json(tmp_path: Path) -> None:
    questions, documents = _corpus()
    split = make_company_split(questions, documents, dev_size=20, seed=2, trials=500)
    path = tmp_path / "split.json"
    save_split(split, path, summarize(split, questions, documents), dev_size=20)
    assert load_split(path) == split
