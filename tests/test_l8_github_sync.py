"""L8: mod_sync applies the latest GitHub release within the processing of each mod."""

import hashlib
import io
import json
from pathlib import Path
from typing import Any
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
    GitHubUnavailableError,
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

    def __init__(self, release: GitHubRelease | None, rate_limited: bool = False) -> None:
        super().__init__()
        self.release = release
        self.rate_limited = rate_limited
        self.requests: list[tuple[str, str]] = []

    def get_latest_release(
        self, owner: str, repo: str, include_prerelease: bool = False
    ) -> GitHubRelease | None:
        self.requests.append((owner, repo))
        if self.rate_limited:
            raise GitHubUnavailableError("rate limit", rate_limited=True)
        return self.release


def asset(
    name: str, size: int = 10, digest: str | None = None, url: str | None = None
) -> GitHubAsset:
    return GitHubAsset(
        name=name,
        url=url or f"{REPOSITORY}/releases/download/v2/{name}",
        size=size,
        sha256=digest,
    )


def release_files(
    assets: list[GitHubAsset], stored_files: list[dict] | None = None
) -> list[dict]:
    source = GitHubSource(FixedReleaseProcessor(GitHubRelease("v2", assets, "", False)))
    definition = {"links": {"homepage": REPOSITORY}, "files": stored_files or []}
    return source.fetch(source.find_repository(definition), definition).files


# ----------------------------------------------------------------------
# Release entries
# ----------------------------------------------------------------------


def test_release_gives_one_entry_per_platform_with_api_digests() -> None:
    files = release_files(
        [asset("win-demo.zip", digest="w"), asset("lin-demo.zip", digest="l")]
    )

    assert files == [
        {
            "filename": "lin-demo.zip",
            "size": 10,
            "sha256": "l",
            "download": f"{REPOSITORY}/releases/download/v2/lin-demo.zip",
            "platforms": ["linux"],
        },
        {
            "filename": "win-demo.zip",
            "size": 10,
            "sha256": "w",
            "download": f"{REPOSITORY}/releases/download/v2/win-demo.zip",
            "platforms": ["windows"],
        },
    ]


def test_generic_asset_covers_platforms_without_their_own() -> None:
    files = release_files([asset("win-demo.zip"), asset("demo.zip")])

    assert {entry["filename"]: entry.get("platforms") for entry in files} == {
        "demo.zip": ["linux", "macos"],
        "win-demo.zip": ["windows"],
    }


def test_generic_only_release_gives_a_single_entry_for_every_platform() -> None:
    files = release_files([asset("demo-v2.tar.gz"), asset("demo-v2.zip")])

    assert [(entry["filename"], entry.get("platforms")) for entry in files] == [
        ("demo-v2.zip", None)
    ]


def test_archive_is_preferred_over_executable_of_the_same_platform() -> None:
    files = release_files([asset("setup-demo-win.exe"), asset("win-demo.zip")])

    assert [entry["filename"] for entry in files] == ["win-demo.zip"]


@pytest.mark.parametrize(
    ("assets", "chosen"),
    [
        (["win-32bit-demo-v2.zip", "win-64bit-demo-v2.zip"], "win-64bit-demo-v2.zip"),
        (["win-32bit-demo-v2.zip", "win-demo-v2.zip"], "win-demo-v2.zip"),
        (["demo-win32.zip", "setup-demo-win64.exe"], "setup-demo-win64.exe"),
        (["demo-windows-x86.zip", "demo-windows-x86_64.zip"], "demo-windows-x86_64.zip"),
    ],
)
def test_64_bit_asset_is_chosen_for_windows(assets: list[str], chosen: str) -> None:
    files = release_files([asset(name) for name in assets])

    assert [(entry["filename"], entry.get("platforms")) for entry in files] == [
        (chosen, ["windows"])
    ]


