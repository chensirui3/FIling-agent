# Results

## First live runs (smoke test, dev questions only)

The full agent (`b4_full_agent`: Claude Opus 5.5, effort medium, all seven tools) ran on five dev questions: PepsiCo's FY2021 capex through `fa ask`, plus four more through `fa eval run`, one or more of each question type.

| Question | Type | Result | Notes |
|---|---|---|---|
| PepsiCo FY2021 capex (in USD billions) | metrics | correct ($4.63B; gold "$4.60") | First tried the usual capex tag, got "not reported" with suggestions, switched to PepsiCo's `PaymentsToAcquireProductiveAssets`. |
| Walmart FY2018 days payable outstanding | metrics | correct (42.69) | Used the question's formula exactly, with Walmart's own fiscal labels (FY2018 = year ended Jan 31, 2018). |
| Block FY2019–FY2020 revenue growth | metrics | correct (101.5%) | |
| Verizon FY2022 quick ratio: healthy liquidity? | domain | correct (judge) | "No, about 0.54x"; also explained why the ratio still applies. |
| PepsiCo credit facility increase (8-K) | novel | correct (judge) | $400 million ($3.8B → $4.2B). |

**Measured cost and speed:** $0.04–0.06 per question ($0.054 average over the four-question run), 3.8 tool calls per question, about 11 seconds each with four running in parallel. Judging cost about $0.003 per prose answer. Prompt caching works: on the first question, 8,496 of 13,277 input tokens were cache reads. The pre-run planning estimate was $0.15–0.40 per question, so a full 150-question run costs about $8, not $25–60.

**Defects found by the live runs, all fixed before any scored run:**

- *Too many nullable parameters.* The first request was rejected (no charge) because strict schemas allow at most 16 union-typed parameters per request; ours had 17. Optional tool inputs and answer fields are now left out of `required` instead of being nullable, which leaves no union-typed parameters at all, and a test keeps every request under the limit.
- *Gold precision.* FinanceBench prints metrics with two decimals regardless of precision. "$4.60" is PepsiCo's $4,625 million rounded to 4.6, so treating the trailing zero as precision would have marked the correct $4.63 billion wrong. Tolerance now uses significant decimals.
- *Judge cross-check false alarm.* The judge listed a formula from the gold justification as a "figure". Judge `j2` asks for result figures only, and the cross-check ignores arithmetic expressions.

**An observation for the error analysis:** the capex answer says the cash flow statement "shows this as Capital spending", but the agent only queried XBRL and never opened the statement. The claim happens to be true, but it is not grounded in anything the agent read. Ungrounded source claims will be tracked.

## Dev ladder: effort sweep and baselines (50 dev questions, one run each)

The test split has not been run. With one run of 50 questions, a 95% interval is roughly ±8–14 points, so differences of a few points are noise; the paired tests below decide.

Intervals are Wilson 95% intervals, with bounds rounded outward. An earlier version of this page showed percentile-bootstrap intervals, which run too narrow near 100% (96% was shown as 90–100%).

**Effort sweep** (full agent, Claude Opus 5.5):

| Effort | Accuracy (95% CI) | $/answer | Tool calls | Latency |
|---|---|---|---|---|
| low | 92% (81–97%) | $0.047 | 2.5 | 9s |
| medium | 96% (86–99%) | $0.042 | 3.7 | 12s |
| high | 96% (86–99%) | $0.049 | 4.1 | 11s |

Medium versus high: identical on every question. Medium versus low: two questions apart, McNemar p = 0.5. **Frozen: effort medium**, the cheapest setting at the top accuracy.

**Baseline ladder** (effort medium):

