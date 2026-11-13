"""The question sets experiments run on: FinanceBench and the fresh (post-cutoff) set.

Question ids never collide (`financebench_id_...` vs `fresh_...`), so grading and reports look
answers up in both sets at once and work for a run on either.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from filing_agent.config import Settings, get_settings
from filing_agent.data import financebench as fb
from filing_agent.fresh import benchmark as fresh
from filing_agent.fresh.filings import FreshManifest

Dataset = Literal["financebench", "fresh", "fresh_hard"]
SPLIT_FILES: dict[str, str] = {
    "financebench": "financebench_v1.json",
    "fresh": "fresh_v1.json",
    "fresh_hard": "fresh_hard_v1.json",
}


def load_dataset(dataset: Dataset, settings: Settings | None = None) -> list[fb.Question]:
    settings = settings or get_settings()
    if dataset == "fresh":
        return fresh.load_questions(settings.fresh_questions_file)
    if dataset == "fresh_hard":
        return fresh.load_questions(settings.fresh_hard_questions_file)
    return fb.load_questions(settings.financebench_dir)


def split_path(dataset: Dataset, settings: Settings | None = None) -> Path:
    return (settings or get_settings()).splits_dir / SPLIT_FILES[dataset]


def all_questions(settings: Settings | None = None) -> dict[str, fb.Question]:
    """Every known question by id: FinanceBench's, plus the fresh set's once it is built."""
    settings = settings or get_settings()
    questions = {q.id: q for q in fb.load_questions(settings.financebench_dir)}
    for path in (settings.fresh_questions_file, settings.fresh_hard_questions_file):
        if path.is_file():
            questions |= {q.id: q for q in fresh.load_questions(path)}
    return questions


def all_documents(settings: Settings | None = None) -> dict[str, fb.Document]:
    """Filing metadata by doc_name: FinanceBench's documents plus the fresh filings."""
    settings = settings or get_settings()
    documents = fb.load_documents(settings.financebench_dir)
    if settings.fresh_manifest_file.is_file():
        for filing in FreshManifest.load(settings.fresh_manifest_file).filings:
            documents[filing.doc_name] = fb.Document(
                doc_name=filing.doc_name,
                company=filing.company,
                doc_type=filing.doc_type,
                doc_period=filing.fiscal_year,
                doc_link=filing.url,
            )
    return documents
