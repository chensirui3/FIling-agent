"""XBRL company facts: periods, the company's own fiscal labels, and restatement-aware selection.

Two traps this module exists to avoid:

1. A fact's `fy`/`fp` describe the filing it came from, not the fact. A FY2019 10-K reports FY2017
   to FY2019 income values, and all three carry fy=2019. Periods come from `start`/`end`.
2. Fiscal-year labels are company conventions: Best Buy's "fiscal 2023" and Ulta's "fiscal 2022"
   both end on 2023-01-28. A period's label is the `fy`/`fp` of the first 10-K or 10-Q that
   reported it as its own period. It is never inferred from the calendar.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PeriodType = Literal["instant", "annual", "quarter", "ytd", "other"]
PeriodFilter = Literal["instant", "annual", "quarter", "ytd", "other", "any"]
Policy = Literal["first_reported", "latest"]
TAXONOMY_ORDER = ("us-gaap", "ifrs-full", "srt", "dei")


class RawFact(BaseModel):
    """One fact as delivered by the companyfacts API."""

    start: date | None = None
    end: date
    val: float
    accn: str
    fy: int | None = None
    fp: str | None = None
    form: str
    filed: date
    frame: str | None = None


class ConceptFacts(BaseModel):
    label: str | None = None
    description: str | None = None
    units: dict[str, list[RawFact]]


class CompanyFacts(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    cik: int
    entity_name: str = Field(alias="entityName")
    facts: dict[str, dict[str, ConceptFacts]]  # taxonomy -> concept -> facts

    def concepts(self) -> Iterator[tuple[str, str, ConceptFacts]]:
        for taxonomy, concepts in self.facts.items():
            for name, concept in concepts.items():
                yield taxonomy, name, concept


def period_type(start: date | None, end: date) -> PeriodType:
    if start is None:
        return "instant"
    days = (end - start).days
    if 340 <= days <= 380:
        return "annual"
    if 80 <= days <= 100:
        return "quarter"
    if 170 <= days <= 200 or 260 <= days <= 290:
        return "ytd"
    return "other"


def is_periodic_report(form: str) -> bool:
    return form.startswith(("10-K", "10-Q", "20-F", "40-F"))


PeriodKey = tuple[date | None, date]


@dataclass(frozen=True)
class PeriodLabel:
    fiscal_year: int | None
    fiscal_period: str | None  # FY, Q1, Q2, Q3, Q4
    accession: str  # the filing the label was taken from
    inferred: bool = False  # True if no filing reported the period as its own current period


def _periodic_records(facts: CompanyFacts) -> Iterator[RawFact]:
    for _, _, concept in facts.concepts():
        for records in concept.units.values():
            for record in records:
                if record.fy is not None and is_periodic_report(record.form):
                    yield record


def filing_period_ends(facts: CompanyFacts) -> dict[str, date]:
    """Each filing's own period end: the latest end of its duration facts (else of its instants).

    Durations are preferred because cover-page instants (shares outstanding) are dated after the
    period end.
    """
    durations: dict[str, date] = {}
    instants: dict[str, date] = {}
    for record in _periodic_records(facts):
        target = durations if record.start is not None else instants
        if record.end > target.get(record.accn, date.min):
            target[record.accn] = record.end
    return {accn: durations.get(accn) or instants[accn] for accn in {*durations, *instants}}


def period_labels(facts: CompanyFacts) -> dict[PeriodKey, PeriodLabel]:
    """The company's own fiscal label for every (start, end) it reports.

    A label is taken from the earliest filing whose own period ends on that date, i.e. the filing
    that reported the period as its current period. Periods that only ever appear as comparatives
    (the first years of a company's XBRL history) get a label inferred from the year offset.
    """
    ends = filing_period_ends(facts)
    current: dict[PeriodKey, RawFact] = {}
    comparative: dict[PeriodKey, RawFact] = {}
    for record in _periodic_records(facts):
        key = (record.start, record.end)
        target = current if ends.get(record.accn) == record.end else comparative
        best = target.get(key)
        if best is None or (record.filed, record.accn) < (best.filed, best.accn):
            target[key] = record

    labels: dict[PeriodKey, PeriodLabel] = {}
    for key, record in current.items():
        fiscal_period = record.fp
        if period_type(*key) == "quarter" and fiscal_period == "FY":
            fiscal_period = "Q4"  # a three-month period reported in a 10-K
        labels[key] = PeriodLabel(record.fy, fiscal_period, record.accn)
    for key, record in comparative.items():
        if key in labels or record.fy is None:
            continue
        kind = period_type(*key)
        days_back = (ends[record.accn] - record.end).days
        years_back = round(days_back / 365.25)
        if kind == "instant" and record.fp not in (None, "FY") and days_back > 0:
            years_back = max(years_back, 1)  # a 10-Q's prior fiscal-year-end balance sheet
        fiscal_period = "FY" if kind in ("annual", "instant") else record.fp
        labels[key] = PeriodLabel(record.fy - years_back, fiscal_period, record.accn, True)
    return labels


class ReportedValue(BaseModel):
    value: float
    form: str
    accession: str
    filed: date


class XbrlFact(BaseModel):
    concept: str
    label: str | None
    unit: str
    value: float
    start: date | None
    end: date
    period_type: PeriodType
    fiscal_year: int | None
    fiscal_period: str | None
    fiscal_label_inferred: bool = False
    form: str
    accession: str
    filed: date
    restated: bool = False
    reported_values: list[ReportedValue] = Field(default_factory=list)


class UnknownConceptError(LookupError):
    def __init__(self, concept: str, suggestions: list[str]) -> None:
        hint = f" Close matches: {', '.join(suggestions)}." if suggestions else ""
        super().__init__(f"Concept {concept!r} is not reported by this company.{hint}")
        self.suggestions = suggestions


def resolve_concept(facts: CompanyFacts, concept: str) -> tuple[str, str, ConceptFacts]:
    """Accepts 'us-gaap:Revenues' or a bare 'Revenues' (searched in TAXONOMY_ORDER)."""
    taxonomy, _, name = concept.rpartition(":")
    if taxonomy:
        found = facts.facts.get(taxonomy, {}).get(name)
        if found is not None:
            return taxonomy, name, found
    else:
        ordered = [t for t in TAXONOMY_ORDER if t in facts.facts]
        ordered += sorted(t for t in facts.facts if t not in TAXONOMY_ORDER)
        for candidate in ordered:
            found = facts.facts[candidate].get(name)
            if found is not None:
                return candidate, name, found
    suggestions = [m.concept for m in find_concepts(facts, " ".join(concept_words(name)), 5)]
    raise UnknownConceptError(concept, suggestions)


def get_facts(
    facts: CompanyFacts,
    concept: str,
    *,
    fiscal_year: int | None = None,
    period: PeriodFilter = "any",
    unit: str | None = None,
    policy: Policy = "first_reported",
    labels: dict[PeriodKey, PeriodLabel] | None = None,
) -> list[XbrlFact]:
    """One fact per (unit, period), most recent period first.

    `first_reported` returns the value from the earliest periodic report of that period (what a
    question about that filing should match); `latest` returns the most recently filed value.
    Either way, `restated` flags periods whose value changed across filings.
    """
    taxonomy, name, concept_facts = resolve_concept(facts, concept)
    labels = labels if labels is not None else period_labels(facts)
    out: list[XbrlFact] = []
    for unit_name, records in concept_facts.units.items():
        if unit is not None and unit_name != unit:
            continue
        by_period: dict[PeriodKey, list[RawFact]] = defaultdict(list)
        for record in records:
            if is_periodic_report(record.form):
                by_period[(record.start, record.end)].append(record)
        for key, group in by_period.items():
            kind = period_type(*key)
            if period != "any" and kind != period:
                continue
            label = labels.get(key)
            year = label.fiscal_year if label else None
            if fiscal_year is not None and year != fiscal_year:
                continue
            group.sort(key=lambda r: (r.filed, r.accn))
            chosen = group[0] if policy == "first_reported" else group[-1]
            first_of_each_value: dict[float, RawFact] = {}
            for record in group:
                first_of_each_value.setdefault(record.val, record)
            restated = len(first_of_each_value) > 1
            out.append(
                XbrlFact(
                    concept=f"{taxonomy}:{name}",
                    label=concept_facts.label,
                    unit=unit_name,
                    value=chosen.val,
                    start=key[0],
                    end=key[1],
                    period_type=kind,
                    fiscal_year=year,
                    fiscal_period=label.fiscal_period if label else None,
                    fiscal_label_inferred=label.inferred if label else False,
                    form=chosen.form,
                    accession=chosen.accn,
                    filed=chosen.filed,
                    restated=restated,
                    reported_values=[
                        ReportedValue(value=r.val, form=r.form, accession=r.accn, filed=r.filed)
                        for r in first_of_each_value.values()
                    ]
                    if restated
                    else [],
                )
            )
    out.sort(key=lambda f: (f.end, f.start or f.end, f.unit), reverse=True)
    return out


# --------------------------------------------------------------------------------------------
# Concept search
# --------------------------------------------------------------------------------------------

_CAMEL = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")
_WORD = re.compile(r"[a-z]{2,}")


def concept_words(name: str) -> list[str]:
    """'PaymentsToAcquirePropertyPlantAndEquipment' -> ['payments', 'to', 'acquire', ...]."""
    return [w.lower() for w in _CAMEL.findall(name)]


def _stem(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


_IGNORED = {"of", "to", "and", "the", "for", "in", "on", "by", "net", "total"}


def _words(text: str) -> set[str]:
    return {_stem(w) for w in _WORD.findall(text.lower()) if w not in _IGNORED}


class ConceptMatch(BaseModel):
    concept: str
    label: str | None
    description: str | None
    units: list[str]
    n_facts: int
    first_year: int | None
    last_year: int | None
    score: float


def find_concepts(facts: CompanyFacts, query: str, top_k: int = 10) -> list[ConceptMatch]:
    """Concepts whose name or label share words with the query, best and most recent first."""
    wanted = _words(query)
    if not wanted:
        return []
    matches: list[ConceptMatch] = []
    for taxonomy, name, concept in facts.concepts():
        title = _words(" ".join(concept_words(name))) | _words(concept.label or "")
        in_title = len(wanted & title)
        in_description = len((wanted - title) & _words(concept.description or ""))
        if not in_title and not in_description:
            continue
        ends = [r.end for records in concept.units.values() for r in records]
        score = 3.0 * in_title + in_description - 0.1 * len(title - wanted)
        matches.append(
            ConceptMatch(
                concept=f"{taxonomy}:{name}",
                label=concept.label,
                description=(concept.description or "")[:240] or None,
                units=sorted(concept.units),
                n_facts=len(ends),
                first_year=min(ends).year if ends else None,
                last_year=max(ends).year if ends else None,
                score=round(score, 2),
            )
        )
    matches.sort(key=lambda m: (-m.score, -(m.last_year or 0), m.concept))
    return matches[:top_k]
