"""PDF filings -> per-page text, tagged with the page's units line and statement type.

Each page keeps two renderings: `text` (reading order, compact; used for search) and `layout`
(column-aligned; used when the agent reads a table). Page numbers are zero-indexed, matching
FinanceBench's `evidence_page_num`.

Some PDFs carry broken font metrics, and pdfplumber then splits numbers with stray spaces
("$ 2 4,020", "7 ,919"). Pages showing that pattern are re-extracted with PDFium, and the page
records which extractor produced its text.
"""

from __future__ import annotations

import json
import os
import re
import textwrap
import time
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

import pdfplumber
import pypdfium2
from pydantic import BaseModel

PARSER_VERSION = "2"

Scale = Literal["thousands", "millions", "billions"]
Statement = Literal[
    "balance_sheet",
    "income_statement",
    "comprehensive_income",
    "cash_flow_statement",
    "equity_statement",
]


Extractor = Literal["pdfplumber", "pypdfium2", "html"]


class ParsedPage(BaseModel):
    page: int
    text: str
    layout: str
    units: Scale | None = None
    units_line: str | None = None
    statement: Statement | None = None
    extractor: Extractor = "pdfplumber"
    error: str | None = None


class ParsedDoc(BaseModel):
    doc_name: str
    parser_version: str
    pdfplumber_version: str
    source_size: int
    n_pages: int
    seconds: float
    pages: list[ParsedPage]

    @property
    def empty_pages(self) -> int:
        return sum(1 for p in self.pages if not p.text.strip())


# --------------------------------------------------------------------------------------------
# Page tagging
# --------------------------------------------------------------------------------------------

_SCALE = r"(?P<scale>thousands|millions|billions)"
_UNITS_IN_PARENS = re.compile(r"\([^()]{0,60}?\b" + _SCALE + r"\b[^()]{0,80}\)", re.IGNORECASE)
_UNITS_AT_START = re.compile(
    r"^\s*(?:\$|u\.?s\.?\s*\$|(?:dollars?|amounts?|in)\b)[^.]{0,40}?\b" + _SCALE + r"\b",
    re.IGNORECASE,
)
_MAX_UNITS_LINE = 100


def detect_units(text: str) -> tuple[Scale | None, str | None]:
    """First line that states the page's scale, e.g. "(In millions, except per share data)"."""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or len(line) > _MAX_UNITS_LINE:
            continue
        match = _UNITS_IN_PARENS.search(line) or _UNITS_AT_START.search(line)
        if match:
            return cast(Scale, match.group("scale").lower()), line
    return None, None


