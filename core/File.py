import logging
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)

# ------------------------------
#         CONSTANTS
# ------------------------------

# Default encoding attempts (ordered by frequency in mod files)
DEFAULT_ENCODINGS: List[str] = [
    "utf-8",  # Most common modern encoding
    "utf-8-sig",  # UTF-8 with BOM (common in Windows files)
    "cp1252",  # Windows Western European
    "latin-1",  # ISO-8859-1
]


def safe_read(
        path: Path | str,
        encodings: list[str] | None = None,
) -> str:
    """Read a text file safely with automatic encoding detection.

    This function attempts to read a file using multiple encodings.

    Args:
        path: Path to file to read
        encodings: List of encodings to try (uses DEFAULT_ENCODINGS if None)

    Returns:
        The file content

    Raises:
        FileNotFoundError: If file doesn't exist
    """
    path = Path(path)
    encodings = encodings or DEFAULT_ENCODINGS

    logger.debug(f"Attempting to read file: {path}")

    # Check file existence
    if not path.exists():
        error_msg = f"File not found: {path}"
        logger.error(error_msg)
        raise FileNotFoundError(error_msg)

    # Try each encoding
    for encoding in encodings:
        try:
            logger.debug(f"Trying encoding: {encoding}")
            with open(path, "r", encoding=encoding) as f:
                return f.read()
        except UnicodeDecodeError:
            continue

    logger.warning(
        f"Read {path.name} with fallback"
    )

    with open(path, "rb") as f:
        return f.read().decode("utf-8", errors="replace")
