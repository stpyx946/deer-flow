"""Unit tests for the Firecrawl community tools."""

import json
from unittest.mock import MagicMock, patch

from types import SimpleNamespace as _P2NS

from deerflow.config.app_config import AppConfig as _P2AppConfig
from deerflow.config.deer_flow_context import DeerFlowContext as _P2Ctx
from deerflow.config.sandbox_config import SandboxConfig as _P2SandboxConfig

_P2_APP_CONFIG = _P2AppConfig(sandbox=_P2SandboxConfig(use="test"))
_P2_RUNTIME = _P2NS(context=_P2Ctx(app_config=_P2_APP_CONFIG, thread_id="test-thread"))


def _runtime_with_config(config):
    ctx = _P2Ctx.__new__(_P2Ctx)
    object.__setattr__(ctx, "app_config", config)
    object.__setattr__(ctx, "thread_id", "test-thread")
    object.__setattr__(ctx, "agent_name", None)
    return _P2NS(context=ctx)


class TestWebSearchTool:
    @patch("deerflow.community.firecrawl.tools.FirecrawlApp")
    def test_search_uses_web_search_config(self, mock_firecrawl_cls):
        search_config = MagicMock()
        search_config.model_extra = {"api_key": "firecrawl-search-key", "max_results": 7}
        fake_config = MagicMock()
        fake_config.get_tool_config.return_value = search_config

        mock_result = MagicMock()
        mock_result.web = [
            MagicMock(title="Result", url="https://example.com", description="Snippet"),
        ]
        mock_firecrawl_cls.return_value.search.return_value = mock_result

        from deerflow.community.firecrawl.tools import web_search_tool

        result = web_search_tool.func(query="test query", runtime=_runtime_with_config(fake_config))

        assert json.loads(result) == [
            {
                "title": "Result",
                "url": "https://example.com",
                "snippet": "Snippet",
            }
        ]
        fake_config.get_tool_config.assert_called_with("web_search")
        mock_firecrawl_cls.assert_called_once_with(api_key="firecrawl-search-key")
        mock_firecrawl_cls.return_value.search.assert_called_once_with("test query", limit=7)


class TestWebFetchTool:
    @patch("deerflow.community.firecrawl.tools.FirecrawlApp")
    def test_fetch_uses_web_fetch_config(self, mock_firecrawl_cls):
        fetch_config = MagicMock()
        fetch_config.model_extra = {"api_key": "firecrawl-fetch-key"}

        def get_tool_config(name):
            if name == "web_fetch":
                return fetch_config
            return None

        fake_config = MagicMock()
        fake_config.get_tool_config.side_effect = get_tool_config

        mock_scrape_result = MagicMock()
        mock_scrape_result.markdown = "Fetched markdown"
        mock_scrape_result.metadata = MagicMock(title="Fetched Page")
        mock_firecrawl_cls.return_value.scrape.return_value = mock_scrape_result

        from deerflow.community.firecrawl.tools import web_fetch_tool

        result = web_fetch_tool.func(url="https://example.com", runtime=_runtime_with_config(fake_config))

        assert result == "# Fetched Page\n\nFetched markdown"
        fake_config.get_tool_config.assert_any_call("web_fetch")
        mock_firecrawl_cls.assert_called_once_with(api_key="firecrawl-fetch-key")
        mock_firecrawl_cls.return_value.scrape.assert_called_once_with(
            "https://example.com",
            formats=["markdown"],
        )
