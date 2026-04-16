# Design: Eliminate Global Mutable State in Configuration System

> Implements [#1811](https://github.com/bytedance/deer-flow/issues/1811) · Tracked in [#2151](https://github.com/bytedance/deer-flow/issues/2151)
>
> **Phase 1 (shipped):** [PR #2271](https://github.com/bytedance/deer-flow/pull/2271) — frozen config tree, purify `from_file()`, 3-tier `AppConfig.current()` lifecycle, `DeerFlowContext` for agent execution path.
>
> **Phase 2 (proposed):** eliminate the remaining implicit-state surface (`_global` / `_override` / `current()`) via pure explicit parameter passing. See §8.

## Problem

`deerflow/config/` had three structural issues:

1. **Dual source of truth** — each sub-config existed both as an `AppConfig` field and a module-level global (e.g. `_memory_config`). Consumers didn't know which to trust.
2. **Side-effect coupling** — `AppConfig.from_file()` silently mutated 8 sub-module globals via `load_*_from_dict()` calls.
3. **Incomplete isolation** — `ContextVar` only scoped `AppConfig`, not the 8 sub-config globals.

## Design Principle

**Config is a value object, not live shared state.** Constructed once, immutable, no reload. New config = new object + rebuild agent.

## Solution

### 1. Frozen AppConfig (full tree)

All config models set `frozen=True`, including `DatabaseConfig` and `RunEventsConfig` (added late in review). No mutation after construction.

```python
class MemoryConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

class AppConfig(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)
    memory: MemoryConfig
    title: TitleConfig
    ...
```

Changes use copy-on-write: `config.model_copy(update={...})`.

### 2. Pure `from_file()`

`AppConfig.from_file()` is a pure function — returns a frozen object, no side effects. All 8 `load_*_from_dict()` calls and their imports were removed.

### 3. Deleted sub-module globals

Every sub-config module's global state was deleted:

| Deleted | Files |
|---------|-------|
| `_memory_config`, `get_memory_config()`, `set_memory_config()`, `load_memory_config_from_dict()` | `memory_config.py` |
| `_title_config`, `get_title_config()`, `set_title_config()`, `load_title_config_from_dict()` | `title_config.py` |
| Same pattern | `summarization_config.py`, `subagents_config.py`, `guardrails_config.py`, `tool_search_config.py`, `checkpointer_config.py`, `stream_bridge_config.py`, `acp_config.py` |
| `_extensions_config`, `reload_extensions_config()`, `reset_extensions_config()`, `set_extensions_config()` | `extensions_config.py` |
| `reload_app_config()`, `reset_app_config()`, `set_app_config()`, mtime detection, `push/pop_current_app_config()` | `app_config.py` |

Consumers migrated from `get_memory_config()` → `AppConfig.current().memory` (~100 call-sites).

### 4. Lifecycle: 3-tier `AppConfig.current()`

The original plan called for a single `ContextVar` with hard-fail on uninitialized access. The shipped lifecycle is a **3-tier fallback** attached to `AppConfig` itself (no separate `context.py` module). The divergence is explained in §7.

```python
# app_config.py
class AppConfig(BaseModel):
    ...

    # Process-global singleton. Atomic pointer swap under the GIL,
    # so no lock is needed for current read/write patterns.
    _global: ClassVar[AppConfig | None] = None

    # Per-context override (tests, multi-client scenarios).
    _override: ClassVar[ContextVar[AppConfig]] = ContextVar("deerflow_app_config_override")

    @classmethod
    def init(cls, config: AppConfig) -> None:
        """Set the process-global. Visible to all subsequent async tasks."""
        cls._global = config

    @classmethod
    def set_override(cls, config: AppConfig) -> Token[AppConfig]:
        """Per-context override. Returns Token for reset_override()."""
        return cls._override.set(config)

    @classmethod
    def reset_override(cls, token: Token[AppConfig]) -> None:
        cls._override.reset(token)

    @classmethod
    def current(cls) -> AppConfig:
        """Priority: per-context override > process-global > auto-load from file."""
        try:
            return cls._override.get()
        except LookupError:
            pass
        if cls._global is not None:
            return cls._global
        logger.warning(
            "AppConfig.current() called before init(); auto-loading from file. "
            "Call AppConfig.init() at process startup to surface config errors early."
        )
        config = cls.from_file()
        cls._global = config
        return config
```

**Why three tiers and not one:**

- **Process-global** is required because `ContextVar` doesn't propagate config updates across async request boundaries. Gateway receives a `PUT /mcp/config` on one request, reloads config, and the next request — in a fresh async context — must see the new value. A plain class variable (`_global`) does this; a `ContextVar` does not.
- **Per-context override** is retained for test isolation and multi-client scenarios. A test can scope its config without mutating the process singleton. `reset_override()` restores the previous state deterministically via `Token`.
- **Auto-load fallback** is a backward-compatibility escape hatch with a warning. Call sites that skipped explicit `init()` (legacy or test) still work, but the warning surfaces the miss.

### 5. Per-invocation context: `DeerFlowContext`

Lives in `deerflow/config/deer_flow_context.py` (not `context.py` as originally planned — the name was reserved to avoid implying a lifecycle module).

```python
@dataclass(frozen=True)
class DeerFlowContext:
    """Typed, immutable, per-invocation context injected via LangGraph Runtime."""
    app_config: AppConfig
    thread_id: str
    agent_name: str | None = None
```

**Fields:**

| Field | Type | Source | Mutability |
|-------|------|--------|-----------|
| `app_config` | `AppConfig` | `AppConfig.current()` at run start | Immutable per-run |
| `thread_id` | `str` | Caller-provided | Immutable per-run |
| `agent_name` | `str \| None` | Caller-provided (bootstrap only) | Immutable per-run |

**Not in context:** `sandbox_id` is mutable runtime state (lazy-acquired mid-execution). It flows through `ThreadState.sandbox` (state channel), not context. All 3 `runtime.context["sandbox_id"] = ...` writes in `sandbox/tools.py` were removed; `SandboxMiddleware.after_agent` reads from `state["sandbox"]` only.

**Construction per entry point:**

```python
# Gateway runtime (worker.py) — primary path
deer_flow_context = DeerFlowContext(
    app_config=AppConfig.current(),
    thread_id=thread_id,
)
agent.astream(input, config=config, context=deer_flow_context)

# DeerFlowClient (client.py)
AppConfig.init(AppConfig.from_file(config_path))
context = DeerFlowContext(app_config=AppConfig.current(), thread_id=thread_id)
agent.stream(input, config=config, context=context)

# LangGraph Server — legacy path, context=None or dict, fallback via resolve_context()
```

### 6. Access pattern by caller type

The shipped code stratifies callers by what `runtime.context` type they see, and tightened middleware access over time:

| Caller type | Access pattern | Examples |
|-------------|---------------|----------|
| Typed middleware (declares `Runtime[DeerFlowContext]`) | `runtime.context.app_config.xxx` — direct field access, no wrapper | `memory_middleware`, `title_middleware`, `thread_data_middleware`, `uploads_middleware`, `loop_detection_middleware` |
| Tools that may see legacy dict context | `resolve_context(runtime).xxx` | `sandbox/tools.py` (bash-guard gate, sandbox config), `task_tool.py` (bash subagent gate) |
| Tools with typed runtime | `runtime.context.xxx` directly | `present_file_tool.py`, `setup_agent_tool.py`, `skill_manage_tool.py` |
| Non-agent paths (Gateway routers, CLI, factories) | `AppConfig.current().xxx` | `app/gateway/routers/*`, `reset_admin.py`, `models/factory.py` |

**Middleware hardening** (late commit `a934a822`): the original plan had middlewares call `resolve_context(runtime)` everywhere. In practice, once the middleware signature was typed as `Runtime[DeerFlowContext]`, the wrapper became defensive noise. The commit removed:
- `try/except` wrappers around `resolve_context(...)` in middlewares and sandbox tools
- Optional `title_config=None` fallback on every `_build_title_prompt` / `_format_for_title_model` helper; they now take `TitleConfig` as a **required parameter**
- Ad-hoc `get_config()` fallback chains in `memory_middleware`

Dropping the swallowed-exception layer means config-resolution bugs surface as errors instead of silently degrading — aligning with let-it-crash.

`resolve_context()` itself still exists and handles three cases:

```python
def resolve_context(runtime: Any) -> DeerFlowContext:
    ctx = getattr(runtime, "context", None)
    if isinstance(ctx, DeerFlowContext):
        return ctx                        # typed path (Gateway, Client)
    if isinstance(ctx, dict):
        return DeerFlowContext(           # legacy dict path (with warning if empty thread_id)
            app_config=AppConfig.current(),
            thread_id=ctx.get("thread_id", ""),
            agent_name=ctx.get("agent_name"),
        )
    # Final fallback: LangGraph configurable (e.g. LangGraph Server)
    cfg = get_config().get("configurable", {})
    return DeerFlowContext(
        app_config=AppConfig.current(),
        thread_id=cfg.get("thread_id", ""),
        agent_name=cfg.get("agent_name"),
    )
```

### 7. Divergence from original plan

Two material divergences from the original design, both driven by implementation feedback:

**7.1 Lifecycle: `ContextVar` → process-global + `ContextVar` override**

*Original:* single `ContextVar` in a new `context.py` module. `get_app_config()` raises `ConfigNotInitializedError` if unset.

*Shipped:* process-global `AppConfig._global` (primary) + `ContextVar` override (scoped) + auto-load with warning (fallback).

*Why:* a `ContextVar` set by Gateway startup is not visible to subsequent requests that spawn fresh async contexts. `PUT /mcp/config` must update config such that the next incoming request sees the new value in *its* async task — this requires process-wide state. ContextVar is retained for test isolation (`reset_override()` works cleanly per test via `Token`) and for per-client scoping if ever needed.

The `ConfigNotInitializedError` was replaced by a warning + auto-load. The hard error caught more legitimate bugs but also broke call sites that historically worked without explicit init (internal scripts, test fixtures during import-time). The warning preserves the signal without breaking backward compatibility; `backend/tests/conftest.py` now has an autouse fixture that sets `_global` to a minimal `AppConfig` so tests never hit auto-load.

**7.2 Module name: `context.py` → lifecycle on `AppConfig`, `deer_flow_context.py` for the invocation context**

*Original:* lifecycle and `DeerFlowContext` both in `deerflow/config/context.py`.

*Shipped:* lifecycle is classmethods on `AppConfig` itself (`init`, `current`, `set_override`, `reset_override`). `DeerFlowContext` and `resolve_context()` live in `deerflow/config/deer_flow_context.py`.

*Why:* the lifecycle operates on `AppConfig` directly — putting it on the class removes one level of module coupling. The per-invocation context is conceptually separate (it's agent-execution plumbing, not config lifecycle) so it got its own file with a distinguishing name.

**7.3 Client lifecycle: `init() + set_override()` → `init()` only**

*Original (never finalized):* `DeerFlowClient.__init__` called both `init()` (process-global) and `set_override()` so two clients with different configs wouldn't clobber each other.

*Shipped:* `init()` only.

*Why (commit `a934a822`):* `set_override()` leaked overrides across test boundaries because the `ContextVar` wasn't reset between client instances. Single-client is the common case, and tests use the autouse fixture for isolation. Multi-client scoping can be added back with explicit `set_override()` if the need arises.

## What doesn't change

- `config.yaml` schema
- `extensions_config.json` loading
- External API behavior (Gateway, DeerFlowClient)

## Migration scope (Phase 1, actual)

- ~100 call-sites: `get_*_config()` → `AppConfig.current().xxx`
- 6 runtime-path migrations: middlewares + sandbox tools read from `runtime.context` or `resolve_context()`
- 3 deleted sandbox_id writes in `sandbox/tools.py`
- ~100 test locations updated; `conftest.py` autouse fixture added
- New tests: `test_config_frozen.py`, `test_deer_flow_context.py`, `test_app_config_reload.py`
- Gateway update flow: `reload_*` → `AppConfig.init(AppConfig.from_file())`
- Dependency: langgraph `Runtime` / `ToolRuntime` (already available at target version)

## 8. Phase 2: pure explicit parameter passing

Phase 1 shipped a working 3-tier `AppConfig.current()` lifecycle. The remaining implicit-state surface is:

- `AppConfig._global: ClassVar` — process-level singleton
- `AppConfig._override: ClassVar[ContextVar]` — per-context override
- `AppConfig.current()` — fallback-chain reader with auto-load warning

Phase 2 proposes removing all three. `AppConfig` reduces to a pure Pydantic value object with `from_file()` as its only factory. All consumers receive `AppConfig` as an explicit parameter, either through a typed constructor, a function signature, or LangGraph `Runtime[DeerFlowContext]`.

### 8.1 Motivation

Phase 1 addressed the **data side** of the problem: config is now a frozen ADT, sub-module globals deleted, `from_file()` pure. The **access side** still relies on implicit ambient lookup:

```python
# Today (Phase 1 shipped):
def _get_memory_prompt() -> str:
    config = AppConfig.current().memory  # implicit global lookup
    ...

# Target (Phase 2):
def _get_memory_prompt(config: MemoryConfig) -> str:  # explicit dependency
    ...
```

Three concrete benefits:

| Benefit | What it buys |
|---------|-------------|
| Referential transparency | A function's result depends only on its inputs. Testing becomes parameter substitution, no `patch.object(AppConfig, "current")` chains |
| Dependency visibility | A function signature declares what config it needs. No "this deep helper secretly reads `.memory`" surprises |
| True multi-config isolation | Two `DeerFlowClient` instances with different configs can run in the same process without any ambient shared state to contend over |

The cost (Phase 1 wouldn't have made this smaller): ~97 production call sites + ~91 test mock sites need touching, plus signature changes for helpers that now accept `config` as a parameter.

### 8.2 Non-agent call paths and their target APIs

Phase 1 got the agent-execution path right (`runtime.context.app_config.xxx`). The unsolved paths split into four categories:

**FastAPI Gateway** → `Depends(get_config)`

```python
# app/gateway/app.py — at startup
app.state.config = AppConfig.from_file()

# app/gateway/deps.py
def get_config(request: Request) -> AppConfig:
    return request.app.state.config

# app/gateway/routers/models.py
@router.get("/models")
def list_models(config: AppConfig = Depends(get_config)):
    ...

# app/gateway/routers/mcp.py — config reload replaces AppConfig.init()
@router.put("/config")
def update_mcp(..., request: Request):
    ...
    request.app.state.config = AppConfig.from_file()
```

`app.state.config` is a FastAPI-owned attribute on the app object, not a module-level global. Scoped to the app's lifetime, only written at startup and config-reload.

**`DeerFlowClient`** → constructor-captured config

```python
class DeerFlowClient:
    def __init__(self, config_path: str | None = None, config: AppConfig | None = None):
        self._config = config or AppConfig.from_file(config_path)

    def chat(self, message: str, thread_id: str) -> str:
        context = DeerFlowContext(app_config=self._config, thread_id=thread_id)
        ...
```

Multiple `DeerFlowClient` instances are now first-class — each owns its config, nothing shared.

**Agent construction (`make_lead_agent`, `_build_middlewares`, prompt helpers)** → threaded through

```python
def make_lead_agent(config: RunnableConfig, app_config: AppConfig):
    middlewares = _build_middlewares(app_config, runtime_config=config)
    ...

def _build_middlewares(app_config: AppConfig, runtime_config: RunnableConfig):
    if app_config.token_usage.enabled:
        middlewares.append(TokenUsageMiddleware())
    ...
```

Every helper that reads config is now on a function-signature chain from `make_lead_agent`.

**Background threads (memory debounce Timer, queue consumers)** → closure-captured

```python
def MemoryQueue.add(self, conversation, user_id, config: MemoryConfig):
    # capture config at enqueue time
    def _flush():
        self._updater.update(conversation, user_id, config)
    self._timer = Timer(config.debounce_seconds, _flush)
    self._timer.start()
```

The captured config lives in the closure, not in a contextvar the thread can't see.

### 8.3 Target `AppConfig` shape

```python
class AppConfig(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    log_level: str = "info"
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    ...  # same fields as Phase 1

    @classmethod
    def from_file(cls, config_path: str | None = None) -> Self:
        """Pure factory. Reads file, returns frozen object. No side effects."""
        ...

    @classmethod
    def resolve_config_path(cls, config_path: str | None = None) -> Path:
        """Unchanged from Phase 1."""
        ...

    def get_model_config(self, name: str) -> ModelConfig | None:
        """Unchanged."""
        ...

    # Removed:
    # - _global: ClassVar
    # - _override: ClassVar[ContextVar]
    # - init(), set_override(), reset_override(), current()
```

### 8.4 `DeerFlowContext` and `resolve_context()` after Phase 2

`DeerFlowContext` is unchanged — it's already Phase 2-compliant.

`resolve_context()` simplifies: the "fall back to `AppConfig.current()`" branch goes away. The dict-context legacy path either constructs `DeerFlowContext` with an explicitly-passed `AppConfig` (fed by caller) or is deleted if no dict-context callers remain.

```python
def resolve_context(runtime: Any) -> DeerFlowContext:
    ctx = getattr(runtime, "context", None)
    if isinstance(ctx, DeerFlowContext):
        return ctx
    raise RuntimeError(
        "runtime.context is not a DeerFlowContext. All callers must construct "
        "and inject one explicitly; there is no global fallback."
    )
```

Let-it-crash: if Phase 2 is done correctly, every caller constructs a typed context. If one doesn't, fail loudly.

### 8.5 Trade-off acknowledgment

The three cases where ambient lookup is genuinely tempting (and why we reject them):

| Tempting case | Why ambient looks easier | Why we still reject it |
|---------------|-------------------------|------------------------|
| Deep helper in `memory/storage.py` needs `memory.storage_path` | Just threaded through 4 call layers | That's exactly the dependency chain you want visible. It's either there or it's hiding |
| Community tool factory reading API keys from config | "Each tool factory doesn't want to take config" | Each tool factory literally needs the config. Passing it is the honest signature |
| Test that wants to "override just one field globally" | `patch.object(AppConfig, "current")` is one line | Tests constructing their own `AppConfig` is one fixture — and that fixture becomes infrastructure for all future tests |

The rejection is consistent: **an explicit parameter is strictly more honest than an implicit global lookup**, in every case.

### 8.6 Scope

- ~97 production call sites: `AppConfig.current()` → parameter
- ~91 test mock sites: `patch.object(AppConfig, "current")` / `AppConfig._global = ...` → fixture injection
- ~30 FastAPI endpoints gain `config: AppConfig = Depends(get_config)`
- ~15 factory / helper functions gain `config: AppConfig` parameter
- Delete from `app_config.py`: `_global`, `_override`, `init`, `current`, `set_override`, `reset_override`
- Simplify `resolve_context()`: remove `AppConfig.current()` fallback

Implementation plan: see [2026-04-12-config-refactor-plan.md §Phase 2](./2026-04-12-config-refactor-plan.md#phase-2-pure-explicit-parameter-passing).
