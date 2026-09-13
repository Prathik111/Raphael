"""Thread-safe cancellation and deadline tokens for execution."""

from __future__ import annotations

import threading
import time


class CancellationToken:
    """Thread-safe cancellation flag shared with executor and sandbox."""

    def __init__(
        self, parent: CancellationToken | None = None, deadline: float | None = None
    ) -> None:
        self._event = threading.Event()
        self._parent = parent
        self._deadline = deadline

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return (
            self._event.is_set()
            or (self._parent is not None and self._parent.cancelled)
            or self.deadline_reached
        )

    @property
    def deadline(self) -> float | None:
        return self._deadline

    @property
    def deadline_reached(self) -> bool:
        return self._deadline is not None and time.monotonic() >= self._deadline

    def child(self, timeout_s: float | None = None) -> CancellationToken:
        """Create a child whose deadline cannot exceed its parent's deadline."""
        deadline = None
        if timeout_s is not None:
            deadline = time.monotonic() + max(timeout_s, 0.0)
        if self._deadline is not None:
            deadline = self._deadline if deadline is None else min(deadline, self._deadline)
        return CancellationToken(parent=self, deadline=deadline)
