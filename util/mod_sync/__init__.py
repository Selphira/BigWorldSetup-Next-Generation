"""Mod synchronization package for BWS-EE.

This package provides tools to synchronize mod definitions from multiple sources:
- LCC (lcc-docs repository)
- TP2 files (WeiDU mod metadata)
- INI files (mod configuration)
- Archive files (for hash calculation)
- GitHub (for version checking and download links)

The synchronization follows strict priority rules for each field and can be
run either via GUI (PySide6) or CLI.

Usage:
    # GUI mode
    python -m util.mod_sync

    # CLI mode
    python -m util.mod_sync --cli --definitions-dir data/mods

    # CLI mode restricted to some mods and games
    python -m util.mod_sync --cli --only c#brage ascension --game bg2ee eet

See __main__.py for full CLI options.
"""

__version__ = "1.0.0"
__author__ = "BWS-EE Team"

from util.mod_sync.models.sync_result import (
    ModFilter,
    ModSyncResult,
    SyncConfig,
    SyncStats,
    SyncStatus,
)
from util.mod_sync.orchestrator import ModSyncOrchestrator

__all__ = [
    "ModFilter",
    "SyncConfig",
    "SyncStats",
    "ModSyncResult",
    "SyncStatus",
    "ModSyncOrchestrator",
]
