"""FinanceBench open-source sample: pinned download, integrity checks and typed loading.

Source: https://github.com/patronus-ai/financebench (CC-BY-NC-4.0). Files are fetched at a pinned
commit, and each one is checked against the git blob SHA-1 that the GitHub API reports for it.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import time
from collections import Counter
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

import httpx
from pydantic import BaseModel, Field

REPO = "patronus-ai/financebench"
# Head of `main` on 2024-12-03 ("Merge pull request #10 from patronus-ai/pdf").
COMMIT = "cc39aeb4afdf33909ee1412188bf89035950c2eb"

QUESTIONS_FILE = "data/financebench_open_source.jsonl"
DOCUMENTS_FILE = "data/financebench_document_information.jsonl"
PDF_DIR = "pdfs"
MANIFEST_NAME = "manifest.json"

QuestionType = Literal["metrics-generated", "domain-relevant", "novel-generated"]
QUESTION_TYPES: tuple[QuestionType, ...] = (
    "metrics-generated",
    "domain-relevant",
    "novel-generated",
)
PdfSelection = Literal["none", "needed", "all"]

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


# --------------------------------------------------------------------------------------------
# Typed records
# --------------------------------------------------------------------------------------------


class Evidence(BaseModel):
    """One gold evidence passage for a question."""

    doc_name: str
    page: int = Field(description="Zero-indexed page number in the source PDF.")
    text: str
    full_page_text: str


class Question(BaseModel):
    """One benchmark question with its gold answer and evidence."""

    id: str
    company: str
    doc_name: str
    question_type: QuestionType
    question_reasoning: str | None = None
    domain_question_num: str | None = None
    question: str
    answer: str
    justification: str | None = None
    evidence: list[Evidence]
    meta: dict[str, Any] | None = None  # generator details for non-FinanceBench questions

    @property
    def evidence_pages(self) -> list[int]:
        return sorted({e.page for e in self.evidence})


class Document(BaseModel):
    """Metadata for one filing in the FinanceBench corpus."""

    doc_name: str
    company: str
    gics_sector: str | None = None
    doc_type: str = Field(description="10k, 10q, 8k, earnings or 10k_annualreport.")
    doc_period: int
    doc_link: str | None = None


def question_from_raw(row: dict[str, Any]) -> Question:
    return Question(
        id=row["financebench_id"],
        company=row["company"],
        doc_name=row["doc_name"],
        question_type=row["question_type"],
        question_reasoning=row.get("question_reasoning"),
        domain_question_num=row.get("domain_question_num"),
        question=row["question"],
        answer=row["answer"],
        justification=row.get("justification"),
        evidence=[
            Evidence(
                doc_name=e["doc_name"],
                page=int(e["evidence_page_num"]),
                text=e["evidence_text"],
                full_page_text=e.get("evidence_text_full_page") or "",
            )
            for e in row.get("evidence") or []
        ],
    )


def document_from_raw(row: dict[str, Any]) -> Document:
    return Document(
        doc_name=row["doc_name"],
        company=row["company"],
        gics_sector=row.get("gics_sector"),
        doc_type=str(row["doc_type"]).strip().lower(),
        doc_period=int(row["doc_period"]),
        doc_link=row.get("doc_link"),
    )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(f"{path} not found. Run `fa data download` first.")
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def load_questions(root: Path) -> list[Question]:
    """All questions, sorted by id."""
    return sorted(
        (question_from_raw(r) for r in _read_jsonl(root / QUESTIONS_FILE)), key=lambda q: q.id
    )


def load_documents(root: Path) -> dict[str, Document]:
    """Document metadata keyed by doc_name, in sorted order.

    Upstream lists one filing twice with conflicting `doc_period` values; the first row wins.
    `duplicate_documents` reports such cases.
    """
    docs: dict[str, Document] = {}
    for row in _read_jsonl(root / DOCUMENTS_FILE):
        doc = document_from_raw(row)
        docs.setdefault(doc.doc_name, doc)
    return dict(sorted(docs.items()))


def duplicate_documents(root: Path) -> dict[str, int]:
    """doc_name -> row count, for filings listed more than once in the metadata file."""
    counts = Counter(row["doc_name"] for row in _read_jsonl(root / DOCUMENTS_FILE))
    return {name: n for name, n in sorted(counts.items()) if n > 1}


def pdf_path(root: Path, doc_name: str) -> Path:
    return root / PDF_DIR / f"{doc_name}.pdf"


# --------------------------------------------------------------------------------------------
# Download with integrity checks
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RemoteFile:
    path: str  # repository-relative, forward slashes
    sha: str  # git blob SHA-1
    size: int


class IntegrityError(RuntimeError):
    """A downloaded file did not match its expected git blob SHA-1."""


def git_blob_sha1(data: bytes) -> str:
    """SHA-1 of the git blob object for `data` (what `git hash-object` prints)."""
    digest = hashlib.sha1(usedforsecurity=False)
    digest.update(b"blob %d\0" % len(data))
    digest.update(data)
    return digest.hexdigest()


class FinanceBenchDownloader:
    """Fetches repository files at a pinned commit and verifies each one."""

    def __init__(
        self,
        dest: Path,
        client: httpx.Client,
        *,
        commit: str = COMMIT,
        github_token: str | None = None,
        max_attempts: int = 4,
        backoff_s: float = 1.0,
    ) -> None:
        self.dest = dest
        self.client = client
        self.commit = commit
        self.github_token = github_token
        self.max_attempts = max_attempts
        self.backoff_s = backoff_s

    def _get(self, url: str, *, api: bool) -> httpx.Response:
        headers = {"Accept": "application/vnd.github+json"} if api else {}
        if api and self.github_token:
            headers["Authorization"] = f"Bearer {self.github_token}"
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = self.client.get(url, headers=headers, follow_redirects=True)
            except httpx.TransportError:
                if attempt == self.max_attempts:
                    raise
            else:
                if response.status_code < 400:
                    return response
                if response.status_code not in _RETRYABLE_STATUS or attempt == self.max_attempts:
                    response.raise_for_status()
            time.sleep(self.backoff_s * 2 ** (attempt - 1) * (1 + random.random() / 2))
        raise AssertionError("unreachable")

    def list_dir(self, repo_dir: str) -> list[RemoteFile]:
        url = f"https://api.github.com/repos/{REPO}/contents/{quote(repo_dir)}?ref={self.commit}"
        entries = self._get(url, api=True).json()
        files = (
            RemoteFile(path=e["path"], sha=e["sha"], size=int(e["size"]))
            for e in entries
            if e.get("type") == "file"
        )
        return sorted(files, key=lambda f: f.path)

    def local_path(self, remote: RemoteFile) -> Path:
        return self.dest.joinpath(*remote.path.split("/"))

    def is_current(self, remote: RemoteFile) -> bool:
        path = self.local_path(remote)
        if not path.is_file() or path.stat().st_size != remote.size:
            return False
        return git_blob_sha1(path.read_bytes()) == remote.sha

    def fetch(self, remote: RemoteFile) -> Path:
        url = f"https://raw.githubusercontent.com/{REPO}/{self.commit}/{quote(remote.path)}"
        data = self._get(url, api=False).content
        actual = git_blob_sha1(data)
        if actual != remote.sha:
            raise IntegrityError(f"{remote.path}: expected blob {remote.sha}, got {actual}")
        path = self.local_path(remote)
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(path.name + ".part")
        partial.write_bytes(data)
        os.replace(partial, path)
        return path


@dataclass
class DownloadReport:
    commit: str
    downloaded: list[str] = field(default_factory=list)
    already_current: list[str] = field(default_factory=list)

    @property
    def files(self) -> list[str]:
        return sorted(self.downloaded + self.already_current)


ProgressCallback = Callable[[str, bool], None]


def _sync(
    downloader: FinanceBenchDownloader,
    files: Iterable[RemoteFile],
    report: DownloadReport,
    *,
    workers: int,
    on_file: ProgressCallback | None,
) -> None:
    pending: list[RemoteFile] = []
    for remote in files:
        if downloader.is_current(remote):
            report.already_current.append(remote.path)
            if on_file:
                on_file(remote.path, False)
        else:
            pending.append(remote)
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(downloader.fetch, remote): remote for remote in pending}
        for future in as_completed(futures):
            remote = futures[future]
            future.result()
            report.downloaded.append(remote.path)
            if on_file:
                on_file(remote.path, True)


def _update_manifest(dest: Path, commit: str, remotes: Iterable[RemoteFile]) -> None:
    path = dest / MANIFEST_NAME
    manifest: dict[str, Any] = {}
    if path.is_file():
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest.get("commit") != commit:
            manifest = {}
    files: dict[str, Any] = manifest.get("files", {})
    for remote in remotes:
        files[remote.path] = {"sha": remote.sha, "size": remote.size}
    manifest = {
        "repo": REPO,
        "commit": commit,
        "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "files": dict(sorted(files.items())),
    }
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def download(
    dest: Path,
    *,
    pdfs: PdfSelection = "needed",
    client: httpx.Client | None = None,
    commit: str = COMMIT,
    github_token: str | None = None,
    workers: int = 6,
    backoff_s: float = 1.0,
    on_file: ProgressCallback | None = None,
    on_listing: Callable[[int], None] | None = None,
) -> DownloadReport:
    """Mirror the question file, the document metadata and the selected PDFs into `dest`.

    `pdfs="needed"` fetches only the filings referenced by the 150 questions; `"all"` fetches the
    full corpus (used for the shared-corpus setting). Files already present and verified are kept.
    """
    owns_client = client is None
    http = client or httpx.Client(
        timeout=httpx.Timeout(120.0, connect=20.0),
        headers={"User-Agent": "filing-agent/0.1 (FinanceBench downloader)"},
    )
    try:
        downloader = FinanceBenchDownloader(
            dest, http, commit=commit, github_token=github_token, backoff_s=backoff_s
        )
        data_listing = {f.path: f for f in downloader.list_dir("data")}
        missing = {QUESTIONS_FILE, DOCUMENTS_FILE} - data_listing.keys()
        if missing:
            raise FileNotFoundError(f"Not found in {REPO}@{commit[:7]}: {sorted(missing)}")
        metadata = [data_listing[QUESTIONS_FILE], data_listing[DOCUMENTS_FILE]]
        report = DownloadReport(commit=commit)
        _sync(downloader, metadata, report, workers=1, on_file=None)

        pdf_files: list[RemoteFile] = []
        if pdfs != "none":
            pdf_files = [f for f in downloader.list_dir(PDF_DIR) if f.path.endswith(".pdf")]
            if pdfs == "needed":
                needed = {f"{PDF_DIR}/{q.doc_name}.pdf" for q in load_questions(dest)}
                absent = needed - {f.path for f in pdf_files}
                if absent:
                    raise FileNotFoundError(f"PDFs missing upstream: {sorted(absent)}")
                pdf_files = [f for f in pdf_files if f.path in needed]

        if on_listing:
            on_listing(len(pdf_files))
        _sync(downloader, pdf_files, report, workers=workers, on_file=on_file)
        _update_manifest(dest, commit, metadata + pdf_files)
        return report
    finally:
        if owns_client:
            http.close()
