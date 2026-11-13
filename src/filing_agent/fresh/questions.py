"""Numeric questions about the fresh filings, built from their inline-XBRL figures.

Every figure in a statement table carries its XBRL concept, period and scale, and the parser
knows the page and row it is printed on. Questions built from these figures therefore have exact
gold values and evidence pages:

- **extraction:** one line item for one period, named the way an analyst would ("total
  revenue", "capital expenditures"). A figure is used only when its printed row label agrees
  with that name ("Net sales" for revenue, "Payments for property and equipment" for capex).
- **derived:** a margin, growth rate, ratio or difference of two figures. The formula is stated
  in the company's own row labels, so the gold has one meaning.

Each component figure is checked against the SEC XBRL API (companyfacts) for the same filing
(accession number), concept, unit and period. A disagreement drops the question.
"""

from __future__ import annotations

import math
import random
import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from filing_agent.corpus.html_parse import HtmlFiling, html_statement
from filing_agent.data.financebench import Evidence, Question
from filing_agent.fresh.filings import FreshFiling

GENERATOR = "fresh-xbrl/1"
DEFAULT_SEED = 7

Family = Literal["income", "balance", "cash_flow"]
PeriodKind = Literal["quarter", "ytd", "year", "instant"]
ApiStatus = Literal["verified", "not_in_api", "mismatch"]

FAMILIES: tuple[Family, ...] = ("income", "balance", "cash_flow")
STATEMENT: dict[Family, str] = {
    "income": "income_statement",
    "balance": "balance_sheet",
    "cash_flow": "cash_flow_statement",
}
STATEMENT_NAMES = {
    "income_statement": "income statement",
    "balance_sheet": "balance sheet",
    "cash_flow_statement": "cash flow statement",
}
SCALE_WORDS = {3: "thousands", 6: "millions", 9: "billions"}
DISPLAY_NAMES = {"McDonalds": "McDonald's", "Paypal": "PayPal"}


def display_name(company: str) -> str:
    return DISPLAY_NAMES.get(company, company)


def possessive(name: str) -> str:
    """ "Walmart's", "McDonald's", "General Mills'", "American Express's"."""
    if name.endswith("'s"):
        return name
    if name.endswith("s") and not name.endswith("ss"):
        return f"{name}'"
    return f"{name}'s"


def long_date(day: date) -> str:
    return f"{day:%B} {day.day}, {day.year}"


# --------------------------------------------------------------------------------------------
# Figures on statement pages
# --------------------------------------------------------------------------------------------

_FOOTNOTE = re.compile(r"\s*(?:\((?:\d{1,2}|[a-z]|note \d+[a-z]?)\)|\*+)\s*$", re.IGNORECASE)


def clean_label(cell: str) -> str:
    label = cell.strip().rstrip(":").strip()
    while (stripped := _FOOTNOTE.sub("", label)) != label:
        label = stripped.rstrip(":").strip()
    return label


@dataclass(frozen=True)
class Figure:
    """A whole-company figure printed in a primary statement table."""

    concept: str  # "us-gaap:Revenues"
    unit: str  # "USD", "USD/shares"
    value: float  # in the unit (dollars, dollars per share)
    scale: int  # 6 = printed in millions
    displayed: str  # "186,100"
    start: date | None  # None for a balance (instant)
    end: date
    page: int
    statement: str
    row: str  # the printed table row, cells joined by " | "

    @property
    def taxonomy(self) -> str:
        return self.concept.split(":", 1)[0]

    @property
    def name(self) -> str:
        return self.concept.split(":", 1)[-1]

    @property
    def days(self) -> int | None:
        return None if self.start is None else (self.end - self.start).days + 1

    @property
    def decimals(self) -> int:
        """Decimals as printed ("4,389.5" -> 1)."""
        return len(self.displayed.split(".")[1]) if "." in self.displayed else 0

    @property
    def label(self) -> str:
        return clean_label(self.row.split(" | ")[0])

    @property
    def scaled(self) -> float:
        """The value in the printed scale (186,100 for $186.1 billion printed in millions)."""
        return self.value / 10.0**self.scale


