"""One entry point for SEC data used by the agent tools and the CLI."""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable, Iterable

from filing_agent.config import Settings, get_settings
from filing_agent.sec.companies import Company, CompanyRegistry
from filing_agent.sec.http import (
    SecClient,
    companyfacts_url,
    submissions_page_url,
    submissions_url,
)
from filing_agent.sec.submissions import Submissions, older_page_names, parse_submissions
from filing_agent.sec.xbrl import (
    CompanyFacts,
    ConceptMatch,
    PeriodFilter,
    PeriodKey,
    PeriodLabel,
    Policy,
    XbrlFact,
    find_concepts,
    get_facts,
    period_labels,
)


class SecService:
    def __init__(
        self, client: SecClient, registry: CompanyRegistry, *, max_companies: int = 6
    ) -> None:
        self.client = client
        self.registry = registry
        self.max_companies = max_companies
        # companyfacts files reach tens of MB for large filers; keep only a few parsed at once
        self._facts: OrderedDict[int, tuple[CompanyFacts, dict[PeriodKey, PeriodLabel]]] = (
            OrderedDict()
        )
        self._lock = threading.Lock()

    @classmethod
    def from_settings(
        cls, settings: Settings | None = None, *, offline: bool | None = None
    ) -> SecService:
        settings = settings or get_settings()
        client = SecClient(
            settings.sec_user_agent,
            settings.sec_cache_dir,
            offline=settings.sec_offline if offline is None else offline,
        )
        return cls(client, CompanyRegistry.load(settings.companies_file))

    def company(self, query: str) -> Company:
        return self.registry.resolve(query)

    def _facts_and_labels(
        self, company: Company
    ) -> tuple[CompanyFacts, dict[PeriodKey, PeriodLabel]]:
        # Eval runs share one service across worker threads; the lock also stops two workers
        # parsing the same multi-megabyte companyfacts file at once.
        with self._lock:
            cached = self._facts.get(company.cik)
            if cached is not None:
                self._facts.move_to_end(company.cik)
                return cached
            data = self.client.get_json(companyfacts_url(company.cik))
            facts = CompanyFacts.model_validate(data)
            entry = (facts, period_labels(facts))
            self._facts[company.cik] = entry
            while len(self._facts) > self.max_companies:
                self._facts.popitem(last=False)
            return entry

    def company_facts(self, company: Company) -> CompanyFacts:
        return self._facts_and_labels(company)[0]

    def find_concepts(self, company_query: str, query: str, top_k: int = 10) -> list[ConceptMatch]:
        facts, _ = self._facts_and_labels(self.company(company_query))
        return find_concepts(facts, query, top_k)

    def get_facts(
        self,
        company_query: str,
        concept: str,
        *,
        fiscal_year: int | None = None,
        period: PeriodFilter = "any",
        unit: str | None = None,
        policy: Policy = "first_reported",
    ) -> list[XbrlFact]:
        facts, labels = self._facts_and_labels(self.company(company_query))
        return get_facts(
            facts,
            concept,
            fiscal_year=fiscal_year,
            period=period,
            unit=unit,
            policy=policy,
            labels=labels,
        )

    def submissions(self, company: Company, *, include_older: bool = False) -> Submissions:
        data = self.client.get_json(submissions_url(company.cik))
        pages = (
            [self.client.get_json(submissions_page_url(n)) for n in older_page_names(data)]
            if include_older
            else []
        )
        return parse_submissions(data, pages)

    def warm(
        self,
        companies: Iterable[Company],
        *,
        include_older: bool = False,
        on_done: Callable[[Company], None] | None = None,
    ) -> None:
        """Fetch (or confirm cached) submissions and company facts for each company."""
        for company in companies:
            self.submissions(company, include_older=include_older)
            self.client.get_json(companyfacts_url(company.cik))
            if on_done:
                on_done(company)
