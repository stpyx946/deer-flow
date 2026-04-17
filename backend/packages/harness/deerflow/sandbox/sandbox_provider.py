from abc import ABC, abstractmethod

from deerflow.config.app_config import AppConfig
from deerflow.reflection import resolve_class
from deerflow.sandbox.sandbox import Sandbox


class SandboxProvider(ABC):
    """Abstract base class for sandbox providers"""

    @abstractmethod
    def acquire(self, thread_id: str | None = None) -> str:
        """Acquire a sandbox environment and return its ID.

        Returns:
            The ID of the acquired sandbox environment.
        """
        pass

    @abstractmethod
    def get(self, sandbox_id: str) -> Sandbox | None:
        """Get a sandbox environment by ID.

        Args:
            sandbox_id: The ID of the sandbox environment to retain.
        """
        pass

    @abstractmethod
    def release(self, sandbox_id: str) -> None:
        """Release a sandbox environment.

        Args:
            sandbox_id: The ID of the sandbox environment to destroy.
        """
        pass


_default_sandbox_provider: SandboxProvider | None = None


def get_sandbox_provider(app_config: AppConfig, **kwargs) -> SandboxProvider:
    """Get the sandbox provider singleton.

    Returns a cached singleton instance. Use `reset_sandbox_provider()` to clear
    the cache, or `shutdown_sandbox_provider()` to properly shutdown and clear.

    Args:
        app_config: Application config used the first time the singleton is built.
                    Ignored on subsequent calls — the cached instance is returned
                    regardless of the config passed.

    Returns:
        A sandbox provider instance.
    """
    global _default_sandbox_provider
    if _default_sandbox_provider is None:
        cls = resolve_class(app_config.sandbox.use, SandboxProvider)
        _default_sandbox_provider = cls(app_config=app_config, **kwargs) if _accepts_app_config(cls) else cls(**kwargs)
    return _default_sandbox_provider


def _accepts_app_config(cls: type) -> bool:
    """Return True when the provider's __init__ accepts an ``app_config`` kwarg."""
    import inspect

    try:
        sig = inspect.signature(cls.__init__)
    except (TypeError, ValueError):
        return False
    return "app_config" in sig.parameters


def reset_sandbox_provider() -> None:
    """Reset the sandbox provider singleton.

    This clears the cached instance without calling shutdown.
    The next call to `get_sandbox_provider()` will create a new instance.
    Useful for testing or when switching configurations.

    Note: If the provider has active sandboxes, they will be orphaned.
    Use `shutdown_sandbox_provider()` for proper cleanup.
    """
    global _default_sandbox_provider
    _default_sandbox_provider = None


def shutdown_sandbox_provider() -> None:
    """Shutdown and reset the sandbox provider.

    This properly shuts down the provider (releasing all sandboxes)
    before clearing the singleton. Call this when the application
    is shutting down or when you need to completely reset the sandbox system.
    """
    global _default_sandbox_provider
    if _default_sandbox_provider is not None:
        if hasattr(_default_sandbox_provider, "shutdown"):
            _default_sandbox_provider.shutdown()
        _default_sandbox_provider = None


def set_sandbox_provider(provider: SandboxProvider) -> None:
    """Set a custom sandbox provider instance.

    This allows injecting a custom or mock provider for testing purposes.

    Args:
        provider: The SandboxProvider instance to use.
    """
    global _default_sandbox_provider
    _default_sandbox_provider = provider
