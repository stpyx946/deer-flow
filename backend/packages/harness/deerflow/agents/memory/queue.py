"""Memory update queue with debounce mechanism."""

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from deerflow.config.app_config import AppConfig

logger = logging.getLogger(__name__)


# Module-level config pointer set by the middleware that owns the queue.
# The queue runs on a background Timer thread where ``Runtime`` and FastAPI
# request context are not accessible; the enqueuer (which does have runtime
# context) is responsible for plumbing ``AppConfig`` through ``add()``.


@dataclass
class ConversationContext:
    """Context for a conversation to be processed for memory update."""

    thread_id: str
    messages: list[Any]
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    agent_name: str | None = None
    user_id: str | None = None
    correction_detected: bool = False
    reinforcement_detected: bool = False


class MemoryUpdateQueue:
    """Queue for memory updates with debounce mechanism.

    This queue collects conversation contexts and processes them after
    a configurable debounce period. Multiple conversations received within
    the debounce window are batched together.

    The queue captures an ``AppConfig`` reference at construction time and
    reuses it for the MemoryUpdater it spawns. Callers must construct a
    fresh queue when the config changes rather than reaching into a global.
    """

    def __init__(self, app_config: AppConfig):
        """Initialize the memory update queue.

        Args:
            app_config: Application config. The queue reads its own
                ``memory`` section for debounce timing and hands the full
                config to :class:`MemoryUpdater`.
        """
        self._app_config = app_config
        self._queue: list[ConversationContext] = []
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._processing = False

    def add(
        self,
        thread_id: str,
        messages: list[Any],
        agent_name: str | None = None,
        user_id: str | None = None,
        correction_detected: bool = False,
        reinforcement_detected: bool = False,
    ) -> None:
        """Add a conversation to the update queue."""
        config = self._app_config.memory
        if not config.enabled:
            return

        with self._lock:
            existing_context = next(
                (context for context in self._queue if context.thread_id == thread_id),
                None,
            )
            merged_correction_detected = correction_detected or (existing_context.correction_detected if existing_context is not None else False)
            merged_reinforcement_detected = reinforcement_detected or (existing_context.reinforcement_detected if existing_context is not None else False)
            context = ConversationContext(
                thread_id=thread_id,
                messages=messages,
                agent_name=agent_name,
                user_id=user_id,
                correction_detected=merged_correction_detected,
                reinforcement_detected=merged_reinforcement_detected,
            )

            # Check if this thread already has a pending update
            # If so, replace it with the newer one
            self._queue = [c for c in self._queue if c.thread_id != thread_id]
            self._queue.append(context)

            # Reset or start the debounce timer
            self._reset_timer()

        logger.info("Memory update queued for thread %s, queue size: %d", thread_id, len(self._queue))

    def _reset_timer(self) -> None:
        """Reset the debounce timer."""
        config = self._app_config.memory

        # Cancel existing timer if any
        if self._timer is not None:
            self._timer.cancel()

        # Start new timer
        self._timer = threading.Timer(
            config.debounce_seconds,
            self._process_queue,
        )
        self._timer.daemon = True
        self._timer.start()

        logger.debug("Memory update timer set for %ss", config.debounce_seconds)

    def _process_queue(self) -> None:
        """Process all queued conversation contexts."""
        # Import here to avoid circular dependency
        from deerflow.agents.memory.updater import MemoryUpdater

        with self._lock:
            if self._processing:
                # Already processing, reschedule
                self._reset_timer()
                return

            if not self._queue:
                return

            self._processing = True
            contexts_to_process = self._queue.copy()
            self._queue.clear()
            self._timer = None

        logger.info("Processing %d queued memory updates", len(contexts_to_process))

        try:
            updater = MemoryUpdater(self._app_config)

            for context in contexts_to_process:
                try:
                    logger.info("Updating memory for thread %s", context.thread_id)
                    success = updater.update_memory(
                        messages=context.messages,
                        thread_id=context.thread_id,
                        agent_name=context.agent_name,
                        correction_detected=context.correction_detected,
                        reinforcement_detected=context.reinforcement_detected,
                        user_id=context.user_id,
                    )
                    if success:
                        logger.info("Memory updated successfully for thread %s", context.thread_id)
                    else:
                        logger.warning("Memory update skipped/failed for thread %s", context.thread_id)
                except Exception as e:
                    logger.error("Error updating memory for thread %s: %s", context.thread_id, e)

                # Small delay between updates to avoid rate limiting
                if len(contexts_to_process) > 1:
                    time.sleep(0.5)

        finally:
            with self._lock:
                self._processing = False

    def flush(self) -> None:
        """Force immediate processing of the queue.

        This is useful for testing or graceful shutdown.
        """
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

        self._process_queue()

    def clear(self) -> None:
        """Clear the queue without processing.

        This is useful for testing.
        """
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._queue.clear()
            self._processing = False

    @property
    def pending_count(self) -> int:
        """Get the number of pending updates."""
        with self._lock:
            return len(self._queue)

    @property
    def is_processing(self) -> bool:
        """Check if the queue is currently being processed."""
        with self._lock:
            return self._processing


# Queues keyed by ``id(AppConfig)`` so tests and multi-client setups with
# distinct configs do not share a debounce queue.
_memory_queues: dict[int, MemoryUpdateQueue] = {}
_queue_lock = threading.Lock()


def get_memory_queue(app_config: AppConfig) -> MemoryUpdateQueue:
    """Get or create the memory update queue for the given app config."""
    key = id(app_config)
    with _queue_lock:
        queue = _memory_queues.get(key)
        if queue is None:
            queue = MemoryUpdateQueue(app_config)
            _memory_queues[key] = queue
        return queue


def reset_memory_queue(app_config: AppConfig | None = None) -> None:
    """Reset memory queue(s).

    Pass an ``app_config`` to reset only its queue, or omit to reset all
    (useful at test teardown).
    """
    with _queue_lock:
        if app_config is not None:
            queue = _memory_queues.pop(id(app_config), None)
            if queue is not None:
                queue.clear()
            return
        for queue in _memory_queues.values():
            queue.clear()
        _memory_queues.clear()
