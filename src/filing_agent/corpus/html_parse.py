"""EDGAR HTML filings (10-K/10-Q, usually inline XBRL) -> the same per-page records as PDFs.

- Pages follow the filing's own page breaks (CSS page-break-before/after, usually on `<hr>`).
  A filing without them is cut into pages of about 700 words.
- Hidden blocks, such as the inline-XBRL header, are dropped from the text.
- Each table row becomes one line with cells joined by " | ". EDGAR puts the "$", a negative's
  closing ")" and "%" in cells of their own, and they are joined back to their numbers:
  "Net sales | $186,100 | $169,335".

Inline XBRL also tags each reported figure with its concept and period. `parse_html` returns
those facts with the page each one is printed on, plus the filing's contexts and its `dei:`
cover facts (fiscal year and period).
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date

import lxml
from lxml import html as lxml_html
from lxml.html import HtmlElement

from filing_agent.corpus.parse import (
    PARSER_VERSION,
    ParsedDoc,
    ParsedPage,
    Statement,
    detect_statement,
    detect_units,
)

HTML_PARSER_VERSION = f"html-2/{PARSER_VERSION}"
FALLBACK_PAGE_WORDS = 700

_BLOCK = {
    "p", "div", "br", "li", "ul", "ol", "dl", "dt", "dd", "h1", "h2", "h3", "h4", "h5", "h6",
    "section", "article", "center", "blockquote", "pre", "table", "tr", "hr", "body",
}  # fmt: skip
_SKIP = {"script", "style", "head", "title", "noscript", "header"}  # "header" = ix:header
_SPACES = re.compile(r"\s+")
_DASHES = {"—", "–", "-", "‒", "―"}


def _tag(element: HtmlElement) -> str:
    """Lowercased tag without its namespace prefix ("ix:nonFraction" -> "nonfraction")."""
    tag = element.tag if isinstance(element.tag, str) else ""
    return tag.lower().rsplit(":", 1)[-1]


def _style(element: HtmlElement) -> str:
    return (element.get("style") or "").replace(" ", "").lower()


def _hidden(element: HtmlElement) -> bool:
    return _tag(element) in _SKIP or "display:none" in _style(element)


def _clean(text: str) -> str:
    return _SPACES.sub(" ", text.replace("\xa0", " ")).strip()


def merge_cells(cells: list[str]) -> list[str]:
    """Join "$" to the next cell and ")", "%", ")%" to the previous one."""
    merged: list[str] = []
    prefix = ""
    for cell in cells:
        if cell in ("$", "(", "$(", "($"):
            prefix += cell
            continue
        if cell in (")", "%", ")%", "%)") and merged:
            merged[-1] += cell
            continue
        merged.append(prefix + cell)
        prefix = ""
    if prefix:
        merged.append(prefix)
    return merged


# --------------------------------------------------------------------------------------------
# Inline XBRL
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Context:
    id: str
    end: date  # the instant, or the end of the duration
    start: date | None = None  # None for an instant
    dimensions: tuple[tuple[str, str], ...] = ()  # (axis, member) pairs; () = whole company


@dataclass(frozen=True)
class InlineFact:
    concept: str  # "us-gaap:Revenues"
    context: str
    unit: str | None
    decimals: str | None  # "-6", "2", "INF"
    scale: int
    negative: bool  # the `sign="-"` attribute: the displayed figure is the absolute value
    format: str | None
    text: str  # as printed, e.g. "186,100"
    page: int
    line: int = -1  # the line of the page it is printed on (a table row for statement figures)

    def value(self) -> float | None:
        """The fact's value in its unit (dollars, shares, pure), or None if unreadable."""
        text = self.text.strip()
        fmt = (self.format or "").lower()
        if "zero" in fmt or text in _DASHES:
            number = 0.0
        else:
            digits = text.replace(",", "").replace(" ", "")
            if "comma-decimal" in fmt or "numcommadecimal" in fmt:
                digits = text.replace(".", "").replace(" ", "").replace(",", ".")
            try:
                number = float(digits)
            except ValueError:
                return None
        number *= 10.0**self.scale
        return -number if self.negative else number


def _date(text: str) -> date:
    return date.fromisoformat(text.strip()[:10])


def _contexts(root: HtmlElement) -> dict[str, Context]:
    out: dict[str, Context] = {}
    for element in root.iter():
        if _tag(element) != "context" or not element.get("id"):
            continue
        start = end = instant = None
        dims: list[tuple[str, str]] = []
        for child in element.iter():
            tag = _tag(child)
            if tag == "startdate":
                start = _date(child.text_content())
            elif tag == "enddate":
                end = _date(child.text_content())
            elif tag == "instant":
                instant = _date(child.text_content())
            elif tag in ("explicitmember", "typedmember"):
                dims.append((child.get("dimension") or "", _clean(child.text_content())))
        if instant is not None:
            out[element.get("id")] = Context(element.get("id"), instant, None, tuple(dims))
        elif end is not None:
            out[element.get("id")] = Context(element.get("id"), end, start, tuple(dims))
    return out