def statement_figures(html: HtmlFiling) -> list[Figure]:
    tags = [html_statement(page) for page in html.pages]
    figures: list[Figure] = []
    for fact in html.facts:
        context = html.contexts.get(fact.context)
        statement = tags[fact.page] if fact.page < len(tags) else None
        if context is None or context.dimensions or statement not in STATEMENT_NAMES:
            continue
        unit = html.units.get(fact.unit or "")
        value = fact.value()
        row = html.line_text(fact)
        if unit is None or value is None or not row:
            continue
        figures.append(
            Figure(
                concept=fact.concept,
                unit=unit,
                value=value,
                scale=fact.scale,
                displayed=fact.text,
                start=context.start,
                end=context.end,
                page=fact.page,
                statement=statement or "",
                row=row,
            )
        )
    return figures


# --------------------------------------------------------------------------------------------
# Periods
# --------------------------------------------------------------------------------------------


def duration_kind(days: int | None) -> PeriodKind | None:
    if days is None:
        return "instant"
    if 80 <= days <= 100:
        return "quarter"
    if 170 <= days <= 285:
        return "ytd"
    if 350 <= days <= 380:
        return "year"
    return None


@dataclass(frozen=True)
class Period:
    kind: PeriodKind
    start: date | None
    end: date

    def matches(self, figure: Figure) -> bool:
        return figure.end == self.end and figure.start == self.start

    def matches_prior_year(self, figure: Figure) -> bool:
        """The same period one year earlier (52/53-week years end a few days apart)."""
        return (
            duration_kind(figure.days) == self.kind and 358 <= (self.end - figure.end).days <= 372
        )

    def phrase(self) -> str:
        if self.kind == "instant":
            return f"as of {long_date(self.end)}"
        if self.kind == "year":
            return f"in the fiscal year ended {long_date(self.end)}"
        days = (self.end - self.start).days + 1 if self.start else 0
        weeks, remainder = divmod(days, 7)
        if remainder == 0 and weeks not in (13, 26, 39):
            # 12-week quarters and 36-week years to date (PepsiCo, Costco) are named in weeks
            return f"in the {weeks} weeks ended {long_date(self.end)}"
        months = {3: "three", 6: "six", 9: "nine"}.get(round(days / 30.4), str(round(days / 30.4)))
        return f"in the {months} months ended {long_date(self.end)}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "start": self.start.isoformat() if self.start else None,
            "end": self.end.isoformat(),
        }


def filing_periods(figures: Sequence[Figure], end: date) -> dict[PeriodKind, Period]:
    """The filing's own periods: each duration kind ending at `end`, with its usual start."""
    starts: dict[PeriodKind, Counter[date]] = {}
    for figure in figures:
        kind = duration_kind(figure.days)
        if figure.end == end and figure.start is not None and kind is not None:
            starts.setdefault(kind, Counter())[figure.start] += 1
    periods: dict[PeriodKind, Period] = {
        kind: Period(kind, c.most_common(1)[0][0], end) for kind, c in starts.items()
    }
    periods["instant"] = Period("instant", None, end)
    return periods


def period_end(filing: FreshFiling, figures: Sequence[Figure]) -> date | None:
    if filing.report_date is not None:
        return filing.report_date
    ends = Counter(f.end for f in figures if f.statement == "income_statement")
    return ends.most_common(1)[0][0] if ends else None


# --------------------------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Metric:
    key: str
    family: Family
    name: str  # generic name used in formulas when the printed label is unusable
    stem: str  # question with {owner} (possessive), {company} and {period}
    concepts: tuple[str, ...]  # us-gaap concepts, most specific first
    label: re.Pattern[str]  # the printed row label must match
    unit: str = "USD"


def _m(
    key: str, family: Family, name: str, stem: str, concepts: str, label: str, unit: str = "USD"
) -> Metric:
    return Metric(
        key, family, name, stem, tuple(concepts.split()), re.compile(label, re.IGNORECASE), unit
    )


