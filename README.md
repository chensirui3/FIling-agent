# Filing Research Agent

An agent that answers analyst questions about US public companies, such as *"What is 3M's FY2018 capital expenditure?"* or *"Does 3M have a healthy quick ratio in Q2 FY2023?"*. It uses the SEC's XBRL financial-data API, search over filing text, and a calculator. It is graded on the open-source sample of [FinanceBench](https://github.com/patronus-ai/financebench).

The evaluation is the core of the project:

- component metrics (retrieval recall, XBRL coverage)
- attribution of each failure to retrieval, table reading or arithmetic
- before/after scores with confidence intervals for every fix

The tools are also exposed as an MCP server.

> **Status:** complete. Headline results are below; [docs/RESULTS.md](docs/RESULTS.md) has every run and [docs/ERROR_ANALYSIS.md](docs/ERROR_ANALYSIS.md) every failure.

## Results

The frozen system is Claude Opus 5.5 at medium effort, with filing search, page reading, SEC XBRL facts and a calculator. Each row is one run; 95% Wilson intervals are in parentheses.

| Evaluation | Closed book (no tools) | Full agent |
|---|---|---|
| FinanceBench dev (50 questions) | 80% | 96% (86–99%) |
| FinanceBench test (100) | not run | **85%** (76.7–90.7%); 88% after correcting 2 wrong golds and a judge error |
| Fresh benchmark: filings published after the model's training cutoff (114) | **0%** (all refusals) | 100% (96.7–100%) |
| Fresh hard tier: segment figures, two-year changes, ratios without formulas (75) | not run | 94.7% (87.1–97.9%); 98.7% counting the readings 3 ambiguous questions allowed |

- **FinanceBench is largely memorized.** With no documents, the model answers 80% of dev questions, but none about filings published after its training cutoff. The fresh benchmark ([docs/FRESH.md](docs/FRESH.md)) is built from 38 post-cutoff 10-Ks and 10-Qs, with golds taken from the filings' own XBRL tags and checked against the SEC API.
- **The agent's accuracy is research, not recall.** It scores 100% on the post-cutoff questions. It uses the XBRL API only when the figure exists nowhere else (23 of 25 two-year-change questions, against 2 of 114 in the first tier).
- **Almost every failure is on the question side.** Of 21 failures in 339 questions, 2 are agent errors (both used a source for the wrong period). The rest are unstated definitions, judgment calls, ambiguous questions, wrong golds and one judge error. There are no retrieval, table-reading or arithmetic failures.
- **The one measured fix was rejected.** A prompt change targeting the period error fixed it, but dev dropped 96% → 90% on three judgment questions (p = 0.25). The pre-registered rule rejected it, and the negative result is reported.

Total API spend for all development and evaluation: about $38.

## Setup

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12.

```bash
uv sync
cp .env.example .env   # then fill in SEC_USER_AGENT and your Claude API credentials
uv run fa --help
```

## Data

```bash
uv run fa data download        # questions, document metadata, and the 84 PDFs the 150 questions use
uv run fa data stats           # dataset summary
uv run fa data parse           # PDFs -> per-page text (plain and column-aligned), units and statement tags
uv run fa data verify          # checks each gold evidence passage is on its labeled page
uv run fa data split           # company-grouped dev/test split -> configs/splits/
```

See [docs/DATASET_NOTES.md](docs/DATASET_NOTES.md) for composition, quirks found in the source data, and the split.

## Search and retrieval eval

```bash
uv run fa data download --pdfs all && uv run fa data parse --docs all   # full 358-filing corpus
uv run fa index build --name contextual                                 # BM25 over page-bounded chunks
uv run fa index search "purchases of property, plant and equipment" --company 3M --year 2018
uv run fa eval retrieval --modes bm25                                   # gold-page recall, 4 settings
```

Results and what they imply for the agent are in [docs/RESULTS.md](docs/RESULTS.md).

## SEC XBRL data

Needs `SEC_USER_AGENT` (a name and contact email) in `.env`. Responses are cached under `data/cache/sec/`; set `FA_SEC_OFFLINE=1` to answer only from the cache.

```bash
uv run fa sec warm                                   # submissions + XBRL facts for all 40 companies
uv run fa sec verify-companies                       # every CIK matches the SEC entity name
uv run fa sec concepts 3M "payments to acquire property plant and equipment"
uv run fa sec facts 3M PaymentsToAcquirePropertyPlantAndEquipment --year 2018 --period annual
uv run fa sec filings 3M --form 10-K
```

How periods, fiscal-year labels and restatements are handled is in [docs/METHODOLOGY.md](docs/METHODOLOGY.md).

## Fresh benchmark (post-cutoff filings)

FinanceBench is public, and the model answers much of it from memory: closed book scores 80% on dev. The fresh benchmark uses each company's latest 10-K or 10-Q filed after the agent model's training cutoff.

```bash
uv run fa fresh select --filed-after 2026-08-01 --as-of 2026-11-12   # pick 38 filings, pin their SHA-256
uv run fa fresh fetch                                                # download or read from cache, verify, parse
uv run fa index build --name contextual_fresh --fresh               # FinanceBench + fresh filings
uv run fa fresh draft-text                                           # Claude drafts the text questions (~$0.85, saved)
uv run fa fresh questions                                            # 114 questions, split, review sheet
uv run fa fresh hard-questions                                       # hard tier: 75 questions, no formulas, fiscal periods
```

