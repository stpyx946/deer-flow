
# --- Phase 2 config-refactor test helper ---
# Memory APIs now take MemoryConfig / AppConfig explicitly. Tests construct a
# minimal config once and reuse it across call sites.
from deerflow.config.app_config import AppConfig as _TestAppConfig
from deerflow.config.memory_config import MemoryConfig as _TestMemoryConfig
from deerflow.config.sandbox_config import SandboxConfig as _TestSandboxConfig

_TEST_MEMORY_CONFIG = _TestMemoryConfig(enabled=True)
_TEST_APP_CONFIG = _TestAppConfig(sandbox=_TestSandboxConfig(use="test"), memory=_TEST_MEMORY_CONFIG)
# -------------------------------------------

"""Tests for user_id propagation in memory updater."""
from unittest.mock import MagicMock, patch

from deerflow.agents.memory.updater import get_memory_data, clear_memory_data, _save_memory_to_file


def test_get_memory_data_passes_user_id():
    mock_storage = MagicMock()
    mock_storage.load.return_value = {"version": "1.0"}
    with patch("deerflow.agents.memory.updater.get_memory_storage", return_value=mock_storage):
        get_memory_data(_TEST_MEMORY_CONFIG, user_id="alice")
        mock_storage.load.assert_called_once_with(None, user_id="alice")


def test_save_memory_passes_user_id():
    mock_storage = MagicMock()
    mock_storage.save.return_value = True
    with patch("deerflow.agents.memory.updater.get_memory_storage", return_value=mock_storage):
        _save_memory_to_file(_TEST_MEMORY_CONFIG, {"version": "1.0"}, user_id="bob")
        mock_storage.save.assert_called_once_with({"version": "1.0"}, None, user_id="bob")


def test_clear_memory_data_passes_user_id():
    mock_storage = MagicMock()
    mock_storage.save.return_value = True
    with patch("deerflow.agents.memory.updater.get_memory_storage", return_value=mock_storage):
        clear_memory_data(_TEST_MEMORY_CONFIG, user_id="charlie")
        # Verify save was called with user_id
        assert mock_storage.save.call_args.kwargs["user_id"] == "charlie"
