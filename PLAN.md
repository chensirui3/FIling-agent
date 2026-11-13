# Filing Research Agent — Build Plan

**Status:** proposal, waiting for go-ahead · **Written:** 2026-11-08 · Facts below were checked on that date.

An agent that answers analyst questions about US public companies, such as *"What is 3M's FY2018 capital expenditure?"* or *"Does 3M have a healthy quick ratio in Q2 FY2023?"*. It works from three kinds of tools: the SEC's XBRL financial-data API, search and reading over filing text, and a calculator. It is graded on the 150-question open-source sample of FinanceBench.

The point of the project is the evaluation, more than the agent. That means component metrics, attributing each failure to a stage (retrieval, table reading, arithmetic), and a fix log with before/after scores and confidence intervals. The same tools are also exposed as an MCP server you can use from Claude Code or Claude Desktop.

---

## 0. Definition of done

1. **Reproducible pipeline.** One command per stage. A fresh clone with the saved runs regenerates every table and figure in the write-up.
2. **Headline result.** Test-set accuracy with a 95% CI, plus refusal rate, cost per question and latency. Shown next to the FinanceBench paper's reference numbers and against our own baselines, including "put the whole filing in context".
3. **Error analysis.** Every dev-set failure has a stage label and a sub-code. We report how well the automatic attribution agrees with human review.
4. **Fix log.** At least 3 fixes chosen from the error analysis. Each one has before/after accuracy on dev and test, a paired 95% CI, a McNemar p-value, a flip table (questions fixed vs. broken), and the change in its targeted error count. Fixes that didn't help are reported too.
5. **Trusted grader.** The LLM judge agrees with human labels at least 90% of the time on at least 50 cases. The grader passes its sanity checks: gold answers score about 100%, and empty or wrong answers fail.
6. **MCP server.** All tools are exposed and work in Claude Code. A parity test shows the MCP tools return the same outputs as the in-process tools.
7. **Engineering.** Tests and CI pass, and lint and type checks are clean.

---

## 1. Facts this plan is built on

### 1.1 FinanceBench (computed from the dataset files)

| Item | Value |
|---|---|
| Questions (open-source sample) | **150**: 50 `metrics-generated`, 50 `domain-relevant`, 50 `novel-generated` |
| Coverage | **32 companies, 84 filings** |
| Filing type, by question | 10-K 112 · 10-Q 15 · earnings release 14 · 8-K 9 |
| Fiscal periods | 2015–2024; 96 of 150 are 2022–2023 |
| Full corpus | 361 filings from 40 companies in the document metadata; **368 PDFs (705 MB)** in the repo; the 84 we need are 166 MB |
| Gold evidence | 189 evidence items: 1 page for 115 questions, 2–3 pages for 35. `evidence_page_num` is **zero-indexed**. Evidence is always in the question's own filing. |
| Gold answer shapes | 52 bare numbers (all 50 metrics questions are bare numbers), 46 text with figures, 37 yes/no plus rationale, 15 text only |
| Question fields | `financebench_id, company, doc_name, question_type, question_reasoning, domain_question_num, question, answer, justification, evidence[{evidence_text, evidence_page_num, evidence_text_full_page, doc_name}]` |
| Document fields | `doc_name, company, gics_sector, doc_type (10k/10q/8k/Earnings/10k_annualreport), doc_period, doc_link` |
| License | **CC-BY-NC-4.0**. Scripts download the data; it is never committed. Attribution goes in the README. |

**Paper reference numbers** (Islam et al. 2023, GPT-4-Turbo, human-graded on these 150 questions):

| Setting | Correct | Refused |
|---|---|---|
| Closed book | 9% | 88% |
| Shared vector store (all filings) | 19% | 68% |
| Single vector store (the right filing) | 50% | 39% |
| Long context | 79% | 4% |
| Oracle evidence pages | 85% | 0% |

Claude 2 scored 76% with long context. The paper's rubric treats very small rounding errors and scale differences (billions vs. millions) as correct. A correct conclusion whose figures contradict the evidence counts as incorrect. "Failed to answer" means the model explicitly said it lacked the information. The paper's retrieval was LangChain, Chroma and `text-embedding-ada-002`, with single-pass RAG and no agent.

### 1.2 SEC EDGAR APIs

- `data.sec.gov/submissions/CIK##########.json` returns filing history, tickers, former names and fiscal year end.
- `data.sec.gov/api/xbrl/companyfacts/CIK##########.json` returns every XBRL fact for a company. `companyconcept/...` returns one concept. `frames/us-gaap/<Concept>/USD/CY2019Q1I.json` returns a cross-company slice; period formats are `CY####`, `CY####Q#` and `CY####Q#I`.
- No authentication. **Maximum 10 requests per second.** Every request **must send a User-Agent with a name and contact email**. Crawling is not allowed.
- **XBRL pitfall:** a fact's `fy`/`fp` fields describe the *filing* it came from, not the fact's own period. A FY2019 10-K reports FY2017–FY2019 income values, and all three carry `fy=2019`. The period has to come from `start`/`end`.
- **Fiscal-label pitfall:** Best Buy's "fiscal 2023" and Ulta's "fiscal 2022" both end on Jan 28, 2023. We use each company's own label (from the filing's `dei` cover data) and never infer it from the calendar.
- Full-text search covers 2001 onward through the sec.gov/edgar/search UI. The JSON endpoint behind it is undocumented, so it is a stretch-goal MCP tool only.

