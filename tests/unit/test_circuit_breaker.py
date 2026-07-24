"""Tests for the CircuitBreaker state machine."""

from unittest.mock import AsyncMock

import pytest

from cortex.llm.circuit_breaker import CircuitBreaker, CircuitOpenError, CircuitState
import asyncio


class TestCircuitState:
    """Enum correctness."""

    def test_states(self):
        assert CircuitState.CLOSED.value == "closed"
        assert CircuitState.OPEN.value == "open"
        assert CircuitState.HALF_OPEN.value == "half_open"


class TestCircuitOpenError:
    """Exception carries opened_at and retry_after."""

    def test_attributes(self):
        err = CircuitOpenError(opened_at=100.0, retry_after=30.0)
        assert err.opened_at == 100.0
        assert err.retry_after == 30.0

    def test_str_representation(self):
        err = CircuitOpenError(opened_at=100.0, retry_after=30.0)
        msg = str(err)
        assert "100.0" in msg
        assert "30.0" in msg


class TestCircuitBreakerDefaults:
    """Default constructor values."""

    def test_defaults(self):
        cb = CircuitBreaker()
        assert cb.failure_threshold == 5
        assert cb.recovery_timeout == 30.0
        assert cb.half_open_successes == 3
        assert cb.state == CircuitState.CLOSED


class TestCircuitBreakerClosedToOpen:
    """CLOSED → OPEN after failure_threshold failures in failure_window."""

    @pytest.mark.asyncio
    async def test_stays_closed_on_success(self):
        """Success does not transition to OPEN."""
        cb = CircuitBreaker(failure_threshold=3, _time=lambda: 0.0)
        assert cb.state == CircuitState.CLOSED

        mock_coro = AsyncMock(return_value="ok")
        result = await cb.call(mock_coro())
        assert result == "ok"
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_opens_after_threshold_failures(self):
        """After failure_threshold failures in window → OPEN."""
        cb = CircuitBreaker(failure_threshold=3, _time=lambda: 0.0)
        assert cb.state == CircuitState.CLOSED

        mock_coro = AsyncMock(side_effect=ValueError("fail"))
        for _ in range(3):
            with pytest.raises(ValueError):
                await cb.call(mock_coro())

        assert cb.state == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_raises_circuit_open_error(self):
        """In OPEN state, call raises CircuitOpenError with retry_after."""
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=30.0, _time=lambda: 0.0)

        mock_coro = AsyncMock(side_effect=ValueError("fail"))
        for _ in range(3):
            with pytest.raises(ValueError):
                await cb.call(mock_coro())

        assert cb.state == CircuitState.OPEN

        with pytest.raises(CircuitOpenError) as exc_info:
            await cb.call(AsyncMock()())

        assert exc_info.value.opened_at == 0.0
        assert exc_info.value.retry_after == 30.0

    @pytest.mark.asyncio
    async def test_old_failures_pruned(self):
        """Failures outside the window are pruned — resets the count."""
        cb = CircuitBreaker(failure_threshold=2, failure_window=60.0, _time=lambda: _time_val)

        _time_val = 0.0
        mock_coro = AsyncMock(side_effect=ValueError("fail"))

        # First failure at t=0
        with pytest.raises(ValueError):
            await cb.call(mock_coro())

        _time_val = 70.0  # outside 60s window

        # Second failure at t=70 — old failure pruned, count=1 not >=2
        with pytest.raises(ValueError):
            await cb.call(mock_coro())
        assert cb.state == CircuitState.CLOSED

        # Third failure at t=70.0 (still the same instant, second stays)
        with pytest.raises(ValueError):
            await cb.call(mock_coro())
        assert cb.state == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_success_resets_failure_count(self):
        """A success in CLOSED resets the failure count."""
        cb = CircuitBreaker(failure_threshold=3, _time=lambda: 0.0)

        mock_fail = AsyncMock(side_effect=ValueError("fail"))
        with pytest.raises(ValueError):
            await cb.call(mock_fail())
        with pytest.raises(ValueError):
            await cb.call(mock_fail())

        # Now a success — resets count
        mock_ok = AsyncMock(return_value="ok")
        result = await cb.call(mock_ok())
        assert result == "ok"

        # Two more failures should NOT trip (count reset to 0)
        with pytest.raises(ValueError):
            await cb.call(mock_fail())
        with pytest.raises(ValueError):
            await cb.call(mock_fail())
        assert cb.state == CircuitState.CLOSED

        # Third failure trips since count is now 3
        with pytest.raises(ValueError):
            await cb.call(mock_fail())
        assert cb.state == CircuitState.OPEN


