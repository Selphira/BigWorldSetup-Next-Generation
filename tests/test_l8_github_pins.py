"""L8: mods pinned to a GitHub commit follow their branch and report newer releases."""

from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

import pytest

from util.mod_sync import orchestrator as orchestrator_module
from util.mod_sync.mergers.definition_merger import DefinitionMerger
from util.mod_sync.models.sync_result import SyncConfig, SyncStatus
from util.mod_sync.orchestrator import ModSyncOrchestrator
from util.mod_sync.processors.github_processor import (
    GitHubAsset,
    GitHubCommit,
    GitHubProcessor,
    GitHubRelease,
)
from util.mod_sync.sources.archive_source import ArchiveSource
from util.mod_sync.sources.github_recipes import GitHubRecipe, GitHubRecipes, RecipeMode
from util.mod_sync.sources.github_source import GitHubPinError, GitHubSource

PLATFORM = "windows"
REPOSITORY = "https://github.com/author/demo"
OLD_SHA = "a" * 40
NEW_SHA = "b" * 40
OLD_DATE = datetime(2026, 1, 10, tzinfo=timezone.utc)
NEW_DATE = datetime(2026, 3, 1, tzinfo=timezone.utc)


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class FakeGitHub(GitHubProcessor):
    """GitHub serving fixed commits and release, with commit archives in a local directory."""

    def __init__(self, server_dir: Path, commits: dict[str, GitHubCommit], release: GitHubRelease | None = None):
        super().__init__()
        self.server_dir = server_dir
        self.commits = commits
        self.release = release
        self.commit_requests: list[str] = []

    def get_commit(self, owner: str, repo: str, ref: str) -> GitHubCommit | None:
        self.commit_requests.append(ref)
        return self.commits.get(ref) or next(
            (commit for commit in self.commits.values() if commit.sha.startswith(ref)), None
        )

    def get_latest_release(self, owner: str, repo: str, include_prerelease: bool = False) -> GitHubRelease | None:
        return self.release

    def commit_archive(self, owner: str, repo: str, sha: str) -> tuple[str, str]:
        filename = f"{repo}-{sha}.zip"
        return filename, (self.server_dir / filename).as_uri()


class OfflineLCCSource:
    def __init__(self, force_refresh: bool = False) -> None:
        pass

    def fetch_all(self) -> bool:
        return True

    def get_all_mods(self) -> dict[str, dict]:
        return {"demo": {"tp2": "demo", "games": ["bg2ee"]}}


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(orchestrator_module, "LCCSource", OfflineLCCSource)
    for name in ("mods", "archives", "server"):
        (tmp_path / name).mkdir()
    return tmp_path


def serve_commit(workspace: Path, sha: str, content: bytes) -> None:
    (workspace / "server" / f"demo-{sha}.zip").write_bytes(content)


def write_definition(workspace: Path, **fields: Any) -> Path:
    path = workspace / "mods" / "demo.json"
    definition = {"tp2": "demo", "version": "1", "games": ["bg2ee"], "links": {"homepage": REPOSITORY}, **fields}
    path.write_text(json.dumps(definition), encoding="utf-8")
    return path


def synchronizer(workspace: Path, github: FakeGitHub, **options: Any) -> ModSyncOrchestrator:
    config = SyncConfig(
        definitions_dir=workspace / "mods",
        archives_dir=workspace / "archives",
        check_github_versions=True,
        calculate_hashes=True,
        github_check_interval_hours=0,
        github_checks_file=workspace / "github_checks.json",
        github_recipes_file=workspace / "github_recipes.json",
        **options,
    )
    orchestrator = ModSyncOrchestrator(config)
    orchestrator.archive_source = ArchiveSource(workspace / "archives", PLATFORM)
    orchestrator.github_processor = github
    orchestrator.github_source = GitHubSource(github)
    return orchestrator


def recipes(workspace: Path) -> GitHubRecipes:
    return GitHubRecipes(workspace / "github_recipes.json")


def master_recipe(sha: str = OLD_SHA, date: datetime = OLD_DATE) -> GitHubRecipe:
    return GitHubRecipe(RecipeMode.BRANCH, sha, date, "master")


# ----------------------------------------------------------------------
# Pinning
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ref", "expected"),
    [("master", ("master", None)), ("a1b2c3d", (None, "a1b2c3d")), ("dev@a1b2c3d", ("dev", "a1b2c3d"))],
)
def test_pin_reference_names_a_branch_a_commit_or_both(ref: str, expected: tuple) -> None:
    assert GitHubRecipe.parse_ref(ref) == expected


def test_pinning_a_branch_records_its_last_commit(workspace: Path) -> None:
    write_definition(workspace)
    github = FakeGitHub(workspace / "server", {"master": GitHubCommit(OLD_SHA, OLD_DATE)})

    recipe = synchronizer(workspace, github).pin("Demo", "master")

    assert recipe == master_recipe()
    assert recipe.version == "master@aaaaaaa"
    assert recipes(workspace).get("demo") == recipe


def test_pinning_a_commit_of_a_branch_stays_on_it(workspace: Path) -> None:
    write_definition(workspace)
    github = FakeGitHub(workspace / "server", {"x": GitHubCommit(OLD_SHA, OLD_DATE)})

    recipe = synchronizer(workspace, github).pin("demo", "master@aaaaaaa")

    assert (recipe.mode, recipe.commit, recipe.version) == (RecipeMode.COMMIT, OLD_SHA, "master@aaaaaaa")


