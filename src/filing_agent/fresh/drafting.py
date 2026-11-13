"""Text questions about the fresh filings, drafted by Claude from one discussion page each.

- **Page.** For each filing, one of its three most explanatory pages: the ones that most often
  say why a figure changed ("primarily due to", "driven by", "partially offset by").
  Financial statements are excluded. These pages are in MD&A or the notes.
- **Draft.** Claude reads that page and proposes two questions about the reasons, drivers or
  components behind a change or an amount. Each comes with a short answer, a verbatim
  supporting quote and the answer's key figures.
- **Checks.** A candidate is kept only if:
  - its quote appears word for word on the page
  - every key figure is in the quote
  - the question names the company and the period's end date
  - the question does not give away a key figure
  The first candidate that passes becomes the question. People review them afterwards.
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from filing_agent.agent.pricing import cost_usd
from filing_agent.corpus.parse import ParsedDoc
from filing_agent.data.financebench import Evidence, Question
from filing_agent.fresh.filings import FreshFiling
from filing_agent.fresh.questions import display_name, long_date
from filing_agent.tools.base import strict_schema

PROMPT_VERSION = "fresh-text/1"
DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "low"
CANDIDATES = 2
TOP_PAGES = 3
MIN_EXPLANATIONS = 4
MAX_QUOTE = 800
MAX_ANSWER = 700

EXPLANATION = re.compile(
    r"primarily (?:due to|driven by|as a result of|reflect\w*)|driven (?:primarily |largely |"
    r"mainly )?by|partially offset by|attributable to|as a result of|reflecting|resulting from",
    re.IGNORECASE,
)


def discussion_pages(doc: ParsedDoc, top: int = TOP_PAGES) -> list[int]:
    """The most explanatory non-statement pages (cover and contents excluded)."""
    scored = sorted(
        ((len(EXPLANATION.findall(p.text)), p.page) for p in doc.pages if p.statement is None),
        reverse=True,
    )
    return [page for score, page in scored if score >= MIN_EXPLANATIONS and page >= 2][:top]


# --------------------------------------------------------------------------------------------
# Prompt and output
# --------------------------------------------------------------------------------------------

SYSTEM = """\
You write evaluation questions for a financial research agent. The agent can search many SEC \
filings and must find the answer itself; it will never see the page you are given.

You get one page from a company's 10-Q or 10-K. Write {n} candidate questions that an equity \
analyst would ask and that this page answers in its text, not only in a table.

Each question must be:
- Self-contained. Name the company and the period by its end date, for example "in the three \
months ended September 30, 2026". Never mention the page, "this filing's text" or "the passage".
- Answerable from this page alone, with no outside knowledge and no other filings.
- About the reasons, drivers, components or circumstances behind a change or an amount: what \
drove a segment's sales change, what caused a margin to move, what a charge consisted of, how \
a cash use was funded. Avoid questions answered by a single figure from a financial statement \
table, yes/no questions, boilerplate (risk factors, forward-looking-statement language, \
accounting policies) and anything speculative.
- Free of giveaways: the question must not contain the answer's figures.

