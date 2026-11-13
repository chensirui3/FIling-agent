from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from filing_agent.config import Settings
from filing_agent.data import financebench as fb
from filing_agent.eval.datasets import (
    all_documents,
    all_questions,
    load_dataset,
    split_path,
)
from filing_agent.eval.experiment import ExperimentConfig
from filing_agent.eval.report import accuracy_interval
from filing_agent.fresh.benchmark import save_questions
from filing_agent.fresh.filings import FreshFiling, FreshManifest
from tests.factories import question, raw_document, raw_question


def _settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    monkeypatch.setenv("FA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FA_CONFIGS_DIR", str(tmp_path / "configs"))
    settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]
    root = settings.financebench_dir
    (root / "data").mkdir(parents=True)
    rows = [raw_question("financebench_id_00001", "Acme", "ACME_2022_10K")]
    (root / fb.QUESTIONS_FILE).write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )
    (root / fb.DOCUMENTS_FILE).write_text(
        json.dumps(raw_document("ACME_2022_10K", "Acme")) + "\n", encoding="utf-8"
    )
    return settings


def test_fresh_questions_and_filings_join_financebench(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings(monkeypatch, tmp_path)
    assert set(all_questions(settings)) == {"financebench_id_00001"}  # no fresh set yet

    fresh_q = question("fresh_ACME_2026Q3_10Q_text", "Acme", "ACME_2026Q3_10Q")
    save_questions(settings.fresh_questions_file, [fresh_q])
    FreshManifest(
        rule="test",
        filed_after=date(2026, 8, 1),
        as_of=date(2026, 11, 12),
        filings=[
            FreshFiling(
                doc_name="ACME_2026Q3_10Q",
                company="Acme",
                cik=1,
                form="10-Q",
                accession="0000000001-26-000003",
                filing_date=date(2026, 10, 30),
                report_date=date(2026, 9, 30),
                fiscal_year=2026,
                fiscal_period="Q3",
                url="https://www.sec.gov/acme.htm",
                size=1,
                sha256="0" * 64,
            )
        ],
    ).save(settings.fresh_manifest_file)

    assert set(all_questions(settings)) == {"financebench_id_00001", fresh_q.id}
    assert [q.id for q in load_dataset("fresh", settings)] == [fresh_q.id]
    assert [q.id for q in load_dataset("financebench", settings)] == ["financebench_id_00001"]
    documents = all_documents(settings)
    assert documents["ACME_2026Q3_10Q"].doc_type == "10q"
    assert documents["ACME_2026Q3_10Q"].doc_period == 2026
    assert "ACME_2022_10K" in documents
    assert split_path("fresh", settings).name == "fresh_v1.json"


def test_dataset_field_keeps_financebench_hashes_stable() -> None:
    base = {"name": "b4_full_agent", "description": "x"}
    implicit = ExperimentConfig.model_validate(base)
    explicit = ExperimentConfig.model_validate(base | {"dataset": "financebench"})
    fresh = ExperimentConfig.model_validate(base | {"dataset": "fresh"})
    assert implicit.config_hash() == explicit.config_hash()
    assert fresh.config_hash() != implicit.config_hash()


def test_accuracy_interval_is_wilson_for_single_answers() -> None:
    low, high = accuracy_interval([0.0] * 114)
    assert low == 0.0 and high == pytest.approx(0.0326, abs=5e-4)  # not a zero-width interval
    low, high = accuracy_interval([1.0] * 48 + [0.0] * 2)
    assert (round(low, 3), round(high, 3)) == (0.865, 0.989)
    low, high = accuracy_interval([0.5, 1.0, 1.0, 0.0])  # repetitions: bootstrap over questions
    assert 0.0 <= low < high <= 1.0
