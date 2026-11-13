"""Company registry: FinanceBench company names, tickers and aliases -> SEC CIK."""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

_SUFFIXES = {
    "inc",
    "incorporated",
    "corp",
    "corporation",
    "co",
    "company",
    "plc",
    "ltd",
    "holdings",
    "group",
    "the",
}


def normalize_name(name: str) -> str:
    """Lower-case, '&' -> 'and', punctuation removed, corporate suffixes dropped."""
    text = name.lower().replace("&", " and ").replace("'", "")
    words = [w for w in re.split(r"[^a-z0-9]+", text) if w and w not in _SUFFIXES]
    return " ".join(words)


class Company(BaseModel):
    name: str
    cik: int
    tickers: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    note: str | None = None

    @property
    def cik10(self) -> str:
        """Zero-padded 10-digit CIK, as used in data.sec.gov URLs."""
        return f"{self.cik:010d}"


class UnknownCompanyError(LookupError):
    def __init__(self, query: str, suggestions: list[str]) -> None:
        hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
        super().__init__(f"No company matches {query!r}.{hint}")
        self.query = query
        self.suggestions = suggestions


class CompanyRegistry:
    def __init__(self, companies: list[Company], *, verified: bool = False) -> None:
        self.companies = companies
        self.verified = verified
        self._by_cik = {c.cik: c for c in companies}
        self._by_ticker = {t.upper(): c for c in companies for t in c.tickers}
        self._by_name: dict[str, Company] = {}
        for company in companies:
            for name in [company.name, *company.aliases]:
                self._by_name.setdefault(normalize_name(name), company)

    @classmethod
    def load(cls, path: Path) -> CompanyRegistry:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        companies = [Company.model_validate(c) for c in data["companies"]]
        return cls(companies, verified=bool(data.get("verified", False)))

    def by_cik(self, cik: int) -> Company | None:
        return self._by_cik.get(cik)

    def resolve(self, query: str) -> Company:
        """Exact CIK, ticker or (normalized) name/alias, then a unique word-prefix match."""
        text = query.strip()
        if text.isdigit() and int(text) in self._by_cik:
            return self._by_cik[int(text)]
        if text.upper() in self._by_ticker:
            return self._by_ticker[text.upper()]
        key = normalize_name(text)
        if key in self._by_name:
            return self._by_name[key]
        candidates = {
            c.cik: c
            for name, c in self._by_name.items()
            if key and (name.startswith(key) or key.startswith(name))
        }
        if len(candidates) == 1:
            return next(iter(candidates.values()))
        query_words = set(key.split())
        scored = sorted(
            (
                (len(query_words & set(name.split())) / max(len(query_words), 1), c.name)
                for name, c in self._by_name.items()
            ),
            reverse=True,
        )
        suggestions = list(dict.fromkeys(name for score, name in scored if score > 0))[:3]
        raise UnknownCompanyError(query, suggestions)
