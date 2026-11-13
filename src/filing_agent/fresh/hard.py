"""A harder tier of fresh questions, aimed at the failure modes the first tier designed out.

The first tier pinned each question to one filing by its period end date, stated each formula
in the filing's own labels, and asked only for whole-company figures. The full agent answered
all 114. These questions keep exact golds but take those supports away:

- **Fiscal periods.** Periods are named the way the company names them: Walmart's quarter
  ended July 31, 2026 is "the second quarter of fiscal 2027", Ulta's quarter ended August 1,
  2026 is "the second quarter of fiscal 2026". A filing inside a change of fiscal year end
  (Amcor's transition period) keeps dates.
- **breakdown.** Revenue or operating income of one segment, product line or region. These
  facts carry a dimension, so the SEC XBRL API does not have them; most are printed only in
  a note table.
- **history.** The change in revenue, net income or diluted EPS against the same period two
  years earlier. That period's filing is not in the corpus. Its figure comes from the SEC XBRL
  API, as first reported, and is used only when every later report of it agrees.
- **definition.** A standard ratio with no formula given: gross, operating and net margin,
  effective tax rate, current ratio, free cash flow. Each is asked only where the statements
  allow one reading: a single revenue line, a single capital-spending line, and no
  noncontrolling interest to include or exclude.
"""

from __future__ import annotations

import random
import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from filing_agent.corpus.html_parse import HtmlFiling, html_statement
from filing_agent.data.financebench import Question
from filing_agent.fresh.filings import FreshFiling
from filing_agent.fresh.questions import (
    DEFAULT_SEED,
    METRIC,
    SCALE_WORDS,
    Draft,
    Figure,
    FilingOutcome,
    Period,
    api_index,
    api_status,
    clean_label,
    display_name,
    duration_kind,
    filing_periods,
    find_figure,
    period_end,
    possessive,
    rounding_decimals,
    statement_figures,
    to_question,
)

GENERATOR = "fresh-hard/1"
PREFIX = "fresh_hard"
KINDS = ("breakdown", "history", "definition")
_ORDINAL = {"Q1": "first", "Q2": "second", "Q3": "third"}


# --------------------------------------------------------------------------------------------
# Periods in the company's own terms
# --------------------------------------------------------------------------------------------


def is_transition(html: HtmlFiling) -> bool:
    """A filing inside a change of fiscal year end. Every 10-Q cover mentions a "transition
    period" once or twice (the transition-report checkbox), so only repeated use counts."""
    return sum(page.lower().count("transition period") for page in html.pages[1:]) >= 5


def period_words(filing: FreshFiling, period: Period, transition: bool) -> tuple[str, str]:
    """(preposition, name): ("in", "the second quarter of fiscal 2027")."""
    fiscal = not transition and filing.fiscal_period in (*_ORDINAL, "FY")
    if fiscal:
        fy, fp = filing.fiscal_year, filing.fiscal_period
        quarter = f"the {_ORDINAL[fp]} quarter of fiscal {fy}" if fp in _ORDINAL else ""
        if period.kind == "year" and fp == "FY":
            return "in", f"fiscal {fy}"
        if period.kind == "quarter" and quarter:
            return "in", quarter
        if period.kind == "ytd" and fp in ("Q2", "Q3"):
            return "in", f"the first {'half' if fp == 'Q2' else 'nine months'} of fiscal {fy}"
        if period.kind == "instant":
            return "at", f"the end of {quarter or f'fiscal {fy}'}"
    phrase = period.phrase()
    if phrase.startswith("as of "):
        return "as of", phrase.removeprefix("as of ")
    return "in", phrase.removeprefix("in ")


def _units_text(figure: Figure) -> str | None:
    word = SCALE_WORDS.get(figure.scale)
    return f"Answer in USD {word}." if word else None


def _money(value: float, decimals: int) -> str:
    return f"${value:.{decimals}f}" if value >= 0 else f"-${-value:.{decimals}f}"


def _percent(value: float) -> tuple[str, str]:
    """(gold, instruction) for a percentage, with one more decimal on a rounding boundary."""
    decimals = rounding_decimals(value, 1)
    places = "one decimal place" if decimals == 1 else "two decimal places"
    return f"{value:.{decimals}f}%", f"Answer in percent, rounded to {places}."


# --------------------------------------------------------------------------------------------
# breakdown: one segment, product line or region
# --------------------------------------------------------------------------------------------