For each question, also give:
- answer: one to three sentences with the figures exactly as printed on the page. Name the \
main drivers or components; minor items can be left out.
- evidence_quote: one contiguous passage copied exactly from the page (at most 600 \
characters) that supports the whole answer.
- key_figures: every number in your answer, written exactly as in the quote ("$1.2 billion", \
"8%").
- topic: a few words, such as "segment sales drivers" or "gross margin change".
"""


class TextCandidate(BaseModel):
    question: str
    answer: str
    evidence_quote: str
    key_figures: list[str]
    topic: str


class DraftOutput(BaseModel):
    candidates: list[TextCandidate]


OUTPUT_SCHEMA: dict[str, Any] = strict_schema(DraftOutput)


def user_prompt(filing: FreshFiling, page: int, text: str) -> str:
    period = long_date(filing.report_date) if filing.report_date else "unknown"
    return (
        f"Company: {display_name(filing.company)}\n"
        f"Filing: Form {filing.form} for the period ended {period}, filed {filing.filing_date}\n"
        f"Page {page} of the filing:\n<page>\n{text}\n</page>"
    )


# --------------------------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------------------------

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_TRANSLATE = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-"})


def normalize(text: str) -> str:
    return " ".join(text.translate(_TRANSLATE).replace("\xa0", " ").split()).casefold()


class CheckedCandidate(TextCandidate):
    problems: list[str] = Field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.problems


def check_candidate(
    candidate: TextCandidate, page_text: str, filing: FreshFiling
) -> CheckedCandidate:
    problems: list[str] = []
    quote, page = normalize(candidate.evidence_quote), normalize(page_text)
    question = normalize(candidate.question)
    if not quote or quote not in page:
        problems.append("quote not found on the page")
    if len(candidate.evidence_quote) > MAX_QUOTE:
        problems.append("quote too long")
    if not candidate.answer.strip() or len(candidate.answer) > MAX_ANSWER:
        problems.append("answer empty or too long")
    missing = [f for f in candidate.key_figures if normalize(f) not in quote]
    if missing:
        problems.append(f"figures not in the quote: {missing}")
    numbers = {n.rstrip(".,") for n in _NUMBER.findall(candidate.answer)}
    if unsupported := sorted(n for n in numbers if n and n not in page):
        problems.append(f"answer numbers not on the page: {unsupported}")
    leaked = [f for f in candidate.key_figures if re.search(r"\d", f) and normalize(f) in question]
    if leaked:
        problems.append(f"question gives away {leaked}")
    name = display_name(filing.company).split()[0]
    if normalize(name) not in question:
        problems.append("question does not name the company")
    if filing.report_date and normalize(long_date(filing.report_date)) not in question:
        problems.append("question does not name the period end date")
    fields = candidate.model_dump(exclude={"problems"})  # a re-check replaces old problems
    return CheckedCandidate(**fields, problems=problems)


# --------------------------------------------------------------------------------------------
# Drafting
# --------------------------------------------------------------------------------------------


class TextDraft(BaseModel):
    doc_name: str
    page: int
    model: str
    effort: str
    prompt_version: str = PROMPT_VERSION
    candidates: list[CheckedCandidate] = Field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    error: str | None = None

    def chosen(self, number: int | None = None) -> tuple[int, CheckedCandidate] | None:
        """(1-based number, candidate): the given one, or the first that passed every check."""
        for i, candidate in enumerate(self.candidates, start=1):
            if (number is None and candidate.valid) or i == number:
                return i, candidate
        return None


def draft_filing(
    client: Any,
    filing: FreshFiling,
    doc: ParsedDoc,
    *,
    model: str = DEFAULT_MODEL,
    effort: str = DEFAULT_EFFORT,
    seed: int = 7,
    max_tokens: int = 6_000,
) -> TextDraft:
    pages = discussion_pages(doc)
    draft = TextDraft(doc_name=filing.doc_name, page=-1, model=model, effort=effort)
    if not pages:
        draft.error = "no explanatory page"
        return draft
    draft.page = random.Random(f"{seed}:{filing.doc_name}:text").choice(pages)
    text = doc.pages[draft.page].text
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=SYSTEM.format(n=CANDIDATES),
        messages=[{"role": "user", "content": user_prompt(filing, draft.page, text)}],
        thinking={"type": "adaptive"},
        output_config={
            "effort": effort,
            "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA},
        },
    )
    usage = response.usage
    draft.input_tokens, draft.output_tokens = usage.input_tokens, usage.output_tokens
    draft.cost_usd = cost_usd(
        response.model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=getattr(usage, "cache_read_input_tokens", None) or 0,
        cache_write_tokens=getattr(usage, "cache_creation_input_tokens", None) or 0,
    )
    if response.model != model:
        draft.error = f"served by {response.model}, expected {model}"
        return draft
    if response.stop_reason != "end_turn":
        draft.error = f"stopped with {response.stop_reason}"
        return draft
    body = "".join(block.text for block in response.content if block.type == "text")
    try:
        output = DraftOutput.model_validate_json(body)
    except ValidationError as exc:
        draft.error = f"output did not match the schema: {exc}"
        return draft
    draft.candidates = [check_candidate(c, text, filing) for c in output.candidates]
    return draft


def draft_all(
    client: Any,
    work: Sequence[tuple[FreshFiling, ParsedDoc]],
    *,
    model: str = DEFAULT_MODEL,
    effort: str = DEFAULT_EFFORT,
    workers: int = 4,
    max_cost_usd: float = 3.0,
    on_done: Callable[[TextDraft], None] | None = None,
) -> list[TextDraft]:
    """Draft for every filing in `work`, stopping new requests once the cost cap is reached."""
    spent = 0.0
    drafts: list[TextDraft] = []

    def one(item: tuple[FreshFiling, ParsedDoc]) -> TextDraft | None:
        if spent >= max_cost_usd:
            return None
        filing, doc = item
        try:
            return draft_filing(client, filing, doc, model=model, effort=effort)
        except Exception as exc:  # one failed request must not lose the others
            return TextDraft(
                doc_name=filing.doc_name,
                page=-1,
                model=model,
                effort=effort,
                error=f"{type(exc).__name__}: {exc}",
            )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for draft in pool.map(one, work):
            if draft is None:
                continue
            spent += draft.cost_usd or 0.0
            drafts.append(draft)
            if on_done:
                on_done(draft)
    return drafts


def load_drafts(path: Path) -> dict[str, TextDraft]:
    if not path.is_file():
        return {}
    drafts = [
        TextDraft.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return {d.doc_name: d for d in drafts}


def save_drafts(path: Path, drafts: Iterable[TextDraft]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(drafts, key=lambda d: d.doc_name)
    path.write_text("\n".join(d.model_dump_json() for d in ordered) + "\n", encoding="utf-8")


def failures(drafts: Iterable[TextDraft]) -> dict[str, str]:
    """doc_name -> why no candidate can be used (request error, or every candidate's problems)."""
    out: dict[str, str] = {}
    for draft in drafts:
        if draft.error:
            out[draft.doc_name] = draft.error
        elif draft.chosen() is None:
            out[draft.doc_name] = (
                "; ".join(
                    f"#{i}: {', '.join(c.problems)}"
                    for i, c in enumerate(draft.candidates, start=1)
                )
                or "no candidates"
            )
    return out


def text_question(
    filing: FreshFiling, draft: TextDraft, number: int, candidate: TextCandidate, page_text: str
) -> Question:
    return Question(
        id=f"fresh_{filing.doc_name}_text",
        company=filing.company,
        doc_name=filing.doc_name,
        question_type="novel-generated",
        question_reasoning="Information extraction (text)",
        question=candidate.question,
        answer=candidate.answer,
        justification=candidate.evidence_quote,
        evidence=[
            Evidence(
                doc_name=filing.doc_name,
                page=draft.page,
                text=candidate.evidence_quote,
                full_page_text=page_text,
            )
        ],
        meta={
            "generator": draft.prompt_version,
            "kind": "text",
            "family": "text",
            "metric": candidate.topic,
            "model": draft.model,
            "effort": draft.effort,
            "page": draft.page,
            "candidate": number,
            "key_figures": candidate.key_figures,
        },
    )


def choose_candidate(
    draft: TextDraft, page_text: str, filing: FreshFiling, number: int | None = None
) -> tuple[int, CheckedCandidate] | None:
    """Re-check every candidate with the current rules; return the requested one (a reviewer's
    pick, kept even if a check fails) or the first that passes."""
    rechecked = [check_candidate(c, page_text, filing) for c in draft.candidates]
    for i, candidate in enumerate(rechecked, start=1):
        if (number is None and candidate.valid) or i == number:
            return i, candidate
    return None
