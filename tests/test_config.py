from pathlib import Path

import pytest

from filing_agent.config import PROJECT_ROOT, Settings

_ENV_VARS = ("FA_DATA_DIR", "FA_RESULTS_DIR", "FA_CONFIGS_DIR", "SEC_USER_AGENT", "FA_AGENT_MODEL")


def _settings() -> Settings:
    return Settings(_env_file=None)  # pyright: ignore[reportCallIssue]


def test_defaults_live_inside_the_project(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    settings = _settings()
    assert settings.data_dir == PROJECT_ROOT / "data"
    assert settings.financebench_dir == PROJECT_ROOT / "data" / "raw" / "financebench"
    assert settings.splits_dir == PROJECT_ROOT / "configs" / "splits"
    assert settings.agent_model != settings.judge_model


def test_environment_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SEC_USER_AGENT", "Jane Doe jane@example.com")
    settings = _settings()
    assert settings.data_dir == tmp_path
    assert settings.sec_user_agent == "Jane Doe jane@example.com"
