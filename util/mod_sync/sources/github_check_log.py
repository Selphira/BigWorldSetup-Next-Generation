"""Dates of the last GitHub release check of each mod."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class GitHubCheckLog:
    """Remember when each mod was last checked on GitHub, to space the checks out.

    Only checks GitHub answered are recorded, so a mod refused by the rate
    limit stays due. The log is written after each record: an interrupted
    run keeps the checks already made.
    """

    def __init__(self, log_path: Path, interval: timedelta):
        """Initialize the log.

        Args:
            log_path: JSON file {tp2 (lowercase): ISO date of the last check}
            interval: Minimal delay between two checks of a mod (zero: always check)
        """
        self.log_path = Path(log_path)
        self.interval = interval
        self._checked_at = self._load()

    def is_due(self, tp2_name: str, now: datetime | None = None) -> bool:
        """Tell whether a mod may be checked again."""
        checked_at = self._checked_at.get(tp2_name.lower())
        if checked_at is None or self.interval <= timedelta(0):
            return True
        return (now or self._now()) - checked_at >= self.interval

    def record(self, tp2_name: str, now: datetime | None = None) -> None:
        """Record a check GitHub answered, and save the log."""
        self._checked_at[tp2_name.lower()] = now or self._now()
        self._save()

    def forget(self, tp2_name: str) -> None:
        """Make a mod due again, and save the log."""
        if self._checked_at.pop(tp2_name.lower(), None) is not None:
            self._save()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> dict[str, datetime]:
        try:
            stored = json.loads(self.log_path.read_text(encoding="utf-8"))
            return {
                tp2: datetime.fromisoformat(checked_at) for tp2, checked_at in stored.items()
            }
        except FileNotFoundError:
            return {}
        except (OSError, ValueError, TypeError, AttributeError) as e:
            logger.warning(
                f"Unreadable GitHub check log {self.log_path}, every mod is due: {e}"
            )
            return {}

    def _save(self) -> None:
        stored = {
            tp2: checked_at.isoformat() for tp2, checked_at in sorted(self._checked_at.items())
        }
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            partial_path = self.log_path.with_suffix(".part")
            partial_path.write_text(json.dumps(stored, indent=2), encoding="utf-8")
            partial_path.replace(self.log_path)
        except OSError as e:
            logger.error(f"Could not save GitHub check log {self.log_path}: {e}")

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)
