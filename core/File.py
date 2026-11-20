import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# ------------------------------
#         CONSTANTS
# ------------------------------

# Encodings tried in order when reading mod text files. "utf-8-sig" also reads
# plain UTF-8 and drops a leading BOM; "cp1252" rejects a few bytes that
# "latin-1" accepts. "latin-1" decodes any byte, so it must stay last: it ends
# the search and no further fallback is needed.
TEXT_ENCODINGS: tuple[str, ...] = ("utf-8-sig", "cp1252", "latin-1")


def safe_read(path: Path | str) -> str:
    """Read a text file, trying each encoding of TEXT_ENCODINGS in order.

    Args:
        path: Path to file to read

    Returns:
        The file content

    Raises:
        FileNotFoundError: If file doesn't exist
    """
    path = Path(path)

    if not path.exists():
        error_msg = f"File not found: {path}"
        logger.error(error_msg)
        raise FileNotFoundError(error_msg)

    *attempts, last_resort = TEXT_ENCODINGS
    for encoding in attempts:
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            logger.debug(f"Could not decode {path.name} as {encoding}")

    return path.read_text(encoding=last_resort)


def format_size(bytes_count: int) -> str:
    """Format byte size for display."""
    for unit in ["B", "KB", "MB", "GB"]:
        if bytes_count < 1024.0:
            return f"{bytes_count:.1f} {unit}"
        bytes_count /= 1024.0
    return f"{bytes_count:.1f} TB"
