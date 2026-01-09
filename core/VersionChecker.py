"""
Version checker for GitHub releases.
Implements caching to avoid rate limiting.
"""

from dataclasses import dataclass
import json
import logging
import time

import requests

from constants import (
    APP_UPDATE_CHECK_FILE,
    APP_VERSION,
    CACHE_DIR,
    DOWNLOAD_TIMEOUT,
    GITHUB_API_BASE,
    GITHUB_REPO_NAME,
    GITHUB_REPO_OWNER,
)

logger = logging.getLogger(__name__)


@dataclass
class VersionInfo:
    """Information about a version."""

    version: str
    release_url: str
    published_at: str
    is_newer: bool


class VersionChecker:
    """Checks for new application versions on GitHub."""

    def __init__(self):
        """Initialize version checker."""
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self._cache_file = APP_UPDATE_CHECK_FILE

    def check_for_update(self) -> VersionInfo | None:
        """
        Check if a new version is available on GitHub.

        Returns:
            VersionInfo if update available, None otherwise
        """
        try:
            version_info = self._fetch_latest_release()
            self._update_cache(version_info)
            return version_info
        except Exception as e:
            logger.warning(f"Failed to check for updates: {e}")
            return self._get_cached_version_info()

    def _fetch_latest_release(self) -> VersionInfo | None:
        """
        Fetch latest release info from GitHub API.

        Returns:
            VersionInfo if successful, None otherwise
        """
        url = f"{GITHUB_API_BASE}/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}/releases/latest"

        try:
            response = requests.get(
                url,
                timeout=DOWNLOAD_TIMEOUT,
                headers={"Accept": "application/vnd.github.v3+json"},
            )
            response.raise_for_status()

            data = response.json()
            latest_version = data["tag_name"].lstrip("v")

            version_info = VersionInfo(
                version=latest_version,
                release_url=data["html_url"],
                published_at=data["published_at"],
                is_newer=self._is_newer_version(latest_version, APP_VERSION),
            )

            logger.info(f"Latest version: {latest_version}, Current: {APP_VERSION}")
            return version_info

        except requests.RequestException as e:
            logger.error(f"GitHub API request failed: {e}")
            return None
        except (KeyError, ValueError) as e:
            logger.error(f"Failed to parse GitHub response: {e}")
            return None

    @staticmethod
    def _is_newer_version(latest: str, current: str) -> bool:
        """
        Compare version strings with support for pre-release suffixes.

        Supports formats:
        - X.Y.Z (e.g., 1.2.3)
        - X.Y.Z-suffix (e.g., 1.2.3-beta, 0.9.0-alpha)

        Pre-release versions are considered older than release versions:
        1.0.0-beta < 1.0.0 < 1.0.1-alpha < 1.0.1

        Args:
            latest: Latest version string
            current: Current version string

        Returns:
            True if latest is newer than current
        """
        try:
            # Parse version with optional pre-release suffix
            def parse_version(version_str):
                # Split on '-' to separate version from suffix
                parts = version_str.split("-", 1)
                version_part = parts[0]
                suffix = parts[1].lower() if len(parts) > 1 else None

                # Parse numeric version parts
                version_numbers = [int(x) for x in version_part.split(".")]

                # Define suffix priority (lower = older)
                # No suffix (release) = highest priority
                suffix_priority = {
                    "alpha": 1,
                    "beta": 2,
                    "rc": 3,
                    None: 4,  # Release version
                }

                # Get base suffix type (e.g., 'beta' from 'beta2')
                suffix_type = None
                suffix_number = 0
                if suffix:
                    # Try to extract number from suffix (e.g., 'beta2' -> 'beta', 2)
                    import re

                    match = re.match(r"([a-z]+)(\d+)?", suffix)
                    if match:
                        suffix_type = match.group(1)
                        suffix_number = int(match.group(2)) if match.group(2) else 0

                priority = suffix_priority.get(suffix_type, 0)

                return version_numbers, priority, suffix_number

            latest_nums, latest_priority, latest_suffix_num = parse_version(latest)
            current_nums, current_priority, current_suffix_num = parse_version(current)

            # Pad version numbers with zeros if needed
            max_len = max(len(latest_nums), len(current_nums))
            latest_nums.extend([0] * (max_len - len(latest_nums)))
            current_nums.extend([0] * (max_len - len(current_nums)))

            # Compare: first by version numbers, then by suffix priority, then by suffix number
            if latest_nums != current_nums:
                return latest_nums > current_nums

            if latest_priority != current_priority:
                return latest_priority > current_priority

            # Same version and suffix type, compare suffix numbers
            return latest_suffix_num > current_suffix_num

        except (ValueError, AttributeError) as e:
            logger.warning(f"Failed to compare versions: {latest} vs {current} - {e}")
            return False

    def _update_cache(self, version_info: VersionInfo | None) -> None:
        """
        Update cache file with latest check information.

        Args:
            version_info: Version information to cache
        """
        try:
            cache_data = {
                "last_check": time.time(),
                "version_info": {
                    "version": version_info.version,
                    "release_url": version_info.release_url,
                    "published_at": version_info.published_at,
                    "is_newer": version_info.is_newer,
                }
                if version_info
                else None,
            }

            with open(self._cache_file, "w", encoding="utf-8") as f:
                json.dump(cache_data, f, indent=2)

        except Exception as e:
            logger.warning(f"Failed to update version cache: {e}")

    def _get_cached_version_info(self) -> VersionInfo | None:
        """
        Get version info from cache.

        Returns:
            Cached VersionInfo if available, None otherwise
        """
        try:
            if not self._cache_file.exists():
                return None

            with open(self._cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                info = data.get("version_info")

                if info:
                    return VersionInfo(**info)

        except Exception as e:
            logger.warning(f"Failed to read cached version info: {e}")

        return None
