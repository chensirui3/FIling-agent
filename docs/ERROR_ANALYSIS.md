# Error analysis

Every failure of the frozen system across four evaluations, read against its trace and the filing. That is 339 graded questions: FinanceBench dev and test, and the fresh benchmark's two tiers.

**The frozen system** is `b4`: Claude Opus 5.5, effort medium, prompt v1, with text search, page reading, SEC XBRL facts and a calculator.

**Each failure goes to the first stage that went wrong:**
- **Retrieval:** the right filing or page was never found.
- **Wrong source or period:** the agent used a document or column for a different period.
- **Table reading:** wrong units, row or column.
- **Arithmetic:** a calculation error.
- **Definition:** the question leaves a formula unstated, and the agent used a different standard one than the gold.
- **Judgment:** a qualitative conclusion that reasonable analysts could draw either way.
- **Ambiguous question:** no company or year, or a word or period label with two readings.
- **Wrong gold:** the gold contradicts the filing.
- **Grading error:** a mistake by the judge or the numeric grader.

## Counts

| Evaluation | Questions | Failures | Retrieval | Wrong source or period | Table reading | Arithmetic | Definition | Judgment | Ambiguous question | Wrong gold | Grading error |
|---|---|---|---|---|---|---|---|---|---|---|---|
| FinanceBench dev | 50 | 2 | 0 | 0 | 0 | 0 | 1 | 0 | 1 | 0 | 0 |
| FinanceBench test | 100 | 15 | 0 | 1 | 0 | 0 | 5 | 4 | 2 | 2 | 1 |
| Fresh, first tier | 114 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Fresh, hard tier | 75 | 4 | 0 | 1 | 0 | 0 | 1 | 0 | 2 | 0 | 0 |
| **All** | **339** | **21** | **0** | **2** | **0** | **0** | **7** | **4** | **5** | **2** | **1** |

- **Ambiguous questions.** Dev: PepsiCo's restructuring costs "directly outlined in the income statement" (the gold comes from a note). Test: two questions name no company or no year. Hard tier: two questions I generated, FedEx's fiscal label across its change of year end and Walmart's segment "revenue" (net sales or total revenues).
- **Wrong golds.** Both on test: Boeing's tax rates with reversed signs, and Pfizer "spinning off Upjohn" in 2023 when the spin-off was completed in 2020.

## What the counts say

- **No retrieval, table-reading or arithmetic failures.** These were the stages expected to fail.
  - Retrieval matters for the *pipeline*: single-shot RAG over all filings scored 56% on dev with 34% refusals, and the agent loop scored 96% on the same questions (+40 points, p < 0.001). Gold-page recall@8 for raw-question search is only about 15% ([RESULTS.md](RESULTS.md)).
  - The agent recovers by searching again with the filing's own vocabulary and filters.
  - The calculator removes arithmetic errors. Without it the agent scored 94% on dev against 96% (within noise), but no answer of the frozen system has an arithmetic mistake.
- **Two clear agent errors in 339, both picking the wrong source for a period.**
  - *Pfizer, test `00283`:* the remaining Upjohn spin-off cost, taken from older 10-Ks instead of the Q2 2023 10-Q.
  - *Ulta, hard tier:* fiscal 2023 figures from a FinanceBench file labeled fiscal 2024. The agent noticed the conflict and used the file anyway.
- **Most failures are question-side: 19 of 21.** Unstated definitions (working capital, quick ratio, inventory turnover, ROA, free cash flow, book value), judgment calls, ambiguous questions, two golds that contradict the filing, and one judge error. Each is listed with its evidence in [RESULTS.md](RESULTS.md).
- **The grading itself holds up.** In a blind check of 40 judge verdicts, the project owner agreed with all 32 "correct" ones (no leniency) and 3 of 7 "incorrect" ones. The disagreements were the AMD judge error and definition or interpretation cases like those above, where the judge applied the key's figure strictly ([RESULTS.md](RESULTS.md#judge-calibration-blind-human-labels)).
- **Memory, not research, explains FinanceBench's ceiling.**
  - Closed book scores 80% on FinanceBench dev but 0% on the fresh first tier.
  - The full agent scores 96% (dev), 85% (test), 100% (fresh first tier) and 95% (fresh hard tier).
  - The test split is harder mainly because it has more ambiguous and single-document questions.

## Fixes

| Change | Before → after | Decision |
|---|---|---|
| Live-test defects found before any scored run: strict-schema union limit, judge figure check, numeric regex | broken → working | adopted |
| Effort sweep: low / medium / high | 92% / 96% / 96% on dev | medium (cheapest at the top) |
| Prompt v2: check the period a document states (targets the Ulta error) | history questions 24/25 → 25/25; dev 96% → 90% (3 judgment flips, p = 0.25) | **rejected** by the rule written before the runs |

**Measuring fixes is the binding constraint.**
- Run-to-run variation on judgment questions is about three of 50 dev questions, as large as the effect of the fix under test.
- The targeted failure itself was a one-in-four slip.
- A credible fix measurement needs three or more repetitions per question on both sides: about $15 for dev and the hard tier together.

## What would most improve the scores

1. **Grade definitions, not formulas.** Accept any standard definition the answer states (e.g. the two quick-ratio formulas), as an analyst would. This would turn about seven "failures" into passes without changing the agent.
2. **Name the company and the period in every question.** Two test failures and both period ambiguities on the hard tier disappear.
3. **Agent: a hard period check on documents.** Compare the period a document states with its label in code, not in the prompt, and drop mismatches before the model sees them. This targets both clear errors without the variance a prompt change brings.
