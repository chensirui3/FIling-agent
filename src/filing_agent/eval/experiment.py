"""Experiment configurations: one YAML file per system or baseline being evaluated.

Every setting that can change a result lives in the config, and a run records the config's hash,
so two runs with the same hash ran the same system. Changing a prompt, a tool set or a budget
means a new config (and a new hash), never an edit to an old run.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from filing_agent.agent.loop import AgentConfig, Effort
from filing_agent.agent.prompts import PROMPT_VERSION
from filing_agent.eval.datasets import Dataset
from filing_agent.tools.filing_tools import ALL_TOOLS

ContextKind = Literal["none", "oracle_evidence", "retrieved", "full_filing"]
CorpusScope = Literal["all", "gold_filing"]


class ExperimentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    dataset: Dataset = Field(
        default="financebench", description="question set: FinanceBench or the fresh set"
    )
    model: str = "claude-opus-5-5"
    effort: Effort = "medium"
    tools: list[str] = Field(default_factory=lambda: list(ALL_TOOLS))
    corpus: CorpusScope = Field(
        default="all",
        description="all filings, or only the question's own filing (single-filing setting)",
    )
    context: ContextKind = Field(
        default="none", description="what goes in the prompt before the question"
    )
    retrieved_k: int = Field(default=8, description="pages of search results for 'retrieved'")
    index: str = "contextual"
    max_turns: int = 16
    max_tool_calls: int = 40
    max_tokens: int = 16_000
    wall_clock_s: float = 300.0
    prompt_version: str = PROMPT_VERSION
    sec_offline: bool = Field(default=True, description="answer SEC requests only from the cache")

    @field_validator("tools")
    @classmethod
    def _known_tools(cls, tools: list[str]) -> list[str]:
        unknown = sorted(set(tools) - set(ALL_TOOLS))
        if unknown:
            raise ValueError(f"unknown tools: {unknown}")
        return [t for t in ALL_TOOLS if t in tools]  # canonical order keeps prompts byte-stable

    @property
    def uses_xbrl(self) -> bool:
        return any(t.startswith("xbrl_") for t in self.tools)

    def config_hash(self) -> str:
        data = self.model_dump(mode="json")
        if data["dataset"] == "financebench":
            del data["dataset"]  # added later: FinanceBench configs keep their earlier hashes
        canonical = json.dumps(data, sort_keys=True)
        return hashlib.sha256(canonical.encode()).hexdigest()[:10]

    def agent_config(self) -> AgentConfig:
        return AgentConfig(
            model=self.model,
            effort=self.effort,
            max_turns=self.max_turns,
            max_tool_calls=self.max_tool_calls,
            max_tokens=self.max_tokens,
            wall_clock_s=self.wall_clock_s,
            fallbacks=False,  # every scored answer must come from the model under test
            prompt_version=self.prompt_version,
        )


def load_experiment(path: Path) -> ExperimentConfig:
    return ExperimentConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
