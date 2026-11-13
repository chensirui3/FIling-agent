# Methodology

## Agent tools

The agent has seven tools: `resolve_company`, `list_filings`, `search_filings`, `read_filing_page`, `xbrl_find_concepts`, `xbrl_get_facts` and `calculate`. Each is defined once (`src/filing_agent/tools/`) as a name, a description, a Pydantic input model and a function. The agent loop and the MCP server use the same definitions, so the two cannot drift apart.

- **Strict schemas.** Each tool's JSON schema is generated from its input model for Claude's `strict: true` mode: closed objects, every field listed as required (optional ones are nullable), and no unsupported keywords. Pydantic re-validates every call, including the bounds strict schemas cannot express, and returns a precise message when an input is wrong.
- **Errors are classified.** Expected failures come back to the model as tool errors it can act on: an unknown company with suggestions, a page out of range, an invalid expression, uncached SEC data. Unexpected exceptions are marked `internal_error`. The error analysis counts those as harness bugs, never as model mistakes.
- **Outputs are capped** per tool (2–24k characters), with an explicit truncation note, so one call cannot flood the context.
- **The calculator takes named inputs.** An expression such as `(capex_2018 / revenue_2018) * 100` comes with `capex_2018=1577` and `revenue_2018=32765`. It is evaluated over a syntax-tree whitelist in exact Decimal arithmetic with half-up rounding; there is no `eval`. Because inputs are named, a trace shows which numbers went into which formula. That is how the error analysis separates a misread table from a wrong formula or a wrong calculation.
- **Scale is shown explicitly.** XBRL values come in whole units with a display string beside them (`1577000000 USD` reads as `1,577 million USD`), since scale mistakes are a classic failure.
- **Settings restrict what tools can see.** A context can hide XBRL entirely (text-only baselines) or limit search and reading to a given set of filings (the single-filing baseline). The tools have no path to the benchmark's question or answer files.

Run any tool by hand with `fa tools run <name> '<json input>'`; `fa tools list` shows all of them.

## Agent loop

`src/filing_agent/agent/loop.py` is a hand-written tool-use loop on the Anthropic SDK. Owning the loop makes every budget, trace field and failure class explicit.

- **Model settings.** Claude Opus 5.5 by default. Thinking is adaptive, depth is set by `output_config.effort` (default `medium`; an effort sweep comes before the baseline is frozen), and thinking summaries are requested so traces show why the agent did what it did. These models reject sampling parameters (temperature), so runs are not deterministic, and every comparison uses repeated runs.
- **Structured final answer.** The last message must be JSON matching a strict schema (`agent/answer.py`), enforced with `output_config.format` alongside the tools:
  - status: answered, or insufficient information
  - the answer in one to three sentences
  - the key figure, with explicit unit and scale
  - period, calculation, sources, confidence

  Numeric grading therefore never has to dig a number out of prose.
- **Append-only conversation.** Each assistant turn goes back unchanged, including its thinking blocks, so they stay valid and the cached prefix keeps growing. Top-level `cache_control` caches through the last block on every request.
- **Tool results.** All results for a turn return in one user message. Failures are flagged `is_error` for the model; unexpected exceptions are also marked internal.
- **Budgets.** At most 16 model turns, 40 tool calls and 5 minutes of wall-clock time per question. When the turn or tool budget runs out, the final request disables tools and asks for an answer from what was found. An agent still asking for tools at that point ends as `budget_exhausted`.
- **Every run ends with a status:** `answered`, `invalid_answer`, `truncated`, `refusal`, `budget_exhausted`, `timeout`, `api_error` or `model_mismatch`. The last three are infrastructure failures. They go to an errors sidecar and are never graded as wrong answers.
- **Served-model check.** Every response's `model` must equal the requested model, or the run fails with `model_mismatch`.
- **Refusal fallback.** Anthropic's server-side refusal fallback (`fallbacks="default"`) can answer with a different model. It is on for interactive `fa ask` and off in eval runs, so every scored answer comes from the model under test.
- **Trace.** Every model call (tokens, cache reads and writes, cost, latency, stop reason, served model), every tool call (input, output, error class, latency), and the full transcript. Cost comes from the recorded token counts at Anthropic's published rates, never from text-length estimates.

## Running experiments

An experiment is a YAML file in `configs/experiments/`: model, effort, tools, corpus scope, prompt context and budgets. A run records the config's hash, so two runs with the same hash ran the same system. A change to a prompt, tool set or budget is a new config, never an edit to an old run. The baseline ladder:

| Config | What the model gets | What it isolates |
|---|---|---|
| `b0_closed_book` | the question only | memorization (FinanceBench is public) |
| `b1_oracle_evidence` | the gold pages + calculator | reading and reasoning, with retrieval solved |
| `b2_single_filing` | text tools limited to the question's filing | everything but picking the filing |
| `b3_shared_text` | text tools over all 358 filings | the text-only agent |
| `b3r_classic_rag` | top-8 search hits in the prompt, one call | what the agent loop adds over one retrieval |
| `b4_full_agent` | text tools + XBRL + calculator | the main system |
| `b5_no_calculator` | `b4` without the calculator | what exact arithmetic is worth |
| `b6_long_context` | the whole filing in the prompt | the strongest simple baseline |

