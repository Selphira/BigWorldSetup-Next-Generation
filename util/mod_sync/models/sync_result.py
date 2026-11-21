"""Data models for synchronization results and configuration."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from constants import MOD_SYNC_GITHUB_CHECK_INTERVAL_HOURS, MOD_SYNC_GITHUB_CHECKS_FILE


class SyncStatus(Enum):
    """Status of a mod synchronization."""

    CREATED = "created"
    UPDATED = "updated"
    UNCHANGED = "unchanged"
    ERROR = "error"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class ComponentChanges:
    """Component differences a TP2 merge brings to a stored definition.

    Keys are installable components: a MUC group stands for its choices,
    since its ``choice_N`` key only reflects its rank in the TP2.
    """

    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    kept_subs: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.added or self.removed or self.kept_subs)

    def describe(self) -> str:
        """Return a one-line report of the changes to review."""
        sections = [
            ("added", self.added),
            ("removed", self.removed),
            ("SUB kept outside TP2", self.kept_subs),
        ]
        return "; ".join(
            f"components {label}: {', '.join(keys)}" for label, keys in sections if keys
        )


@dataclass
class ModSyncResult:
    """Result of synchronizing a single mod."""

    tp2_name: str
    status: SyncStatus
    message: str = ""
    sources_used: list[str] = field(default_factory=list)
    component_changes: ComponentChanges = field(default_factory=ComponentChanges)

    def __str__(self) -> str:
        icon = {
            SyncStatus.CREATED: "✓",
            SyncStatus.UPDATED: "✓",
            SyncStatus.UNCHANGED: "→",
            SyncStatus.ERROR: "✗",
            SyncStatus.SKIPPED: "⚠",
        }[self.status]
        text = f"{icon} {self.tp2_name}: {self.status.value} {self.message}"
        if self.component_changes:
            text += f" [{self.component_changes.describe()}]"
        return text


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
    github_deferred: int = 0  # mods checked too recently
    github_rate_limited: bool = False

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

    def get_component_report(self) -> str:
        """Get the component changes to review, one line per mod."""
        changed = [result for result in self.results if result.component_changes]
        if not changed:
            return ""

        report = "\n=== Component Changes ===\n"
        for result in changed:
            report += f"  {result.tp2_name}: {result.component_changes.describe()}\n"
        return report

    def get_github_report(self) -> str:
        """Get the GitHub report: outdated mods and checks left for a later run."""
        report = ""
        if self.github_outdated:
            report += "\n=== Outdated GitHub Mods ===\n"
            for tp2, current, latest in self.github_outdated:
                report += f"  {tp2}: {current} → {latest}\n"

        if self.github_deferred:
            report += f"\nGitHub checks deferred (checked too recently): {self.github_deferred}\n"
        if self.github_rate_limited:
            report += "\nGitHub API rate limit reached: remaining mods were not checked\n"
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
    github_check_interval_hours: float = MOD_SYNC_GITHUB_CHECK_INTERVAL_HOURS
    github_checks_file: Path = MOD_SYNC_GITHUB_CHECKS_FILE

    mod_filter: ModFilter = field(default_factory=ModFilter)

    def __post_init__(self) -> None:
        """Convert paths to Path objects."""
        self.definitions_dir = Path(self.definitions_dir)
        self.github_checks_file = Path(self.github_checks_file)
        if self.archives_dir:
            self.archives_dir = Path(self.archives_dir)
        if self.mods_dir:
            self.mods_dir = Path(self.mods_dir)
