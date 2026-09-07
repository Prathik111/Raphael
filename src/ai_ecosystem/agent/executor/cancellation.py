"""Cooperative cancellation token (Gate 8).

Cancellation is cooperative: pending/ready work is never started after
cancellation, queued futures are cancelled where the pool allows, and
already-running Python threads run to completion (they cannot be killed).
The token records the request; the executor records the honest outcome.
"""

from __future__ import annotations

import threading


class CancellationToken:
    """Thread-safe cancellation flag shared with the executor."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        """Request cancellation (idempotent)."""
        self._event.set()

    @property
    def cancelled(self) -> bool:
        """True once cancel() has been called."""
        return self._event.is_set()
