"""
Version checker for GitHub releases.
Implements caching to avoid rate limiting.
"""

from dataclasses import dataclass
import json
import logging
import re
import time

import requests

from constants import (
    APP_UPDATE_CHECK_FILE,
    APP_VERSION,
    DOWNLOAD_TIMEOUT,
    GITHUB_API_BASE,
    GITHUB_REPO_NAME,
    GITHUB_REPO_OWNER,
    VERSION_RELEASE_PRIORITY,
    VERSION_SUFFIX_PRIORITY,
)

logger = logging.getLogger(__name__)

_SUFFIX_PATTERN = re.compile(r"([a-z]+)(\d+)?")


def _version_key(version: str) -> tuple[tuple[int, ...], int, int]:
    """
    Build a sort key (numbers, suffix priority, suffix number) for "X.Y.Z[-suffixN]".

    Trailing zeros are dropped so that 1.0 and 1.0.0 compare equal.
    A suffix without a letter prefix ranks as a release, like the original parser.
    """
    numbers_part, _, suffix = version.partition("-")
    numbers = [int(part) for part in numbers_part.split(".")]
    while numbers and numbers[-1] == 0:
        numbers.pop()

    match = _SUFFIX_PATTERN.match(suffix.lower())
    if not match:
        return tuple(numbers), VERSION_RELEASE_PRIORITY, 0

    priority = VERSION_SUFFIX_PRIORITY.get(match.group(1), 0)
    return tuple(numbers), priority, int(match.group(2) or 0)


@dataclass
class VersionInfo:
    """Information about a version."""

    version: str
    release_url: str
    published_at: str
    is_newer: bool


class _ReleaseCheckFailed(Exception):
    """GitHub could not tell which release is the latest one."""


class VersionChecker:
    """Checks for new application versions on GitHub."""

    def __init__(self):
        """Initialize version checker."""
        self._cache_file = APP_UPDATE_CHECK_FILE

    def check_for_update(self) -> VersionInfo | None:
        """
        Check which release is the latest one on GitHub.

        A failed check falls back to the last known release without
        overwriting it, so that an outage does not erase what was learnt.

        Returns:
            Latest known release (see is_newer), None if none was ever fetched
        """
        try:
            version_info = self._fetch_latest_release()
        except _ReleaseCheckFailed as e:
            logger.warning(f"Failed to check for updates: {e}")
            return self._get_cached_version_info()

        self._update_cache(version_info)
        return version_info

    def _fetch_latest_release(self) -> VersionInfo:
        """
        Fetch latest release info from GitHub API.

        Returns:
            Latest release

        Raises:
            _ReleaseCheckFailed: If GitHub cannot be reached or its answer is unusable
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
        except (requests.RequestException, ValueError) as e:
            raise _ReleaseCheckFailed(f"GitHub API request failed: {e}") from e

        try:
            latest_version = data["tag_name"].lstrip("v")
            release_url = data["html_url"]
            published_at = data["published_at"]
        except (KeyError, TypeError, AttributeError) as e:
            raise _ReleaseCheckFailed(f"Unexpected GitHub response: {e!r}") from e

        logger.info(f"Latest version: {latest_version}, Current: {APP_VERSION}")
        return VersionInfo(
            version=latest_version,
            release_url=release_url,
            published_at=published_at,
            is_newer=self._is_newer_version(latest_version, APP_VERSION),
        )

    @staticmethod
    def _is_newer_version(latest: str, current: str) -> bool:
        """
        Compare version strings with support for pre-release suffixes.

        Pre-release versions are older than their release:
        1.0.0-beta < 1.0.0 < 1.0.1-alpha < 1.0.1

        Returns:
            True if latest is newer than current, False if either is unreadable
        """
        try:
            return _version_key(latest) > _version_key(current)
        except (ValueError, AttributeError) as e:
            logger.warning(f"Failed to compare versions: {latest} vs {current} - {e}")
            return False

    def _update_cache(self, version_info: VersionInfo) -> None:
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
                },
            }

            with open(self._cache_file, "w", encoding="utf-8") as f:
                json.dump(cache_data, f, indent=2)

        except Exception as e:
            logger.warning(f"Failed to update version cache: {e}")

    def _get_cached_version_info(self) -> VersionInfo | None:
        """
        Get version info from cache.

        is_newer is compared again to the running version: the cache may have
        been written by an older build of the application.

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
                    return VersionInfo(
                        version=info["version"],
                        release_url=info["release_url"],
                        published_at=info["published_at"],
                        is_newer=self._is_newer_version(info["version"], APP_VERSION),
                    )

        except Exception as e:
            logger.warning(f"Failed to read cached version info: {e}")

        return None