_STATEMENT_PATTERNS: tuple[tuple[Statement, re.Pattern[str]], ...] = (
    (
        "cash_flow_statement",
        re.compile(r"statements?\s+of\s+(?:consolidated\s+)?cash\s*flow", re.IGNORECASE),
    ),
    (
        "comprehensive_income",
        re.compile(
            r"statements?\s+of\s+(?:consolidated\s+)?comprehensive\s+(?:income|loss|earnings)",
            re.IGNORECASE,
        ),
    ),
    (
        "income_statement",
        re.compile(
            r"statements?\s+of\s+(?:consolidated\s+)?(?:income|operations|earnings)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "balance_sheet",
        re.compile(
            r"balance\s+sheets?\b|statements?\s+of\s+(?:consolidated\s+)?financial\s+"
            r"(?:position|condition)",
            re.IGNORECASE,
        ),
    ),
    (
        "equity_statement",
        re.compile(
            r"statements?\s+of\s+(?:changes\s+in\s+)?(?:(?:share|stock)holders['’]?\s+)?equity",
            re.IGNORECASE,
        ),
    ),
)
_TITLE_LINES = 8
_MAX_TITLE_LINE = 100
_INDEX_LINE = re.compile(r"\.{4,}|\s\d{1,3}\s*$")


def detect_statement(text: str) -> Statement | None:
    """Primary-statement tag from the page's title lines; None for notes, MD&A and indexes."""
    head = [line.strip() for line in text.splitlines() if line.strip()][:_TITLE_LINES]
    found: list[Statement] = []
    for line in head:
        if len(line) > _MAX_TITLE_LINE or _INDEX_LINE.search(line):
            continue
        for name, pattern in _STATEMENT_PATTERNS:
            match = pattern.search(line)
            if match and match.start() <= 40:
                found.append(name)
                break
    # A head that names several statements is a table of contents or an auditor's report.
    return found[0] if len(set(found)) == 1 else None


_SPLIT_NUMBER = re.compile(r"(?<![\d.,])\d{1,3} ,\d{3}\b|\$ ?\d \d{1,2},\d{3}\b")


def has_split_numbers(text: str) -> bool:
    """True if numbers look broken by stray spaces ("7 ,919", "$ 2 4,020")."""
    return bool(_SPLIT_NUMBER.search(text))


def _pdfium_text(document: pypdfium2.PdfDocument, index: int) -> str:
    page = document[index]
    textpage = page.get_textpage()
    try:
        return textpage.get_text_range().replace("\r\n", "\n").replace("\r", "\n")
    finally:
        textpage.close()
        page.close()


def _clean_layout(layout: str) -> str:
    lines = [line.rstrip() for line in layout.splitlines()]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return re.sub(r"\n{3,}", "\n\n", textwrap.dedent("\n".join(lines)))


# --------------------------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------------------------


def _tagged_page(index: int, text: str, layout: str, extractor: Extractor) -> ParsedPage:
    units, units_line = detect_units(text)
    return ParsedPage(
        page=index,
        text=text,
        layout=layout,
        units=units,
        units_line=units_line,
        statement=detect_statement(text),
        extractor=extractor,
    )


def _parse_with_pdfium(path: Path) -> list[ParsedPage]:
    """Whole-document fallback for files pdfminer cannot open (e.g. damaged xref tables)."""
    document = pypdfium2.PdfDocument(str(path))
    try:
        pages: list[ParsedPage] = []
        for index in range(len(document)):
            try:
                text = _pdfium_text(document, index)
                pages.append(_tagged_page(index, text, text, "pypdfium2"))
            except Exception as exc:
                pages.append(
                    ParsedPage(
                        page=index,
                        text="",
                        layout="",
                        extractor="pypdfium2",
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
        return pages
    finally:
        document.close()


def parse_pdf(path: Path, doc_name: str | None = None) -> ParsedDoc:
    start = time.perf_counter()
    try:
        pages = _parse_with_pdfplumber(path)
    except Exception as plumber_error:
        try:
            pages = _parse_with_pdfium(path)
        except Exception:
            raise plumber_error from None
    return ParsedDoc(
        doc_name=doc_name or path.stem,
        parser_version=PARSER_VERSION,
        pdfplumber_version=pdfplumber.__version__,
        source_size=path.stat().st_size,
        n_pages=len(pages),
        seconds=round(time.perf_counter() - start, 2),
        pages=pages,
    )


def _parse_with_pdfplumber(path: Path) -> list[ParsedPage]:
    pages: list[ParsedPage] = []
    fallback: pypdfium2.PdfDocument | None = None
    try:
        with pdfplumber.open(path) as pdf:
            for index, page in enumerate(pdf.pages):
                try:
                    text = page.extract_text() or ""
                    layout = _clean_layout(page.extract_text(layout=True) or "")
                    extractor: Extractor = "pdfplumber"
                    if has_split_numbers(text):
                        fallback = fallback or pypdfium2.PdfDocument(str(path))
                        alternative = _pdfium_text(fallback, index)
                        if alternative.strip() and not has_split_numbers(alternative):
                            text, layout, extractor = alternative, alternative, "pypdfium2"
                    pages.append(_tagged_page(index, text, layout, extractor))
                except Exception as exc:  # one unreadable page must not lose the whole filing
                    pages.append(
                        ParsedPage(
                            page=index, text="", layout="", error=f"{type(exc).__name__}: {exc}"
                        )
                    )
                finally:
                    page.close()
    finally:
        if fallback is not None:
            fallback.close()
    return pages


def parsed_path(parsed_dir: Path, doc_name: str) -> Path:
    return parsed_dir / f"{doc_name}.json"


FAILURES_FILE = "_failures.json"


def load_parse_failures(parsed_dir: Path) -> dict[str, str]:
    """doc_name -> error for filings that could not be parsed by any extractor."""
    path = parsed_dir / FAILURES_FILE
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def record_parse_failures(parsed_dir: Path, outcomes: Iterable[ParseOutcome]) -> dict[str, str]:
    failures = load_parse_failures(parsed_dir)
    for outcome in outcomes:
        if outcome.status == "failed":
            failures[outcome.doc_name] = outcome.error or "unknown error"
        else:
            failures.pop(outcome.doc_name, None)
    parsed_dir.mkdir(parents=True, exist_ok=True)
    (parsed_dir / FAILURES_FILE).write_text(
        json.dumps(dict(sorted(failures.items())), indent=2) + "\n", encoding="utf-8"
    )
    return failures


def load_parsed(parsed_dir: Path, doc_name: str) -> ParsedDoc:
    path = parsed_path(parsed_dir, doc_name)
    if not path.is_file():
        raise FileNotFoundError(f"{path} not found. Run `fa data parse` first.")
    return ParsedDoc.model_validate_json(path.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class ParseOutcome:
    doc_name: str
    status: Literal["parsed", "cached", "failed"]
    n_pages: int = 0
    empty_pages: int = 0
    seconds: float = 0.0
    error: str | None = None


def _is_current(out_file: Path, pdf_file: Path) -> ParsedDoc | None:
    if not out_file.is_file():
        return None
    try:
        doc = ParsedDoc.model_validate_json(out_file.read_text(encoding="utf-8"))
    except ValueError:
        return None
    if doc.parser_version != PARSER_VERSION or doc.source_size != pdf_file.stat().st_size:
        return None
    return doc


def _parse_one(pdf_file: str, out_file: str, doc_name: str) -> ParseOutcome:
    """Worker entry point (module level so it can be pickled for a process pool)."""
    try:
        doc = parse_pdf(Path(pdf_file), doc_name)
    except Exception as exc:
        return ParseOutcome(doc_name, "failed", error=f"{type(exc).__name__}: {exc}")
    out = Path(out_file)
    partial = out.with_name(out.name + ".part")
    partial.write_text(doc.model_dump_json(), encoding="utf-8")
    os.replace(partial, out)
    return ParseOutcome(doc_name, "parsed", doc.n_pages, doc.empty_pages, doc.seconds)


def default_workers() -> int:
    return max(1, min(12, (os.cpu_count() or 2) - 2))


def parse_corpus(
    pdf_files: Mapping[str, Path],
    parsed_dir: Path,
    *,
    workers: int | None = None,
    force: bool = False,
    on_done: Callable[[ParseOutcome], None] | None = None,
) -> list[ParseOutcome]:
    """Parse every PDF in `pdf_files` (doc_name -> path), skipping up-to-date outputs."""
    parsed_dir.mkdir(parents=True, exist_ok=True)
    outcomes: list[ParseOutcome] = []
    todo: list[tuple[str, Path, Path]] = []
    for doc_name, pdf_file in sorted(pdf_files.items()):
        out_file = parsed_path(parsed_dir, doc_name)
        cached = None if force else _is_current(out_file, pdf_file)
        if cached is not None:
            outcome = ParseOutcome(
                doc_name, "cached", cached.n_pages, cached.empty_pages, cached.seconds
            )
            outcomes.append(outcome)
            if on_done:
                on_done(outcome)
        else:
            todo.append((doc_name, pdf_file, out_file))

    todo.sort(key=lambda item: item[1].stat().st_size, reverse=True)  # big files first
    n_workers = workers if workers is not None else default_workers()
    if n_workers <= 1 or len(todo) <= 1:
        for doc_name, pdf_file, out_file in todo:
            outcome = _parse_one(str(pdf_file), str(out_file), doc_name)
            outcomes.append(outcome)
            if on_done:
                on_done(outcome)
    else:
        with ProcessPoolExecutor(max_workers=n_workers) as pool:
            futures = [
                pool.submit(_parse_one, str(pdf_file), str(out_file), doc_name)
                for doc_name, pdf_file, out_file in todo
            ]
            for future in as_completed(futures):
                outcome = future.result()
                outcomes.append(outcome)
                if on_done:
                    on_done(outcome)
    return sorted(outcomes, key=lambda o: o.doc_name)