| Config | What the model gets | Accuracy (95% CI) | Refused | $/answer |
|---|---|---|---|---|
| `b0_closed_book` | the question only | **80%** (67–89%) | 2% | $0.017 |
| `b3r_classic_rag` | top-8 pages from one search, one call | 56% (42–69%) | **34%** | $0.035 |
| `b3_shared_text` | text tools, all filings | 92% (81–97%) | 0% | $0.036 |
| `b2_single_filing` | text tools, the gold filing only | 92% (81–97%) | 0% | $0.053 |
| `b5_no_calculator` | full agent without the calculator | 94% (83–98%) | 0% | $0.052 |
| `b4_full_agent` | text tools + XBRL + calculator | **96%** (86–99%) | 0% | $0.042 |
| `b1_oracle_evidence` | the gold pages + calculator | 98% (89–100%) | 0% | $0.027 |

Paired comparisons:

| Change | Accuracy change (95% CI) | McNemar p |
|---|---|---|
| Closed book → full agent | +16 points (+6 to +26) | 0.008 |
| Classic RAG → full agent | +40 points (+26 to +54) | < 0.001 |
| Text-only agent → full agent (XBRL) | +4 (0 to +10) | 0.5 |
| No calculator → full agent | +2 (0 to +6) | 1.0 |
| Single filing → oracle pages | +6 (0 to +14) | 0.25 |

### The main finding so far: on this model, FinanceBench mostly measures memory

**Closed-book accuracy is 80%**, against 9% for GPT-4-Turbo in the FinanceBench paper.

- *Metrics questions:* Claude Opus 5.5 answers all 16 dev metrics questions correctly with no documents and no tools.
- *Other types:* 76% of domain questions and 65% of novel questions.
- *No leakage:* the closed-book traces contain only the question. On Walmart's FY2018 days payable outstanding, for example, the model recalled the 10-K's payables, inventories and cost of sales and computed the gold 42.69, noting the inputs were "recalled from memory of the FY2018 10-K, not checked against the filing".

FinanceBench and these companies' filings have been public for years, so the open-source sample can no longer separate research ability from recall for a current frontier model.

What still shows up clearly:

- **The agent beats memory.** Tools add 16 points over closed book (p = 0.008); the agent fixes recall errors on the qualitative questions.
- **Bad retrieval is worse than no retrieval.** Classic single-shot RAG scores 24 points *below* closed book, with 34% refusals: told to rely on its sources, the model declines when the retrieved pages are wrong. Raw-question search finds the gold page in the top 8 only about 15% of the time across all filings, as the retrieval eval below shows.
- **XBRL and the calculator are within noise on dev.** At this ceiling, 50 questions cannot show their value.

**The remaining failures** (b4, effort medium, 2 of 50) are judgment errors, not retrieval or arithmetic:

- *JPMorgan liquidation value per share.* The agent used book value per share ($82.31). The gold uses tangible book value per share ($66.56), which the 10-Q itself reports, and goodwill is worth nothing in a liquidation.
- *PepsiCo FY2022 restructuring costs.* The agent answered 0, because the income statement has no separate restructuring line and the question says to answer 0 in that case. The gold's $411 million comes from Note 3, so this question is ambiguous (see the errata in `DATASET_NOTES.md`).

At low effort two more appeared: leaving JPMorgan's loss-making Corporate segment out of "lowest net revenue", and using a stricter quick-ratio definition for Amcor.

**Spend** for the smoke test, the sweep and the dev ladder: $19.42 (agent $18.12, judge $1.26, `fa ask` $0.04).

## Fresh benchmark: filings published after the training cutoff (114 questions, one run each)

**The set** ([FRESH.md](FRESH.md)): 38 filings (36 10-Qs and two 10-Ks) filed from August to November 2026, with three questions each:
- *extraction:* one line item for one period
- *derived:* a stated formula over two figures
- *text:* the drivers behind a change, drafted by Claude from one page

**The system** is the frozen `b4`: Claude Opus 5.5, effort medium, prompt v1. Its index adds the 38 fresh filings to the 358 FinanceBench ones.

