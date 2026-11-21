"""Data models for synchronization results and configuration."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class SyncStatus(Enum):
    """Status of a mod synchronization."""

    CREATED = "created"
    UPDATED = "updated"
    UNCHANGED = "unchanged"
    ERROR = "error"
    SKIPPED = "skipped"


@dataclass
class ModSyncResult:
    """Result of synchronizing a single mod."""

    tp2_name: str
    status: SyncStatus
    message: str = ""
    sources_used: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        icon = {
            SyncStatus.CREATED: "✓",
            SyncStatus.UPDATED: "✓",
            SyncStatus.UNCHANGED: "→",
            SyncStatus.ERROR: "✗",
            SyncStatus.SKIPPED: "⚠",
        }[self.status]
        return f"{icon} {self.tp2_name}: {self.status.value} {self.message}"


@dataclass
class SyncStats:
    """Statistics from synchronization run."""

    total_lcc_mods: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0
    errors: int = 0
    results: list[ModSyncResult] = field(default_factory=list)
    github_outdated: list[tuple[str, str, str]] = field(
        default_factory=list
    )  # (tp2, current, latest)

    def add_result(self, result: ModSyncResult) -> None:
        """Add a mod result and update counters."""
        self.results.append(result)
        match result.status:
            case SyncStatus.CREATED:
                self.created += 1
            case SyncStatus.UPDATED:
                self.updated += 1
            case SyncStatus.UNCHANGED:
                self.unchanged += 1
            case SyncStatus.SKIPPED:
                self.skipped += 1
            case SyncStatus.ERROR:
                self.errors += 1

    def get_summary(self) -> str:
        """Get formatted summary."""
        return f"""
=== Synchronization Summary ===
Total LCC mods: {self.total_lcc_mods}
✓ Created: {self.created}
✓ Updated: {self.updated}
→ Unchanged: {self.unchanged}
⚠ Skipped: {self.skipped}
✗ Errors: {self.errors}
"""

    def get_github_report(self) -> str:
        """Get GitHub outdated mods report."""
        if not self.github_outdated:
            return ""

        report = "\n=== Outdated GitHub Mods ===\n"
        for tp2, current, latest in self.github_outdated:
            report += f"  {tp2}: {current} → {latest}\n"
        return report


@dataclass(frozen=True)
class ModFilter:
    """Explicit selection of the mods a synchronization run may touch.

    An empty criterion selects everything, so the default filter keeps
    every mod. Identifiers are compared case-insensitively.
    """

    tp2_names: frozenset[str] = frozenset()
    games: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        object.__setattr__(self, "tp2_names", frozenset(name.lower() for name in self.tp2_names))
        object.__setattr__(self, "games", frozenset(game.lower() for game in self.games))

    def skip_reason(self, tp2_name: str, supported_games: Iterable[str]) -> str | None:
        """Return why a mod is excluded by the filter, or None when it is selected."""
        if self.tp2_names and tp2_name.lower() not in self.tp2_names:
            return "not selected (--only)"

        if self.games and not self.games & {game.lower() for game in supported_games}:
            return "no selected game supported (--game)"

        return None


@dataclass
class SyncConfig:
    """Configuration for mod synchronization."""

    definitions_dir: Path
    archives_dir: Path | None = None
    mods_dir: Path | None = None
    force_lcc_refresh: bool = False
    github_token: str | None = None

    # Actions
    create_new: bool = True
    update_existing: bool = True
    extract_tp2: bool = True
    extract_ini: bool = True
    extract_archives: bool = False
    calculate_hashes: bool = False
    check_github_versions: bool = False

    mod_filter: ModFilter = field(default_factory=ModFilter)

    def __post_init__(self) -> None:
        """Convert paths to Path objects."""
        self.definitions_dir = Path(self.definitions_dir)
        if self.archives_dir:
            self.archives_dir = Path(self.archives_dir)
        if self.mods_dir:
            self.mods_dir = Path(self.mods_dir)
