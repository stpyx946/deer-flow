import json

from exa_py import Exa
from langchain.tools import ToolRuntime, tool

from deerflow.config.app_config import AppConfig
from deerflow.config.deer_flow_context import resolve_context


def _get_exa_client(app_config: AppConfig, tool_name: str = "web_search") -> Exa:
    tool_config = app_config.get_tool_config(tool_name)
    api_key = None
    if tool_config is not None and "api_key" in tool_config.model_extra:
        api_key = tool_config.model_extra.get("api_key")
    return Exa(api_key=api_key)


@tool("web_search", parse_docstring=True)
def web_search_tool(query: str, runtime: ToolRuntime) -> str:
    """Search the web.

    Args:
        query: The query to search for.
    """
    try:
        app_config = resolve_context(runtime).app_config
        tool_config = app_config.get_tool_config("web_search")
        max_results = 5
        search_type = "auto"
        contents_max_characters = 1000
        if tool_config is not None:
            max_results = tool_config.model_extra.get("max_results", max_results)
            search_type = tool_config.model_extra.get("search_type", search_type)
            contents_max_characters = tool_config.model_extra.get("contents_max_characters", contents_max_characters)

        client = _get_exa_client(app_config)
        res = client.search(
            query,
            type=search_type,
            num_results=max_results,
            contents={"highlights": {"max_characters": contents_max_characters}},
        )

        normalized_results = [
            {
                "title": result.title or "",
                "url": result.url or "",
                "snippet": "\n".join(result.highlights) if result.highlights else "",
            }
            for result in res.results
        ]
        json_results = json.dumps(normalized_results, indent=2, ensure_ascii=False)
        return json_results
    except Exception as e:
        return f"Error: {str(e)}"


@tool("web_fetch", parse_docstring=True)
def web_fetch_tool(url: str, runtime: ToolRuntime) -> str:
    """Fetch the contents of a web page at a given URL.
    Only fetch EXACT URLs that have been provided directly by the user or have been returned in results from the web_search and web_fetch tools.
    This tool can NOT access content that requires authentication, such as private Google Docs or pages behind login walls.
    Do NOT add www. to URLs that do NOT have them.
    URLs must include the schema: https://example.com is a valid URL while example.com is an invalid URL.

    Args:
        url: The URL to fetch the contents of.
    """
    try:
        client = _get_exa_client(resolve_context(runtime).app_config, "web_fetch")
        res = client.get_contents([url], text={"max_characters": 4096})

        if res.results:
            result = res.results[0]
            title = result.title or "Untitled"
            text = result.text or ""
            return f"# {title}\n\n{text[:4096]}"
        else:
            return "Error: No results found"
    except Exception as e:
        return f"Error: {str(e)}"