### 1.3 Claude API (current models and constraints)

| Model | ID | Input / output per MTok | Cache read |
|---|---|---|---|
| Claude Opus 5.5 (default) | `claude-opus-5-5` | $4 / $20 | $0.20 |
| Claude Sonnet 5.5 | `claude-sonnet-5-5` | $2 / $10 | $0.20 |
| Claude Haiku 5.5 | `claude-haiku-5-5` | $0.10 / $0.50 (up to 100K prompt) | — |

Constraints on Opus 5.5 and Sonnet 5.5 that shape the design:

- **Thinking is always on** (adaptive). `effort` controls depth; Opus 5.5 defaults to `medium`, so we always set it explicitly.
- **Forced `tool_choice` (`any`/`tool`) returns a 400.** We use `auto`, `strict: true` tools and prompt instructions instead.
- **No `temperature` / `top_p`.** Current models return a 400 for them, so runs are not deterministic. **We need repeated runs and must report variance.**
- Thinking blocks have to be passed back unchanged, so the conversation history is append-only.
- Python SDK: `anthropic` 2.6.0. Prompt caching and the Message Batches API (50% off) are both available.

### 1.4 MCP Python SDK

`mcp` 2.3.0 (v2 API):

- Server: `from mcp.server import MCPServer`, with `@mcp.tool()` and `@mcp.resource()` decorators.
- `uv run mcp dev server.py` opens the MCP Inspector; `--transport streamable-http` serves over HTTP.
- Client: `from mcp import Client`.

### 1.5 Your machine

- Windows 11, system Python 3.14. We'll use a uv-managed **Python 3.12** to avoid wheel gaps; **uv is not installed yet**.
- git 2.46 and Node 24 (for the MCP Inspector); no Docker.
- **No NVIDIA GPU**, so embeddings run on CPU. 16 threads, 16 GB RAM, 242 GB free.
- The project path contains a space (`filing agent`), so MCP config paths must be quoted.

---

## 2. Architecture

```
FinanceBench question
        │
        ▼
┌──────────────────────────┐   tool calls    ┌───────────────────────────────────────────┐
│ Agent (Claude, manual    │ ──────────────► │ Tool layer (ONE implementation)           │
│ tool-use loop, budgets,  │ ◄────────────── │  resolve_company · list_filings           │
│ full trace capture)      │   results       │  search_filings · read_filing_page        │
└────────────┬─────────────┘                 │  xbrl_find_concepts · xbrl_get_facts      │
             │ submit_answer (structured)    │  calculate                                │
             ▼                               └───────┬──────────────────────┬────────────┘
┌──────────────────────────┐                         │                      │
│ Grader: numeric rules +  │               SEC data.sec.gov       Local filing index
│ LLM judge (validated)    │               (disk cache, ≤8 req/s,  (BM25 + embeddings over
└────────────┬─────────────┘                offline mode in evals)  361 FinanceBench filings)
             ▼                                       ▲
 Metrics + CIs ─► Error attribution ─► Fix log       │
                  (uses traces + gold evidence)      MCP server exposes the same tools
                                                     to Claude Code / Claude Desktop
```

---

## 3. Repository layout

```
filing agent/
├── README.md                  # front page: results, diagram, MCP quickstart, reproduction
├── PLAN.md                    # this file (milestone checklist kept up to date)
├── LICENSE                    # MIT (all dependencies below are permissive)
├── pyproject.toml / uv.lock   # console scripts: `fa` (CLI) and `filing-agent-mcp`
├── .python-version            # 3.12
├── .env.example               # ANTHROPIC_API_KEY, SEC_USER_AGENT, FA_AGENT_MODEL, FA_JUDGE_MODEL, FA_MAX_COST_USD
├── .github/workflows/ci.yml   # ruff, pyright, pytest (offline)
├── configs/
│   ├── models.yaml            # model IDs + price table (cost is computed from recorded tokens)
│   ├── splits/financebench_v1.json   # dev/test question IDs (committed; IDs only)
│   ├── b0_closed_book.yaml … b6_long_context.yaml    # baseline ladder (§7)
│   └── v0_agent.yaml, v1_<fix>.yaml, …               # main system; each fix inherits the last
├── src/filing_agent/
│   ├── config.py              # YAML → pydantic models; resolved-config hashing
│   ├── cli.py                 # `fa` (typer)
│   ├── data/      financebench.py · splits.py · verify.py
│   ├── sec/       http.py · companies.py · submissions.py · xbrl.py · fulltext.py (stretch)
│   ├── corpus/    parse.py · chunk.py · index.py · search.py · live.py (stretch)
│   ├── tools/     schemas.py · impl.py · calculator.py · registry.py
│   ├── agent/     prompts/ (versioned .md) · loop.py · answer.py · trace.py
│   ├── eval/      runner.py · normalize.py · grade_numeric.py · judge.py · grade.py · stats.py
│   │              retrieval_eval.py · xbrl_coverage.py · compare.py · report.py
│   ├── analysis/  taxonomy.py · attribute.py · labeling.py
│   └── mcp_server/server.py
├── tests/                     # offline by default; `-m live` for network/API tests
├── data/        (gitignored)  # raw/ pdfs/ parsed/ index/ cache/sec/
├── results/
│   ├── runs/<run_id>/         # config.yaml, results.jsonl, errors.jsonl, grades.jsonl, metrics.json, traces/ (gitignored)
│   └── labels/                # gold_review.csv, judge_calibration.csv, attribution_audit.csv
└── docs/        METHODOLOGY.md · RESULTS.md · ERROR_ANALYSIS.md · FIX_LOG.md · DATASET_NOTES.md · MCP.md · figures/
```

