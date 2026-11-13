"""System prompts, versioned. A prompt change is a new version, never an edit in place."""

from __future__ import annotations

from collections.abc import Collection

PROMPT_VERSION = "v1"  # the default; configs pin newer versions explicitly
PROMPT_VERSIONS = ("v1", "v2")

# v2 adds one step. On the fresh hard tier the agent took a figure from a filing whose label
# said fiscal 2024 while its own text said fiscal 2023, and noticed but used it anyway.
_LABEL_CHECK = (
    "A filing's name and listed fiscal year are labels, and labels can be wrong. Before "
    "using a figure, check the period the document itself states (its title, cover or "
    "column headings). If that is not the period asked about, do not use the figure; get "
    "the asked period from another filing{xbrl}."
)

_ROLE = (
    "You are a financial research analyst. You answer questions about US public companies "
    "from their SEC filings and the financial data they report. Your answers are checked "
    "against the filings, so every figure must come from a source you looked at."
)

_ANSWER = (
    "When you are done, reply with the final answer as JSON in the required format: the "
    "status, a direct answer of one to three sentences that states the figure with its unit, "
    "the key figure itself (value, unit, scale), the period, the calculation if you computed "
    "anything, and the sources you used. If the information needed is not available, set "
    "answer_status to insufficient_information and say what is missing instead of guessing."
)


def system_prompt(tools: Collection[str], version: str = PROMPT_VERSION) -> str:
    """The prompt of `version`, adapted to the tools a configuration provides."""
    if version not in PROMPT_VERSIONS:
        raise ValueError(f"Unknown prompt version {version!r}")
    if not tools:
        return "\n\n".join([_ROLE, "Answer from your own knowledge; you have no tools.", _ANSWER])

    steps = [
        "Work out the company, the fiscal period and exactly what is asked. Companies label "
        "fiscal years their own way (a retailer's fiscal 2022 can end in January 2023); use the "
        "company's own labels."
    ]
    if version == "v2" and {"search_filings", "read_filing_page", "list_filings"} & set(tools):
        xbrl = " or with xbrl_get_facts" if "xbrl_get_facts" in tools else ""
        steps.append(_LABEL_CHECK.format(xbrl=xbrl))
    if "xbrl_get_facts" in tools:
        steps.append(
            "For standard line items in 10-K and 10-Q financial statements (revenue, net "
            "income, capital expenditure, total assets and the like), xbrl_find_concepts and "
            "xbrl_get_facts return the exact reported values with their periods; use the value "
            "as first reported for the period asked about. Segment details, non-GAAP measures, "
            "earnings releases, qualitative questions and questions about a specific statement "
            "need the filing text."
        )
    if "search_filings" in tools:
        steps.append(
            "To find text and tables, call search_filings filtered to the company and fiscal "
            "year (or to one filing from list_filings), using the vocabulary filings use. Read "
            "a page with read_filing_page before relying on a number from it: check the units "
            "line (thousands or millions) and which column holds which period."
        )
    if "calculate" in tools:
        steps.append("Do every computation with calculate, naming each input, even simple ones.")
    steps.append(
        "If the question defines a metric or a rounding rule, follow it exactly; otherwise use "
        "the standard definition and say which one you used. Answer in the unit and scale the "
        "question asks for."
    )
    workflow = "How to work:\n" + "\n".join(f"- {step}" for step in steps)
    return "\n\n".join([_ROLE, workflow, _ANSWER])