def _measure(text: str) -> str:
    return text.strip().rsplit(":", 1)[-1]  # "iso4217:USD" -> "USD", "xbrli:shares" -> "shares"


def _units(root: HtmlElement) -> dict[str, str]:
    """unitRef id -> unit as the SEC XBRL API names it: "USD", "USD/shares", "shares", "pure"."""
    out: dict[str, str] = {}
    for element in root.iter():
        if _tag(element) != "unit" or not element.get("id"):
            continue
        numerator = [_measure(m.text_content()) for m in element.iter() if _tag(m) == "measure"]
        divide = [child for child in element.iter() if _tag(child) == "divide"]
        if divide:
            parts = {
                _tag(part): _measure(part.text_content())
                for part in divide[0].iter()
                if _tag(part) in ("unitnumerator", "unitdenominator")
            }
            out[element.get("id")] = (
                f"{parts.get('unitnumerator', '')}/{parts.get('unitdenominator', '')}"
            )
        elif numerator:
            out[element.get("id")] = numerator[0]
    return out


def _dei(root: HtmlElement) -> dict[str, str]:
    """`dei:` cover facts, e.g. DocumentFiscalYearFocus -> "2027" (first occurrence wins)."""
    out: dict[str, str] = {}
    for element in root.iter():
        if _tag(element) not in ("nonnumeric", "nonfraction"):
            continue
        name = element.get("name") or ""
        if name.lower().startswith("dei:"):
            out.setdefault(name.split(":", 1)[1], _clean(element.text_content()))
    return out


# --------------------------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------------------------


class _Pages:
    def __init__(self) -> None:
        self.pages: list[list[str]] = [[]]
        self.pieces: list[str] = []  # raw inline text of the current line
        self.breaks = 0
        self.anchors: list[tuple[HtmlElement, int, int]] = []  # (ix element, page, line)

    def text(self, text: str | None) -> None:
        if text:
            self.pieces.append(text)

    def newline(self) -> None:
        # Inline pieces join as a browser renders them: "CONSOLIDA" + "TED" is one word, and
        # whitespace only where the markup has it.
        if self.pieces and (line := _clean("".join(self.pieces))):
            self.pages[-1].append(line)
        self.pieces = []

    def line(self, text: str) -> None:
        self.newline()
        self.pages[-1].append(text)

    def anchor(self, element: HtmlElement) -> None:
        """Remember the line an inline-XBRL figure lands on (the next line written)."""
        self.anchors.append((element, len(self.pages) - 1, len(self.pages[-1])))

    def page_break(self) -> None:
        self.newline()
        self.breaks += 1
        if self.pages[-1]:
            self.pages.append([])


def _table(table: HtmlElement, out: _Pages) -> None:
    out.newline()
    for row in table.iter():
        if _tag(row) != "tr" or _hidden(row):
            continue
        cells = [
            _clean(cell.text_content())
            for cell in row
            if _tag(cell) in ("td", "th") and not _hidden(cell)
        ]
        merged = merge_cells([c for c in cells if c])
        if merged:
            for element in row.iter():
                if _tag(element) == "nonfraction":
                    out.anchor(element)
            out.line(" | ".join(merged))


_PAGE_HEIGHT = re.compile(r"(?:^|;)(?:min-)?height:(\d+(?:\.\d+)?)pt")


def _is_page_box(tag: str, style: str) -> bool:
    """A fixed-layout page: a div as tall as a printed page (Letter 792pt, A4 842pt, Legal 1008pt).

    Some EDGAR generators (General Mills' 10-Q, for one) emit no CSS page breaks; each
    printed page is a positioned div of page height instead.
    """
    match = _PAGE_HEIGHT.search(style) if tag == "div" else None
    return bool(match) and 700 <= float(match.group(1)) <= 1100  # type: ignore[union-attr]


def _walk(element: HtmlElement, out: _Pages) -> None:
    if not isinstance(element.tag, str) or _hidden(element):
        return
    style = _style(element)
    tag = _tag(element)
    page_box = _is_page_box(tag, style)
    if page_box or "page-break-before:always" in style or "break-before:page" in style:
        out.page_break()
    if tag == "table":
        _table(element, out)
    else:
        block = tag in _BLOCK
        if block:
            out.newline()
        if tag == "nonfraction":
            out.anchor(element)
        out.text(element.text)
        for child in element:
            _walk(child, out)
            out.text(child.tail)
        if block:
            out.newline()
    if page_box or "page-break-after:always" in style or "break-after:page" in style:
        out.page_break()


def _split_long(lines: list[str]) -> Iterator[list[str]]:
    page: list[str] = []
    words = 0
    for line in lines:
        page.append(line)
        words += len(line.split())
        if words >= FALLBACK_PAGE_WORDS:
            yield page
            page, words = [], 0
    if page:
        yield page


