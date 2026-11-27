"""GitHub repository processor for download links and version checking."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import logging
import re
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

GITHUB_API_URL = "https://api.github.com"
API_TIMEOUT_SECONDS = 10

# Statuses GitHub answers with once the API rate limit is exhausted
RATE_LIMIT_STATUSES = (403, 429)


def parse_github_date(value: str) -> datetime:
    """Parse an API date such as ``2025-06-01T12:00:00Z``."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class GitHubUnavailableError(Exception):
    """GitHub could not tell whether a repository has a release."""

    def __init__(self, message: str, rate_limited: bool = False):
        super().__init__(message)
        self.rate_limited = rate_limited


@dataclass
class GitHubAsset:
    """GitHub release asset information."""

    name: str
    url: str
    size: int
    sha256: str | None = None  # From GitHub API digest field


@dataclass
class GitHubRelease:
    """GitHub release information."""

    tag_name: str
    assets: list[GitHubAsset]
    published_at: str
    is_prerelease: bool

    @property
    def published_date(self) -> datetime:
        return parse_github_date(self.published_at)


@dataclass(frozen=True, slots=True)
class GitHubCommit:
    """GitHub commit, dated by its committer."""

    sha: str
    date: datetime


class GitHubProcessor:
    """Process GitHub links and fetch release information."""

    GITHUB_REPO_PATTERN = re.compile(r"https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$")

    # Pattern for download URLs (releases, archive, raw, etc.)
    GITHUB_DOWNLOAD_PATTERN = re.compile(
        r"https?://github\.com/([^/]+)/([^/]+)/(?:releases|archive|raw|blob)"
    )

    OS_PREFIXES = {
        "lin-": "linux",
        "linux-": "linux",
        "mac-": "macos",
        "macos-": "macos",
        "osx-": "macos",
        "win-": "windows",
        "windows-": "windows",
    }

    OS_SUFFIXES = {
        ".exe": "windows",
    }

    # Downloadable asset kinds, in order of preference
    ASSET_EXTENSION_PREFERENCE = (".zip", ".7z", ".tar.gz", ".tgz", ".exe")

    # CPU architecture markers: a 64-bit asset is preferred over an unmarked
    # one, and both over a 32-bit one
    ARCH_64_PATTERN = re.compile(
        r"(?<![0-9a-z])(?:64-?bits?|x64|x86[_-]64|amd64|win64)(?![0-9a-z])"
    )
    ARCH_32_PATTERN = re.compile(r"(?<![0-9a-z])(?:32-?bits?|x86|i[3-6]86|win32)(?![0-9a-z])")

    OS_PATTERNS = {
        "linux": ["linux", "lin"],
        "macos": ["macos", "mac", "osx", "darwin"],
        "windows": ["windows", "win64", "win32", "win", "w32", "w64"],
    }

    def __init__(self, github_token: str | None = None):
        """Initialize GitHub processor.

        Args:
            github_token: Optional GitHub token for higher rate limits
                         (60 req/h without, 5000 req/h with token)
        """
        self.github_token = github_token

    def is_github_link(self, url: str) -> bool:
        """Check if URL is a GitHub repository link."""
        if not url:
            return False
        return bool(self.GITHUB_REPO_PATTERN.match(url)) or bool(
            self.GITHUB_DOWNLOAD_PATTERN.match(url)
        )

    def extract_repo_info(self, url: str) -> tuple[str, str] | None:
        """Extract owner and repo from GitHub URL.

        Handles both repository URLs and download URLs.

        Args:
            url: GitHub repository or download URL

        Returns:
            Tuple of (owner, repo) or None if invalid
        """
        # Try repository pattern first
        match = self.GITHUB_REPO_PATTERN.match(url)
        if match:
            return match.group(1), match.group(2)

        # Try download pattern
        match = self.GITHUB_DOWNLOAD_PATTERN.match(url)
        if match:
            return match.group(1), match.group(2)

        return None

    def extract_repo_url(self, url: str) -> str | None:
        """Extract clean repository URL from any GitHub URL.

        Converts download URLs to repository homepage URLs.

        Args:
            url: Any GitHub URL

        Returns:
            Clean repository URL or None if invalid

        Examples:
            https://github.com/owner/repo/releases/download/v1.0/file.zip
            -> https://github.com/owner/repo

            https://github.com/owner/repo
            -> https://github.com/owner/repo
        """
        repo_info = self.extract_repo_info(url)
        if not repo_info:
            return None

        owner, repo = repo_info
        return f"https://github.com/{owner}/{repo}"

    def detect_os_from_filename(self, filename: str) -> str | None:
        """Detect target OS from filename using prefixes, suffixes, and patterns.

        Detection priority:
        1. Prefixes (lin-, mac-, win-, etc.)
        2. Suffixes (.exe)
        3. Patterns in filename (*-linux-*, *-win.*, etc.)

        Args:
            filename: Asset filename

        Returns:
            OS name ('linux', 'macos', 'windows') or None if no match
        """
        filename_lower = filename.lower()

        # Remove extension for pattern matching
        name_without_ext = filename_lower
        for ext in [".zip", ".tar.gz", ".tgz", ".tar.bz2", ".exe"]:
            if filename_lower.endswith(ext):
                name_without_ext = filename_lower[: -len(ext)]
                break

        # Priority 1: Check prefixes
        for prefix, os_name in self.OS_PREFIXES.items():
            if filename_lower.startswith(prefix):
                return os_name

        # Priority 2: Check suffixes
        for suffix, os_name in self.OS_SUFFIXES.items():
            if filename_lower.endswith(suffix):
                return os_name

        # Priority 3: Check patterns in filename
        # Look for patterns like "name-linux-version", "name-win.zip", etc.
        for os_name, patterns in self.OS_PATTERNS.items():
            for pattern in patterns:
                # Match "-pattern-" or "-pattern." or "-pattern" at end
                if f"-{pattern}-" in name_without_ext:
                    return os_name
                if f"-{pattern}." in filename_lower:
                    return os_name
                if f"_{pattern}_" in name_without_ext:
                    return os_name
                if f"_{pattern}." in filename_lower:
                    return os_name
                if name_without_ext.endswith(f"-{pattern}"):
                    return os_name
                if name_without_ext.endswith(f"_{pattern}"):
                    return os_name

        return None

    def _parse_sha256_from_digest(self, digest: dict | None) -> str | None:
        """Parse SHA256 from GitHub digest object.

        GitHub digest format: {"sha256": "hex_string"}

        Args:
            digest: Digest dictionary from API or None

        Returns:
            SHA256 hex string or None
        """
        if not digest:
            return None

        # GitHub format: {"sha256": "abc123..."}
        if isinstance(digest, dict) and "sha256" in digest:
            return digest["sha256"]

        # Format 2: String "sha256:abc123..."
        if isinstance(digest, str) and digest.startswith("sha256:"):
            return digest[7:]  # Remove "sha256:" prefix

        return None

    def get_latest_release(
        self, owner: str, repo: str, include_prerelease: bool = False
    ) -> GitHubRelease | None:
        """Fetch latest release from GitHub API.

        Args:
            owner: Repository owner
            repo: Repository name
            include_prerelease: Whether to include pre-releases

        Returns:
            GitHubRelease with SHA256 from API, or None when the repository has no release

        Raises:
            GitHubUnavailableError: GitHub did not answer (rate limit, network error...)
        """
        data = self._api_get(f"repos/{owner}/{repo}/releases/latest")
        if data is None:
            logger.debug(f"No releases found for {owner}/{repo}")
            return None

        if not include_prerelease and data.get("prerelease", False):
            logger.debug(f"Skipping pre-release for {owner}/{repo}")
            return None

        return GitHubRelease(
            tag_name=data["tag_name"],
            assets=self._release_assets(owner, repo, data.get("assets", [])),
            published_at=data["published_at"],
            is_prerelease=data.get("prerelease", False),
        )

    def get_commit(self, owner: str, repo: str, ref: str) -> GitHubCommit | None:
        """Fetch the commit a branch name or a commit hash designates.

        Returns:
            The commit, or None when the repository has no such reference

        Raises:
            GitHubUnavailableError: GitHub did not answer (rate limit, network error...)
        """
        data = self._api_get(f"repos/{owner}/{repo}/commits/{urllib.parse.quote(ref, safe='')}")
        if data is None:
            return None
        return GitHubCommit(
            sha=data["sha"], date=parse_github_date(data["commit"]["committer"]["date"])
        )

    @staticmethod
    def commit_archive(owner: str, repo: str, sha: str) -> tuple[str, str]:
        """Return the filename and download URL of the source archive of a commit."""
        return f"{repo}-{sha}.zip", f"https://github.com/{owner}/{repo}/archive/{sha}.zip"

    @staticmethod
    def tag_archive(owner: str, repo: str, tag: str) -> tuple[str, str]:
        """Return the filename and download URL of the source archive of a tag.

        The filename is the one GitHub serves: a leading "v" is dropped and
        slashes become dashes ("v1.2" gives "repo-1.2.zip").
        """
        version = tag.lstrip("vV").replace("/", "-")
        url = (
            f"https://github.com/{owner}/{repo}/archive/refs/tags/"
            f"{urllib.parse.quote(tag, safe='/')}.zip"
        )
        return f"{repo}-{version}.zip", url

    def _release_assets(
        self, owner: str, repo: str, assets_data: list[dict]
    ) -> list[GitHubAsset]:
        """Return the downloadable archives of a release, with their API digest."""
        assets = []
        for asset_data in assets_data:
            name = asset_data["name"]
            if not name.lower().endswith(self.ASSET_EXTENSION_PREFERENCE):
                continue

            # Digest field available since June 2025
            sha256 = self._parse_sha256_from_digest(asset_data.get("digest"))
            if not sha256:
                logger.warning(
                    f"No SHA256 digest for {name} in {owner}/{repo} (release may be older than June 2025)"
                )

            assets.append(
                GitHubAsset(
                    name=name,
                    url=asset_data["browser_download_url"],
                    size=asset_data["size"],
                    sha256=sha256,
                )
            )
        return assets

    def _api_get(self, path: str) -> dict[str, Any] | None:
        """Return the JSON answer of the GitHub API, or None when the resource does not exist.

        Raises:
            GitHubUnavailableError: GitHub did not answer (rate limit, network error...)
        """
        headers = {"Accept": "application/vnd.github.v3+json"}
        if self.github_token:
            headers["Authorization"] = f"token {self.github_token}"

        request = urllib.request.Request(f"{GITHUB_API_URL}/{path}", headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=API_TIMEOUT_SECONDS) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code in RATE_LIMIT_STATUSES:
                reset = e.headers.get("X-RateLimit-Reset") if e.headers else None
                raise GitHubUnavailableError(
                    f"GitHub API rate limit exceeded (reset at epoch {reset or 'unknown'})",
                    rate_limited=True,
                ) from e
            raise GitHubUnavailableError(f"HTTP error on GitHub API {path}: {e}") from e
        except Exception as e:
            raise GitHubUnavailableError(f"Error on GitHub API {path}: {e}") from e

    def group_assets_by_os(
        self, assets: list[GitHubAsset]
    ) -> dict[str | None, list[GitHubAsset]]:
        """Group assets by target OS.

        Args:
            assets: List of release assets

        Returns:
            Dict mapping OS name (or None for no prefix) to list of assets
        """
        grouped = {}

        for asset in assets:
            os_name = self.detect_os_from_filename(asset.name)
            if os_name not in grouped:
                grouped[os_name] = []
            grouped[os_name].append(asset)

        return grouped

    def select_best_asset_for_os(self, assets: list[GitHubAsset]) -> GitHubAsset | None:
        """Select the asset to download among those of one platform.

        64-bit assets are preferred, 32-bit ones come last; then archives are
        preferred over executables, .zip first. Equal assets keep the release
        order.

        Args:
            assets: Assets of one platform

        Returns:
            Best asset or None
        """
        if not assets:
            return None

        return min(assets, key=self._asset_preference)

    def _asset_preference(self, asset: GitHubAsset) -> tuple[int, int]:
        """Rank an asset by architecture then extension, lower being preferred."""
        name = asset.name.lower()
        return self._architecture_rank(name), self._extension_rank(name)

    def _architecture_rank(self, name: str) -> int:
        if self.ARCH_64_PATTERN.search(name):
            return 0
        if self.ARCH_32_PATTERN.search(name):
            return 2
        return 1

    def _extension_rank(self, name: str) -> int:
        for rank, extension in enumerate(self.ASSET_EXTENSION_PREFERENCE):
            if name.endswith(extension):
                return rank
        return len(self.ASSET_EXTENSION_PREFERENCE)

    @staticmethod
    def compare_versions(current: str, latest: str) -> bool:
        """Check if current version is outdated.

        Note: Simple string comparison. For proper semver comparison,
        use packaging.version.parse().

        Args:
            current: Current version string
            latest: Latest version string

        Returns:
            True if outdated, False otherwise
        """
        # Normalize versions (remove 'v' prefix)
        current_norm = current.lstrip("vV").strip()
        latest_norm = latest.lstrip("vV").strip()

        return current_norm != latest_norm
