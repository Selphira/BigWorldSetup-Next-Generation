"""Latest GitHub release of a mod, as definition file entries."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from util.mod_sync.processors.github_processor import (
    GitHubAsset,
    GitHubProcessor,
    GitHubRelease,
)
from util.mod_sync.sources.archive_source import ALL_PLATFORMS
from util.mod_sync.sources.github_recipes import GitHubRecipe, RecipeMode

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class GitHubUpdate:
    """Latest release of a mod repository."""

    repository: str
    tag_name: str
    files: list[dict[str, Any]]

    @property
    def version(self) -> str:
        return self.tag_name.lstrip("vV")


@dataclass(frozen=True, slots=True)
class GitHubPinnedUpdate:
    """Commit a pinned mod is now downloaded from, with what deserves review."""

    repository: str
    recipe: GitHubRecipe
    previous: GitHubRecipe
    newer_release: GitHubRelease | None
    files: list[dict[str, Any]]

    @property
    def advanced(self) -> bool:
        return self.recipe.commit != self.previous.commit


class GitHubPinError(Exception):
    """The reference a mod is pinned to cannot be resolved."""


class GitHubSource:
    """Describe the latest GitHub release of a mod as ``files`` entries.

    One entry is produced per platform having an asset; an asset without
    platform marker covers the platforms left without one. Hashes come from
    the API digest; without digest, a stored hash is kept only for the very
    same asset (same download URL and size), since a name and a size do not
    identify the content of a new release.
    """

    def __init__(self, processor: GitHubProcessor):
        self.processor = processor

    def find_repository(self, *definitions: dict[str, Any] | None) -> tuple[str, str] | None:
        """Return the repository of the homepage, or else of a download link.

        Args:
            definitions: Mod data in priority order (stored definition, LCC data)

        Returns:
            (owner, repo), or None when the mod is not on GitHub
        """
        for definition in filter(None, definitions):
            for url in self._candidate_urls(definition):
                if self.processor.is_github_link(url) and (repository := self.processor.extract_repo_info(url)):
                    return repository
        return None

    def fetch(self, repository: tuple[str, str], stored_definition: dict[str, Any] | None) -> GitHubUpdate | None:
        """Return the latest release of a repository.

        Args:
            repository: (owner, repo), as found by ``find_repository``
            stored_definition: Stored definition, whose hashes may still apply

        Returns:
            The release, or None when the repository has no release

        Raises:
            GitHubUnavailableError: GitHub did not answer
        """
        owner, repo = repository
        release = self.processor.get_latest_release(owner, repo)
        if release is None:
            logger.debug(f"No GitHub release found for {owner}/{repo}")
            return None

        stored_files = self._stored_files(stored_definition)
        return GitHubUpdate(
            repository=f"{owner}/{repo}",
            tag_name=release.tag_name,
            files=self._file_entries(release, stored_files),
        )

    def fetch_pinned(
        self, repository: tuple[str, str], recipe: GitHubRecipe, stored_definition: dict[str, Any] | None
    ) -> GitHubPinnedUpdate:
        """Return the commit a pinned mod is downloaded from.

        A branch recipe moves to the last commit of its branch; a commit
        recipe never moves. Either way, a release published after the commit
        is reported: it may make the pin unnecessary.

        Raises:
            GitHubPinError: The branch no longer exists
            GitHubUnavailableError: GitHub did not answer
        """
        owner, repo = repository
        current = recipe
        if recipe.mode is RecipeMode.BRANCH:
            head = self.processor.get_commit(owner, repo, recipe.branch)
            if head is None:
                raise GitHubPinError(f"Branch {recipe.branch} not found in {owner}/{repo}")
            current = recipe.moved_to(head)

        release = self.processor.get_latest_release(owner, repo)
        newer_release = release if release and release.published_date > current.date else None

        return GitHubPinnedUpdate(
            repository=f"{owner}/{repo}",
            recipe=current,
            previous=recipe,
            newer_release=newer_release,
            files=[self._commit_entry(owner, repo, current.commit, self._stored_files(stored_definition))],
        )

    def resolve_pin(self, repository: tuple[str, str], ref: str) -> GitHubRecipe:
        """Return the recipe a pin reference ("branch", "sha" or "branch@sha") designates.

        Raises:
            GitHubPinError: The reference does not exist in the repository
            GitHubUnavailableError: GitHub did not answer
        """
        owner, repo = repository
        branch, commit_ref = GitHubRecipe.parse_ref(ref)
        commit = self.processor.get_commit(owner, repo, commit_ref or branch)
        if commit is None:
            raise GitHubPinError(f"Reference {ref} not found in {owner}/{repo}")

        mode = RecipeMode.COMMIT if commit_ref else RecipeMode.BRANCH
        return GitHubRecipe(mode=mode, commit=commit.sha, date=commit.date, branch=branch)

    def _commit_entry(
        self, owner: str, repo: str, sha: str, stored_files: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Return the entry of a commit archive, keeping what was stored for the same link.

        Its size is unknown until the archive is downloaded.
        """
        filename, url = self.processor.commit_archive(owner, repo, sha)
        stored_entry = next((entry for entry in stored_files if entry.get("download") == url), {})

        entry: dict[str, Any] = {"filename": filename}
        for field in ("size", "sha256"):
            if field in stored_entry:
                entry[field] = stored_entry[field]
        entry["download"] = url
        return entry

    # ------------------------------------------------------------------
    # Repository lookup
    # ------------------------------------------------------------------

    @staticmethod
    def _candidate_urls(definition: dict[str, Any]) -> list[str]:
        links = definition.get("links")
        homepage = links.get("homepage") if isinstance(links, dict) else None
        downloads = [entry.get("download") for entry in GitHubSource._stored_files(definition)]
        return [url for url in (homepage, *downloads) if isinstance(url, str) and url]

    @staticmethod
    def _stored_files(definition: dict[str, Any] | None) -> list[dict[str, Any]]:
        files = (definition or {}).get("files")
        return [entry for entry in files if isinstance(entry, dict)] if isinstance(files, list) else []

    # ------------------------------------------------------------------
    # File entries
    # ------------------------------------------------------------------

    def _file_entries(self, release: GitHubRelease, stored_files: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return one entry per platform having an asset, sorted by filename."""
        grouped_assets = self.processor.group_assets_by_os(release.assets)
        generic_assets = grouped_assets.pop(None, [])

        entries = [
            self._file_entry(self.processor.select_best_asset_for_os(grouped_assets[platform]), [platform], stored_files)
            for platform in ALL_PLATFORMS
            if platform in grouped_assets
        ]

        uncovered_platforms = [platform for platform in ALL_PLATFORMS if platform not in grouped_assets]
        if generic_assets and uncovered_platforms:
            platforms = uncovered_platforms if len(uncovered_platforms) < len(ALL_PLATFORMS) else None
            entries.append(
                self._file_entry(self.processor.select_best_asset_for_os(generic_assets), platforms, stored_files)
            )

        return sorted(entries, key=lambda entry: entry["filename"])

    @staticmethod
    def _file_entry(
        asset: GitHubAsset, platforms: list[str] | None, stored_files: list[dict[str, Any]]
    ) -> dict[str, Any]:
        entry: dict[str, Any] = {"filename": asset.name, "size": asset.size}

        sha256 = asset.sha256 or GitHubSource._stored_hash(asset, stored_files)
        if sha256:
            entry["sha256"] = sha256

        entry["download"] = asset.url
        if platforms:
            entry["platforms"] = platforms
        return entry

    @staticmethod
    def _stored_hash(asset: GitHubAsset, stored_files: list[dict[str, Any]]) -> str | None:
        """Return the stored hash of the same asset, identified by download URL and size."""
        for stored_entry in stored_files:
            if stored_entry.get("download") == asset.url and stored_entry.get("size") == asset.size:
                return stored_entry.get("sha256") or None
        return None
