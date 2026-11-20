"""Console output shared by the maintenance scripts of this directory."""

from constants import ICON_ERROR, ICON_INFO, ICON_SUCCESS, ICON_WARNING

_LEVEL_ICONS: dict[str, str] = {
    "INFO": ICON_INFO,
    "SUCCESS": ICON_SUCCESS,
    "ERROR": ICON_ERROR,
    "WARNING": ICON_WARNING,
}
_DEFAULT_ICON = "•"


def print_status(message: str, level: str = "INFO") -> None:
    """Print a message prefixed with the icon of its level."""
    print(f"{_LEVEL_ICONS.get(level, _DEFAULT_ICON)} {message}")
