"""L8: mod_sync applies the latest GitHub release within the processing of each mod."""

import hashlib
import io
import json
from pathlib import Path
import zipfile

import pytest

from util.mod_sync import orchestrator as orchestrator_module
from util.mod_sync.extractors.selective_extractor import SelectiveExtractor
from util.mod_sync.models.sync_result import SyncConfig, SyncStatus
from util.mod_sync.orchestrator import ModSyncOrchestrator
from util.mod_sync.processors.github_processor import (
    GitHubAsset,
    GitHubProcessor,
    GitHubRelease,
)
from util.mod_sync.sources.archive_source import ArchiveSource
from util.mod_sync.sources.github_source import GitHubSource

PLATFORM = "windows"
REPOSITORY = "https://github.com/author/demo"


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def mod_archive(tp2_text: str, tra_files: dict[str, str] | None = None) -> bytes:
    """Return a zip archive holding a demo mod folder."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("demo/demo.tp2", tp2_text)
        for name, text in (tra_files or {}).items():
            archive.writestr(f"demo/tra/{name}", text)
    return buffer.getvalue()


class FixedReleaseProcessor(GitHubProcessor):
    """GitHub client serving a fixed latest release without network."""

    def __init__(self, release: GitHubRelease | None) -> None:
        super().__init__()
        self.release = release
        self.requests: list[tuple[str, str]] = []

    def get_latest_release(self, owner: str, repo: str, include_prerelease: bool = False) -> GitHubRelease | None:
        self.requests.append((owner, repo))
        return self.release


def asset(name: str, size: int = 10, digest: str | None = None, url: str | None = None) -> GitHubAsset:
    return GitHubAsset(name=name, url=url or f"{REPOSITORY}/releases/download/v2/{name}", size=size, sha256=digest)


def release_files(assets: list[GitHubAsset], stored_files: list[dict] | None = None) -> list[dict]:
    source = GitHubSource(FixedReleaseProcessor(GitHubRelease("v2", assets, "", False)))
    definition = {"links": {"homepage": REPOSITORY}, "files": stored_files or []}
    return source.fetch(definition).files


# ----------------------------------------------------------------------
# Release entries
# ----------------------------------------------------------------------


def test_release_gives_one_entry_per_platform_with_api_digests() -> None:
    files = release_files([asset("win-demo.zip", digest="w"), asset("lin-demo.zip", digest="l")])

    assert files == [
        {"filename": "lin-demo.zip", "size": 10, "sha256": "l", "download": f"{REPOSITORY}/releases/download/v2/lin-demo.zip", "platforms": ["linux"]},
        {"filename": "win-demo.zip", "size": 10, "sha256": "w", "download": f"{REPOSITORY}/releases/download/v2/win-demo.zip", "platforms": ["windows"]},
    ]


def test_generic_asset_covers_platforms_without_their_own() -> None:
    files = release_files([asset("win-demo.zip"), asset("demo.zip")])

    assert {entry["filename"]: entry.get("platforms") for entry in files} == {
        "demo.zip": ["linux", "macos"],
        "win-demo.zip": ["windows"],
    }


def test_generic_only_release_gives_a_single_entry_for_every_platform() -> None:
    files = release_files([asset("demo-v2.tar.gz"), asset("demo-v2.zip")])

    assert [(entry["filename"], entry.get("platforms")) for entry in files] == [("demo-v2.zip", None)]


def test_archive_is_preferred_over_executable_of_the_same_platform() -> None:
    files = release_files([asset("setup-demo-win.exe"), asset("win-demo.zip")])

    assert [entry["filename"] for entry in files] == ["win-demo.zip"]


def test_hash_without_digest_is_kept_only_for_the_very_same_asset() -> None:
    unchanged = asset("lin-demo.zip", size=10)
    replaced = asset("win-demo.zip", size=10)
    stored_files = [
        {"filename": "lin-demo.zip", "size": 10, "sha256": "kept", "download": unchanged.url},
        {"filename": "win-demo.zip", "size": 10, "sha256": "stale", "download": f"{REPOSITORY}/releases/download/v1/win-demo.zip"},
    ]

    files = release_files([unchanged, replaced], stored_files)

    assert {entry["filename"]: entry.get("sha256") for entry in files} == {"lin-demo.zip": "kept", "win-demo.zip": None}


def test_local_archive_of_another_size_is_downloaded_again(tmp_path: Path) -> None:
    archives_dir = tmp_path / "archives"
    archives_dir.mkdir()
    (archives_dir / "demo.zip").write_bytes(b"old release")
    served = tmp_path / "demo.zip"
    served.write_bytes(b"release of the same name")

    archive = ArchiveSource(archives_dir, PLATFORM).process(
        {"filename": "demo.zip", "size": served.stat().st_size, "download": served.as_uri()}
    )

    assert archive.sha256 == sha256(b"release of the same name")


# ----------------------------------------------------------------------
# Synchronization of a GitHub mod
# ----------------------------------------------------------------------


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
    for name in ("mods", "archives", "extracted", "server"):
        (tmp_path / name).mkdir()
    return tmp_path


def publish(workspace: Path, filename: str, content: bytes, digest: str | None = None) -> GitHubAsset:
    path = workspace / "server" / filename
    path.write_bytes(content)
    return GitHubAsset(name=filename, url=path.as_uri(), size=len(content), sha256=digest)


def write_definition(workspace: Path, definition: dict) -> Path:
    path = workspace / "mods" / "demo.json"
    path.write_text(json.dumps(definition), encoding="utf-8")
    return path


def synchronizer(workspace: Path, release: GitHubRelease | None, **options: bool) -> ModSyncOrchestrator:
    config = SyncConfig(
        definitions_dir=workspace / "mods",
        archives_dir=workspace / "archives",
        mods_dir=workspace / "extracted",
        check_github_versions=True,
        calculate_hashes=True,
        **options,
    )
    orchestrator = ModSyncOrchestrator(config)
    orchestrator.archive_source = ArchiveSource(workspace / "archives", PLATFORM)
    orchestrator.github_processor = FixedReleaseProcessor(release)
    orchestrator.github_source = GitHubSource(orchestrator.github_processor)
    return orchestrator


V1_FILES = [{"filename": "win-demo-v1.zip", "size": 1, "sha256": "v1", "download": "https://example.org/v1", "platforms": ["windows"]}]


def test_github_update_is_applied_and_counted_as_updated(workspace: Path) -> None:
    windows_content = mod_archive("BACKUP ~weidu_external/backup/demo~\nVERSION ~v2~\n")
    windows = publish(workspace, "win-demo-v2.zip", windows_content, digest="api-digest-not-trusted-for-machine")
    linux = GitHubAsset("lin-demo-v2.zip", f"{REPOSITORY}/releases/download/v2/lin-demo-v2.zip", 7, "linux-digest")
    definition_path = write_definition(
        workspace, {"tp2": "demo", "version": "1", "games": ["bg2ee"], "links": {"homepage": REPOSITORY}, "files": V1_FILES}
    )

    orchestrator = synchronizer(workspace, GitHubRelease("v2", [windows, linux], "", False), extract_tp2=False)
    stats = orchestrator.run()

    assert [result.status for result in stats.results] == [SyncStatus.UPDATED]
    assert (stats.updated, stats.unchanged) == (1, 0)
    assert stats.github_outdated == [("demo", "1", "v2")]
    assert orchestrator.github_processor.requests == [("author", "demo")]

    definition = json.loads(definition_path.read_text(encoding="utf-8"))
    assert definition["version"] == "2"
    assert {entry["filename"]: entry["sha256"] for entry in definition["files"]} == {
        "lin-demo-v2.zip": "linux-digest",
        "win-demo-v2.zip": sha256(windows_content),
    }


def test_second_synchronization_of_the_same_release_is_unchanged(workspace: Path) -> None:
    content = mod_archive("BACKUP ~weidu_external/backup/demo~\n")
    release = GitHubRelease("v2", [publish(workspace, "win-demo-v2.zip", content, digest=sha256(content))], "", False)
    write_definition(workspace, {"tp2": "demo", "version": "1", "games": ["bg2ee"], "links": {"homepage": REPOSITORY}, "files": V1_FILES})
    synchronizer(workspace, release, extract_tp2=False).run()

    stats = synchronizer(workspace, release, extract_tp2=False).run()

    assert [result.status for result in stats.results] == [SyncStatus.UNCHANGED]
    assert stats.github_outdated == []


def test_changed_archive_is_extracted_again_before_parsing(workspace: Path) -> None:
    extracted_dir = workspace / "extracted"
    old_content = mod_archive("VERSION ~v1~\n", {"old.tra": "@1 = ~Old~"})
    (workspace / "archives" / "win-demo.zip").write_bytes(old_content)
    SelectiveExtractor().extract_for_parsing(
        workspace / "archives" / "win-demo.zip", extracted_dir, "demo", sha256(old_content)
    )

    new_content = mod_archive("VERSION ~v2~\n", {"new.tra": "@1 = ~New~"})
    windows = publish(workspace, "win-demo.zip", new_content, digest=sha256(new_content))
    write_definition(workspace, {"tp2": "demo", "games": ["bg2ee"], "links": {"homepage": REPOSITORY}, "files": V1_FILES})

    synchronizer(workspace, GitHubRelease("v2", [windows], "", False), extract_archives=True, extract_tp2=False).run()

    assert (extracted_dir / "demo" / "tra" / "new.tra").is_file()
    assert not (extracted_dir / "demo" / "tra" / "old.tra").exists()
    assert "v2" in (extracted_dir / "demo" / "demo.tp2").read_text(encoding="utf-8")


def test_up_to_date_extraction_is_kept(workspace: Path) -> None:
    content = mod_archive("VERSION ~v1~\n")
    archive_path = workspace / "archives" / "win-demo.zip"
    archive_path.write_bytes(content)
    extractor = SelectiveExtractor()
    extractor.extract_for_parsing(archive_path, workspace / "extracted", "demo", sha256(content))
    tp2_path = workspace / "extracted" / "demo" / "demo.tp2"
    tp2_path.write_text("edited after extraction", encoding="utf-8")

    assert extractor.extract_for_parsing(archive_path, workspace / "extracted", "demo", sha256(content))
    assert tp2_path.read_text(encoding="utf-8") == "edited after extraction"


class RecordingTP2Source:
    def __init__(self) -> None:
        self.parsed: list[str] = []

    def extract(self, tp2_name: str, supported_games: set[str] | None = None) -> None:
        self.parsed.append(tp2_name)


def test_extraction_without_current_archive_is_not_read(workspace: Path) -> None:
    content = mod_archive("VERSION ~v1~\n")
    archive_path = workspace / "archives" / "win-demo.zip"
    archive_path.write_bytes(content)
    SelectiveExtractor().extract_for_parsing(archive_path, workspace / "extracted", "demo", sha256(content))
    archive_path.unlink()
    unreachable = [{**V1_FILES[0], "filename": "win-demo.zip", "download": (workspace / "server" / "missing.zip").as_uri()}]
    write_definition(workspace, {"tp2": "demo", "games": ["bg2ee"], "files": unreachable})

    orchestrator = synchronizer(workspace, None, extract_archives=True)
    orchestrator.tp2_source = RecordingTP2Source()
    orchestrator.run()

    assert orchestrator.tp2_source.parsed == []
