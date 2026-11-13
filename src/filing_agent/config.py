"""Runtime settings, read from the environment and an optional `.env` file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


def _find_project_root() -> Path:
    """Return the repository root for a source checkout, else the current directory."""
    candidate = Path(__file__).resolve().parents[2]
    if (candidate / "pyproject.toml").is_file():
        return candidate
    return Path.cwd()


PROJECT_ROOT = _find_project_root()


class Settings(BaseSettings):
    """All configurable paths, credentials and defaults in one place."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    data_dir: Path = Field(default=PROJECT_ROOT / "data", alias="FA_DATA_DIR")
    results_dir: Path = Field(default=PROJECT_ROOT / "results", alias="FA_RESULTS_DIR")
    configs_dir: Path = Field(default=PROJECT_ROOT / "configs", alias="FA_CONFIGS_DIR")

    anthropic_api_key: SecretStr | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    sec_user_agent: str | None = Field(default=None, alias="SEC_USER_AGENT")
    sec_offline: bool = Field(default=False, alias="FA_SEC_OFFLINE")
    github_token: str | None = Field(default=None, alias="GITHUB_TOKEN")

    agent_model: str = Field(default="claude-opus-5-5", alias="FA_AGENT_MODEL")
    judge_model: str = Field(default="claude-sonnet-5-5", alias="FA_JUDGE_MODEL")
    max_cost_usd: float = Field(default=20.0, alias="FA_MAX_COST_USD")

    @property
    def financebench_dir(self) -> Path:
        """Mirror of the FinanceBench repository files we use (questions, metadata, PDFs)."""
        return self.data_dir / "raw" / "financebench"

    @property
    def parsed_dir(self) -> Path:
        """One JSON file per parsed filing (page text, layout text, page tags)."""
        return self.data_dir / "parsed"

    @property
    def index_dir(self) -> Path:
        """Search indexes, one directory per index name."""
        return self.data_dir / "index"

    @property
    def models_dir(self) -> Path:
        """Local model cache (embedding models)."""
        return self.data_dir / "models"

    @property
    def sec_cache_dir(self) -> Path:
        """Every SEC response, at a path mirroring its URL (offline evals read only from here)."""
        return self.data_dir / "cache" / "sec"

    @property
    def companies_file(self) -> Path:
        return self.configs_dir / "companies.yaml"

    @property
    def gold_overrides_file(self) -> Path:
        return self.configs_dir / "gold_overrides.yaml"

    @property
    def splits_dir(self) -> Path:
        return self.configs_dir / "splits"

    @property
    def fresh_dir(self) -> Path:
        """The fresh (post-cutoff) benchmark: filing manifest, questions and splits."""
        return self.configs_dir / "fresh"

    @property
    def fresh_manifest_file(self) -> Path:
        return self.fresh_dir / "filings.json"

    @property
    def fresh_questions_file(self) -> Path:
        return self.fresh_dir / "questions.jsonl"

    @property
    def fresh_hard_questions_file(self) -> Path:
        """The harder tier: breakdown, history and definition questions."""
        return self.fresh_dir / "questions_hard.jsonl"

    @property
    def fresh_text_drafts_file(self) -> Path:
        """Claude's drafted text questions, kept so the set rebuilds without new API calls."""
        return self.fresh_dir / "text_drafts.jsonl"

    @property
    def fresh_review_file(self) -> Path:
        """Review verdicts (reject, fix, or pick another drafted candidate) per question id."""
        return self.fresh_dir / "review.yaml"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