BREAKDOWN_AXES = {
    "us-gaap:StatementBusinessSegmentsAxis": "segment",
    "srt:ProductOrServiceAxis": "product",
    "us-gaap:ProductOrServiceAxis": "product",
    "srt:StatementGeographicalAxis": "region",
    "us-gaap:StatementGeographicalAxis": "region",
}
BREAKDOWN_CONCEPTS = {
    "us-gaap:Revenues": "revenue",
    "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax": "revenue",
    "us-gaap:RevenueFromContractWithCustomerIncludingAssessedTax": "revenue",
    "us-gaap:OperatingIncomeLoss": "operating income",
}
_NOT_A_PART = re.compile(
    r"Reportable|Eliminat|Reconcil|Consolidat|Corporate|AllOther|OperatingSegment|Intersegment"
    r"|Other",
    re.IGNORECASE,
)
_SMALL_WORDS = {"And": "and", "Of": "of", "The": "the", "For": "for"}
# Taxonomy-wide members read as words, not as a printed label ("earn from service").
_GENERIC_MEMBERS = {"ProductMember": "products", "ServiceMember": "services"}


def member_label(member: str) -> str:
    """'wmt:WalmartInternationalSegmentMember' -> 'Walmart International'."""
    name = re.sub(r"(?:Segment)?Member$", "", member.rsplit(":", 1)[-1])
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", name).split()
    return " ".join(_SMALL_WORDS.get(w, w) if i else w for i, w in enumerate(words))


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower().replace("&", "and"))


def printed_label(page: str, label: str) -> str | None:
    """The member's name as the page prints it ("Walmart U.S.", "iPhone"), or None."""
    target = _squash(label)
    if not target:
        return None
    for line in page.splitlines():
        for cell in line.split(" | "):
            if _squash(clean_label(cell)) == target:
                return _readable(clean_label(cell))
    words = page.split()
    for size in range(1, 9):
        for i in range(len(words) - size + 1):
            window = " ".join(words[i : i + size]).strip(",;:()")
            if window.endswith(".") and window.count(".") == 1:
                window = window[:-1]  # a sentence's full stop, not "U.S."
            if _squash(window) == target:
                return _readable(window)
    return None


def _readable(label: str) -> str:
    """'INNOVATIVE MEDICINE' -> 'Innovative Medicine'; acronyms such as 'EMEA' stay."""
    return label.title() if label.isupper() and " " in label else label


_NEEDS_THE = re.compile(r"^(?:Americas|U\.?S\.?|United|Rest of)", re.IGNORECASE)


@dataclass(frozen=True)
class Breakdown:
    figure: Figure
    axis: str  # segment, product, region
    measure: str  # revenue, operating income
    label: str  # as printed


def breakdowns(html: HtmlFiling, period: Period, totals: set[float]) -> list[Breakdown]:
    """Single-dimension revenue and operating income figures for the period, one per member."""
    tags = [html_statement(page) for page in html.pages]
    found: dict[tuple[str, str], list[Breakdown]] = {}
    for fact in html.facts:
        context = html.contexts.get(fact.context)
        if context is None or len(context.dimensions) != 1:
            continue
        axis, member = context.dimensions[0]
        kind, measure = BREAKDOWN_AXES.get(axis), BREAKDOWN_CONCEPTS.get(fact.concept)
        if kind is None or measure is None or _NOT_A_PART.search(member.rsplit(":", 1)[-1]):
            continue
        if (context.start, context.end) != (period.start, period.end):
            continue
        value, unit, row = fact.value(), html.units.get(fact.unit or ""), html.line_text(fact)
        if unit != "USD" or value is None or value <= 0 or value in totals or not row:
            continue
        generic = _GENERIC_MEMBERS.get(member.rsplit(":", 1)[-1])
        label = generic or printed_label(html.pages[fact.page], member_label(member))
        if label is None or (kind != "segment" and measure != "revenue"):
            continue
        figure = Figure(
            concept=fact.concept,
            unit=unit,
            value=value,
            scale=fact.scale,
            displayed=fact.text,
            start=context.start,
            end=context.end,
            page=fact.page,
            statement=tags[fact.page] or "notes",
            row=row,
        )
        found.setdefault((measure, member), []).append(Breakdown(figure, kind, measure, label))
    return [items[0] for items in found.values() if len({b.figure.value for b in items}) == 1]