| Config | Accuracy (95% CI) | Refused | $/answer | Latency | Tool calls |
|---|---|---|---|---|---|
| `fresh_b0_closed_book` | **0%** (0–3.3%) | 100% | $0.010 | 3.0s | 0 |
| `fresh_b4_full_agent` | **100%** (96.7–100%) | 0% | $0.056 | 9.3s | 3.3 |

Closed book → full agent: +100 points. All 114 questions flip the same way (exact McNemar p ≈ 1e-34). Every group of the full agent scores 100%:
- by kind: extraction 38/38, derived 38/38, text 38/38
- by split: dev 30/30, test 84/84
- numeric answers: 76/76 even under strict rounding

What the two runs show:

- **Memory explains the FinanceBench closed-book score.** On filings it cannot have seen, the model refuses every question ("I have no access to that 10-Q") and never guesses a figure. Its 80% on FinanceBench dev came from recall.
- **The agent's accuracy is research, not recall.** On unseen filings it answers everything, mostly with the text tools:
  - `search_filings` and `list_filings`: 112 questions
  - `read_filing_page`: 86
  - the calculator: 54
  - `xbrl_get_facts`: only 2

  PG&E's 10-Q is not yet in the SEC's XBRL API; all three PG&E questions were answered from the text.
- **The grading is not lenient.** In spot checks, text answers match their golds on every figure and driver, usually adding exact figures the gold rounds. Numeric answers pass strict rounding.
- **This set is too easy to rank systems or to find errors.** Four design choices make it so:
  - Each question names the company and the period's end date, and each company has one 2026 filing, so picking the filing is trivial.
  - Derived questions state the formula in the filing's own labels.
  - Text questions ask about one distinctive passage that search finds; in 39 of 114 questions the search snippet alone was enough, without reading the page.
  - The questions are unambiguous by construction, which removes the failure modes seen on FinanceBench (definitions, ambiguous golds).

  Finding errors needs harder questions: ones that name periods in fiscal terms, leave definitions to the agent, ask for segment figures (dimensional XBRL facts are not in the API), or combine filings.

**Spend** for the fresh benchmark: $7.90 in all.
- runs: $7.51 (closed book $1.18, full agent $6.33)
- smoke tests: $0.19
- judge: $0.20

Drafting the text questions cost another $0.83. Project total: $28.15.

**Reproduce**

```bash
uv run fa eval run configs/experiments/fresh/b0_closed_book.yaml --split all
uv run fa eval run configs/experiments/fresh/b4_full_agent.yaml --split all
uv run fa eval grade <run_id> && uv run fa eval report <run_id>
uv run fa eval compare <closed_book_run_id> <full_agent_run_id>
```

## Fresh benchmark, hard tier (75 questions, one run)