METRICS: tuple[Metric, ...] = (
    _m("revenue", "income", "total revenue", "What was {owner} total revenue {period}?",
       "Revenues RevenueFromContractWithCustomerExcludingAssessedTax "
       "RevenueFromContractWithCustomerIncludingAssessedTax SalesRevenueNet",
       r"revenue|sales"),
    _m("cost_of_revenue", "income", "cost of revenue",
       "What was {owner} cost of revenue (cost of sales) {period}?",
       "CostOfRevenue CostOfGoodsAndServicesSold CostOfGoodsSold", r"\bcosts? of\b"),
    _m("gross_profit", "income", "gross profit", "What was {owner} gross profit {period}?",
       "GrossProfit", r"gross"),
    _m("operating_income", "income", "operating income",
       "What was {owner} operating income {period}?", "OperatingIncomeLoss",
       r"operat"),
    _m("net_income", "income", "net income attributable to the company",
       "What was the net income attributable to {company} {period}?", "NetIncomeLoss",
       r"net (?:income|earnings)"),
    _m("diluted_eps", "income", "diluted earnings per share",
       "What were {owner} diluted earnings per share {period}?", "EarningsPerShareDiluted",
       r"diluted", unit="USD/shares"),
    _m("rnd", "income", "research and development expense",
       "How much did {company} spend on research and development {period}?",
       "ResearchAndDevelopmentExpense ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost",
       r"research"),
    _m("sga", "income", "selling, general and administrative expenses",
       "What were {owner} selling, general and administrative expenses {period}?",
       "SellingGeneralAndAdministrativeExpense", r"general"),
    _m("income_tax", "income", "income tax expense",
       "What was {owner} income tax expense {period}?", "IncomeTaxExpenseBenefit", r"tax"),
    _m("pretax_income", "income", "income before income taxes",
       "What was {owner} income before income taxes {period}?",
       "IncomeLossFromContinuingOperationsBeforeIncomeTaxes"  # two concept names, split for length
       "ExtraordinaryItemsNoncontrollingInterest "
       "IncomeLossFromContinuingOperationsBeforeIncomeTaxes"
       "MinorityInterestAndIncomeLossFromEquityMethodInvestments",
       r"before"),
    _m("total_assets", "balance", "total assets", "What were {owner} total assets {period}?",
       "Assets", r"total assets"),
    _m("total_liabilities", "balance", "total liabilities",
       "What were {owner} total liabilities {period}?", "Liabilities", r"total liabilities"),
    _m("current_assets", "balance", "total current assets",
       "What were {owner} total current assets {period}?", "AssetsCurrent", r"current assets"),
    _m("current_liabilities", "balance", "total current liabilities",
       "What were {owner} total current liabilities {period}?", "LiabilitiesCurrent",
       r"current liabilities"),
    _m("cash", "balance", "cash and cash equivalents",
       "How much cash and cash equivalents did {company} hold {period}?",
       "CashAndCashEquivalentsAtCarryingValue", r"cash"),
    _m("inventory", "balance", "inventories",
       "What was the carrying amount of {owner} inventories {period}?", "InventoryNet",
       r"inventor"),
    _m("receivables", "balance", "accounts receivable, net",
       "What were {owner} net accounts receivable {period}?",
       "AccountsReceivableNetCurrent", r"receivable"),
    _m("equity", "balance", "shareholders' equity attributable to the company",
       "What was the total shareholders' equity attributable to {company} {period}?",
       "StockholdersEquity", r"equity"),
    _m("long_term_debt", "balance", "long-term debt",
       "What was {owner} long-term debt, excluding the current portion, {period}?",
       "LongTermDebtNoncurrent", r"debt"),
    _m("cfo", "cash_flow", "net cash provided by operating activities",
       "How much net cash did {company} generate from operating activities {period}?",
       "NetCashProvidedByUsedInOperatingActivities", r"operat"),
    _m("capex", "cash_flow", "capital expenditures",
       "How much did {company} spend on capital expenditures (purchases of property and "
       "equipment) {period}?",
       "PaymentsToAcquirePropertyPlantAndEquipment PaymentsToAcquireProductiveAssets",
       r"propert|plant|equipment|capital"),
    _m("dividends", "cash_flow", "cash dividends paid",
       "How much did {company} pay in cash dividends {period}?",
       "PaymentsOfDividendsCommonStock PaymentsOfDividends", r"dividend"),
    _m("buybacks", "cash_flow", "repurchases of common stock",
       "How much cash did {company} spend repurchasing its common stock {period}?",
       "PaymentsForRepurchaseOfCommonStock", r"repurchase|purchase|treasury|buyback"),
    _m("dna", "cash_flow", "depreciation and amortization",
       "What was {owner} depreciation and amortization expense {period}?",
       "DepreciationDepletionAndAmortization DepreciationAndAmortization "
       "DepreciationAmortizationAndAccretionNet",
       r"depreciation"),
)  # fmt: skip
METRIC = {m.key: m for m in METRICS}


