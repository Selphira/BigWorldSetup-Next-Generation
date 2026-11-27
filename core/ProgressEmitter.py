"""Progress percentage forwarded only when its integer value changes."""

from collections.abc import Callable


class ProgressEmitter:
    """
    Forward a progress percentage to a signal, dropping repeated values.

    Loops report each step with `report`; phase starts and terminal values go
    through `force`, which always emits. `restart` forgets the last value
    without emitting, so the first step of a new phase is never dropped.
    """

    def __init__(self, emit: Callable[[int], None]) -> None:
        self._emit = emit
        self._last_value: int | None = None

    def report(self, value: int) -> None:
        """Emit the value unless it equals the last emitted one."""
        if value != self._last_value:
            self.force(value)

    def force(self, value: int) -> None:
        """Emit the value, even if it equals the last emitted one."""
        self._last_value = value
        self._emit(value)

    def restart(self) -> None:
        """Start a new phase: its first reported value is always emitted."""
        self._last_value = None