def breakdown_draft(item: Breakdown, company: str, words: tuple[str, str]) -> Draft | None:
    units = _units_text(item.figure)
    if units is None:
        return None
    prep, name = words
    if item.axis == "segment":
        stem = f"What {item.measure} did {possessive(company)} {item.label} segment report"
    elif item.axis == "product":
        stem = f"How much revenue did {company} earn from {item.label}"
    else:
        region = f"the {item.label}" if _NEEDS_THE.match(item.label) else item.label
        stem = f"How much revenue did {company} generate in {region}"
    figure = item.figure
    return Draft(
        key=f"breakdown_{item.axis}_{item.measure.replace(' ', '_')}",
        kind="breakdown",
        family="income",
        question=f"{stem} {prep} {name}? {units}",
        answer=_money(figure.scaled, figure.decimals),
        justification=f'{item.label} {item.measure}: "{figure.row}" ({figure.concept}, page '
        f"{figure.page}; dimensional, so not in the SEC XBRL API).",
        figures=[figure],
        period=Period(duration_kind(figure.days) or "quarter", figure.start, figure.end),
    )


# --------------------------------------------------------------------------------------------
# history: the same period two years earlier, from the SEC XBRL API
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PastValue:
    value: float
    start: date | None
    end: date
    fiscal_year: int | None
    fiscal_period: str | None
    form: str | None
    filed: str
    accession: str