def find_figure(
    figures: Sequence[Figure], metric: Metric, match: Callable[[Figure], bool]
) -> Figure | None:
    """The metric's figure on its statement for a period, or None if absent or ambiguous."""
    statement = STATEMENT[metric.family]
    for concept in metric.concepts:
        found = [
            f
            for f in figures
            if f.taxonomy == "us-gaap"
            and f.name == concept
            and f.unit == metric.unit
            and f.statement == statement
            and match(f)
        ]
        if not found:
            continue
        if len({f.value for f in found}) > 1:  # one concept and period, two printed values
            return None
        return found[0] if metric.label.search(found[0].label) else None
    return None


# --------------------------------------------------------------------------------------------
# Question drafts
# --------------------------------------------------------------------------------------------


@dataclass
class Draft:
    key: str
    kind: str  # extraction, derived; the hard tier adds breakdown, history, definition
    family: Family
    question: str
    answer: str
    justification: str
    figures: list[Figure]
    period: Period
    formula: str | None = None
    api: ApiStatus = "not_in_api"


def _units_sentence(figure: Figure) -> str | None:
    if figure.unit == "USD/shares":
        return "Answer in USD per share."
    word = SCALE_WORDS.get(figure.scale)
    return f"Answer in USD {word}." if word else None


def _money(value: float, decimals: int) -> str:
    return f"${value:.{decimals}f}" if value >= 0 else f"-${-value:.{decimals}f}"


def where(figure: Figure) -> str:
    return f"{STATEMENT_NAMES.get(figure.statement, 'notes')}, page {figure.page}"


def extraction_draft(metric: Metric, figure: Figure, period: Period, company: str) -> Draft | None:
    units = _units_sentence(figure)
    if units is None or figure.value <= 0:  # signs invite ambiguity ("loss of 5" vs "-5")
        return None
    if figure.unit == "USD/shares":
        answer = f"{figure.value:.{figure.decimals}f}"
    else:
        answer = _money(figure.scaled, figure.decimals)
    stem = metric.stem.format(owner=possessive(company), company=company, period=period.phrase())
    return Draft(
        key=metric.key,
        kind="extraction",
        family=metric.family,
        question=f"{stem} {units}",
        answer=answer,
        justification=f'"{figure.label}" {period.phrase()}: {figure.displayed} ({where(figure)}).',
        figures=[figure],
        period=period,
    )


def rounding_decimals(value: float, decimals: int) -> int:
    """`decimals`, or one more when the value sits on a rounding boundary (x.x5)."""
    shifted = abs(value) * 10**decimals
    return decimals + 1 if abs(shifted - math.floor(shifted) - 0.5) < 0.05 else decimals


_GENERIC_LABELS = {"total", "basic", "diluted", "net", "other", "subtotal"}


def quoted_label(figure: Figure, metric: Metric) -> str:
    label = figure.label
    usable = len(label) >= 4 and label.lower() not in _GENERIC_LABELS and len(label) <= 80
    return f'"{label}"' if usable else metric.name


@dataclass(frozen=True)
class DerivedSpec:
    key: str
    family: Family
    parts: tuple[str, ...]  # metric keys; "prior:<key>" is the same line a year earlier
    kind: Literal["percent", "ratio", "money"]
    stem: str  # {owner}, {company}, {period}, {a}, {b}
    compute: Callable[[float, float], float | None]
    formula: str  # with {a} and {b}


def _ratio(a: float, b: float) -> float | None:
    return a / b if b > 0 else None


