# Fresh benchmark: filings after the training cutoff

**Why it exists.** On the FinanceBench dev split, the closed-book baseline (no tools, no filings) scores 80%, and 100% on the metrics questions ([RESULTS.md](RESULTS.md)). For this model, much of FinanceBench measures memory. Filings published after the agent model's training cutoff (June 2026 for Claude Opus 5.5) cannot have been seen, so questions about them measure research.

## Filings

- **Rule.** For each company, its latest 10-K or 10-Q (no amendments) filed from 2026-08-01 to 2026-11-12.
- **Result.** 38 of the 40 companies: 36 10-Qs and 2 10-Ks (Apple and Costco, fiscal 2026).
  - Activision Blizzard and Foot Locker have no 10-K or 10-Q in the window.
  - The filings cover periods ending from 2026-07-31 to 2026-10-02.
- **Pinned.** `configs/fresh/filings.json` records each filing's accession number, URL, size and the SHA-256 of its primary document (93 MB in total). `fa fresh fetch` refuses a document whose hash differs.
- **Names.** Fresh filings use FinanceBench's style and each company's FinanceBench file prefix: `WALMART_2027Q2_10Q`, `APPLE_2026_10K`.
  - Fiscal year and quarter come from the filing's cover facts (`dei:DocumentFiscalYearFocus`, `dei:DocumentFiscalPeriodFocus`), so they follow each company's own convention.
  - Walmart's quarter ended July 31, 2026 is Q2 of fiscal 2027. Ulta's quarter ended August 1, 2026 is Q2 of fiscal 2026.

### Fiscal-period quirks

| Filing | What the filing says | Handling |
|---|---|---|
| `ORACLE_2027Q1_10Q` | The cover facts say fiscal 2026. The text calls the quarter "the first quarter of fiscal 2027" 58 times and the prior-year quarter "fiscal 2026" 36 times. | The text wins, and the manifest's `fiscal_note` records it. A year overrides the cover only when the text names this quarter for that year *and* the year before (current quarter and its comparison). |
| `AMCOR_2027Q1_10Q` | Amcor moved its fiscal year end from June 30 to December 31, effective July 1, 2026. The quarter ended September 30, 2026 is the first quarter of a six-month transition period. The cover facts say fiscal 2027 Q1, and the text's only "first quarter of fiscal year 2026" is the comparison quarter. | The name follows the cover facts. Questions name periods by their end dates. |
| `FEDEX_2026Q3_10Q` | FedEx "changed its fiscal year end from May 31 to December 31". The cover facts tag the quarter ended September 30, 2026 as fiscal 2026 Q3, but under the old calendar that label means December 2025 to February 2026, and the filing never uses it for July–September. *Corrected after the hard-tier run:* this entry first said the label was right. | The name follows the cover facts. The first tier names periods by dates, so it is unaffected. In the hard tier the fiscal label is ambiguous; see the errata below. |

## Parsing EDGAR HTML

`corpus/html_parse.py` turns a filing's primary HTML document into the same per-page records the PDF parser produces. Search, page reading and chunk headers therefore work unchanged.

- **Pages.**
  - Pages follow the filing's own page breaks (CSS `page-break-before/after`, usually on an `<hr>`).
  - Fixed-layout filings, such as General Mills' 10-Q, have no CSS breaks. Each printed page is a positioned div of page height (792pt), and each such div becomes one page.
  - A document with neither is cut every ~700 words. No fresh filing needed this.
- **Text.**
  - Hidden blocks, such as the inline-XBRL header, are dropped.
  - Inline pieces join the way a browser renders them. Oracle splits words across spans ("CONSOLIDA" + "TED").
- **Tables.** Each row is one line, with cells joined by " | ". The "$", ")" and "%" cells are reattached to their numbers: `Net sales | $186,100 | $175,750`.
- **Page tags.**
  - The units line is looked up cell by cell.
  - Statement titles get two forms the PDF tagger lacks:
    - inverted titles that fill the line ("INCOME STATEMENTS", Microsoft)
    - a registrant prefix ("Johnson & Johnson and subsidiaries consolidated statements of earnings")
  - The PDF tagger itself is unchanged, so the FinanceBench parses reproduce.
- **Located facts.** `parse_html` also returns each tagged figure (`ix:nonFraction`) with its concept, period, dimensions, scale and sign, and the page it is printed on. Questions built from these facts get exact gold pages.

| Fresh corpus | Value |
|---|---|
| Filings | 38 (36 10-Q, 2 10-K) |
| Pages | 2,329 (median 58 per filing; range 31–205) |
| Pages tagged as a primary statement | 247 (at least 4 per filing) |
| Tagged figures located on a page | 55,659 (21,220 without dimensions, i.e. whole-company figures) |

## Questions

The set has **114 questions, three per filing**: one extraction, one derived and one text question. `fa fresh questions` assembles it from cached data only. It builds the numeric questions from the tagged figures, adds the saved text drafts, re-checks them, and applies the review verdicts.

### Numeric questions (76)