def _two_years_before(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:  # February 29
        return day - timedelta(days=365 * years)


def past_value(
    companyfacts: Mapping[str, Any] | None, figure: Figure, years: int = 2
) -> PastValue | None:
    """The same kind of period `years` earlier, as first reported, if no later report differs."""
    if companyfacts is None or figure.start is None:
        return None
    entries = (
        companyfacts.get("facts", {})
        .get(figure.taxonomy, {})
        .get(figure.name, {})
        .get("units", {})
        .get(figure.unit, [])
    )
    target = _two_years_before(figure.end, years)
    kind = duration_kind(figure.days)
    hits = []
    for entry in entries:
        if not entry.get("start"):
            continue
        start, end = date.fromisoformat(entry["start"]), date.fromisoformat(entry["end"])
        if abs((end - target).days) <= 10 and duration_kind((end - start).days + 1) == kind:
            hits.append(entry)
    if not hits or len({float(e["val"]) for e in hits}) > 1:  # absent, or restated later
        return None
    first = min(hits, key=lambda e: (e.get("filed", ""), e.get("accn", "")))
    return PastValue(
        value=float(first["val"]),
        start=date.fromisoformat(first["start"]),
        end=date.fromisoformat(first["end"]),
        fiscal_year=first.get("fy"),
        fiscal_period=first.get("fp"),
        form=first.get("form"),
        filed=first.get("filed", ""),
        accession=first.get("accn", ""),
    )


def past_words(past: PastValue, filing: FreshFiling, kind: str, years: int = 2) -> str:
    """The earlier period's name: fiscal when its own filing labels it consistently."""
    fy = filing.fiscal_year - years
    same_label = past.fiscal_year == fy and past.fiscal_period == filing.fiscal_period
    if kind == "quarter" and past.form == "10-Q" and same_label and fy and past.fiscal_period:
        return f"the {_ORDINAL.get(past.fiscal_period, '')} quarter of fiscal {fy}"
    if kind == "year" and past.form == "10-K" and same_label:
        return f"fiscal {fy}"
    return Period(kind, past.start, past.end).phrase().removeprefix("in ")  # type: ignore[arg-type]


# Totals only: per-share figures two years apart can straddle a stock split (Netflix's
# 10-for-1 in 2025), and nothing in the reported values marks it.
HISTORY_METRICS = {
    "revenue": "total revenue",
    "net_income": "net income attributable to the company",
}


def history_draft(
    key: str,
    figure: Figure,
    past: PastValue,
    filing: FreshFiling,
    words: tuple[str, str],
) -> Draft | None:
    if figure.value <= 0 or past.value <= 0:
        return None
    kind = duration_kind(figure.days) or "quarter"
    company = display_name(filing.company)
    then = past_words(past, filing, kind)
    growth = (figure.value - past.value) / past.value * 100
    answer, units = _percent(growth)
    metric = HISTORY_METRICS[key].replace("the company", company)
    subject = f"the {metric}" if key == "net_income" else f"{possessive(company)} {metric}"
    return Draft(
        key=f"history_{key}",
        kind="history",
        family="income",
        question=(
            f"By what percentage did {subject} change from {then} to {words[1]}? {units} "
            "Use a negative number for a decrease."
        ),
        answer=answer,
        justification=(
            f'{figure.displayed} ("{figure.row.split(" | ")[0]}", page {figure.page}) against '
            f"{past.value:,.4g} for {then} (SEC XBRL API, first reported in {past.form} "
            f"{past.accession}, filed {past.filed}) = {growth:.3f}%."
        ),
        figures=[figure],
        period=Period(kind, figure.start, figure.end),
        formula=f"(current - two years earlier) / two years earlier, {figure.concept}",
    )


# --------------------------------------------------------------------------------------------
# definition: standard ratios with no formula given
# --------------------------------------------------------------------------------------------

REVENUE_CONCEPTS = set(METRIC["revenue"].concepts)
_CAPEX_LIKE = re.compile(
    r"PaymentsTo(?:Acquire|Develop)(?:PropertyPlantAndEquipment|ProductiveAssets|Software|"
    r"IntangibleAssets|OtherProductiveAssets)|PaymentsForSoftware|Capital(?:ized)?(?:Expenditure|"
    r"Software)",
)


def _only(figures: Sequence[Figure], keep: Callable[[Figure], bool]) -> Figure | None:
    """The single distinct figure that matches, or None if there are none or several."""
    found = [f for f in figures if keep(f)]
    return found[0] if found and len({f.value for f in found}) == 1 else None


@dataclass(frozen=True)
class DefinitionSpec:
    key: str
    name: str
    family: str
    kind: str  # percent, ratio, money


DEFINITIONS = (
    DefinitionSpec("gross_margin", "gross margin", "income", "percent"),
    DefinitionSpec("operating_margin", "operating margin", "income", "percent"),
    DefinitionSpec("net_margin", "net profit margin", "income", "percent"),
    DefinitionSpec("effective_tax_rate", "effective tax rate", "income", "percent"),
    DefinitionSpec("current_ratio", "current ratio", "balance", "ratio"),
    DefinitionSpec("free_cash_flow", "free cash flow", "cash_flow", "money"),
)


def definition_parts(
    spec: DefinitionSpec, figures: Sequence[Figure], period: Period
) -> tuple[Figure, Figure] | None:
    """The two figures a standard definition uses, when the statements allow one reading."""

    def on(statement: str) -> Callable[[Figure], bool]:
        return lambda f: f.statement == statement and period.matches(f)

    income, cash = on("income_statement"), on("cash_flow_statement")
    revenue = _only(figures, lambda f: income(f) and f.name in REVENUE_CONCEPTS)
    found: Callable[[str], Figure | None] = lambda key: find_figure(  # noqa: E731
        figures, METRIC[key], period.matches
    )
    if spec.key in ("gross_margin", "operating_margin", "net_margin"):
        top = found({"gross_margin": "gross_profit", "operating_margin": "operating_income",
                     "net_margin": "net_income"}[spec.key])  # fmt: skip
        if spec.key == "net_margin" and top is not None:
            consolidated = _only(figures, lambda f: income(f) and f.name == "ProfitLoss")
            if consolidated is not None and consolidated.value != top.value:
                return None  # attributable or consolidated net income: two readings
        return (top, revenue) if top and revenue else None
    if spec.key == "effective_tax_rate":
        tax, pretax = found("income_tax"), found("pretax_income")
        return (tax, pretax) if tax and pretax else None
    if spec.key == "current_ratio":
        assets, liabilities = found("current_assets"), found("current_liabilities")
        return (assets, liabilities) if assets and liabilities else None
    capex_lines = [f for f in figures if cash(f) and _CAPEX_LIKE.search(f.name)]
    cfo, capex = found("cfo"), found("capex")
    if not cfo or not capex or len({f.name for f in capex_lines}) != 1:
        return None  # several capital-spending lines: is capitalized software capex?
    return cfo, capex


def definition_draft(
    spec: DefinitionSpec, parts: tuple[Figure, Figure], company: str, words: tuple[str, str]
) -> Draft | None:
    a, b = parts
    if a.value <= 0 or b.value <= 0:
        return None
    stem = f"What was {possessive(company)} {spec.name} {words[0]} {words[1]}?"
    if spec.kind == "percent":
        value = a.value / b.value * 100
        answer, units = _percent(value)
        shown = f"{value:.3f}%"
    elif spec.kind == "ratio":
        value = a.value / b.value
        decimals = rounding_decimals(value, 2)
        answer = f"{value:.{decimals}f}"
        units = f"Round to {'two' if decimals == 2 else 'three'} decimal places."
        shown = f"{value:.4f}"
    else:
        word = SCALE_WORDS.get(a.scale)
        if word is None or a.scale != b.scale:
            return None
        value = (a.value - b.value) / 10.0**a.scale
        answer = _money(value, max(a.decimals, b.decimals))
        units, shown = f"Answer in USD {word}.", answer
    op = "-" if spec.kind == "money" else "/"
    return Draft(
        key=f"definition_{spec.key}",
        kind="definition",
        family=spec.family,  # type: ignore[arg-type]
        question=f"{stem} {units}",
        answer=answer,
        justification=(
            f'"{a.label}" {a.displayed} {op} "{b.label}" {b.displayed} = {shown} '
            f"(page {a.page}{'' if a.page == b.page else f' and {b.page}'})."
        ),
        figures=[a, b],
        period=Period(duration_kind(a.days) or "instant", a.start, a.end),
        formula=f"{spec.name}: {a.concept} {op} {b.concept}",
    )


# --------------------------------------------------------------------------------------------
# Per filing
# --------------------------------------------------------------------------------------------


def hard_candidates(
    filing: FreshFiling, html: HtmlFiling, companyfacts: Mapping[str, Any] | None
) -> list[Draft]:
    figures = statement_figures(html)
    end = period_end(filing, figures)
    if end is None:
        return []
    periods = filing_periods(figures, end)
    annual = filing.form == "10-K"
    main = periods.get("year") if annual else periods.get("quarter")
    cash_period = periods.get("year") or periods.get("ytd") or periods.get("quarter")
    transition = is_transition(html)
    company = display_name(filing.company)
    drafts: list[Draft] = []
    if main is not None:
        words = period_words(filing, main, transition)
        totals = {f.value for f in figures if main.matches(f) and f.statement == "income_statement"}
        drafts += [
            d for b in breakdowns(html, main, totals) if (d := breakdown_draft(b, company, words))
        ]
        for key in HISTORY_METRICS:
            figure = find_figure(figures, METRIC[key], main.matches)
            past = past_value(companyfacts, figure) if figure else None
            if figure and past and (draft := history_draft(key, figure, past, filing, words)):
                drafts.append(draft)
    for spec in DEFINITIONS:
        period = {"income": main, "balance": periods["instant"], "cash_flow": cash_period}[
            spec.family
        ]
        parts = definition_parts(spec, figures, period) if period else None
        if parts and period:
            words = period_words(filing, period, transition)
            if draft := definition_draft(spec, parts, company, words):
                drafts.append(draft)
    return drafts


def hard_questions(
    filings: Sequence[tuple[FreshFiling, HtmlFiling, Mapping[str, Any] | None]],
    *,
    seed: int = DEFAULT_SEED,
    per_filing: int = 2,
) -> list[FilingOutcome]:
    """Up to `per_filing` questions per filing, each of a different kind, kept balanced: the
    least-used kinds come first, then the least-used question within a kind."""
    kind_use: Counter[str] = Counter()
    key_use: Counter[str] = Counter()
    outcomes: list[FilingOutcome] = []
    for filing, html, companyfacts in sorted(filings, key=lambda t: t[0].doc_name):
        rng = random.Random(f"{seed}:{filing.doc_name}:hard")
        drafts = hard_candidates(filing, html, companyfacts)
        index = api_index(companyfacts, filing.accession)
        for draft in drafts:
            draft.api = (
                "not_in_api" if draft.kind == "breakdown" else api_status(draft.figures, index)
            )
        usable = [d for d in drafts if d.api != "mismatch"]
        kinds = sorted({d.kind for d in usable}, key=lambda k: (kind_use[k], rng.random()))
        chosen: list[Draft] = []
        for kind in kinds[:per_filing]:
            pool = [d for d in usable if d.kind == kind]
            fewest = min(key_use[d.key] for d in pool)
            draft = rng.choice([d for d in pool if key_use[d.key] == fewest])
            kind_use[kind] += 1
            key_use[draft.key] += 1
            chosen.append(draft)
        questions = [_question(filing, html, d, seed) for d in chosen]
        outcomes.append(
            FilingOutcome(
                doc_name=filing.doc_name,
                candidates=len(drafts),
                chosen=questions,
                mismatches=[d.key for d in drafts if d.api == "mismatch"],
            )
        )
    return outcomes


def _question(filing: FreshFiling, html: HtmlFiling, draft: Draft, seed: int) -> Question:
    question = to_question(filing, draft, seed, prefix=PREFIX, generator=GENERATOR)
    for evidence in question.evidence:
        evidence.full_page_text = (
            html.pages[evidence.page] if evidence.page < len(html.pages) else ""
        )
    return question
