"""Processors for specialized data handling.

This package contains processors for:
- GitHubProcessor: GitHub API interaction, download link resolution, version checking
"""

from util.mod_sync.processors.github_processor import GitHubProcessor, GitHubRelease

__all__ = [
    "GitHubProcessor",
    "GitHubRelease",
]