def test_hash_without_digest_is_kept_only_for_the_very_same_asset() -> None:
    unchanged = asset("lin-demo.zip", size=10)
    replaced = asset("win-demo.zip", size=10)
    stored_files = [
        {"filename": "lin-demo.zip", "size": 10, "sha256": "kept", "download": unchanged.url},
        {
            "filename": "win-demo.zip",
            "size": 10,
            "sha256": "stale",
            "download": f"{REPOSITORY}/releases/download/v1/win-demo.zip",
        },
    ]

    files = release_files([unchanged, replaced], stored_files)

    assert {entry["filename"]: entry.get("sha256") for entry in files} == {
        "lin-demo.zip": "kept",
        "win-demo.zip": None,
    }


def test_release_without_asset_falls_back_to_the_source_archive_of_its_tag() -> None:
    assert release_files([]) == [
        {"filename": "demo-2.zip", "download": f"{REPOSITORY}/archive/refs/tags/v2.zip"}
    ]


def test_source_archive_of_a_tag_keeps_the_hash_stored_for_the_same_link() -> None:
    stored_files = [
        {
            "filename": "demo-2.zip",
            "size": 42,
            "sha256": "kept",
            "download": f"{REPOSITORY}/archive/refs/tags/v2.zip",
        }
    ]

    assert release_files([], stored_files) == stored_files


@pytest.mark.parametrize(
    ("tag", "filename", "url_tag"),
    [
        ("1.14_EE", "demo-1.14_EE.zip", "1.14_EE"),
        ("V2_EE", "demo-2_EE.zip", "V2_EE"),
        ("release/1.0", "demo-release-1.0.zip", "release/1.0"),
        ("1.0 beta", "demo-1.0 beta.zip", "1.0%20beta"),
    ],
)
def test_source_archive_of_a_tag_is_named_as_github_serves_it(
    tag: str, filename: str, url_tag: str
) -> None:
    assert GitHubProcessor.tag_archive("author", "demo", tag) == (
        filename,
        f"{REPOSITORY}/archive/refs/tags/{url_tag}.zip",
    )


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
    MODS = {"demo": {"tp2": "demo", "games": ["bg2ee"]}}

    def __init__(self, force_refresh: bool = False) -> None:
        pass

    def fetch_all(self) -> bool:
        return True

    def get_all_mods(self) -> dict[str, dict]:
        return self.MODS


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(orchestrator_module, "LCCSource", OfflineLCCSource)
    for name in ("mods", "archives", "extracted", "server"):
        (tmp_path / name).mkdir()
    return tmp_path


def publish(
    workspace: Path, filename: str, content: bytes, digest: str | None = None
) -> GitHubAsset:
    path = workspace / "server" / filename
    path.write_bytes(content)
    return GitHubAsset(name=filename, url=path.as_uri(), size=len(content), sha256=digest)


def write_definition(workspace: Path, definition: dict, tp2_name: str = "demo") -> Path:
    path = workspace / "mods" / f"{tp2_name}.json"
    path.write_text(json.dumps(definition), encoding="utf-8")
    return path


def synchronizer(
    workspace: Path, release: GitHubRelease | None, rate_limited: bool = False, **options: Any
) -> ModSyncOrchestrator:
    config = SyncConfig(
        definitions_dir=workspace / "mods",
        archives_dir=workspace / "archives",
        mods_dir=workspace / "extracted",
        check_github_versions=True,
        calculate_hashes=True,
        github_checks_file=workspace / "github_checks.json",
        github_recipes_file=workspace / "github_recipes.json",
        **options,
    )
    orchestrator = ModSyncOrchestrator(config)
    orchestrator.archive_source = ArchiveSource(workspace / "archives", PLATFORM)
    orchestrator.github_processor = FixedReleaseProcessor(release, rate_limited)
    orchestrator.github_source = GitHubSource(orchestrator.github_processor)
    return orchestrator


V1_FILES = [
    {
        "filename": "win-demo-v1.zip",
        "size": 1,
        "sha256": "v1",
        "download": "https://example.org/v1",
        "platforms": ["windows"],
    }
]