The first tier was too easy, so the hard tier ([FRESH.md](FRESH.md#hard-tier)) removes its supports while keeping exact golds:
- periods are named in each company's fiscal terms
- *breakdown:* segment, product and region figures that the XBRL API does not have
- *history:* changes against two years earlier, a period that is only in the XBRL API
- *definition:* standard ratios with no formula given

Same frozen `b4` system and index.

| Kind | Accuracy | Tools the agent chose |
|---|---|---|
| breakdown | 24/25 | search 25, page reads 9; never XBRL (it cannot help) |
| history | 24/25 | **XBRL facts on 23 of 25**, calculator 25 |
| definition | 23/25 | search 25, calculator 25, page reads 15 |
| **All** | **71/75 = 94.7%** (87.1–97.9%) | $0.057 per question, 9.5s, 3.5 tool calls |

- **The agent adapts its tools to the question.** On the first tier it called `xbrl_get_facts` on 2 of 114 questions. Here it did so on 23 of 25 history questions, which is where the figure exists only in the API.
- **Dev 18/20, test 53/55.** Total spend $4.31.

**The four failures, read against the filings.** Three come from my questions, and only one is the agent's mistake:

| Question | Gold | Agent | Classification |
|---|---|---|---|
| FedEx net profit margin, "third quarter of fiscal 2026" | 1.3% (July–Sept 2026) | 4.4% (Dec 2025–Feb 2026) | **Ambiguous question (period).** FedEx "changed its fiscal year end from May 31 to December 31". Under its old calendar, fiscal 2026's third quarter is December to February, and the filing never calls July–September by that name. The label came from the filing's cover tags. The agent took the old-calendar reading and even gave the other (1.3%). |
| PepsiCo free cash flow, first nine months of fiscal 2026 | $5,768M (operating cash flow − capital spending) | $5,857M | **Ambiguous question (definition).** The agent used PepsiCo's own published definition, which adds back $89M of proceeds from selling property. Leaving the definition to the agent allows both. |
| Walmart Sam's Club U.S. segment "revenue", Q2 fiscal 2027 | $25,713M (net sales) | $26,367M | **Ambiguous question (measure).** Walmart separates net sales from total revenues, which include membership income; the agent gave the latter. The question should have said "net sales". |
| Ulta net income change from Q2 fiscal 2024 to Q2 fiscal 2026 | +11.7% | −6.0% | **Agent error (wrong period).** The agent took the earlier figure from FinanceBench's file `ULTABEAUTY_2024Q2_EARNINGS`, which is Ulta's *fiscal 2023* second-quarter release (dated August 24, 2023). It noticed that the release "itself calls" the period fiscal 2023, but used it anyway instead of the XBRL API. |

**Adjudicated:** counting the three answers the questions allowed, 74/75 = 98.7% (92.8–99.8%). The one real error is a period mistake, the same family as FinanceBench's remaining failures (judgment, not retrieval or arithmetic). There were no arithmetic errors, no table-reading errors and no retrieval misses.

**What this changes.**
- *The benchmark:* three generator rules to tighten.
  - Treat a fiscal label as ambiguous when the XBRL API shows the same fiscal year and quarter for a different period (FedEx), and use dates there.
  - Skip free cash flow when the filing publishes its own definition.
  - Name segment measures as the company prints them ("net sales").
- *The agent:* when a filing's text names a different period than its label, trust the text and fetch the asked period elsewhere (the XBRL API). This is a prompt change to measure before and after.
- *The corpus metadata:* `ULTABEAUTY_2024Q2_EARNINGS` is recorded in the dataset errata.

Project spend so far: $32.46.

## FinanceBench test split (frozen `b4`, prompt v1, one run)

The system is the one frozen on dev: Claude Opus 5.5, effort medium, prompt v1, all 358 filings, text tools, XBRL tools and the calculator. Prompt v2 was rejected (next section), so v1 stayed. The test questions had never been run.

**All 100 questions ran, in two parts.** The first part stopped at its $5.50 cost cap after 90 questions: test questions cost $0.063 each against $0.042 on dev, so a cap set from the dev cost was too low. The run was then resumed for the other ten, one question at a time under a new cap. Same config hash, same run directory.

| | Accuracy (95% CI) |
|---|---|
| **All 100 (strict)** | **85/100 = 85.0%** (76.7–90.7%) |
| Adjusted for wrong golds and a judge error | 88/100 = 88.0% (80.2–93.0%) |
| metrics-generated | 33/34 = 97.1% |
| domain-relevant | 27/33 = 81.8% |
| novel-generated | 25/33 = 75.8% |
| 10-K / 10-Q / 8-K / earnings questions | 67/75 / 6/10 / 5/5 / 7/10 |

- **Cost and speed:** agent $6.17 ($0.062 per answer), judge $0.29; about 10.5s and 3.7 tool calls per answer.
- **Disagreements:** the judge and the figure check disagreed on 7 answers. All 7 were read and are correct: the agent added extra figures that did not contradict the gold.

**Every failure, read against the filing** (15 strict failures):

| Category | Count | Questions |
|---|---|---|
| Agent error: wrong source | 1 | `00283` Pfizer's remaining Upjohn spin-off cost: the agent used FY2019–FY2022 10-K estimates, not the Q2 2023 10-Q the question is about |
| The gold uses a different formula, the agent a standard one | 5 | `00005` Corning and `00080` PayPal working capital: the gold uses operating items only or excludes cash, the agent current assets minus current liabilities. `00540` AES inventory turnover: ending vs. average inventory. `00807` 3M quick ratio: (current assets − inventory) vs. (cash + securities + receivables). `10420` AES return on assets: the agent used consolidated net loss including noncontrolling interests (−1.42%), and itself named the attributable figure (about −1.5%, which rounds to the gold's −0.02) |
| Judgment or interpretation | 4 | `00790` "Is CVS capital-intensive?": the gold reasons from ROA, the agent from capex and PP&E intensity. `00651` J&J EPS growth: reported (4.0%) vs. constant-currency (3.5%) guidance. `01902` Best Buy's best category "by top line": revenue size vs. growth. `01911` MGM "Adjusted EBIT": MGM reports no such measure; the agent used operating income, while the gold calls it negative |
| The question names no company or year | 2 | `00601` "What drove the reduction in SG&A…" (Ulta's file; the agent guessed Amcor). `02416` Pfizer acquisitions "in this 10-K" (the agent took the latest 10-K, the gold FY2021's). In FinanceBench's single-filing setting the file supplies these; across all filings it does not |
| The gold contradicts the filing | 2 | `00585` Boeing's effective tax rate: the agent gives the 10-K's own (0.6)% and 14.7%, while the gold reverses both signs. `02419` "Is Pfizer spinning off Upjohn as of Q2 2023?": the spin-off was completed in 2020, as the agent says |
| Judge error | 1 | `00995` AMD's products: the judge confirms the agent's segment figures match the filing, yet marks the answer wrong |

The adjusted score counts the last two rows as correct (three questions). Formula and judgment disagreements stay wrong, because the question allowed the gold's reading too.

**What the test run shows.**
- **One clear agent error in 100.** It picked the wrong document for a period-specific figure, the same family as the Ulta slip on the fresh hard tier.
- **The rest are question-side ambiguity.** Most failures are unstated definitions (working capital, quick ratio, inventory turnover) and judgment calls, as on dev. FinanceBench grades one formula per question, while analysts use several standard ones.
- **Metrics questions are near perfect again (33/34).** On dev, the closed-book baseline also got all of these right, so they mostly measure recall. The one miss is a definition choice.
- **Test is harder than dev:** 85% vs. 96%, driven by novel and domain questions, 10-Q filings and earnings releases. It has more questions that depend on a single document, and several with unstated definitions.

## Judge calibration (blind human labels)

The LLM judge grades every prose answer: 98 of 150 FinanceBench questions and the 38 fresh text questions. The project owner graded a blind sample of its verdicts on the frozen system's runs (FinanceBench dev and test, fresh first tier: 136 judge-graded answers).
- **The sample:** 32 answers the judge called correct and 8 it called incorrect, shuffled under neutral ids.
- **What the grader saw:** exactly what the judge saw (question, answer key, justification, response) and the judge's rubric, but never its verdict.

| | Agreement |
|---|---|
| Judge said **correct** (leniency check) | **32/32** (95% CI 89–100%) |
| Judge said incorrect (harshness check) | 3/7, plus 1 marked unsure |
| Overall, weighted to the judge's mix of verdicts (121 correct, 15 incorrect) | **93.7%** (raw 89.7% on the stratified sample) |
| Cohen's kappa | 0.55 |

**The judge is not lenient.** No answer it passed was failed by the person, so the reported accuracies are not inflated by grading. The planned bar of 90% agreement is met.

**Where they disagreed, the judge was the stricter one:**
- `00995` AMD's products: a plain judge error. Its own rationale says the figures "do match", but the verdict is incorrect.
- `00080` PayPal and `00005` Corning working capital: both answers say "yes", as the key does. The key's figure uses a narrower definition (operating items only, or cash excluded); the judge failed the different figure and the person accepted the standard formula.
- `01902` Best Buy's best category "by top line": revenue size versus growth.
- `00807` 3M's quick ratio (marked unsure): 0.85 versus the key's 0.96, from two standard formulas, with the same conclusion.

**What it means for the scores.**
- Each disagreement sits in the definition or judgment categories of the [error analysis](ERROR_ANALYSIS.md), plus the one judge error already counted there.
- If a person would pass about 4 in 7 of the judge's "incorrect" prose answers (95% CI 25–84%), FinanceBench test under human grading would be about 92% (88–96%) rather than the strict 85%. That extrapolation rests on only 7 items.
- The strict scores remain the headline; they are a conservative bound.

## Fix 1: check the period a document states (prompt v2)

**The failure it targets.** On the hard tier, the agent took Ulta's fiscal 2023 figure from a file labeled fiscal 2024. It noticed the release says fiscal 2023, but used the figure anyway.

**The change.** Prompt v2 adds one step to the workflow:
> A filing's name and listed fiscal year are labels, and labels can be wrong. Before using a figure, check the period the document itself states (its title, cover or column headings). If that is not the period asked about, do not use the figure; get the asked period from another filing or with xbrl_get_facts.

Nothing else changes: model, effort, tools, index and budgets are the same. The closed-book prompt is identical.

**Decision rule, written before the runs.** v2 replaces v1 only if both hold:
- **No regression on FinanceBench dev:** no dev question correct under v1 becomes wrong under v2.
- **The target is fixed:** on the 25 hard-tier history questions, v2 is at least as accurate as v1, and the Ulta question is correct in most repetitions.

With 25 and 50 questions, no plausible change can reach statistical significance. The rule only guards against adopting a change that breaks something.

**Results.** Prompt v1 is the frozen system; v2 differs only by the added step.

| Measurement | v1 | v2 |
|---|---|---|
| Hard-tier history questions | 24/25 | **25/25** |
| The Ulta question, repeated | 3/4 (original failure plus 3 reruns, all correct) | 4/4 |
| FinanceBench dev (regression check) | **48/50 = 96%** | 45/50 = 90% |

- **Dev change:** −6 points (95% CI −14 to 0), McNemar p = 0.25. None fixed; three broke.

**Decision: v2 is rejected, and v1 stays frozen.** The rule allowed no regressions on dev, and three dev questions broke. None of the three involves the new step. All are judgment calls, and two of them (JPMorgan, Amcor) also failed in the low-effort run:
- **Verizon's FY2022 quick ratio:** the same 0.54, but a hedged "is liquidity healthy?" verdict.
- **JPMorgan's lowest-revenue segment:** the loss-making Corporate segment was left out.
- **Amcor's quick ratio:** v2 used the stricter definition (0.53 → 0.57) instead of the gold's (0.67 → 0.69).

The drop is therefore most likely run-to-run variation, but the rule was fixed in advance and is not reinterpreted afterwards.

**What this shows about measuring fixes.**
- Run-to-run variation on judgment questions (about three of 50 dev questions) is as large as the effect under test.
- The targeted failure itself was a one-in-four slip: v1 answered correctly in three reruns.
- Measuring a fix this small needs repeated runs (three or more per question) on both sides. That is the main constraint on further fix work.

**Cost:** $5.25 (dev under v2 $3.22 with judging; history questions $1.71; Ulta reruns $0.31).


## Retrieval (component eval, no LLM)

How often does search put a gold evidence page near the top when the raw question text is the query? This measures the search layer alone, before any agent writes better queries.

**Setup**

- **Corpus:** 358 FinanceBench filings (two truncated upstream PDFs are unreadable), split into 178,829 page-bounded chunks of up to 200 words with 30 words of overlap.
- **Ranking:** BM25, with results collapsed to one hit per page.
- **Index variants:** `plain` indexes chunk text only. `contextual` prefixes each chunk with company · filing type and period · page · statement · units line, e.g. `3M | 10-K 2018 | page 59 | cash flow statement | (Millions)`.
- **What counts as a hit:** a page in the question's own filing that holds one of its evidence passages (the labeled page, or an equally matching duplicate page).
- **Strictness:** the same figure on another page (MD&A, or the next year's comparative column) does not count, so these numbers understate what an agent can find.
- **Settings:** each one narrows the search space, so the gaps show the cost of each choice.

| Index | Setting | hit@1 | hit@5 | hit@10 | hit@20 | full@10 | MRR |
|---|---|---|---|---|---|---|---|
| contextual | all filings | 5% | 9% | 15% | 20% | 15% | 0.079 |
| contextual | same company | 10% | 21% | 33% | 42% | 27% | 0.158 |
| contextual | company + fiscal year | 17% | 41% | 53% | 65% | 44% | 0.279 |
| contextual | gold filing only | 21% | 45% | 59% | 71% | 49% | 0.324 |
| plain | all filings | 5% | 7% | 10% | 13% | 9% | 0.063 |
| plain | same company | 8% | 13% | 19% | 27% | 18% | 0.116 |
| plain | company + fiscal year | 13% | 29% | 42% | 56% | 37% | 0.211 |
| plain | gold filing only | 15% | 35% | 48% | 63% | 44% | 0.248 |

`hit@k` means some gold page is in the top k. `full@10` means every evidence item of the question is in the top 10 (35 questions span several pages). n = 150 per row. On a single 150-question row, the 95% interval is roughly ±8 points.

**Findings**

1. **Contextual headers help in every setting**, by 5 to 14 points at hit@10. The largest gain is within a company: 19% → 33%. There the header carries the fiscal year and filing type, which is what separates one company's filings from each other.
2. **Picking the right filing is the biggest loss.** At hit@10 with headers: 15% across all filings → 33% within the company → 53% with company and fiscal year → 59% within the gold filing. A company filter alone doubles recall, and adding the fiscal year adds another 20 points.
3. **Raw questions are weak queries even inside the right filing:** 59% hit@10 and 21% hit@1. By question type within the gold filing:

   | Question type | hit@10 |
   |---|---|
   | domain-relevant | 36% |
   | metrics-generated | 68% |
   | novel-generated | 72% |

   Domain-relevant questions do worst because their analyst wording ("Is 3M a capital-intensive business?") does not appear in filings.
4. **Long 10-Ks are the hardest filings** (50% hit@10 within the filing); short earnings releases are the easiest (100%).
5. **Dev and test agree within noise:** 52% vs. 62% hit@10 within the gold filing, at n = 50 vs. 100.

**What this means for the agent**

- Resolve the company and fiscal year before searching, and pass them as filters.
- Write queries in the filing's own vocabulary ("purchases of property, plant and equipment", not "capex").
- Go straight to a statement page when the question names the statement; pages are tagged by statement type.
- Use XBRL for standard line items instead of searching for them.

The agent's search tool uses the `contextual` index with these filters.

**Dense and hybrid search** are implemented and tested (`fa index build --dense`) but were not run at corpus scale. On this machine's CPU (Intel i5-1240P), bge-small embeds about 10 chunks per second, so one dense index would take about 5 hours. They remain a candidate fix, to be measured if the error analysis shows retrieval misses that BM25 plus the agent's own query writing do not fix. The options then are an overnight embedding run or an embedding API.

**Reproduce**

```bash
uv run fa index build --name plain --no-headers
uv run fa index build --name contextual
uv run fa eval retrieval --modes bm25
```

Per-question ranks and dev/test breakdowns are in `results/retrieval/financebench_v1.json`.
