"""Circuit Breaker — State machine for fast-failing on repeated LLM failures.

Wraps an async LLM call (``LLMClient.chat()``) with a configurable state machine:

    CLOSED → OPEN → HALF_OPEN → CLOSED

Usage::

    cb = CircuitBreaker()
    result = await cb.call(client.chat(messages))
"""

from __future__ import annotations

import enum
import time
from asyncio import Lock
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


class CircuitState(enum.Enum):
    """Current state of the circuit breaker."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """Raised when a request is rejected because the circuit is OPEN.

    Args:
        opened_at: Time (monotonic) when the circuit entered OPEN state.
        retry_after: Seconds a caller should wait before retrying.
    """

    def __init__(self, opened_at: float, retry_after: float) -> None:
        self.opened_at = opened_at
        self.retry_after = retry_after
        super().__init__(f"Circuit is OPEN (opened_at={opened_at}, retry_after={retry_after})")


class CircuitBreaker:
    """Async circuit breaker protecting LLM calls from cascading failures.
    All state transitions are serialized via ``asyncio.Lock``. The
    ``state`` property read is not independently locked — may see a stale
    value during a concurrent transition (acceptable for monitoring/logging).

    Parameters
    ----------
    failure_threshold:
        Consecutive failures within ``failure_window`` seconds to trip OPEN.
    recovery_timeout:
        Seconds to stay OPEN before transitioning to HALF_OPEN.
    half_open_successes:
        Successful calls in HALF_OPEN to transition back to CLOSED.
    failure_window:
        Sliding window in seconds for counting failures.
    _time:
        Injectable clock (default ``time.monotonic``) — used for deterministic
        testing.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        half_open_successes: int = 3,
        failure_window: float = 60.0,
        _time: Callable[[], float] | None = None,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_successes = half_open_successes
        self.failure_window = failure_window
        self._time = _time or time.monotonic

        self._lock = Lock()
        self._state = CircuitState.CLOSED

        # Failure tracking (CLOSED state)
        self._failure_timestamps: list[float] = []

        # Half-open tracking
        self._half_open_success_count = 0

        # Open state timer
        self._opened_at: float | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def state(self) -> CircuitState:
        """Current state of the circuit breaker. Read is not independently locked — may see a stale value during a concurrent transition."""
        return self._state

    async def call(self, coro: Awaitable[T]) -> T:
        """Execute ``coro`` through the circuit breaker state machine.

        Args:
            coro: An awaitable (typically ``client.chat(…)``).

        Returns:
            The coroutine's result on success.

        Raises:
            CircuitOpenError: If the circuit is OPEN and has not elapsed
                ``recovery_timeout``.
            Exception: The underlying coroutine's exception (re-raised).
        """
        async with self._lock:
            self._prune_old_failures()

            if self._state is CircuitState.OPEN:
                elapsed = self._time() - self._opened_at
                if elapsed < self.recovery_timeout:
                    raise CircuitOpenError(
                        opened_at=self._opened_at,
                        retry_after=self.recovery_timeout - elapsed,
                    )
                # Timeout elapsed → transition to HALF_OPEN
                self._state = CircuitState.HALF_OPEN
                self._half_open_success_count = 0

            if self._state is CircuitState.HALF_OPEN:
                return await self._handle_half_open(coro)

            # CLOSED (default / fall-through)
            return await self._handle_closed(coro)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _handle_closed(self, coro: Awaitable[T]) -> T:
        """Execute in CLOSED state — pass through, track failures."""
        try:
            result = await coro
        except Exception:
            self._failure_timestamps.append(self._time())
            if len(self._failure_timestamps) >= self.failure_threshold:
                self._trip_open()
            raise

        # Success — reset failure count
        self._failure_timestamps.clear()
        return result

    async def _handle_half_open(self, coro: Awaitable[T]) -> T:
        """Execute in HALF_OPEN state — trial request."""
        try:
            result = await coro
        except Exception:
            # Failure → back to OPEN, timer resets
            self._trip_open()
            raise

        # Success → increment
        self._half_open_success_count += 1
        if self._half_open_success_count >= self.half_open_successes:
            self._reset_closed()
        return result

    def _trip_open(self) -> None:
        """Transition to OPEN state."""
        self._state = CircuitState.OPEN
        self._opened_at = self._time()
        self._failure_timestamps.clear()
        self._half_open_success_count = 0

    def _reset_closed(self) -> None:
        """Transition to CLOSED state."""
        self._state = CircuitState.CLOSED
        self._failure_timestamps.clear()
        self._half_open_success_count = 0
        self._opened_at = None

    def _prune_old_failures(self) -> None:
        """Remove failure timestamps older than ``failure_window``."""
        now = self._time()
        cutoff = now - self.failure_window
        self._failure_timestamps = [t for t in self._failure_timestamps if t > cutoff]