class TestCircuitBreakerOpenToHalfOpen:
    """OPEN → HALF_OPEN after recovery_timeout."""

    @pytest.mark.asyncio
    async def test_transitions_to_half_open_after_timeout(self):
        """After recovery_timeout, state becomes HALF_OPEN."""
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=30.0, _time=lambda: _time_val)

        _time_val = 0.0
        mock_fail = AsyncMock(side_effect=ValueError("fail"))
        for _ in range(2):
            with pytest.raises(ValueError):
                await cb.call(mock_fail())

        assert cb.state == CircuitState.OPEN

        # Advance clock past recovery_timeout, then call — should transition to HALF_OPEN and pass through
        _time_val = 31.0
        mock_ok = AsyncMock(return_value="recovered")
        result = await cb.call(mock_ok())
        assert result == "recovered"
        assert cb.state == CircuitState.HALF_OPEN

    @pytest.mark.asyncio
    async def test_stays_open_before_timeout(self):
        """Calling before recovery_timeout passes still raises CircuitOpenError."""
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=30.0, _time=lambda: _time_val)

        _time_val = 0.0
        mock_fail = AsyncMock(side_effect=ValueError("fail"))
        for _ in range(2):
            with pytest.raises(ValueError):
                await cb.call(mock_fail())
        assert cb.state == CircuitState.OPEN

        _time_val = 10.0  # only 10s elapsed, still < 30
        with pytest.raises(CircuitOpenError):
            await cb.call(AsyncMock()())

        assert cb.state == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_retry_after_updates_on_reopen(self):
        """When HALF_OPEN fails and re-opens, the timer resets."""
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=30.0, _time=lambda: _time_val)

        _time_val = 0.0
        mock_fail = AsyncMock(side_effect=ValueError("fail"))
        for _ in range(2):
            with pytest.raises(ValueError):
                await cb.call(mock_fail())
        assert cb.state == CircuitState.OPEN

        # Transition to HALF_OPEN
        _time_val = 31.0
        with pytest.raises(ValueError):
            await cb.call(mock_fail())
        # HALF_OPEN failure → back to OPEN, timer reset to now (31.0)
        assert cb.state == CircuitState.OPEN

        # At 60.0 (only 29s since re-open), should still raise CircuitOpenError
        _time_val = 60.0
        with pytest.raises(CircuitOpenError):
            await cb.call(AsyncMock()())
        assert cb.state == CircuitState.OPEN

        # At 62.0 (31s since re-open), transitions to HALF_OPEN
        _time_val = 62.0
        mock_ok = AsyncMock(return_value="ok")
        result = await cb.call(mock_ok())
        assert result == "ok"
        assert cb.state == CircuitState.HALF_OPEN


class TestCircuitBreakerHalfOpenToClosed:
    """HALF_OPEN → CLOSED after half_open_successes successes."""

    @pytest.mark.asyncio
    async def test_closes_after_required_successes(self):
        """half_open_successes successes in HALF_OPEN → CLOSED."""
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=30.0, half_open_successes=3, _time=lambda: _time_val)

        _time_val = 0.0
        mock_fail = AsyncMock(side_effect=ValueError("fail"))
        for _ in range(2):
            with pytest.raises(ValueError):
                await cb.call(mock_fail())

        # Move to HALF_OPEN
        _time_val = 31.0
        # First success
        result = await cb.call(AsyncMock(return_value="ok1")())
        assert result == "ok1"
        assert cb.state == CircuitState.HALF_OPEN

        # Second success
        _time_val = 32.0
        result = await cb.call(AsyncMock(return_value="ok2")())
        assert result == "ok2"
        assert cb.state == CircuitState.HALF_OPEN

        # Third success → CLOSED
        _time_val = 33.0
        result = await cb.call(AsyncMock(return_value="ok3")())
        assert result == "ok3"
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_half_open_failure_opens_again(self):
        """A failure in HALF_OPEN → OPEN, resetting timer."""
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=30.0, half_open_successes=3, _time=lambda: _time_val)

        _time_val = 0.0
        mock_fail = AsyncMock(side_effect=ValueError("fail"))
        for _ in range(2):
            with pytest.raises(ValueError):
                await cb.call(mock_fail())
        assert cb.state == CircuitState.OPEN

        _time_val = 31.0
        # HALF_OPEN, one success
        result = await cb.call(AsyncMock(return_value="ok1")())
        assert result == "ok1"
        assert cb.state == CircuitState.HALF_OPEN

        # Then a failure → back to OPEN
        _time_val = 32.0
        with pytest.raises(ValueError):
            await cb.call(mock_fail())
        assert cb.state == CircuitState.OPEN

        # Check timer was reset (need another 30s)
        _time_val = 33.0
        with pytest.raises(CircuitOpenError):
            await cb.call(AsyncMock()())

        _time_val = 63.0
        result = await cb.call(AsyncMock(return_value="ok_after_reopen")())
        assert result == "ok_after_reopen"
        assert cb.state == CircuitState.HALF_OPEN


class TestCircuitBreakerConcurrency:
    """Async safety with concurrent calls."""

    @pytest.mark.asyncio
    async def test_lock_prevents_race(self):
        """Concurrent access to state is safe (basic smoke test)."""

        cb = CircuitBreaker(failure_threshold=2, _time=lambda: 0.0)
        mock_fail = AsyncMock(side_effect=ValueError("fail"))

        exceptions = []

        async def fail_call():
            try:
                await cb.call(mock_fail())
            except (ValueError, CircuitOpenError):
                exceptions.append(True)

        # Fire 4 concurrent failing calls
        await asyncio.gather(fail_call(), fail_call(), fail_call(), fail_call())
        # After at least 2 failures, the breaker should be OPEN
        assert cb.state == CircuitState.OPEN
        assert len(exceptions) == 4