---

## 4. Data layer

| Step | What happens | Detail |
|---|---|---|
| 4.1 Download | Questions, document metadata and PDFs from the FinanceBench GitHub repo | Each PDF is checked against its git blob SHA from the GitHub API. Phase A is the 84 filings for the 150 questions (166 MB); phase B is all 361 for the shared-corpus setting. Downloads land in their own folder and are treated as untrusted. |
| 4.2 Parse | PDF → one row per page (zero-indexed, matching `evidence_page_num`) | `pdfplumber` layout-preserving text, plus per page: statement title, section heading, **units line** (e.g. "in millions, except per share data"), and tables as Markdown when detected confidently. Runs in parallel (spawn-safe on Windows). Output: `data/parsed/<doc>.jsonl`. |
| 4.3 Verify alignment | Is each gold evidence item on the page it's labeled with? | Fuzzy-match `evidence_text` against the parsed page (rapidfuzz). Count hits on the labeled page, within ±1 page, and elsewhere. Anything below 95% gets investigated before retrieval metrics are trusted. |
| 4.4 Company registry | 40 company names → CIK, tickers, fiscal year end, fiscal-label convention | From `company_tickers.json` and the submissions API, plus `overrides.yaml` for renamed or acquired companies (e.g. Activision Blizzard, acquired and delisted in 2023), since the current ticker file lists only active tickers. |
| 4.5 Gold audit | Check that the gold labels are sound | Programmatic checks over all 150 (duplicates, missing fields, answer-length outliers). A close read of 30 cases stratified by question type. I re-derive 10–15 gold answers from the filings myself. Suspect labels go into the errata in `docs/DATASET_NOTES.md`. |
| 4.6 Split | Dev ≈50, test ≈100 | **Grouped by company** (no company in both), stratified by question type, fixed seed. We check that baseline accuracy on the two halves agrees within noise. Only IDs are committed. |
| 4.7 Leakage guard | The agent must not be able to reach the answers | Tools can only read the SEC cache/API and the filing index; the question files are never indexed. **The agent gets no web search tool**, because FinanceBench answers are public. This is structural, not a prompt instruction. |

---

## 5. Tools (one implementation shared by the agent and the MCP server)

| Tool | Purpose | Key inputs → outputs |
|---|---|---|
| `resolve_company` | Name or ticker → identity | `query` → `{cik, name, tickers, fiscal_year_end, former_names}` |
| `list_filings` | Which filings exist for a company and period | `company, form_type?, fiscal_year?` → `[{doc_id, form, period_end, fiscal_label, filed, accession, pages}]` |
| `search_filings` | Find passages | `query, company?, doc_id?, form_type?, fiscal_year?, top_k` → `[{doc_id, page, score, header, text≤1.5k chars}]` |
| `read_filing_page` | Read a full statement page | `doc_id, page, neighbors=0` → full page text, units line, tables as Markdown |
| `xbrl_find_concepts` | Find the right XBRL tag (names vary, e.g. `Revenues` vs `RevenueFromContractWithCustomerExcludingAssessedTax`) | `company, query, top_k` → `[{concept, label, units, periods_available}]` |
| `xbrl_get_facts` | Get reported values | `company, concept, fiscal_year?, period=annual\|quarterly\|instant, quarter?` → facts with explicit `start/end`, duration, company fiscal label, form, accession, filed date. Values are deduplicated (as first reported) and restatements are flagged. |
| `calculate` | All arithmetic | `expression` + named `variables` (e.g. `capex/revenue*100` with `{capex: 1749, revenue: 34229}`) → `{result, substituted_expression}`. Uses an AST whitelist and Decimal; no `eval`. |
| `submit_answer` | Final answer (ends the loop) | `answer_status (answered\|insufficient_information), answer, value, unit, scale, period, calculation, sources[{doc_id, page} or {concept, accession}], confidence` |

Conventions:

- Pydantic input/output models generate strict-compatible JSON Schema, used both for Claude (`strict: true`) and for MCP.
- Outputs are capped, with an explicit "truncated" notice.
- Failures come back as `is_error` results with a message the model can act on.
- Every call is logged with its arguments, output and latency. Named calculator variables are what make "were the inputs right?" checkable later.

---

## 6. The agent

- **Loop:** a manual tool-use loop on the Anthropic Python SDK. We own the loop for full trace capture, budgets and per-call cost accounting, with no beta dependency.
- **Model:** `claude-opus-5-5` by default (see §15 and §17 for the cost trade-off).
  - Adaptive thinking.
  - `effort` set explicitly; low, medium and high are swept on dev before the baseline is frozen.
  - `tool_choice: auto`, strict tools.
  - Parallel tool calls are allowed, and their results go back together in one user message.
- **Prompt caching:** tools and the system prompt stay byte-stable and versioned, and the conversation prefix is cached. A test asserts `cache_read_input_tokens > 0`.
- **System prompt** (versioned):
  - Role.
  - Workflow: company → period → metric → source.
  - Source routing: XBRL for statement line items in 10-K/10-Q; filing text for segments, MD&A, non-GAAP figures, earnings releases and qualitative questions.
  - Reading: read the whole page, check the units line and the column headers.
  - Arithmetic: every computed figure goes through `calculate`; use formulas exactly as the question defines them; follow its rounding instructions.
  - Periods: use the company's own fiscal labels.
  - Answers: value, unit and period, or an explicit "insufficient information".