DERIVED: tuple[DerivedSpec, ...] = (
    DerivedSpec("gross_margin", "income", ("gross_profit", "revenue"), "percent",
                "What was {owner} gross margin {period}, calculated as {a} divided by {b}?",
                _ratio, "{a} / {b}"),
    DerivedSpec("gross_margin_from_cost", "income", ("revenue", "cost_of_revenue"), "percent",
                "What was {owner} gross margin {period}, calculated as {a} minus {b}, "
                "divided by {a}?",
                lambda a, b: (a - b) / a if a > 0 else None, "({a} - {b}) / {a}"),
    DerivedSpec("operating_margin", "income", ("operating_income", "revenue"), "percent",
                "What was {owner} operating margin {period}, calculated as {a} divided by {b}?",
                _ratio, "{a} / {b}"),
    DerivedSpec("net_margin", "income", ("net_income", "revenue"), "percent",
                "What was {owner} net profit margin {period}, calculated as {a} divided by {b}?",
                _ratio, "{a} / {b}"),
    DerivedSpec("rnd_intensity", "income", ("rnd", "revenue"), "percent",
                "What was {owner} research and development spending as a share of revenue "
                "{period}, calculated as {a} divided by {b}?",
                _ratio, "{a} / {b}"),
    DerivedSpec("effective_tax_rate", "income", ("income_tax", "pretax_income"), "percent",
                "What was {owner} effective tax rate {period}, calculated as {a} divided by {b}?",
                _ratio, "{a} / {b}"),
    DerivedSpec("revenue_growth", "income", ("revenue", "prior:revenue"), "percent",
                "By what percentage did {owner} {a} change {period}, compared with the same "
                "period a year earlier?",
                lambda a, b: (a - b) / b if b > 0 else None,
                "({a} - prior-year {a}) / prior-year {a}"),
    DerivedSpec("current_ratio", "balance", ("current_assets", "current_liabilities"), "ratio",
                "What was {owner} current ratio {period}, calculated as {a} divided by {b}?",
                _ratio, "{a} / {b}"),
    DerivedSpec("liabilities_to_assets", "balance", ("total_liabilities", "total_assets"),
                "percent",
                "What share of {owner} total assets was financed by liabilities {period}, "
                "calculated as {a} divided by {b}?",
                _ratio, "{a} / {b}"),
    DerivedSpec("working_capital", "balance", ("current_assets", "current_liabilities"), "money",
                "What was {owner} working capital {period}, calculated as {a} minus {b}?",
                lambda a, b: a - b, "{a} - {b}"),
    DerivedSpec("free_cash_flow", "cash_flow", ("cfo", "capex"), "money",
                "What was {owner} free cash flow {period}, calculated as {a} minus {b}?",
                lambda a, b: a - b, "{a} - {b}"),
    DerivedSpec("capex_to_cfo", "cash_flow", ("capex", "cfo"), "percent",
                "What were {owner} capital expenditures as a share of its operating cash flow "
                "{period}, calculated as {a} divided by {b}?",
                _ratio, "{a} / {b}"),
    DerivedSpec("shareholder_returns", "cash_flow", ("dividends", "buybacks"), "money",
                "How much cash did {company} return to shareholders {period}, calculated as {a} "
                "plus {b}?",
                lambda a, b: a + b, "{a} + {b}"),
)  # fmt: skip