**Question sets.**
- A config's `dataset` field picks FinanceBench (the default) or the fresh set of post-cutoff filings ([FRESH.md](FRESH.md)).
- The fresh configs live in `configs/experiments/fresh/`. They copy `b0` and `b4` and change only the dataset and, for `b4`, the index (`contextual_fresh`: FinanceBench and fresh filings together).
- The field is left out of the hash when it is the default, so FinanceBench configs keep their earlier hashes.
- Question ids never collide, so `fa eval grade` and `fa eval report` work for runs on either set.
- Reports on the fresh set add a breakdown by kind: extraction, derived, text.

`fa eval run <config> --split dev|test|all --reps N` writes `results/runs/<run_id>/`:

- `config.json`: config, hash, question ids, repetitions, git commit, package versions, and a hash of the system prompt
- `results.jsonl`: one row per (question, repetition) with an outcome to grade
- `errors.jsonl`: infrastructure failures (API errors, timeouts, a different served model, harness exceptions), never graded
- `traces/`: full transcripts, kept local

Guarantees:

- **Resume.** Re-running with `--resume <run_id>` skips completed pairs and retries failures. It refuses to continue under a different config.
- **Cost cap.** The cap (`--max-cost`, default `FA_MAX_COST_USD`) stops scheduling new questions once recorded spend reaches it, counting spend from earlier attempts of the same run.
- **Isolated questions.** Every question gets a fresh agent and conversation. Only read-only resources are shared: the index, parsed pages, and the SEC cache, behind a lock.
- **Offline SEC data.** XBRL experiments refuse to start unless every company's SEC data is already cached.
- **Dry run.** `--dry-run` prints the plan and a rough cost range without calling anything.

## Grading

Each answer gets one label:

- **correct** or **incorrect**
- **refusal**: the agent said the information was not available
- **no_answer**: the run ended without a valid answer (cut off, out of budget, or not matching the answer schema). It counts against accuracy and is reported separately.

Infrastructure failures (API errors, timeouts, a different served model) are never graded.

**Bare-number golds (52 of 150) are graded by deterministic rules** (`eval/grading.py`).

- *Reading the gold.* The gold is parsed as a money amount, a percent or a plain number. Money takes its scale from the question ("(in USD millions)", "Answer in USD billions"), which covers all 19 money golds. `configs/gold_overrides.yaml` holds reviewed corrections, if any are needed.
- *Reading the prediction.* The predicted figure comes from the answer's structured value, unit and scale (the first number in the prose is a fallback). It is expressed in the gold's terms:
  - money is rescaled, so $1.577 billion matches a gold of $1,577 million; the FinanceBench authors also accept a different scale for an equivalent amount
  - 0.654 matches 65.4%
  - 1.37% matches a ratio of 0.0137
- *Tolerance.* **Correct if the prediction rounds to the gold at the precision it is written with (within half a unit of its last decimal), or is within 0.1% of it**, whichever is wider. The relative part absorbs drift from rounded intermediate inputs (24.27 against 24.26). The rounding part stops coarse golds from accepting real errors (0.88 against 0.8). Two variants are recorded as a sensitivity check: strict (exact after rounding) and lenient (within 1%).
- *Diagnostic flags* on wrong answers: near miss (within 5%), a factor-of-1,000 or -1,000,000 scale slip, and a flipped sign. They feed the error taxonomy (E3 unit/scale, E4 sign, A3 rounding).

**Grader check.** `fa eval grader-check` builds synthetic answers for every numeric gold and confirms each verdict:

| Synthetic answer | Expected verdict |
|---|---|
| The gold itself | correct |
| The same amount in another scale | correct |
| A percent written as a fraction | correct |
| The figure only in the prose | correct |
| A value that rounds to the gold | correct |
| A value beyond rounding | incorrect |
| A ×1,000 scale slip | incorrect |
| A flipped sign | incorrect |
| "Information is missing" | refusal |

All 425 checks pass. The check caught one flaw during development: a "one unit of the last decimal" tolerance let 10%-wrong answers pass on coarse golds such as 0.8 and 0.01, which is why the rule is "rounds to the gold".

**The other 98 golds** (prose, yes/no with reasons, text with figures) go to an LLM judge (`eval/judge.py`).

- *Model.* Claude Sonnet 5.5, deliberately a different model from the Opus 5.5 agent, to limit self-preference.
- *Rubric.* It mirrors the paper's human grading:
  - correct when the conclusion and key figures match the reference up to rounding, or an equivalent amount in another scale
  - incorrect when the conclusion differs, figures differ beyond rounding, the asked-for fact is missing, or the reasoning contradicts the reference
  - refusal when the response says it cannot answer
