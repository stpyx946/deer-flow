"""Tests for DeerFlowContext and resolve_context()."""

from dataclasses import FrozenInstanceError
from unittest.mock import MagicMock, patch

import pytest

from deerflow.config.app_config import AppConfig
from deerflow.config.deer_flow_context import DeerFlowContext, resolve_context
from deerflow.config.sandbox_config import SandboxConfig


def _make_config(**overrides) -> AppConfig:
    defaults = {"sandbox": SandboxConfig(use="test")}
    defaults.update(overrides)
    return AppConfig(**defaults)


class TestDeerFlowContext:
    def test_frozen(self):
        ctx = DeerFlowContext(app_config=_make_config(), thread_id="t1")
        with pytest.raises(FrozenInstanceError):
            ctx.app_config = _make_config()

    def test_fields(self):
        config = _make_config()
        ctx = DeerFlowContext(app_config=config, thread_id="t1", agent_name="test-agent")
        assert ctx.thread_id == "t1"
        assert ctx.agent_name == "test-agent"
        assert ctx.app_config is config

    def test_agent_name_default(self):
        ctx = DeerFlowContext(app_config=_make_config(), thread_id="t1")
        assert ctx.agent_name is None

    def test_thread_id_required(self):
        with pytest.raises(TypeError):
            DeerFlowContext(app_config=_make_config())  # type: ignore[call-arg]


class TestResolveContext:
    def test_returns_typed_context_directly(self):
        """Gateway/Client path: runtime.context is DeerFlowContext → return as-is."""
        config = _make_config()
        ctx = DeerFlowContext(app_config=config, thread_id="t1")
        runtime = MagicMock()
        runtime.context = ctx
        assert resolve_context(runtime) is ctx

    def test_raises_on_none_context(self):
        """Without a typed DeerFlowContext, resolve_context refuses to guess."""
        runtime = MagicMock()
        runtime.context = None
        with pytest.raises(RuntimeError, match="resolve_context: runtime.context is not a DeerFlowContext"):
            resolve_context(runtime)

    def test_raises_on_dict_context(self):
        """Legacy dict shape is no longer supported — we raise instead of lazily loading AppConfig."""
        runtime = MagicMock()
        runtime.context = {"thread_id": "old-dict", "agent_name": "from-dict"}
        with pytest.raises(RuntimeError, match="resolve_context: runtime.context is not a DeerFlowContext"):
            resolve_context(runtime)
