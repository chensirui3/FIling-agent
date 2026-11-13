"""Synthetic FinanceBench-shaped records for tests (no benchmark content is copied here)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from filing_agent.data.financebench import Document, Question, document_from_raw, question_from_raw


def make_pdf(pages: Sequence[Sequence[str]]) -> bytes:
    """A minimal valid PDF: one Helvetica text line per string, one page per inner list."""

    def escape(line: str) -> str:
        return line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    n = len(pages)
    font_id = 3 + 2 * n
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(n))
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode(),
    ]
    for i, lines in enumerate(pages):
        ops = ["BT", "/F1 11 Tf", "14 TL", "72 740 Td"]
        ops += [f"({escape(line)}) Tj T*" for line in lines]
        ops.append("ET")
        stream = "\n".join(ops).encode("latin-1")
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {4 + 2 * i} 0 R "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>".encode()
        )
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objects.append(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
    )

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref_at,
    )
    return bytes(out)


def raw_question(
    qid: str,
    company: str,
    doc_name: str,
    question_type: str = "metrics-generated",
    answer: str = "$100.00",
    pages: tuple[int, ...] = (3,),
) -> dict[str, Any]:
    return {
        "financebench_id": qid,
        "company": company,
        "doc_name": doc_name,
        "question_type": question_type,
        "question_reasoning": None,
        "domain_question_num": None,
        "question": f"What is the FY2020 revenue for {company}?",
        "answer": answer,
        "justification": None,
        "dataset_subset_label": "OPEN_SOURCE",
        "evidence": [
            {
                "evidence_text": f"Revenue {i}",
                "doc_name": doc_name,
                "evidence_page_num": page,
                "evidence_text_full_page": f"Full page {page}",
            }
            for i, page in enumerate(pages)
        ],
    }


def raw_document(doc_name: str, company: str, doc_type: str = "10k", period: int = 2020) -> dict:
    return {
        "doc_name": doc_name,
        "company": company,
        "gics_sector": "Industrials",
        "doc_type": doc_type,
        "doc_period": period,
        "doc_link": f"https://example.com/{doc_name}.pdf",
    }


def question(*args: Any, **kwargs: Any) -> Question:
    return question_from_raw(raw_question(*args, **kwargs))


def document(*args: Any, **kwargs: Any) -> Document:
    return document_from_raw(raw_document(*args, **kwargs))