def derived_draft(
    spec: DerivedSpec,
    figures: Sequence[Figure],
    period: Period,
    company: str,
) -> Draft | None:
    parts: list[Figure] = []
    for part in spec.parts:
        key = part.removeprefix("prior:")
        match = period.matches_prior_year if part.startswith("prior:") else period.matches
        figure = find_figure(figures, METRIC[key], match)
        if figure is None:
            return None
        parts.append(figure)
    a, b = parts
    if a.unit != "USD" or b.unit != "USD" or (spec.kind == "money" and a.scale != b.scale):
        return None
    if a.value <= 0 or b.value <= 0:
        # A negative component reads two ways in a label formula: "Net cash used in operating
        # activities" printed as 527 is -527 in XBRL.
        return None
    value = spec.compute(a.value, b.value)
    if value is None:
        return None
    labels = {
        "a": quoted_label(a, METRIC[spec.parts[0].removeprefix("prior:")]),
        "b": quoted_label(b, METRIC[spec.parts[1].removeprefix("prior:")]),
    }
    stem = spec.stem.format(
        owner=possessive(company), company=company, period=period.phrase(), **labels
    )
    if spec.kind == "percent":
        decimals = rounding_decimals(value * 100, 1)
        answer = f"{value * 100:.{decimals}f}%"
        places = "one decimal place" if decimals == 1 else "two decimal places"
        units = f"Answer in percent, rounded to {places}."
        if spec.key == "revenue_growth":
            units += " Use a negative number for a decrease."
        shown = f"{value * 100:.3f}%"
    elif spec.kind == "ratio":
        decimals = rounding_decimals(value, 2)
        answer = f"{value:.{decimals}f}"
        units = f"Round to {'two' if decimals == 2 else 'three'} decimal places."
        shown = f"{value:.4f}"
    else:
        word = SCALE_WORDS.get(a.scale)
        if word is None:
            return None
        decimals = max(a.decimals, b.decimals)
        answer = _money(value / 10.0**a.scale, decimals)
        units = f"Answer in USD {word}."
        shown = answer
    formula = spec.formula.format(**labels)
    pieces = "; ".join(f'"{f.label}" {f.displayed} ({where(f)})' for f in parts)
    return Draft(
        key=spec.key,
        kind="derived",
        family=spec.family,
        question=f"{stem} {units}",
        answer=answer,
        justification=f"{formula} = {shown}, using {pieces}.",
        figures=parts,
        period=period,
        formula=formula,
    )


# --------------------------------------------------------------------------------------------
# Cross-check against the SEC XBRL API
# --------------------------------------------------------------------------------------------

ApiKey = tuple[str, str, str, str | None, str]


def api_index(companyfacts: Mapping[str, Any] | None, accession: str) -> dict[ApiKey, list[float]]:
    """companyfacts values from one filing, keyed by (taxonomy, concept, unit, start, end)."""
    index: dict[ApiKey, list[float]] = {}
    for taxonomy, concepts in (companyfacts or {}).get("facts", {}).items():
        for concept, body in concepts.items():
            for unit, entries in body.get("units", {}).items():
                for entry in entries:
                    if entry.get("accn") == accession:
                        key = (taxonomy, concept, unit, entry.get("start"), entry["end"])
                        index.setdefault(key, []).append(float(entry["val"]))
    return index


def api_status(figures: Iterable[Figure], index: Mapping[ApiKey, list[float]]) -> ApiStatus:
    if not index:
        return "not_in_api"
    status: ApiStatus = "verified"
    for figure in figures:
        start = figure.start.isoformat() if figure.start else None
        values = index.get(
            (figure.taxonomy, figure.name, figure.unit, start, figure.end.isoformat())
        )
        if not values:
            status = "not_in_api"
        elif not any(math.isclose(v, figure.value, rel_tol=1e-9, abs_tol=1e-6) for v in values):
            return "mismatch"
    return status


# --------------------------------------------------------------------------------------------
# Choosing questions for each filing
# --------------------------------------------------------------------------------------------


def candidate_drafts(
    filing: FreshFiling, figures: Sequence[Figure], rng: random.Random
) -> list[Draft]:
    end = period_end(filing, figures)
    if end is None:
        return []
    periods = filing_periods(figures, end)
    company = display_name(filing.company)
    annual = filing.form == "10-K"
    income = periods.get("year") if annual else periods.get("quarter")
    if not annual and "ytd" in periods and rng.random() < 1 / 3:
        income = periods["ytd"]  # a year-to-date column now and then: picking the column matters
    cash_flow = periods.get("year") or periods.get("ytd") or periods.get("quarter")
    period_for: dict[Family, Period | None] = {
        "income": income,
        "balance": periods["instant"],
        "cash_flow": cash_flow,
    }
    drafts: list[Draft] = []
    for metric in METRICS:
        period = period_for[metric.family]
        figure = find_figure(figures, metric, period.matches) if period else None
        if period and figure and (draft := extraction_draft(metric, figure, period, company)):
            drafts.append(draft)
    for spec in DERIVED:
        period = period_for[spec.family]
        if period and (draft := derived_draft(spec, figures, period, company)):
            drafts.append(draft)
    if any(d.key == "gross_margin" for d in drafts):  # the company's own gross profit line wins
        drafts = [d for d in drafts if d.key != "gross_margin_from_cost"]
    return drafts


