from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from deerflow.config.app_config import AppConfig

pytestmark = pytest.mark.real_from_file


def _write_config(path: Path, *, model_name: str, supports_thinking: bool) -> None:
    path.write_text(
        yaml.safe_dump(
            {
                "sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"},
                "models": [
                    {
                        "name": model_name,
                        "use": "langchain_openai:ChatOpenAI",
                        "model": "gpt-test",
                        "supports_thinking": supports_thinking,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def _write_extensions_config(path: Path) -> None:
    path.write_text(json.dumps({"mcpServers": {}, "skills": {}}), encoding="utf-8")


def test_from_file_reads_model_name(tmp_path, monkeypatch):
    """``AppConfig.from_file`` is the only lifecycle method now; there is no
    process-global ``init/current``. Each consumer holds its own captured
    AppConfig instance.
    """
    config_path = tmp_path / "config.yaml"
    extensions_path = tmp_path / "extensions_config.json"
    _write_extensions_config(extensions_path)
    _write_config(config_path, model_name="test-model", supports_thinking=False)

    monkeypatch.setenv("DEER_FLOW_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("DEER_FLOW_EXTENSIONS_CONFIG_PATH", str(extensions_path))

    config = AppConfig.from_file(str(config_path))
    assert config.models[0].name == "test-model"


def test_from_file_each_call_returns_fresh_instance(tmp_path, monkeypatch):
    """Two reads of the same file produce separate AppConfig instances —
    no hidden singleton, no memoization. Callers decide when to re-read.
    """
    config_path = tmp_path / "config.yaml"
    extensions_path = tmp_path / "extensions_config.json"
    _write_extensions_config(extensions_path)
    _write_config(config_path, model_name="model-a", supports_thinking=False)

    monkeypatch.setenv("DEER_FLOW_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("DEER_FLOW_EXTENSIONS_CONFIG_PATH", str(extensions_path))

    config_a = AppConfig.from_file(str(config_path))
    assert config_a.models[0].name == "model-a"

    _write_config(config_path, model_name="model-b", supports_thinking=True)
    config_b = AppConfig.from_file(str(config_path))
    assert config_b.models[0].name == "model-b"
    assert config_a is not config_b


def test_config_version_check(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    extensions_path = tmp_path / "extensions_config.json"
    _write_extensions_config(extensions_path)

    config_path.write_text(
        yaml.safe_dump(
            {
                "config_version": 1,
                "sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"},
                "models": [],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("DEER_FLOW_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("DEER_FLOW_EXTENSIONS_CONFIG_PATH", str(extensions_path))

    config = AppConfig.from_file(str(config_path))
    assert config is not None