- **Extraction (38 questions, 23 different line items).** Each asks for one line item for one period, under a standard name such as "total revenue", "capital expenditures" or "total current liabilities". A figure is used only when:
  - its printed row label agrees with the name ("Net sales" for revenue; "Payments for property and equipment" for capex)
  - its value is positive
  - its concept and period have exactly one printed value
- **Derived (38 questions, 13 different formulas).** These cover margins, revenue growth, effective tax rate, current ratio, liabilities to assets, working capital, free cash flow, capex as a share of operating cash flow, and cash returned to shareholders.
  - The formula is stated in the company's own row labels (`"Operating income" divided by "Total revenues"`), so the gold has one meaning.
  - Every component must be positive. Otherwise a label like "Net cash used in operating activities" (printed 527, tagged -527) reads two ways.

**Spread.**
- Filing *i* takes its extraction question from statement *i* mod 3 (income, balance, cash flow) and its derived question from the next statement.
- Within a statement, the least-used metric so far is picked; ties are broken by a seeded generator (seed 7).
- About a third of 10-Q income-statement questions ask for the year-to-date column instead of the quarter.

| Kind | Income statement | Balance sheet | Cash flow statement | Total |
|---|---|---|---|---|
| Extraction | 14 | 13 | 11 | 38 |
| Derived | 14 | 13 | 11 | 38 |

Periods: 19 quarters, 27 year-to-date, 4 fiscal years, 26 balance-sheet dates.

**Wording.**
- Periods are named by their end dates, in the filing's own terms: "three months ended July 31, 2026", "fiscal year ended September 26, 2026". For PepsiCo's 12-week quarters it is "36 weeks ended September 5, 2026".
- Units are stated: "Answer in USD millions", "USD thousands" where the filing reports in thousands, "USD per share".
- Percents are rounded to one decimal place and ratios to two. A value on a rounding boundary (24.45%) gets one more decimal.

**Golds and evidence.**
- Golds use the bare-number format the grader already reads (`$186100`, `15.0%`, `1.25`), so numeric grading is unchanged.
- The evidence is the printed row or rows plus the full page text.
- `meta` records each component's concept, period, value and printed row.

**Verification.**
- Each component figure was compared with the SEC XBRL API (companyfacts) for the same accession number, concept, unit and period. A disagreement would drop the question; none occurred.
- 74 of the 76 questions match. PG&E's two are "not in API": the API had not processed its 10-Q (filed October 22) when checked on November 12. In runs, the agent therefore has to find PG&E's figures through text search.

### Text questions (38)

`fa fresh draft-text` drafts them once with Claude and saves the drafts to `configs/fresh/text_drafts.jsonl`. The set then rebuilds without new API calls.

- **Page.**
  - Each filing contributes one page, drawn (seeded) from its three most explanatory pages: the non-statement pages that most often say why a figure moved ("primarily due to", "driven by", "partially offset by").
  - This finds the MD&A results discussion even in filings whose "Item 2" heading can't be located (Amazon, Intel, JPMorgan, Microsoft, PayPal) or that put MD&A before the statements (American Express, JPMorgan).
- **Draft.**
  - Claude Opus 5.5 (effort low, prompt `fresh-text/1`) reads only that page and proposes two analyst questions about the drivers or components behind a change or an amount.
  - Each candidate comes with a short answer, a verbatim supporting quote and its key figures.
  - Cost: $0.83 for all 38 filings.
- **Automatic checks**, re-run every time the set is built. A candidate is used only if:
  - its quote appears word for word on the page (ignoring typographic quotes and dashes)
  - its key figures are in the quote
  - every number in its answer is on the page
  - the question names the company and the period's end date
  - the question doesn't give away a key figure

  The first passing candidate is used. 35 filings use their first candidate. AES, Oracle and PG&E use their second, because the first listed figures that were not in its quote.
- **First-pass review.** I (Claude) read all 38 chosen questions against their pages. Changes are recorded in `configs/fresh/review.yaml`:
  - **J&J:** the question said "the period ended September 27, 2026", which could mean the quarter or the nine months. The narrative follows the third-quarter table (−19.2% operational change; the nine-month table shows −3.0%), so the question now names the fiscal third quarter.
  - **American Water Works:** candidate 1 blurred a two-step rate request with its first step, so candidate 2 (Missouri rate case) is used instead.
  - **Verified as drafted:** four more entries record checks with no change. Verizon's and JPMorgan's periods match their pages; the extra detail in AmEx's answer and the figures in Apple's are on their pages.
- **Grading.** Text answers go to the LLM judge, with figures cross-checked, like FinanceBench's free-text answers.
- **Caveat.** The same model family drafts these questions and answers them as the agent. The wording may suit Claude better than other models. The numeric questions are template-based and avoid this, and the review sample is checked by a person.

## Split

`configs/splits/fresh_v1.json` keeps FinanceBench's company partition:
- **Dev:** the 10 dev companies of `financebench_v1` (30 questions).
- **Test:** the other 28 companies (84 questions).

Work tuned on dev never sees a test company in either benchmark.

## Review

`results/fresh/review_sheet.md` holds a seeded sample of 10 numeric and 10 text questions, at most one per company in each half. Each item shows the gold with its evidence and a link to the filing, so a person can check it against the source.

