"""Durable persistence of JSON files: atomic replacement and setting aside unreadable ones."""

from datetime import datetime
import json
import os
from pathlib import Path
import tempfile

from constants import UNREADABLE_FILE_STAMP_FORMAT, UNREADABLE_FILE_SUFFIX


def write_json_atomic(path: Path, value: object) -> None:
    """Flush a sibling temporary file before replacing the destination."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def set_aside_unreadable(path: Path) -> Path:
    """Rename an unreadable file next to itself, keeping it for diagnosis.

    Errors are propagated: each caller decides whether a failure is fatal.

    Returns:
        New path of the set-aside file
    """
    stamp = datetime.now().strftime(UNREADABLE_FILE_STAMP_FORMAT)
    target = path.with_name(f"{path.name}{UNREADABLE_FILE_SUFFIX}{stamp}")
    path.rename(target)
    return target