@dataclass
class HtmlFiling:
    pages: list[str]
    facts: list[InlineFact] = field(default_factory=list)
    contexts: dict[str, Context] = field(default_factory=dict)
    dei: dict[str, str] = field(default_factory=dict)
    units: dict[str, str] = field(default_factory=dict)  # unitRef id -> "USD", "USD/shares"

    def line_text(self, fact: InlineFact) -> str:
        lines = self.pages[fact.page].split("\n") if fact.page < len(self.pages) else []
        return lines[fact.line] if 0 <= fact.line < len(lines) else ""


def _fact(element: HtmlElement, page: int, line: int) -> InlineFact:
    scale = element.get("scale") or "0"
    return InlineFact(
        concept=element.get("name") or "",
        context=element.get("contextref") or "",
        unit=element.get("unitref"),
        decimals=element.get("decimals"),
        scale=int(scale) if scale.lstrip("-").isdigit() else 0,
        negative=element.get("sign") == "-",
        format=element.get("format"),
        text=_clean(element.text_content()),
        page=page,
        line=line,
    )


def parse_html(data: bytes) -> HtmlFiling:
    """Pages, located inline-XBRL facts, contexts and cover (dei) facts of an HTML filing."""
    root = lxml_html.fromstring(data)
    out = _Pages()
    _walk(root, out)
    out.newline()
    # Only the last page can be empty (a break opens a new page only after a non-empty one),
    # so dropping empties keeps every other page's index.
    pages = [lines for lines in out.pages if lines]
    located = list(out.anchors)
    if out.breaks == 0 and pages:  # no page breaks: cut by length, then re-place the facts
        pages = list(_split_long(pages[0]))
        starts = [sum(len(p) for p in pages[:i]) for i in range(len(pages))]
        located = []
        for element, _, line in out.anchors:
            page = max(i for i, s in enumerate(starts) if s <= line)
            located.append((element, page, line - starts[page]))
    last = max(len(pages) - 1, 0)
    return HtmlFiling(
        pages=["\n".join(lines) for lines in pages],
        facts=[_fact(element, min(page, last), line) for element, page, line in located],
        contexts=_contexts(root),
        dei=_dei(root),
        units=_units(root),
    )


def html_pages(data: bytes) -> list[str]:
    """Page texts of an HTML filing, in order."""
    return parse_html(data).pages


# Statement titles in EDGAR HTML that the PDF tagger misses. The PDF tagger stays unchanged so
# the FinanceBench parses reproduce; these forms are rewritten before it runs:
# - a registrant prefix: "Johnson & Johnson and subsidiaries consolidated statements of earnings"
# - an inverted title that fills the whole line: "INCOME STATEMENTS", "CASH FLOWS STATEMENTS"
#   (Microsoft). The whole-line rule leaves note headings such as "Income Statement –
#   Disaggregation of Income Statement Expenses" untagged.
_REGISTRANT_PREFIX = re.compile(
    r"^.{1,60}?\band subsidiaries\s+(?=(?:condensed\s+)?consolidated\b)", re.I
)
_INVERTED_TITLE = re.compile(
    r"^(?P<prefix>(?:condensed\s+)?(?:consolidated\s+)?)"
    r"(?P<subject>comprehensive\s+income|income|cash\s*flows?|(?:stock|share)holders['’]?\s+equity)"
    r"\s+statements?(?:\s*\(unaudited\))?$",
    re.IGNORECASE,
)


def html_statement(text: str) -> Statement | None:
    lines = []
    for raw in text.splitlines():
        line = _REGISTRANT_PREFIX.sub("", raw.strip())
        if match := _INVERTED_TITLE.match(line):
            line = f"{match['prefix']}statements of {match['subject']}"
        lines.append(line)
    return detect_statement("\n".join(lines))


def parsed_doc_from_html(filing: HtmlFiling, doc_name: str, source_size: int) -> ParsedDoc:
    pages = []
    for index, text in enumerate(filing.pages):
        # Table rows put several cells on one line; look for the units line cell by cell.
        units, units_line = detect_units(text.replace(" | ", "\n"))
        pages.append(
            ParsedPage(
                page=index,
                text=text,
                layout=text,
                units=units,
                units_line=units_line,
                statement=html_statement(text),
                extractor="html",
            )
        )
    return ParsedDoc(
        doc_name=doc_name,
        parser_version=HTML_PARSER_VERSION,
        pdfplumber_version=f"lxml {lxml.__version__}",
        source_size=source_size,
        n_pages=len(pages),
        seconds=0.0,
        pages=pages,
    )


def parse_html_filing(data: bytes, doc_name: str) -> ParsedDoc:
    start = time.perf_counter()
    doc = parsed_doc_from_html(parse_html(data), doc_name, len(data))
    return doc.model_copy(update={"seconds": round(time.perf_counter() - start, 2)})