def test_github_update_is_applied_and_counted_as_updated(workspace: Path) -> None:
    windows_content = mod_archive("BACKUP ~weidu_external/backup/demo~\nVERSION ~v2~\n")
    windows = publish(
        workspace,
        "win-demo-v2.zip",
        windows_content,
        digest="api-digest-not-trusted-for-machine",
    )
    linux = GitHubAsset(
        "lin-demo-v2.zip",
        f"{REPOSITORY}/releases/download/v2/lin-demo-v2.zip",
        7,
        "linux-digest",
    )
    definition_path = write_definition(
        workspace,
        {
            "tp2": "demo",
            "version": "1",
            "games": ["bg2ee"],
            "links": {"homepage": REPOSITORY},
            "files": V1_FILES,
        },
    )

    orchestrator = synchronizer(
        workspace, GitHubRelease("v2", [windows, linux], "", False), extract_tp2=False
    )
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
    release = GitHubRelease(
        "v2",
        [publish(workspace, "win-demo-v2.zip", content, digest=sha256(content))],
        "",
        False,
    )
    write_definition(
        workspace,
        {
            "tp2": "demo",
            "version": "1",
            "games": ["bg2ee"],
            "links": {"homepage": REPOSITORY},
            "files": V1_FILES,
        },
    )
    synchronizer(workspace, release, extract_tp2=False, github_check_interval_hours=0).run()

    stats = synchronizer(
        workspace, release, extract_tp2=False, github_check_interval_hours=0
    ).run()

    assert [result.status for result in stats.results] == [SyncStatus.UNCHANGED]
    assert stats.github_outdated == []


def test_archive_of_the_previous_release_is_retired(workspace: Path) -> None:
    previous_content = b"manually downloaded v1"
    (workspace / "archives" / "win-demo-v1.zip").write_bytes(previous_content)
    previous_files = [
        {**V1_FILES[0], "size": len(previous_content), "sha256": sha256(previous_content)}
    ]
    write_definition(
        workspace,
        {
            "tp2": "demo",
            "games": ["bg2ee"],
            "links": {"homepage": REPOSITORY},
            "files": previous_files,
        },
    )
    windows = publish(workspace, "win-demo-v2.zip", b"v2")

    synchronizer(workspace, GitHubRelease("v2", [windows], "", False), extract_tp2=False).run()

    assert sorted(path.name for path in (workspace / "archives").iterdir()) == [
        "_old",
        "win-demo-v2.zip",
    ]
    retired = (
        workspace / "archives" / "_old" / f"win-demo-v1.{sha256(previous_content)[:8]}.zip"
    )
    assert retired.read_bytes() == previous_content


def test_archive_served_from_a_new_link_is_downloaded_and_hashed_again(workspace: Path) -> None:
    local_content = b"same name, same size"
    served_content = b"other content, size"
    (workspace / "archives" / "demo-v1.6.zip").write_bytes(local_content)
    forum_entry = {
        "filename": "demo-v1.6.zip",
        "size": len(local_content),
        "sha256": sha256(local_content),
        "download": "https://forum.example.org/download/913",
    }
    definition_path = write_definition(
        workspace,
        {
            "tp2": "demo",
            "games": ["bg2ee"],
            "links": {"homepage": REPOSITORY},
            "files": [forum_entry],
        },
    )
    release = GitHubRelease(
        "v1.6", [publish(workspace, "demo-v1.6.zip", served_content)], "", False
    )

    synchronizer(workspace, release, extract_tp2=False).run()

    [entry] = json.loads(definition_path.read_text(encoding="utf-8"))["files"]
    assert entry["sha256"] == sha256(served_content)
    assert (workspace / "archives" / "demo-v1.6.zip").read_bytes() == served_content
    assert (
        workspace / "archives" / "_old" / f"demo-v1.6.{sha256(local_content)[:8]}.zip"
    ).exists()