- **Budgets:** at most 20 model turns and 40 tool calls, a 5-minute wall-clock ceiling per question, and capped tool outputs. If the model stops without calling `submit_answer`, it gets one reminder. After that, a structured-output extraction runs on its last text and the answer is flagged `answer_extracted`.
- **Integrity checks:**
  - Every response's `model` must equal the requested model; a mismatch fails the attempt loudly.
  - A `refusal` stop reason is its own outcome.
  - **Anthropic's server-side refusal fallback is off in eval runs**, so every scored answer comes from the model under test. It is on for interactive `fa ask` and MCP use.
- **Retries:** SDK retries on 408/409/429/5xx with jittered backoff. Attempt counts are recorded and kept out of latency.
- **Trace per (question, rep):**
  - IDs: run, config hash and git SHA.
  - Requested vs. served model.
  - Every message, including thinking blocks.
  - Every tool call: arguments, output, latency and error.
  - Usage per call: input, output, cache read and cache write.
  - Cost, wall time, stop reason, status (`ok` / `truncated` / `refusal`) and the final structured answer.

---

## 7. Experiment configurations (the baseline ladder)

| ID | Setting | What the agent gets | Paper analog (GPT-4-Turbo) | What it tells us |
|---|---|---|---|---|
| B0 | Closed book | The question only | 9% | Memorization floor and contamination check (FinanceBench has been public since 2023) |
| B1 | Oracle evidence | Gold evidence pages in the prompt, plus calculator | 85% | Ceiling for reading and reasoning |
| B2 | Single filing | Told which filing; search and read inside it; calculator | 50% | Removes filing selection |
| B3 | Shared corpus, text only | All 361 filings; list, search, read, calculator | 19% | Text-only agent |
| B3r | Classic RAG | One hybrid retrieval → one answer call | 19% | Agent vs. single-shot RAG with the same model and index |
| **B4** | **Full agent** | **B3 plus XBRL tools** | — | **The main system** |
| B5 | Full agent, no calculator | B4 minus `calculate` | — | What the calculator is worth |
| B6 | Long context | The whole parsed filing in context | 79% | Strongest simple baseline, and the cost comparison |

**How to read the gaps:**

- B1 − B2 ≈ the cost of finding the right page.
- B2 − B3 ≈ the cost of finding the right filing.
- B4 − B3 = what XBRL adds; B4 − B5 = what the calculator adds.
- B4 vs. B6 asks whether the agent beats putting the whole filing in context, and at what cost.

The same ladder is re-run on the final system so the "loss waterfall" can be shown before and after the fixes. Paper numbers are 2023 GPT-4-Turbo with single-pass RAG: reference points, not a controlled comparison.

---

## 8. Grading

1. **Outcome labels.**
   - Each answer is `correct`, `incorrect` or `refusal`, as in the paper.
   - Infra failures (API errors after retries, timeouts, tool crashes) go to `errors.jsonl` and are **never scored as wrong**.
   - Answers cut off at `max_tokens` get status `truncated`.
