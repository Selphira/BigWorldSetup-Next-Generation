"""Data sources for mod synchronization.

This package contains extractors for different data sources:
- LCCSource: LCC database (lcc-docs)
- TP2Source: WeiDU TP2 files
- IniSource: Mod INI configuration files
- ArchiveSource: Archive download and hash calculation
- GitHubSource: Latest GitHub release as file entries
"""

from util.mod_sync.sources.archive_source import ArchiveFile, ArchiveSource
from util.mod_sync.sources.github_source import GitHubSource, GitHubUpdate
from util.mod_sync.sources.ini_source import IniData, IniParser, IniSource
from util.mod_sync.sources.lcc_source import LCCSource
from util.mod_sync.sources.tp2_source import TP2Source

__all__ = [
    "LCCSource",
    "TP2Source",
    "IniSource",
    "IniData",
    "IniParser",
    "ArchiveSource",
    "ArchiveFile",
    "GitHubSource",
    "GitHubUpdate",
]
