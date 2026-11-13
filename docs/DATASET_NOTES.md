# FinanceBench: dataset notes

- **Source:** [patronus-ai/financebench](https://github.com/patronus-ai/financebench), pinned at commit `cc39aeb` (2024-12-03).
- **Integrity:** every file is verified against its git blob SHA-1.
- **License:** CC-BY-NC-4.0. Islam et al., *FinanceBench: A New Benchmark for Financial Question Answering* (2023), arXiv:2311.11944.
- **Regenerate the counts:** `uv run fa data stats` prints everything in this section.

## Composition of the open-source sample

| Item | Value |
|---|---|
| Questions | 150 (50 `metrics-generated`, 50 `domain-relevant`, 50 `novel-generated`) |
| Filings referenced | 84 filings from 32 companies |
| Filing type, by question | 10-K 112 · 10-Q 15 · earnings release 14 · 8-K 9 |
| Fiscal period, by question | 2015–2024; 2022 (61) and 2023 (35) dominate |
| Full corpus | 360 filings from 40 companies in the metadata; 368 PDFs in the repository |

## Gold evidence

- **189 evidence items.** 115 questions have one passage; 35 have two or three, always on different pages.
- **Same filing.** Every evidence passage comes from the question's own filing.
- **Zero-indexed pages.** `evidence_page_num` uses the same numbering as our parsed pages (page 0 is the first PDF page).

## Parsing and page alignment

- **Parsing.** `fa data parse` extracts 12,013 pages from the 84 question filings in about 5 minutes with 12 processes. 65 pages have no text layer (covers and image pages); no filing is more than 20% image-only.
- **Alignment.** `fa data verify` locates each evidence passage by weighted token containment, with multi-digit numbers counting 3×. **All 189 passages are on their labeled page, with no offsets.** Median containment on the labeled page is 1.00; the weakest match is 0.82.
- **Equivalent pages.** Six passages match another page equally well (repeated tables). Retrieval metrics should count those equivalent pages as hits, not only the labeled page.
- **Split numbers, fixed in the parser.**
  - *Cause:* two J&J earnings releases and one Nike 10-K page have broken font metrics, and pdfplumber splits their numbers with stray spaces: `$ 2 4,020` for `$24,020`, `7 ,919` for `7,919`.
  - *Before:* the J&J Q2 2023 statement-of-earnings page matched its evidence at only 0.60. These are the numbers a question about that page needs.
  - *Fix:* pages with this pattern (10 in total) are re-extracted with PDFium, which reads them correctly; each page records which extractor produced it.
  - *Remaining quirk:* PDFium places `$` signs after the figures on those pages. The numbers themselves are right.

## Gold answer shapes (they decide how each question is graded)

| Shape | Count | Grading |
|---|---|---|
| Bare number (`$1577.00`, `24.26`, `5.1%`) | 52 (all 50 metrics questions, plus 2) | Deterministic numeric rules |
| Text with figures | 46 | LLM judge, with key figures checked numerically |
| Yes/no plus rationale | 37 | LLM judge |
| Text only | 15 | LLM judge |

## Metadata quirks

- **A duplicated filing.** `FOOTLOCKER_2023_annualreport` is listed twice, with `doc_period` 2023 and 2022 and the same link. The loader keeps the first row (2023, matching the name). No question uses this filing.
- **PDFs without metadata.** Eight PDFs in the repository have no metadata entry:
  - `ADOBE_2022Q2_10Q`, `FOOTLOCKER_2022_10Q`, `BOSTONPROPERTIES_2015_10K`
  - `AMCOR_2022_8K_2022-04-26`, `AMCOR_2022_8K_2022-07-01`, `BLOCK_2022_8K_dated-2022-01-28`
  - `ULTABEAUTY_2023Q1_EARNINGS`, `ULTABEAUTY_2023Q2_EARNINGS`

  None of them is referenced by a question. In the all-filings setting their company, type and period have to come from the file name.
- **Two truncated PDFs.** `INTEL_2023_8K_dated-2023-02-10` and `INTEL_2023_8K_dated-2023-08-16` end inside their cross-reference tables, with no trailer and no end-of-file marker, so neither pdfminer nor PDFium can open them. Their bytes match upstream, so the damage is in the source repository. No question uses either; the all-filings index covers the remaining 358 filings. `fa data parse` records such files in `data/parsed/_failures.json`, and index builds skip only the filings listed there.
- **No encoding damage.** The question file has no text-encoding artifacts; garbled characters seen in some web viewers are a display issue.

## Errata: suspect or ambiguous gold labels

Recorded with their evidence. Headline scores use FinanceBench's original labels; an adjudicated score is reported alongside.

| Question | Gold | Issue |
|---|---|---|
| `ULTABEAUTY_2024Q2_EARNINGS` (document, not a question) | doc_period 2024 | The file is Ulta's *second quarter fiscal 2023* earnings release (August 24, 2023, net income $300.1M), not fiscal 2024's. No FinanceBench question uses it, but it misled the agent on the fresh hard tier: asked about Q2 fiscal 2024, the agent used this file. |
| `financebench_id_01328` (PepsiCo FY2022 restructuring costs, dev) | $411 million | Ambiguous question. It asks for restructuring costs "directly outlined in Pepsico's income statements" and says to answer 0 if they are not explicitly outlined. The income statement has no restructuring line; the gold's evidence is Note 3 (page 77), so 0 is a defensible literal answer. |

## Dev/test split

The split is in `configs/splits/financebench_v1.json` and was made with `fa data split` (seed 13). It is grouped by company, so no company appears on both sides. A seeded search over company sets then matched the overall mix of question types and filing types.

| Part | Questions | Companies | Domain / metrics / novel | 10-K / other filings |
|---|---|---|---|---|
| Dev | 50 | 10 | 17 / 16 / 17 | 37 / 13 |
| Test | 100 | 22 | 33 / 34 / 33 | 75 / 25 |

**Dev companies:** Amcor, American Express, American Water Works, Block, JPMorgan, Kraft Heinz, Lockheed Martin, PepsiCo, Verizon, Walmart.

## Caveats that affect every result

- **Small sample.** With 150 questions, a single run's 95% confidence interval is about ±8 points; paired comparisons and repeated runs narrow it.
- **Possible contamination.** The sample has been public since November 2023, so models may have seen it. The closed-book baseline measures how much can be answered from memory.
