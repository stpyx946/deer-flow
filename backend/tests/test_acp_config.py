"""Unit tests for ACP agent configuration."""

import json

import pytest
import pytest
import yaml

pytestmark = pytest.mark.real_from_file
from pydantic import ValidationError

from deerflow.config.acp_config import ACPAgentConfig
from deerflow.config.app_config import AppConfig
from deerflow.config.sandbox_config import SandboxConfig


def _make_config(acp_agents: dict | None = None) -> AppConfig:
    return AppConfig(
        sandbox=SandboxConfig(use="test"),
        acp_agents={name: ACPAgentConfig(**cfg) for name, cfg in (acp_agents or {}).items()},
    )


def test_acp_agents_via_app_config():
    cfg = _make_config(
        {
            "claude_code": {
                "command": "claude-code-acp",
                "args": [],
                "description": "Claude Code for coding tasks",
                "model": None,
            }
        }
    )
    agents = cfg.acp_agents
    assert "claude_code" in agents
    assert agents["claude_code"].command == "claude-code-acp"
    assert agents["claude_code"].description == "Claude Code for coding tasks"
    assert agents["claude_code"].model is None


def test_multiple_agents():
    cfg = _make_config(
        {
            "claude_code": {"command": "claude-code-acp", "args": [], "description": "Claude Code"},
            "codex": {"command": "codex-acp", "args": ["--flag"], "description": "Codex CLI"},
        }
    )
    agents = cfg.acp_agents
    assert len(agents) == 2
    assert agents["codex"].args == ["--flag"]


def test_empty_acp_agents():
    cfg = _make_config({})
    assert cfg.acp_agents == {}


def test_default_acp_agents_empty():
    cfg = AppConfig(sandbox=SandboxConfig(use="test"))
    assert cfg.acp_agents == {}


def test_acp_agent_config_defaults():
    cfg = ACPAgentConfig(command="my-agent", description="My agent")
    assert cfg.args == []
    assert cfg.env == {}
    assert cfg.model is None
    assert cfg.auto_approve_permissions is False


def test_acp_agent_config_env_literal():
    cfg = ACPAgentConfig(command="my-agent", description="desc", env={"OPENAI_API_KEY": "sk-test"})
    assert cfg.env == {"OPENAI_API_KEY": "sk-test"}


def test_acp_agent_config_env_default_is_empty():
    cfg = ACPAgentConfig(command="my-agent", description="desc")
    assert cfg.env == {}


def test_acp_agent_preserves_env():
    cfg = _make_config(
        {
            "codex": {
                "command": "codex-acp",
                "args": [],
                "description": "Codex CLI",
                "env": {"OPENAI_API_KEY": "$OPENAI_API_KEY", "FOO": "bar"},
            }
        }
    )
    assert cfg.acp_agents["codex"].env == {"OPENAI_API_KEY": "$OPENAI_API_KEY", "FOO": "bar"}


def test_acp_agent_config_with_model():
    cfg = ACPAgentConfig(command="my-agent", description="desc", model="claude-opus-4")
    assert cfg.model == "claude-opus-4"


def test_acp_agent_config_auto_approve_permissions():
    """P1.2: auto_approve_permissions can be explicitly enabled."""
    cfg = ACPAgentConfig(command="my-agent", description="desc", auto_approve_permissions=True)
    assert cfg.auto_approve_permissions is True


def test_acp_agent_config_missing_command_raises():
    with pytest.raises(ValidationError):
        ACPAgentConfig(description="No command provided")


def test_acp_agent_config_missing_description_raises():
    with pytest.raises(ValidationError):
        ACPAgentConfig(command="my-agent")


def test_app_config_from_file_with_acp_agents(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    extensions_path = tmp_path / "extensions_config.json"
    extensions_path.write_text(json.dumps({"mcpServers": {}, "skills": {}}), encoding="utf-8")

    config_with_acp = {
        "sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"},
        "models": [
            {
                "name": "test-model",
                "use": "langchain_openai:ChatOpenAI",
                "model": "gpt-test",
            }
        ],
        "acp_agents": {
            "codex": {
                "command": "codex-acp",
                "args": [],
                "description": "Codex CLI",
            }
        },
    }
    config_without_acp = {
        "sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"},
        "models": [
            {
                "name": "test-model",
                "use": "langchain_openai:ChatOpenAI",
                "model": "gpt-test",
            }
        ],
    }

    monkeypatch.setenv("DEER_FLOW_EXTENSIONS_CONFIG_PATH", str(extensions_path))

    config_path.write_text(yaml.safe_dump(config_with_acp), encoding="utf-8")
    app = AppConfig.from_file(str(config_path))
    assert set(app.acp_agents) == {"codex"}

    config_path.write_text(yaml.safe_dump(config_without_acp), encoding="utf-8")
    app = AppConfig.from_file(str(config_path))
    assert app.acp_agents == {}
