from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from filing_agent.data import financebench as fb
from tests.factories import raw_document, raw_question


def test_git_blob_sha1_matches_git_hash_object() -> None:
    assert fb.git_blob_sha1(b"") == "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
    assert fb.git_blob_sha1(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"


def test_question_from_raw_maps_fields_and_zero_indexed_pages() -> None:
    q = fb.question_from_raw(raw_question("fb_1", "Acme", "ACME_2020_10K", pages=(7, 0, 7)))
    assert q.id == "fb_1"
    assert q.evidence[0].page == 7
    assert q.evidence_pages == [0, 7]
    assert q.evidence[0].full_page_text == "Full page 7"


def test_document_type_is_normalized() -> None:
    doc = fb.document_from_raw(raw_document("ACME_2020_Q4", "Acme", doc_type="Earnings"))
    assert doc.doc_type == "earnings"
    assert doc.doc_period == 2020


class FakeGitHub:
    """Serves the contents API and raw files for an in-memory repository."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files
        self.served: dict[str, bytes] = dict(files)
        self.fail_first: set[str] = set()
        self.requests: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(str(request.url))
        path = request.url.path
        if request.url.host == "api.github.com":
            directory = path.removeprefix(f"/repos/{fb.REPO}/contents/")
            entries = [
                {"type": "file", "path": p, "sha": fb.git_blob_sha1(data), "size": len(data)}
                for p, data in self.files.items()
                if p.rsplit("/", 1)[0] == directory
            ]
            return httpx.Response(200, json=entries)
        if request.url.host == "raw.githubusercontent.com":
            repo_path = path.removeprefix(f"/{fb.REPO}/{fb.COMMIT}/")
            if repo_path in self.fail_first:
                self.fail_first.discard(repo_path)
                return httpx.Response(503)
            if repo_path in self.served:
                return httpx.Response(200, content=self.served[repo_path])
        return httpx.Response(404)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))


def _repo() -> FakeGitHub:
    questions = "\n".join(
        json.dumps(raw_question(f"q{i}", "Acme", "ACME_2020_10K")) for i in range(2)
    )
    documents = "\n".join(
        json.dumps(raw_document(name, "Acme")) for name in ("ACME_2020_10K", "ACME_2021_10K")
    )
    return FakeGitHub(
        {
            fb.QUESTIONS_FILE: questions.encode(),
            fb.DOCUMENTS_FILE: documents.encode(),
            "data/unrelated.txt": b"not needed",
            "pdfs/ACME_2020_10K.pdf": b"%PDF-1.4 needed",
            "pdfs/ACME_2021_10K.pdf": b"%PDF-1.4 only in the full corpus",
        }
    )


def test_download_needed_pdfs_then_skip_verified_files(tmp_path: Path) -> None:
    repo = _repo()
    with repo.client() as client:
        first = fb.download(tmp_path, pdfs="needed", client=client, workers=2, backoff_s=0)
        second = fb.download(tmp_path, pdfs="needed", client=client, workers=2, backoff_s=0)

    expected = [fb.DOCUMENTS_FILE, fb.QUESTIONS_FILE, "pdfs/ACME_2020_10K.pdf"]
    assert first.files == sorted(expected)
    assert sorted(first.downloaded) == sorted(expected)
    assert second.downloaded == []
    assert sorted(second.already_current) == sorted(expected)
    assert not (tmp_path / "pdfs" / "ACME_2021_10K.pdf").exists()
    assert not (tmp_path / "data" / "unrelated.txt").exists()

    manifest = json.loads((tmp_path / fb.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["commit"] == fb.COMMIT
    assert set(manifest["files"]) == set(expected)
    assert [q.id for q in fb.load_questions(tmp_path)] == ["q0", "q1"]
    assert set(fb.load_documents(tmp_path)) == {"ACME_2020_10K", "ACME_2021_10K"}


def test_download_all_pdfs(tmp_path: Path) -> None:
    repo = _repo()
    with repo.client() as client:
        report = fb.download(tmp_path, pdfs="all", client=client, backoff_s=0)
    assert "pdfs/ACME_2021_10K.pdf" in report.downloaded


def test_corrupted_file_is_rejected_and_not_written(tmp_path: Path) -> None:
    repo = _repo()
    repo.served["pdfs/ACME_2020_10K.pdf"] = b"%PDF-1.4 tampered"
    with repo.client() as client, pytest.raises(fb.IntegrityError):
        fb.download(tmp_path, pdfs="needed", client=client, backoff_s=0)
    assert not (tmp_path / "pdfs" / "ACME_2020_10K.pdf").exists()


def test_transient_server_errors_are_retried(tmp_path: Path) -> None:
    repo = _repo()
    repo.fail_first.add("pdfs/ACME_2020_10K.pdf")
    with repo.client() as client:
        report = fb.download(tmp_path, pdfs="needed", client=client, backoff_s=0)
    assert "pdfs/ACME_2020_10K.pdf" in report.downloaded


def test_duplicate_document_rows_keep_the_first(tmp_path: Path) -> None:
    rows = [
        raw_document("ACME_2023_annualreport", "Acme", period=2023),
        raw_document("ACME_2023_annualreport", "Acme", period=2022),
        raw_document("ACME_2022_10K", "Acme", period=2022),
    ]
    path = tmp_path / fb.DOCUMENTS_FILE
    path.parent.mkdir(parents=True)
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    docs = fb.load_documents(tmp_path)
    assert list(docs) == ["ACME_2022_10K", "ACME_2023_annualreport"]
    assert docs["ACME_2023_annualreport"].doc_period == 2023
    assert fb.duplicate_documents(tmp_path) == {"ACME_2023_annualreport": 2}


def test_loading_before_download_explains_what_to_do(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="fa data download"):
        fb.load_questions(tmp_path)