**Human review.** The project owner checked all 20 sampled items against the filings and found no problems.

Verdicts go into `configs/fresh/review.yaml`:
- `reject` drops a question
- `fix` replaces its wording or gold
- `candidate: 2` switches to another draft

## Hard tier

The first tier turned out too easy: the full agent answered all 114 (see Results). Every question pinned one filing by its end date, derived questions stated their formula, and all figures were whole-company totals. `fa fresh hard-questions` builds a second tier of 75 questions (two per filing, of different kinds) that removes those supports while keeping exact golds.

- **Fiscal period names.** Every question names its period the way the company does. Walmart's quarter ended July 31, 2026 is "the second quarter of fiscal 2027"; Ulta's quarter ended August 1, 2026 is "the second quarter of fiscal 2026"; Oracle's quarter ended August 31, 2026 is "the first quarter of fiscal 2027". Amcor's filing sits in a fiscal-year transition, so its periods keep their dates.
- **breakdown (25 questions).** Revenue or operating income of one segment, product line or region: "What revenue did Walmart's Walmart U.S. segment report in the second quarter of fiscal 2027?"
  - These facts carry an XBRL dimension, so the SEC XBRL API does not have them; most are printed only in a note table.
  - The name in the question is the one printed on the page ("Walmart U.S.", "Innovative Medicine", "the Americas"). Totals, eliminations and "other" members are left out.
- **history (25).** The change in total revenue or net income against the same period two years earlier: "By what percentage did Intel's total revenue change from the third quarter of fiscal 2024 to the third quarter of fiscal 2026?"
  - That period's filing is not in the corpus; its figure comes from the SEC XBRL API.
  - The gold uses the value first reported, and only when every later report of that period agrees. A restated period is never asked about.
  - The earlier period keeps a fiscal name only when its own filing labels it that way; otherwise it gets its dates.
  - EPS is left out: a stock split between the periods (Netflix's 10-for-1) would make the change meaningless, and nothing in the reported values marks it.
- **definition (25).** A standard ratio with no formula given: gross, operating and net margin, effective tax rate, current ratio, free cash flow. Each is asked only where the statements allow one reading:
  - margins: a single revenue line (Walmart's net sales and total revenues would give two answers)
  - net margin: no noncontrolling interest separating consolidated from attributable net income
  - free cash flow: a single capital-spending line (is capitalized software capex?)
- **Verification.** Current figures are checked against the SEC XBRL API as before: all 49 that the API reports match. Breakdown figures are dimensional and come from the printed page. PG&E's current figures are not in the API yet.
- **Split.** The same company partition: `fresh_hard_v1`, dev 20 and test 55.

### Hard tier results and errata

Results: 71/75 = 94.7% (87.1–97.9%) for the full agent. Three of the four failures come from ambiguous questions, so the adjudicated score is 74/75 = 98.7%. Details are in [RESULTS.md](RESULTS.md).

| Question | Issue | Fix for the next version |
|---|---|---|
| `fresh_hard_FEDEX_2026Q3_10Q_definition_net_margin` | "Third quarter of fiscal 2026" is ambiguous across FedEx's change of fiscal year. | Use dates when the XBRL API shows the same fiscal label for a different period. |
| `fresh_hard_PEPSICO_2026Q3_10Q_definition_free_cash_flow` | PepsiCo publishes its own free cash flow definition, which adds back proceeds from sales of property ($89M). | Skip free cash flow when the filing defines it. |
| `fresh_hard_WALMART_2027Q2_10Q_breakdown_segment_revenue` | "Revenue" can mean net sales ($25,713M, the gold) or total revenues including membership income ($26,367M). | Name the measure as printed ("net sales"). |

## Search index

`contextual_fresh` holds the 358 readable FinanceBench filings plus the 38 fresh ones: 396 filings and 187,317 chunks. The agent has to find the fresh filing among the whole corpus.

## Commands

```bash
uv run fa fresh select --filed-after 2026-08-01 --as-of 2026-11-12   # pick and pin (live: filing lists and documents)
uv run fa fresh fetch                                                # download or read from cache, verify hashes, parse
uv run fa index build --name contextual_fresh --fresh               # FinanceBench + fresh filings
uv run fa fresh draft-text                                           # Claude drafts text questions (~$0.85; saved)
uv run fa fresh questions                                            # build the set, split, review sheet (offline)
uv run fa fresh hard-questions                                       # the hard tier: 75 questions (offline)
```

With the committed manifest, `fa fresh fetch` alone rebuilds the parsed corpus, and `fa fresh questions` regenerates the same questions from the cache.

## Results

With one run of each baseline on all 114 questions:
- **Closed book:** 0% (0–3.3%), refusing every question.
- **Full agent:** 100% (96.7–100%), at $0.056 per question.

The closed-book result confirms that FinanceBench's 80% closed-book score was memory. The full-agent result shows its accuracy is research rather than recall. The set is too easy to find the agent's errors, though: every question pins one filing and states its formula. Details and next steps are in [RESULTS.md](RESULTS.md).