def _choose(
    drafts: Sequence[Draft],
    kind: str,
    family_order: Sequence[Family],
    usage: Counter[str],
    rng: random.Random,
) -> Draft | None:
    for family in family_order:
        pool = [d for d in drafts if d.kind == kind and d.family == family and d.api != "mismatch"]
        if pool:
            fewest = min(usage[d.key] for d in pool)
            choice = rng.choice([d for d in pool if usage[d.key] == fewest])
            usage[choice.key] += 1
            return choice
    return None


def to_question(
    filing: FreshFiling,
    draft: Draft,
    seed: int,
    *,
    prefix: str = "fresh",
    generator: str = GENERATOR,
) -> Question:
    evidence: dict[tuple[int, str], Evidence] = {}
    for figure in draft.figures:
        evidence.setdefault(
            (figure.page, figure.row),
            Evidence(
                doc_name=filing.doc_name, page=figure.page, text=figure.row, full_page_text=""
            ),
        )
    return Question(
        id=f"{prefix}_{filing.doc_name}_{draft.key}",
        company=filing.company,
        doc_name=filing.doc_name,
        question_type="metrics-generated",
        question_reasoning=(
            "Information extraction"
            if draft.kind in ("extraction", "breakdown")
            else "Numerical reasoning"
        ),
        question=draft.question,
        answer=draft.answer,
        justification=draft.justification,
        evidence=list(evidence.values()),
        meta={
            "generator": generator,
            "seed": seed,
            "kind": draft.kind,
            "metric": draft.key,
            "family": draft.family,
            "period": draft.period.as_dict(),
            "formula": draft.formula,
            "xbrl_api": draft.api,
            "accession": filing.accession,
            "components": [
                {
                    "concept": f.concept,
                    "unit": f.unit,
                    "value": f.value,
                    "displayed": f.displayed,
                    "start": f.start.isoformat() if f.start else None,
                    "end": f.end.isoformat(),
                    "page": f.page,
                    "row": f.row,
                }
                for f in draft.figures
            ],
        },
    )


@dataclass
class FilingOutcome:
    doc_name: str
    candidates: int
    chosen: list[Question]
    mismatches: list[str]  # draft keys whose figures disagree with the XBRL API


def numeric_questions(
    filings: Sequence[tuple[FreshFiling, HtmlFiling, Mapping[str, Any] | None]],
    *,
    seed: int = DEFAULT_SEED,
) -> list[FilingOutcome]:
    """One extraction and one derived question per filing, spread across the statements.

    Filing i asks its extraction question about statement family i mod 3 and its derived
    question about the next family; within a family the least-used metric so far is picked
    (ties broken by the seeded generator), so the set covers many line items.
    """
    usage: Counter[str] = Counter()
    outcomes: list[FilingOutcome] = []
    for i, (filing, html, companyfacts) in enumerate(sorted(filings, key=lambda t: t[0].doc_name)):
        rng = random.Random(f"{seed}:{filing.doc_name}")
        drafts = candidate_drafts(filing, statement_figures(html), rng)
        index = api_index(companyfacts, filing.accession)
        for draft in drafts:
            draft.api = api_status(draft.figures, index)
        extraction_order = FAMILIES[i % 3 :] + FAMILIES[: i % 3]
        derived_order = extraction_order[1:] + extraction_order[:1]
        chosen = [
            draft
            for draft in (
                _choose(drafts, "extraction", extraction_order, usage, rng),
                _choose(drafts, "derived", derived_order, usage, rng),
            )
            if draft is not None
        ]
        page_text = dict(enumerate(html.pages))
        questions = []
        for draft in chosen:
            question = to_question(filing, draft, seed)
            for evidence in question.evidence:
                evidence.full_page_text = page_text.get(evidence.page, "")
            questions.append(question)
        outcomes.append(
            FilingOutcome(
                doc_name=filing.doc_name,
                candidates=len(drafts),
                chosen=questions,
                mismatches=[d.key for d in drafts if d.api == "mismatch"],
            )
        )
    return outcomes
