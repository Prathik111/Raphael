"""Circuit breaker: fail fast after repeated downstream failures.

States: CLOSED (calls pass) -> OPEN (calls rejected immediately) ->
HALF_OPEN (one probe call) -> CLOSED or back to OPEN. Adopted where a
flapping dependency must not stall the runtime; behavior without a
breaker is unchanged anywhere it is not installed.
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Callable, Optional

from ai_ecosystem.core.errors.exceptions import AiEcosystemError


class CircuitOpenError(AiEcosystemError):
    """Call rejected: the circuit is open (downstream presumed down)."""


class BreakerState(str, Enum):
    """Breaker positions."""

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreaker:
    """Threshold + timeout breaker around fallible callables."""

    def __init__(
        self,
        failure_threshold: int = 3,
        reset_timeout_s: float = 30.0,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        import threading

        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        if not reset_timeout_s >= 0:
            raise ValueError("reset_timeout_s must be >= 0")
        self._threshold = failure_threshold
        self._reset_timeout = reset_timeout_s
        self._clock = clock or time.monotonic
        self._state = BreakerState.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._lock = threading.Lock()
        self._half_open_in_flight = False

    @property
    def state(self) -> BreakerState:
        """Current position (OPEN auto-advances to HALF_OPEN on read)."""
        with self._lock:
            return self._state_unlocked()

    def _state_unlocked(self) -> BreakerState:
        if self._state is BreakerState.OPEN and (
                self._clock() - self._opened_at >= self._reset_timeout):
            self._state = BreakerState.HALF_OPEN
            self._half_open_in_flight = False
        return self._state

    def call(self, fn: Callable[[], object]) -> object:
        """Run fn through the breaker (raises CircuitOpenError when open).

        Exactly one caller probes in HALF_OPEN; the rest fail fast until
        the probe resolves -- no thundering herd.
        """
        with self._lock:
            state = self._state_unlocked()
            if state is BreakerState.OPEN:
                raise CircuitOpenError("circuit is open")
            if state is BreakerState.HALF_OPEN:
                if self._half_open_in_flight:
                    raise CircuitOpenError("circuit probe already in flight")
                self._half_open_in_flight = True
        try:
            result = fn()
        except CircuitOpenError:
            raise
        except Exception:
            self._record_failure()
            raise
        self._record_success()
        return result

    def _record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._state is BreakerState.HALF_OPEN or (
                    self._failures >= self._threshold):
                self._state = BreakerState.OPEN
                self._opened_at = self._clock()
                self._half_open_in_flight = False

    def _record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._state = BreakerState.CLOSED
            self._half_open_in_flight = False