def test_unknown_reference_is_refused_and_nothing_recorded(workspace: Path) -> None:
    write_definition(workspace)
    github = FakeGitHub(workspace / "server", {})

    with pytest.raises(GitHubPinError):
        synchronizer(workspace, github).pin("demo", "missing-branch")
    assert recipes(workspace).get("demo") is None


# ----------------------------------------------------------------------
# Synchronization of a pinned mod
# ----------------------------------------------------------------------


def test_branch_recipe_moves_to_the_last_commit_and_warns(workspace: Path, caplog: pytest.LogCaptureFixture) -> None:
    content = b"archive of the new commit"
    serve_commit(workspace, NEW_SHA, content)
    definition_path = write_definition(workspace, files=[{"filename": f"demo-{OLD_SHA}.zip", "size": 1}])
    recipes(workspace).set("demo", master_recipe())
    github = FakeGitHub(workspace / "server", {"master": GitHubCommit(NEW_SHA, NEW_DATE)})

    with caplog.at_level(logging.WARNING):
        stats = synchronizer(workspace, github).run()

    assert [result.status for result in stats.results] == [SyncStatus.UPDATED]
    definition = json.loads(definition_path.read_text(encoding="utf-8"))
    assert definition["version"] == "master@bbbbbbb"
    assert [(entry["filename"], entry["size"], entry["sha256"]) for entry in definition["files"]] == [
        (f"demo-{NEW_SHA}.zip", len(content), sha256(content))
    ]
    assert recipes(workspace).get("demo") == master_recipe(NEW_SHA, NEW_DATE)
    assert stats.github_pins_advanced == [("demo", "master@aaaaaaa", "master@bbbbbbb")]
    assert "moved to master@bbbbbbb" in caplog.text


def test_release_published_after_the_pinned_commit_is_reported(
    workspace: Path, caplog: pytest.LogCaptureFixture
) -> None:
    serve_commit(workspace, OLD_SHA, b"pinned")
    write_definition(workspace)
    recipes(workspace).set("demo", GitHubRecipe(RecipeMode.COMMIT, OLD_SHA, OLD_DATE, "master"))
    release = GitHubRelease("v5", [GitHubAsset("demo-v5.zip", f"{REPOSITORY}/v5.zip", 3)], "2026-02-01T00:00:00Z", False)
    github = FakeGitHub(workspace / "server", {}, release)

    with caplog.at_level(logging.WARNING):
        stats = synchronizer(workspace, github).run()

    assert github.commit_requests == []
    assert stats.github_releases_after_pin == [("demo", "v5", "2026-02-01", "master@aaaaaaa")]
    assert "release v5 published on 2026-02-01" in caplog.text
    assert "Releases Published After The Pinned Commit" in stats.get_github_report()


def test_release_older_than_the_pinned_commit_is_not_reported(workspace: Path) -> None:
    serve_commit(workspace, OLD_SHA, b"pinned")
    write_definition(workspace)
    recipes(workspace).set("demo", GitHubRecipe(RecipeMode.COMMIT, OLD_SHA, OLD_DATE))
    release = GitHubRelease("v4", [], "2025-12-01T00:00:00Z", False)

    stats = synchronizer(workspace, FakeGitHub(workspace / "server", {}, release)).run()

    assert stats.github_releases_after_pin == []


def test_commit_archive_that_cannot_be_downloaded_leaves_everything_unchanged(workspace: Path) -> None:
    definition_path = write_definition(workspace, files=[{"filename": f"demo-{OLD_SHA}.zip", "size": 1}])
    stored = definition_path.read_text(encoding="utf-8")
    recipes(workspace).set("demo", master_recipe())
    github = FakeGitHub(workspace / "server", {"master": GitHubCommit(NEW_SHA, NEW_DATE)})

    stats = synchronizer(workspace, github).run()

    assert [result.status for result in stats.results] == [SyncStatus.ERROR]
    assert definition_path.read_text(encoding="utf-8") == stored
    assert recipes(workspace).get("demo") == master_recipe()
    assert not (workspace / "github_checks.json").exists()


def test_unpinned_mod_uses_its_latest_release_again(workspace: Path) -> None:
    write_definition(workspace)
    recipes(workspace).set("demo", master_recipe())
    (workspace / "server" / "demo-v5.zip").write_bytes(b"v5")
    asset = GitHubAsset("demo-v5.zip", (workspace / "server" / "demo-v5.zip").as_uri(), 2)
    github = FakeGitHub(workspace / "server", {}, GitHubRelease("v5", [asset], "2026-02-01T00:00:00Z", False))
    orchestrator = synchronizer(workspace, github)

    assert orchestrator.unpin("demo")
    orchestrator.run()

    definition = json.loads((workspace / "mods" / "demo.json").read_text(encoding="utf-8"))
    assert (definition["version"], definition["files"][0]["filename"]) == ("5", "demo-v5.zip")


def test_pinned_version_wins_over_the_tp2_version() -> None:
    merger = DefinitionMerger("demo")
    merger.add_source("pin", {"version": "master@aaaaaaa"})
    merger.add_source("tp2", {"version": "v4.4"})

    assert merger.merge({})["version"] == "master@aaaaaaa"