def test_identical_archive_served_from_a_new_link_keeps_its_hash(workspace: Path) -> None:
    content = b"archive moved to GitHub"
    (workspace / "archives" / "demo-v1.6.zip").write_bytes(content)
    forum_entry = {
        "filename": "demo-v1.6.zip",
        "size": len(content),
        "sha256": sha256(content),
        "download": "https://forum.example.org/913",
    }
    definition_path = write_definition(
        workspace,
        {
            "tp2": "demo",
            "games": ["bg2ee"],
            "links": {"homepage": REPOSITORY},
            "files": [forum_entry],
        },
    )
    release = GitHubRelease("v1.6", [publish(workspace, "demo-v1.6.zip", content)], "", False)

    synchronizer(workspace, release, extract_tp2=False).run()

    [entry] = json.loads(definition_path.read_text(encoding="utf-8"))["files"]
    assert entry["sha256"] == sha256(content)
    assert sorted(path.name for path in (workspace / "archives").iterdir()) == ["demo-v1.6.zip"]


def test_previous_archive_is_kept_when_the_new_one_cannot_be_downloaded(
    workspace: Path,
) -> None:
    (workspace / "archives" / "win-demo-v1.zip").write_bytes(b"v1")
    write_definition(
        workspace,
        {
            "tp2": "demo",
            "games": ["bg2ee"],
            "links": {"homepage": REPOSITORY},
            "files": V1_FILES,
        },
    )
    missing = GitHubAsset(
        "win-demo-v2.zip", (workspace / "server" / "missing.zip").as_uri(), 2, None
    )

    synchronizer(workspace, GitHubRelease("v2", [missing], "", False), extract_tp2=False).run()

    assert (workspace / "archives" / "win-demo-v1.zip").read_bytes() == b"v1"


def test_recently_checked_mod_is_not_checked_again(workspace: Path) -> None:
    write_definition(
        workspace, {"tp2": "demo", "games": ["bg2ee"], "links": {"homepage": REPOSITORY}}
    )
    synchronizer(workspace, None).run()

    deferred = synchronizer(workspace, None, github_check_interval_hours=1)
    stats = deferred.run()
    always = synchronizer(workspace, None, github_check_interval_hours=0)
    always.run()

    assert deferred.github_processor.requests == []
    assert stats.github_deferred == 1
    assert always.github_processor.requests == [("author", "demo")]


