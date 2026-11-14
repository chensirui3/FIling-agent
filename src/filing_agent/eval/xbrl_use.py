"""Which metric questions the agent answered from SEC XBRL data alone.

FinanceBench's 50 "metrics-generated" questions ask for statement figures and ratios, the kind
of data the XBRL API carries. For each one, the saved trace shows where the evidence came from:

- `xbrl`: only `xbrl_get_facts` returned data (no filing text was searched or read);
- `text`: only filing search or page reading did;
- `both` or `none` otherwise.

A question answered correctly by the `xbrl` route is answerable from the API alone, so the
count is a demonstrated lower bound on XBRL answerability. The text route proves nothing either
way: the agent may simply not have tried the API (`tried_xbrl` records whether it did).

Reads local traces; the saved summary holds ids and labels only, no FinanceBench text.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from filing_agent.agent.trace import AgentRun, ToolCall
from filing_agent.data.financebench import Question
from filing_agent.eval.gold import numeric_gold
from filing_agent.eval.grade_run import load_grades

Route = Literal["xbrl", "text", "both", "none"]
XBRL_EVIDENCE = frozenset({"xbrl_get_facts"})
TEXT_EVIDENCE = frozenset({"search_filings", "read_filing_page"})
XBRL_TOOLS = frozenset({"xbrl_get_facts", "xbrl_find_concepts"})
OUT_FILE = "xbrl_use.json"


def evidence_route(tool_calls: Iterable[ToolCall]) -> Route:
    """Where the run's evidence came from, counting only calls that returned data."""
    names = {call.name for call in tool_calls if not call.is_error}
    xbrl, text = bool(names & XBRL_EVIDENCE), bool(names & TEXT_EVIDENCE)
    return "both" if xbrl and text else "xbrl" if xbrl else "text" if text else "none"


@dataclass(frozen=True)
class XbrlUse:
    question_id: str
    run_id: str
    gold_kind: str  # money, percent or number
    route: Route
    tried_xbrl: bool  # any XBRL tool call, failed ones included
    label: str  # the grade: correct, incorrect, refusal or no_answer


def xbrl_use(
    run_dir: Path, questions: Mapping[str, Question], question_type: str = "metrics-generated"
) -> list[XbrlUse]:
    rows: list[XbrlUse] = []
    for grade in load_grades(run_dir):
        question = questions.get(grade.question_id)
        if question is None or question.question_type != question_type or grade.label is None:
            continue
        trace = run_dir / "traces" / f"{grade.question_id}__r{grade.rep}.json"
        if not trace.is_file():
            raise FileNotFoundError(f"{trace} not found: traces are local and not in git")
        run = AgentRun.model_validate_json(trace.read_text(encoding="utf-8"))
        gold = numeric_gold(question)
        rows.append(
            XbrlUse(
                question_id=grade.question_id,
                run_id=run_dir.name,
                gold_kind=gold.kind if gold else "prose",
                route=evidence_route(run.tool_calls),
                tried_xbrl=any(call.name in XBRL_TOOLS for call in run.tool_calls),
                label=grade.label,
            )
        )
    return rows


def summarize(rows: Sequence[XbrlUse]) -> dict[str, object]:
    correct = [r for r in rows if r.label == "correct"]
    by_route = Counter(r.route for r in rows)
    xbrl_correct = sum(r.route == "xbrl" for r in correct)
    return {
        "questions": len(rows),
        "correct": len(correct),
        "by_route": {route: by_route[route] for route in ("xbrl", "text", "both", "none")},
        "correct_from_xbrl_alone": xbrl_correct,
        "answerable_lower_bound": xbrl_correct / len(rows) if rows else None,
        "by_gold_kind": {
            kind: {
                "questions": sum(r.gold_kind == kind for r in rows),
                "correct_from_xbrl_alone": sum(
                    r.gold_kind == kind and r.route == "xbrl" for r in correct
                ),
            }
            for kind in sorted({r.gold_kind for r in rows})
        },
        "text_route_without_trying_xbrl": sum(r.route == "text" and not r.tried_xbrl for r in rows),
    }


def to_json(rows: Sequence[XbrlUse]) -> dict[str, object]:
    return {"summary": summarize(rows), "questions": [asdict(r) for r in rows]}