Selection, HTML parsing, fiscal-period quirks and how the questions are built are in [docs/FRESH.md](docs/FRESH.md).

## Running experiments

```bash
uv run fa eval run configs/experiments/b3_shared_text.yaml --split dev --dry-run   # plan and rough cost
uv run fa eval run configs/experiments/b3_shared_text.yaml --split dev --reps 2 --max-cost 25
uv run fa eval run configs/experiments/b3_shared_text.yaml --resume <run_id>       # retry failures
```

The eight baseline configs, from closed-book to long-context, are described in [docs/METHODOLOGY.md](docs/METHODOLOGY.md#running-experiments).

## Grading and reports

```bash
uv run fa eval grade <run_id>              # numeric rules, then the LLM judge -> grades.jsonl
uv run fa eval report <run_id>             # accuracy with 95% CI, breakdowns, cost, latency -> metrics.json
uv run fa eval compare <run_a> <run_b>     # paired change with 95% CI, McNemar p, fixed/broken questions
uv run fa eval grader-check                # known-good and known-bad answers through the numeric grader
uv run fa eval gold-review                 # how each bare-number gold is read -> results/labels/gold_review.csv
```

The rules (tolerance, scales, percents, refusals) are in [docs/METHODOLOGY.md](docs/METHODOLOGY.md#grading).

## Ask the agent

Needs Claude API credentials: `ant auth login`, or `ANTHROPIC_API_KEY` in `.env`. One question costs about $0.04–0.07 on Claude Opus 5.5, as measured in the evaluation runs.

```bash
uv run fa ask "What is the FY2018 capital expenditure amount (in USD millions) for 3M?"
uv run fa ask "..." --no-xbrl                  # text tools only
uv run fa ask "..." --doc 3M_2018_10K          # restrict the agent to one filing
uv run fa ask "..." --effort high --model claude-sonnet-5-5
```

Each tool call prints as it happens. The full trace (model calls, tool calls, tokens, cost, transcript) is saved under `results/ask/`. How the loop works is in [docs/METHODOLOGY.md](docs/METHODOLOGY.md).

## Agent tools

Seven tools, defined once and shared by the agent and the MCP server: company lookup, filing list, page search, page reading, XBRL concept search, XBRL facts, and an exact calculator with named inputs.

```bash
uv run fa tools list
uv run fa tools run search_filings '{"query": "purchases of property, plant and equipment", "company": "3M", "fiscal_year": 2018}'
uv run fa tools run read_filing_page '{"doc_name": "3M_2018_10K", "page": 59}'
uv run fa tools run calculate '{"expression": "capex / revenue * 100", "variables": [{"name": "capex", "value": 1577}, {"name": "revenue", "value": 32765}]}'
```

`fa data download --pdfs all` fetches all 368 PDFs (705 MB) for the shared-corpus setting. Every file is checked against its git blob SHA at a pinned FinanceBench commit.

## MCP server

The seven tools are served over MCP, so Claude Code, Claude Desktop or any MCP client can research filings with them. The server lists the agent's exact tool definitions and runs calls through the same code; `tests/test_mcp_server.py` checks this parity.

```bash
uv run fa mcp serve                                   # stdio, which desktop clients launch
uv run fa mcp serve --transport http --port 8000      # streamable HTTP at http://127.0.0.1:8000/mcp
uv run fa mcp serve --sec-offline --index contextual  # cached SEC data only; FinanceBench filings only
```

The server needs the parsed filings and an index (`fa data parse`, `fa fresh fetch`, `fa index build`). It uses `SEC_USER_AGENT` from `.env` for live SEC data.

**Claude Code:**

```bash
claude mcp add filing-agent -- uv run --directory "/path/to/filing agent" fa mcp serve
```

**Claude Desktop** (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "filing-agent": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/filing agent", "fa", "mcp", "serve"]
    }
  }
}
```

## Limitations

- **One run per configuration.**
  - Run-to-run variation on judgment questions is about three of 50, as large as small fixes.
  - Repetitions were not affordable, so differences under about 10 points are not significant.
- **The judge is a model.** Claude Sonnet 5.5 grades prose answers, with its figures cross-checked numerically.
  - Every failure and every flagged disagreement was read by hand.
  - No human calibration set was labeled.
- **The fresh text questions were drafted by Claude.** They were checked automatically, read in a first pass, and sampled by a person. The same model family answers them, which may favor its wording; the numeric two-thirds are template-based.
- **Not run:**
  - the whole-filing long-context baseline (about $20–45)
  - dense or hybrid retrieval at corpus scale (about 5 hours of CPU embedding)
  - closed book on the test split

## Development

```bash
uv run pytest          # offline tests
uv run pytest -m live  # tests that need network access or API credentials
uv run ruff check . && uv run ruff format --check . && uv run pyright
```

## Data license and attribution

FinanceBench (Islam et al., 2023, [arXiv:2311.11944](https://arxiv.org/abs/2311.11944)) is released under CC-BY-NC-4.0. This repository does not redistribute it; `fa data download` fetches it from the original source. The code in this repository is MIT-licensed.