def test_provided_mod_never_takes_the_release_of_a_repository(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The LCC entry of the mod points to a fork of its provider
    monkeypatch.setattr(
        OfflineLCCSource,
        "MODS",
        {"demo": {"tp2": "demo", "games": ["bg2ee"], "links": {"homepage": REPOSITORY}}},
    )
    content = mod_archive("BACKUP ~weidu_external/backup/demo~\nVERSION ~fork~\n")
    release = GitHubRelease("fork", [publish(workspace, "demo-fork.zip", content)], "", False)
    definition_path = write_definition(
        workspace,
        {"tp2": "demo", "version": "1", "games": ["bg2ee"], "provided_by": "main"},
    )

    orchestrator = synchronizer(workspace, release)
    orchestrator.run()

    assert orchestrator.github_processor.requests == []
    assert json.loads(definition_path.read_text(encoding="utf-8"))["version"] == "1"
    assert not (workspace / "archives" / "demo-fork.zip").exists()


def test_rate_limit_stops_checks_and_leaves_mods_due(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        OfflineLCCSource,
        "MODS",
        {name: {"tp2": name, "games": ["bg2ee"]} for name in ("demo", "other")},
    )
    for name in ("demo", "other"):
        write_definition(
            workspace,
            {"tp2": name, "games": ["bg2ee"], "links": {"homepage": REPOSITORY}},
            name,
        )

    limited = synchronizer(workspace, None, rate_limited=True)
    stats = limited.run()
    retried = synchronizer(workspace, None)
    retried.run()

    assert limited.github_processor.requests == [("author", "demo")]
    assert stats.github_rate_limited
    assert "rate limit" in stats.get_github_report()
    assert len(retried.github_processor.requests) == 2


def test_changed_archive_is_extracted_again_before_parsing(workspace: Path) -> None:
    extracted_dir = workspace / "extracted"
    old_content = mod_archive("VERSION ~v1~\n", {"old.tra": "@1 = ~Old~"})
    (workspace / "archives" / "win-demo.zip").write_bytes(old_content)
    SelectiveExtractor().extract_for_parsing(
        workspace / "archives" / "win-demo.zip", extracted_dir, "demo", sha256(old_content)
    )

    new_content = mod_archive("VERSION ~v2~\n", {"new.tra": "@1 = ~New~"})
    windows = publish(workspace, "win-demo.zip", new_content, digest=sha256(new_content))
    write_definition(
        workspace,
        {
            "tp2": "demo",
            "games": ["bg2ee"],
            "links": {"homepage": REPOSITORY},
            "files": V1_FILES,
        },
    )

    synchronizer(
        workspace,
        GitHubRelease("v2", [windows], "", False),
        extract_archives=True,
        extract_tp2=False,
    ).run()

    assert (extracted_dir / "demo" / "tra" / "new.tra").is_file()
    assert not (extracted_dir / "demo" / "tra" / "old.tra").exists()
    assert "v2" in (extracted_dir / "demo" / "demo.tp2").read_text(encoding="utf-8")


def test_up_to_date_extraction_is_kept(workspace: Path) -> None:
    content = mod_archive("VERSION ~v1~\n")
    archive_path = workspace / "archives" / "win-demo.zip"
    archive_path.write_bytes(content)
    extractor = SelectiveExtractor()
    extractor.extract_for_parsing(
        archive_path, workspace / "extracted", "demo", sha256(content)
    )
    tp2_path = workspace / "extracted" / "demo" / "demo.tp2"
    tp2_path.write_text("edited after extraction", encoding="utf-8")

    assert extractor.extract_for_parsing(
        archive_path, workspace / "extracted", "demo", sha256(content)
    )
    assert tp2_path.read_text(encoding="utf-8") == "edited after extraction"


def archive_with_code(workspace: Path) -> tuple[Path, str]:
    """Write a demo mod archive holding included WeiDU files and game resources."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("demo/demo.tp2", "VERSION ~v1~\n")
        for name in ("lib/always.tph", "lib/main.tpa", "lib/patch.TPP", "copy/sword.itm"):
            archive.writestr(f"demo/{name}", "content")
    content = buffer.getvalue()
    archive_path = workspace / "archives" / "win-demo.zip"
    archive_path.write_bytes(content)
    return archive_path, sha256(content)


def test_included_weidu_files_are_extracted_but_not_game_resources(workspace: Path) -> None:
    archive_path, digest = archive_with_code(workspace)
    mod_dir = workspace / "extracted" / "demo"

    assert SelectiveExtractor().extract_for_parsing(
        archive_path, workspace / "extracted", "demo", digest
    )

    assert (mod_dir / "lib" / "always.tph").is_file()
    assert (mod_dir / "lib" / "main.tpa").is_file()
    assert (mod_dir / "lib" / "patch.TPP").is_file()
    assert not (mod_dir / "copy").exists()


class RecordingTP2Source:
    def __init__(self) -> None:
        self.parsed: list[str] = []

    def extract(
        self,
        tp2_name: str,
        supported_games: set[str] | None = None,
        labels_language: str = "en_US",
    ) -> None:
        self.parsed.append(tp2_name)


def test_extraction_without_current_archive_is_not_read(workspace: Path) -> None:
    content = mod_archive("VERSION ~v1~\n")
    archive_path = workspace / "archives" / "win-demo.zip"
    archive_path.write_bytes(content)
    SelectiveExtractor().extract_for_parsing(
        archive_path, workspace / "extracted", "demo", sha256(content)
    )
    archive_path.unlink()
    unreachable = [
        {
            **V1_FILES[0],
            "filename": "win-demo.zip",
            "download": (workspace / "server" / "missing.zip").as_uri(),
        }
    ]
    write_definition(workspace, {"tp2": "demo", "games": ["bg2ee"], "files": unreachable})

    orchestrator = synchronizer(workspace, None, extract_archives=True)
    orchestrator.tp2_source = RecordingTP2Source()
    orchestrator.run()

    assert orchestrator.tp2_source.parsed == []
