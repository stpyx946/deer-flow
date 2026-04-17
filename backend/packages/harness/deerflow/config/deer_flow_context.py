"""Per-invocation context for DeerFlow agent execution.

Injected via LangGraph Runtime. Middleware and tools access this
via Runtime[DeerFlowContext] parameters, through resolve_context().
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from deerflow.config.app_config import AppConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeerFlowContext:
    """Typed, immutable, per-invocation context injected via LangGraph Runtime.

    Fields are all known at run start and never change during execution.
    Mutable runtime state (e.g. sandbox_id) flows through ThreadState, not here.
    """

    app_config: AppConfig
    thread_id: str
    agent_name: str | None = None


def resolve_context(runtime: Any) -> DeerFlowContext:
    """Return the typed DeerFlowContext that the runtime carries.

    Gateway mode (``DeerFlowClient``, ``run_agent``) always attaches a typed
    ``DeerFlowContext`` via ``agent.astream(context=...)``; the LangGraph
    Server path uses ``langgraph.json`` registration where the top-level
    ``make_lead_agent`` loads ``AppConfig`` from disk itself, so we still
    arrive here with a typed context.

    Only the dict/None shapes that legacy tests used to exercise would fall
    through this function; we now reject them loudly instead of papering
    over the missing context with an ambient ``AppConfig`` lookup.
    """
    ctx = getattr(runtime, "context", None)
    if isinstance(ctx, DeerFlowContext):
        return ctx

    raise RuntimeError(
        "resolve_context: runtime.context is not a DeerFlowContext "
        "(got type %s). Every entry point must attach one at invoke time — "
        "Gateway/Client via agent.astream(context=DeerFlowContext(...)), "
        "LangGraph Server via the make_lead_agent boundary that loads "
        "AppConfig.from_file()." % type(ctx).__name__
    )