- *Inputs.* The judge sees the question, the reference answer and its justification, and the response as data. It is told not to reward length or tone, and its verdict comes back in a strict schema.
- *Figure cross-check.* The judge copies the key figures from the reference and the response. A deterministic check then compares them, allowing rounding at either figure's precision. When the check and the verdict disagree (the judge says correct but a reference figure has no match, or says incorrect although every figure and the conclusion match), the grade is flagged `needs_review` for a human.
- *Retries and caching.* A failed judgment stays pending, so it is retried on re-grade and never guessed. Verdicts are cached by judge version, model, question and answer, so re-grading costs nothing.
- *Calibration.* A person graded a blind, stratified sample of the judge's verdicts on the frozen system's runs. Results are in [RESULTS.md](RESULTS.md#judge-calibration-blind-human-labels).
  - `fa eval calibration-sheet --run <id> ...` draws 32 answers the judge called correct (the leniency check) and 8 it called incorrect. It shuffles them under neutral ids and writes a sheet with the judge's rubric but not its verdicts.
  - `fa eval calibration-score` reads the person's verdicts and reports agreement per stratum, weighted to how often the judge gives each verdict, plus Cohen's kappa.
  - The sheet quotes FinanceBench, so it stays out of git; the verdicts are saved in `results/calibration/labels.json`.

**Statistics** (`eval/stats.py`):

- Wilson 95% intervals for proportions. Run reports use them for single-repetition accuracy; they stay honest at 0% and 100%, where 0 of 114 gives 0–3.3%. With several repetitions, reports bootstrap over questions so each question's repetitions stay together.
- A paired bootstrap over questions for the change between two systems.
- An exact McNemar test on the questions that flipped.
- A noise-floor rule of thumb of ±1/√(questions × repetitions): about ±6 points for 100 test questions × 3 runs.

## SEC XBRL facts

The agent's XBRL tools read the SEC's `companyfacts` API: every XBRL fact a company has filed, each with a period (`start`/`end`), a value, and the filing it came from (`accn`, `form`, `filed`, `fy`, `fp`). Using these facts naively produces wrong answers in three predictable ways, and the tools are built to avoid each one.

**1. `fy`/`fp` describe the filing, not the fact.**

- *Problem:* a FY2019 10-K reports FY2017, FY2018 and FY2019 income-statement values, and all three carry `fy=2019`. Selecting "fy = 2018" therefore returns the wrong year's numbers.
- *Rule:* periods come from `start`/`end`. Durations of 340–380 days are annual, 80–100 days a quarter, 6- or 9-month spans year-to-date, and a fact with no start is a balance-sheet instant.

**2. Fiscal-year labels are company conventions.**

- *Problem:* Best Buy's "fiscal 2023" and Ulta's "fiscal 2022" both end on January 28, 2023. A calendar rule would mislabel one of them.
- *Rule:* a period's label is the `fy`/`fp` of the earliest filing whose own period ends on that date. A filing's own period end is the latest end date among its duration facts. So FY2018 is labelled by the FY2018 10-K, not by the comparative column of the FY2019 10-K.
- *Quarter four:* a three-month period reported in a 10-K is labelled Q4.
- *Inferred labels:* periods that only ever appear as comparatives (the first years of a company's XBRL history) get a label inferred from the year offset, flagged as `fiscal_label_inferred`.

**3. Values get restated.**

- *Problem:* the same period can carry different values in different filings.
- *Rule:* `first_reported` (the default) returns the value from the earliest periodic report of the period, which is what a question about that filing should match. `latest` returns the most recent value. Either way, `restated` flags the period and lists each distinct value with its filing. Facts from non-periodic forms (8-K, S-1) are ignored.

**Concept discovery.** The same line item goes by different names across companies and years, e.g. `Revenues` vs. `RevenueFromContractWithCustomerExcludingAssessedTax`. So `find_concepts` ranks a company's own concepts by word overlap with the query: CamelCase names split into words, plus labels and descriptions. The agent searches for concepts first instead of guessing tag names.

**Company identity.** `configs/companies.yaml` maps the 40 FinanceBench company names to explicit CIKs, plus tickers and aliases. Fuzzy matching against the SEC ticker file is unreliable here: names collide ("Corning"), and acquired companies such as Activision Blizzard drop out of the current ticker file. `fa sec verify-companies` checks every CIK against the SEC's own entity name and former names.

**Access policy and reproducibility.**

- *Fair access:* requests follow the SEC rules. Every request sends a User-Agent with a name and contact email (`SEC_USER_AGENT`), the client stays at 8 requests per second (the limit is 10), and transient errors are retried with jittered backoff that honors `Retry-After`.
- *Caching:* every response is cached on disk under a path that mirrors its URL.
- *Offline evals:* eval runs set `FA_SEC_OFFLINE=1`, so a cache miss is an error. Tool outputs are then identical from run to run, and no result depends on data fetched mid-run.