2. **Numeric path (the 52 bare-number gold answers).**
   - The gold value is parsed from `$`, commas, parenthesized negatives, `%`, `x`, scale words, and the question's "in USD millions".
   - The prediction's `value` × `scale` is normalized the same way. Percent vs. fraction and billions vs. millions are reconciled.
   - **Correct if |pred − gold| ≤ max(1 unit in the gold's last decimal place, 0.1% of |gold|).**
   - Reported alongside: a strict variant (equal after rounding to the gold's precision) and a 1% lenient variant, as a sensitivity check. Near misses within 5% are logged as a diagnostic.
3. **Judge path (98 text and yes/no answers).**
   - An LLM judge returns structured output: `{verdict, conclusion_matches, gold_key_figures[], pred_key_figures[], contradicts_gold_evidence, rationale}`.
   - The judge only *extracts* key figures; comparing them uses the deterministic numeric rules.
   - The rubric follows the paper's. Inputs are the question, gold answer, gold justification and the prediction; the prediction is treated as data, and length earns no credit.
   - **The judge model is different from the agent model:** Sonnet 5.5 judges an Opus 5.5 agent, to limit self-preference. The judge prompt is versioned, and every judge input and output is stored.
4. **Grader validation, before any number is reported.**
   - Gold answers as predictions → about 100% correct.
   - Empty, "I don't know" or constant answers → 0% correct, with refusals labeled as refusals.
   - Perturbed answers (×1.1, ×1000, sign flip, flipped yes/no) → must fail.
   - **About 60 stratified predictions labeled by you without seeing the judge's verdict** → agreement and Cohen's κ, with a target of at least 90% agreement.
   - Re-grade 50 items to measure the judge's own flip rate.
5. **Suspect gold labels.** These are recorded with page evidence. The headline uses the original labels, and an "adjudicated" score is reported separately.

---

## 9. Statistics and experimental protocol

- **Noise floor.** A paired comparison has roughly ±1/√(n·R) noise. On test (100 questions × 3 reps) that's **about ±6 points**; on dev (50 × 2) it's about ±10. Dev is for direction and diagnosis; decisions rest on test and on low-noise component metrics.
- **Reported per config:**
  - Accuracy with a Wilson 95% CI, per rep and as the mean over reps.
  - Refusal rate, and incorrect-when-answered (hallucination) rate.
  - Breakdowns by question type, filing type and reasoning type, always with counts.
  - Cost per question, latency p50/p90, and tool calls per question.
- **Before/after comparisons** are paired by question:
  - A paired bootstrap CI (10k resamples over questions) on the change in accuracy.
  - An exact McNemar test on majority-vote correctness.
  - A flip table of fixed, broken and unchanged questions.
- **Reps.** 3 reps for main-system test runs, 2 for dev runs, and 1 for diagnostic baselines unless a conclusion depends on them.
- **Discipline.**
  - All analysis and tuning happens on dev. Test runs only for frozen versions, and every test run is logged; nothing is quietly re-run.
  - The headline is recomputed from per-question rows.
  - Results on all 150 questions are also given for comparison with the paper, labeled as including dev.
- **Component metrics.** These are deterministic, cheap and nearly noise-free:
  - Page-retrieval recall@1/5/10/20 and MRR.
  - "XBRL answerability" (can the gold figure for each metrics question be recovered from XBRL?).
  - Page-alignment rate.
  - Evidence-seen rate inside agent runs.

---

## 10. Error analysis

### 10.1 Taxonomy

| Stage | Code | Meaning | Automatic signal |
|---|---|---|---|
| Retrieval | R1 | Wrong company or filing | No tool call touched the gold filing or company-period |
| | R2 | Wrong period (fiscal label, quarter, or comparative column from another filing) | Numbers used belong to a different period |
| | R3 | Right filing, but the gold page was never retrieved or read | Gold page missing from every search result and page read |
| | R4 | XBRL miss: concept not found or wrong concept | XBRL path used, and the gold figure is absent from the returned facts |
| | R5 | Not reachable with these tools (untagged, parse failure) | Evidence text missing from the parsed page |
| Table reading | E1 | Wrong line item | Agent's input number is on the gold page, but in another row |
| | E2 | Wrong column or period within a table | Same row, other column |
| | E3 | Unit or scale error | Agent's value = gold × 10^±3 or 10^±6 |
| | E4 | Sign error | Agent's value = −gold |
| | E5 | Misread narrative text | Judge: conclusion contradicts evidence the agent read |
| Arithmetic and reasoning | A1 | Calculation error | Inputs right; final answer ≠ calculator result, or math done without the calculator |
| | A2 | Wrong formula, or ignored the question's definition | Inputs right; expression ≠ the required formula |
| | A3 | Rounding or precision | Within 5% but outside tolerance |
| | A4 | Wrong conclusion from the right numbers | Figures match; yes/no or judgment differs |
| Other | O1 | Refused although the evidence was found | `insufficient_information` with the evidence seen |
| | O2 | Answered a different question, or answered incompletely | Judge |
| | O3 | Harness or tool bug | Tool exception, parse error, truncation |
| Gold | G1 | Gold answer wrong or ambiguous | Human adjudication with page evidence |

### 10.2 Automatic attribution, in order

1. **O3 check.** Tool exceptions, truncation and parser errors.
2. **Evidence seen?** The gold page appears in any search result or page read, *or* the `evidence_text` fuzzy-matches any tool output, *or* the gold figures appear in returned XBRL facts. If not, the failure is R1–R5, with the sub-code chosen by how close the agent got: company, filing, then page.
3. **Inputs right?** Compare the calculator variables and stated figures with the numbers on the gold evidence pages, after scale normalization. If they don't match, the failure is E1–E4, with the sub-code from where the number sits on the page.
4. **Arithmetic and reasoning.** Calculator result vs. final answer, expression vs. the required formula, the tolerance band, and judge fields. This yields A1–A4 or O1/O2.

### 10.3 Review

- **My review.** I check every automatic label against the trace and correct it.
- **Your audit.** You audit about 30 randomly chosen labels without seeing mine, and we report the agreement.
- **Per-question view.** Every label links to its trace in a static HTML run viewer.

### 10.4 Outputs

- Failure counts by stage and code for each system version, as stacked bars.
- The loss waterfall from the ladder in §7.
- 2–3 annotated example traces per major code.
- Everything is collected in `docs/ERROR_ANALYSIS.md`.

---

## 11. Fix loop

**Protocol for each fix:**

1. **Hypothesis from the data.** For example: "40% of dev failures are E3 because chunks lose the units line."
2. **Change.** The smallest change that tests it, behind a config flag.
3. **Component metric.** Check the targeted metric first.
4. **Dev check.** Dev end-to-end with 2 reps: did the targeted failures get fixed, and did anything regress?
5. **Test run.** Freeze the version, then run test with 3 reps.
6. **Record:**
   - Accuracy before → after, with a paired CI.
   - McNemar p-value and the fixed/broken flips.
   - Targeted error count before → after.
   - Change in cost and latency.
7. **Keep or revert.** Keep the fix if it moves its target without a significant regression. Otherwise revert it and log the negative result.

Fixes stack, and each is measured against the previous version. At the end, a leave-one-out ablation of the final system checks that the gains add up.

**Candidate fixes.** These are hypotheses; the error analysis decides which ones we actually build.

| Fix | Targets | Change | Measured by |
|---|---|---|---|
| F1 Metadata-filtered hybrid retrieval | R1, R3 | BM25 plus embeddings with reciprocal-rank fusion; company, period and form filters | recall@5 on gold pages |
| F2 Contextual chunk headers | R3, E3 | Every chunk prefixed with company · filing · period · statement title · units line | recall@5, E3 count |
| F3 Table-faithful page reading | E1, E2 | Tables as Markdown with column headers; neighboring pages for tables that span pages; optionally Docling for pages the agent opens | B1 (oracle) accuracy, E1/E2 count |
| F4 Period-aware XBRL | R2, R4 | Concept discovery; period chosen by start/end/duration; company fiscal labels; restatement flag | XBRL answerability, R2/R4 count |
| F5 Calculator discipline | A1, A2 | Named-input calculator required for any computed figure; formula glossary for metrics the question doesn't define; recompute before submitting | A1/A2 count, B4 − B5 gap |
| F6 Answer normalization and refusal calibration | O1, A3 | Explicit units, scale and precision in the answer; clearer guidance on answering vs. declining | Refusal rate, near misses |
| F7 Cross-source check | R2, E1–E3 | When XBRL and filing text both have the figure, compare them and investigate disagreements | Flips on metrics questions |

**Fix log row (`docs/FIX_LOG.md`):**
fix · targets · dev before→after · test before→after · Δ test (95% CI) · McNemar p · fixed/broken · targeted errors before→after · Δ $/question · kept?

---

## 12. MCP server

- **Server.** `MCPServer("filing-research")` in `mcp_server/server.py`, run with `uv run filing-agent-mcp`.
- **Tools:**
  - The seven tools from §5, minus `submit_answer`.
  - `xbrl_frames` for cross-company comparisons.
  - Stretch: `fetch_filing` and `edgar_full_text_search`.
- **Resources and prompts.** A `filing://{doc_id}/page/{page}` resource, and an `analyst_question` prompt that carries the agent's workflow.
- **No drift.** Tools use the same functions, the same Pydantic models (structured output) and the same descriptions as the agent. `SEC_USER_AGENT` is passed through the environment.
- **Transports.** stdio by default, and streamable HTTP.
- **Setup docs:**
  - Claude Code: `claude mcp add filing-research -- uv run --directory "<path>" filing-agent-mcp`
  - Claude Desktop: a JSON config snippet, with the path quoted because it contains a space.
- **Tests:**
  - A stdio round trip with `mcp.Client`: list the tools and call each one on fixtures.
  - **Parity:** 10 dev questions run with MCP-backed tools must produce the same tool outputs as in-process tools.
- **Demo.** An Inspector screenshot and a short recording of Claude Code answering a question through the server.

---

## 13. Engineering

- **Stack:**
  - Python 3.12 (uv), `anthropic` 2.x, `mcp` 2.x.
  - `httpx`, `pydantic` 2, `typer`, `rich`.
  - `pdfplumber` (MIT), `bm25s`, `fastembed` (`BAAI/bge-small-en-v1.5`, local, CPU), `numpy`, `pyarrow`.
  - `rapidfuzz`, `scipy`/`statsmodels` (McNemar), `matplotlib`.
  - `pytest`, `ruff`, `pyright`.
- **Configs.** YAML → Pydantic. Each run saves its resolved config and hash, and run IDs are date-confighash-gitsha. The runner enforces a hard **cost cap per run** (`--max-cost`).
- **Runner:**
  - Async with a concurrency limit.
  - Resumable; it skips completed (question, rep) pairs.
  - Isolated state for each attempt.
  - A wall-clock ceiling per case.
  - `results.jsonl` and `errors.jsonl` kept separate.
- **Caching and determinism:**
  - SEC responses are cached on disk. Evals run in **offline mode**, where a cache miss is an error, so tool outputs are identical across runs.
  - Parsed pages, embeddings and judge results (keyed by judge version and inputs) are cached too.
  - Seeds are fixed, iteration is sorted, and prompts and tool lists are byte-stable.
- **Windows.** Spawn-safe multiprocessing, `pathlib` everywhere, UTF-8 mode, quoted paths.
- **Tests (offline by default; `-m live` for network/API tests):**
  - Calculator: correctness, Decimal rounding, rejecting unsafe input.
  - Number parsing: `$1,577.00`, `(1,234)`, `24.26%`, `$8.70 billion`, `0.96x`.
  - Grader: tolerance edges, scale and percent handling, sign; judge known-negatives using recorded responses.
  - XBRL: period selection, fiscal labels and restatements on recorded companyfacts fixtures; all 40 companies resolve to the expected CIK.
  - Retrieval: filters and fusion on a tiny fixture index.
  - Tool schemas: strict-compatible.
  - Agent loop, against a fake client: parallel results returned in one message, budget stop, missing-submit fallback, served-model mismatch fails.
  - Runner: resume, error routing, cost cap.
  - MCP: stdio round trip.
- **CI.** GitHub Actions runs ruff, pyright and offline pytest.
- **What's committed.** Code, configs, split IDs, run summaries, per-question grades and a few curated example traces. **Not committed:** PDFs, FinanceBench files, full traces or `.env`.

---

## 14. Reporting deliverables

- **README.md**
  - A one-paragraph pitch.
  - The headline table: test accuracy ± CI, refusal rate, $/question and latency for B0–B6 and the final system, next to the paper's reference numbers.
  - The architecture diagram.
  - A "where it fails" chart, before vs. after.
  - A fix-log summary, the MCP quickstart, reproduction steps, limitations, and license and attribution notes.
- **docs/**
  - `METHODOLOGY.md`: grading rules, tolerance, judge calibration, statistics, split, reps, contamination.
  - `RESULTS.md`: full tables and breakdowns.
  - `ERROR_ANALYSIS.md`.
  - `FIX_LOG.md`.
  - `DATASET_NOTES.md`: statistics and errata.
  - `MCP.md`.
- **Figures:**
  - Accuracy by version with CIs.
  - Failure stages by version.
  - The loss waterfall.
  - Accuracy vs. cost across configs and effort levels.
  - Retrieval recall@k curves.
- **Run viewer.** One static HTML page per run: question, gold, prediction, grade, attribution and the full trace.

---

## 15. Cost and runtime

These are estimates until a measured 10-question smoke run replaces them; I'll check with you before the first full paid run. Assumptions: about 8 model calls, ~90K input tokens (mostly cache reads) and ~6K output tokens per agent question.

| Agent model | Per question | Per 150-question run | Lean protocol (~1,500 agent question-runs) | Full protocol (~2,800) |
|---|---|---|---|---|
| Opus 5.5 | $0.15–0.40 | $25–60 | ~$250–600 | ~$450–1,100 |
| Sonnet 5.5 | $0.08–0.20 | $12–30 | ~$120–300 | ~$220–550 |
| Haiku 5.5 | ~$0.01 | ~$1–2 | ~$10–20 | ~$20–40 |

- **Lean vs. full.** Lean means 3 fixes, test reps limited to the main comparisons, and a final ladder of B1/B2/B4 only. Full means 4–5 fixes, 3 reps everywhere on test, and the complete final ladder.
- **B6 (long context)** costs about $0.60 per question on Opus 5.5 (roughly $60 for one test pass), so it runs once.
- **Judge, gold checks, closed-book and oracle runs** cost a few dollars in total.
- **Cost controls:** prompt caching, iteration on dev, a hard cost cap per run, and Haiku 5.5 for plumbing and smoke tests only (never for reported numbers).
- **Runtime:**
  - Indexing all 361 filings on CPU takes tens of minutes, once.
  - A 150-question agent run takes roughly 20–40 minutes at 8 concurrent questions, depending on your API rate limits.

---

## 16. Milestones (build order and exit criteria)

- [x] **M0 Scaffold.** Install uv; set up Python 3.12, pyproject, package skeleton, `fa` CLI, ruff/pyright/pytest, CI, `.env.example`, `git init`. *Exit:* `uv run fa --help` works and the tests pass.
- [ ] **M1 Data.** *(Done: download and verify, loaders, stats, split, parse, alignment check (189/189), dataset notes. Remaining: gold audit.)* Download and verify, parse, check page alignment, audit the gold labels, write the dataset notes, make the split. *Exit:* `fa data verify` finds at least 95% of evidence items on their labeled page; split committed.
- [ ] **M2 SEC and XBRL.** *(Done: client, cache, rate limit, registry, period-aware facts, concept search, filings, tests; cache warmed for 40 companies; all 40 CIKs verified. Remaining: XBRL-answerability report.)* HTTP client, cache and rate limit; registry for all 40 companies; concept search; period-aware facts; fixture tests. **XBRL-answerability report** for the 50 metrics questions. *Exit:* report showing how many are recoverable, and why the rest aren't.
- [x] **M3 Retrieval.** *(BM25 plain/contextual over 358 filings with filters; offline eval done. Dense/hybrid implemented but not run at corpus scale (CPU too slow); agent-facing search/read tools come with the agent.)* Chunking, BM25 plus embeddings, filters, the search and read tools. **Offline retrieval eval** (recall@k and MRR on gold pages, by filing type, with and without filters). *Exit:* retrieval report.
- [x] **M4 Agent.** *(Live smoke test: 5/5 dev questions correct at $0.04–0.06 each; three defects found and fixed.)* Calculator, strict tool registry, loop, prompts, traces, `fa ask`. *Exit:* 5 dev questions answered with readable traces; caching and the served-model check verified.
- [ ] **M5 Eval harness and grader.** *(Done: numeric grader, grader check (425/425), gold-review export, statistics, runner with resume/cost cap/errors sidecar, baseline-ladder configs, contexts, `fa eval run`. LLM judge with figure cross-check, `fa eval grade/report/compare`. Remaining: judge calibration export and scoring (human checkpoint), first live runs.)* Runner, numeric grader, judge, sanity checks, statistics, compare, reports and run viewer. **Checkpoint with you:** gold-value review and judge-calibration labels. *Exit:* eval health checklist passes; judge agreement ≥ 90%.
- [x] **M6 Baselines.** *(Dev done: effort frozen at medium; ladder b0–b5 on dev; closed book 80% means FinanceBench mostly measures memory for this model. Test split: frozen b4 (v1), all 100 questions: 85.0% strict (76.7–90.7%), 88.0% adjusted; 1 clear agent error, the rest unstated definitions, judgment, underspecified questions and 2 wrong golds. Not run: b6 long context (cost).)* Smoke run → measured cost → **your OK** → effort sweep on dev → freeze v0 → run the ladder. *Exit:* baseline table.
- [x] **M7 Error analysis.** *(docs/ERROR_ANALYSIS.md: all 21 failures of 339 graded questions classified; 2 agent errors, 0 retrieval/table/arithmetic.)* Automatic attribution, my review, your audit sample, taxonomy counts, examples, loss waterfall. *Exit:* `ERROR_ANALYSIS.md` v0.
- [x] **M8 Fix loop.** *(Fix 1 measured: prompt v2, which checks the period a document states. Rejected under a rule written beforehand: history 24→25/25, but dev 96%→90% (3 judgment flips, p = 0.25). Negative result recorded in RESULTS.md, $5.25.)* 3–5 fixes chosen from the data, each fully measured, then the final ablation. *Exit:* `FIX_LOG.md`, including negative results.
- [x] **M9 MCP server.** *(`fa mcp serve`, stdio and streamable HTTP, exact tool parity test, stdio smoke test, Claude Code and Desktop setup in the README.)* Server, stdio and HTTP transports, parity test, Inspector, Claude Code and Claude Desktop setup docs, demo. *Exit:* the tools work from Claude Code and the parity test passes.
- [x] **M10 Write-up.** *(README results and limitations, RESULTS, METHODOLOGY, FRESH, DATASET_NOTES, ERROR_ANALYSIS. Reduced scope for budget: one fix measured, no repetitions, no b6.)* README, results, methodology, figures, limitations. *Exit:* a clean clone regenerates every table from the saved runs.

---

## 17. What I need from you

1. **Go-ahead** on this plan, or what to change.
2. **Agent model and budget cap.** Opus 5.5 is the default (lean protocol ~$250–600); Sonnet 5.5 costs about half (~$120–300). I'll report the measured cost per question before any full run.
3. **SEC User-Agent:** a name and contact email for the header the SEC requires. I won't fill in your account email on my own.
4. **Anthropic credentials.** I'll check for an existing login or `ANTHROPIC_API_KEY` first. If there's neither, you add a key to `.env`.
5. **About 2 hours of your time at three checkpoints:**
   - Gold-value review: 52 rows, ~20 minutes.
   - Judge-calibration labels: ~60 rows, ~45 minutes.
   - Error-label audit: ~30 rows, ~30 minutes.
6. *(Optional)* A GitHub repo to push to.

**Defaults I'll use unless you object:**

- Python 3.12 via uv.
- `pdfplumber` for parsing, local `fastembed` embeddings, no extra API keys.
- The shared corpus is all 361 filings, and the dev/test split is grouped by company.
- Judge: Sonnet 5.5.
- No web search tool for the agent.
- Refusal fallback off in eval runs.
- MIT license.
- Data, PDFs and full traces stay out of git.

---

## 18. Risks and mitigations

| Risk | Mitigation |
|---|---|
| 150 questions → wide CIs | Paired tests, reps, low-noise component metrics, honest "not significant" reporting |
| Gold label errors | Errata with page evidence; original labels for the headline, adjudicated score alongside |
| Contamination (FinanceBench is public) | Closed-book baseline; no web tool; optional fresh benchmark on new filings (S2) |
| Overfitting fixes to dev | Company-grouped split; only general fixes (no company-specific rules); test only on frozen versions; all test runs logged |
| Unreliable judge | Numeric answers graded deterministically; judge ≠ agent model; human calibration; known-negative tests; flip-rate check |
| Run-to-run nondeterminism (no temperature) | Reps, variance reported, paired designs |
| Cost overrun | Measured smoke run, per-run cost cap, caching, iteration on dev |
| SEC rate limits or blocking | UA header, ≤8 req/s, disk cache, offline evals |
| Page-number misalignment | Fuzzy-match verification of all 189 evidence items before trusting retrieval metrics |
| Weak table extraction | Measured on oracle pages (B1); Docling as a targeted fix |
| Windows quirks | Spawn-safe multiprocessing, UTF-8 mode, quoted paths, `pathlib` |

---

## 19. Stretch goals

- **S1 Live EDGAR mode.** `fetch_filing` downloads any 10-K, 10-Q or 8-K from EDGAR, parses the HTML and indexes it on the fly. Then the MCP server can answer FY2024–FY2026 questions, which is exactly the "FY2024 operating margin" demo.
- **S2 Fresh mini-benchmark.** *(In progress, promoted after closed book scored 80% on dev: 38 post-cutoff filings selected, pinned by SHA-256, parsed from EDGAR HTML with located XBRL facts, and indexed with FinanceBench. 114 questions: 76 numeric (extraction + derived) from located figures, 74 verified against the XBRL API, plus 38 text questions drafted by Claude ($0.83) under automatic checks and a first-pass review; company split fresh_v1 (dev 30 / test 84); review sheet written. Runs: closed book 0% (all refusals) vs full agent 100% (96.7–100%), $7.90. Confirms memorization on FinanceBench; the set is at ceiling, so a harder tier is needed for error analysis. Human review of the 20-item sample: all fine. Hard tier (75: breakdown/history/definition, fiscal period names): 71/75 = 94.7%, adjudicated 74/75; first real error analysis: 1 agent period error, 3 question ambiguities, $4.31.)* 20–30 questions on recent filings, with answers verified against XBRL and the filing pages, as a contamination-free check.
- **S3 Model × effort sweep.** Opus 5.5, Sonnet 5.5 and Haiku 5.5 across effort levels, plotted as an accuracy-vs-cost frontier.
- **S4 Finance-tuned embeddings.** E.g. Voyage `voyage-finance-2` as a retrieval experiment; needs a Voyage key.
- **S5 Second judge.** A second judge for disagreements, and the Batches API for grading at half the cost.
- **S6 Shareable results page.** An interactive results page, published privately as an artifact.
