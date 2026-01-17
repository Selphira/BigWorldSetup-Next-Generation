import platform


class Platform:
    """Utility class for platform detection."""

    _current_platform: str | None = None

    @classmethod
    def get_current(cls) -> str:
        """Get current platform identifier."""
        if cls._current_platform is None:
            system = platform.system().lower()
            cls._current_platform = {
                "windows": "windows",
                "linux": "linux",
                "darwin": "macos",
            }.get(system, "windows")

        return cls._current_platform

    @classmethod
    def is_windows(cls) -> bool:
        """Check if running on Windows."""
        return cls.get_current() == "windows"

    @classmethod
    def is_linux(cls) -> bool:
        """Check if running on Linux."""
        return cls.get_current() == "linux"

    @classmethod
    def is_macos(cls) -> bool:
        """Check if running on macOS."""
        return cls.get_current() == "macos"
