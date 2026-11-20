PAUSE_PREFIX = "pause"


class PauseEntry:
    """Installation pause entry with optional description."""

    _counter = 0

    def __init__(self, description: str = ""):
        PauseEntry._counter += 1

        self.pause_id = f"{PAUSE_PREFIX}:{PauseEntry._counter}"
        self.description = description

    @staticmethod
    def is_pause(component_id: str) -> bool:
        return component_id.startswith(f"{PAUSE_PREFIX}:")

    @staticmethod
    def parse(component_id: str) -> tuple[str, str]:
        """Parse pause entry format: pause:id::description

        Returns:
            Tuple of (pause_id, description)
        """
        if "::" in component_id:
            pause_id, description = component_id.split("::", 1)
            return pause_id, description
        return component_id, ""

    @staticmethod
    def reset_counter() -> None:
        PauseEntry._counter = 0

    def __str__(self) -> str:
        if self.description:
            return f"{self.pause_id}::{self.description}"
        return self.pause_id
