"""L8: mod_sync hashes the machine archives and keeps those of other platforms."""

import hashlib
import json
from pathlib import Path

import pytest

from util.mod_sync import orchestrator as orchestrator_module
from util.mod_sync.models.sync_result import ModFilter, SyncConfig, SyncStatus
from util.mod_sync.orchestrator import ModSyncOrchestrator
from util.mod_sync.sources.archive_source import ArchiveSource

PLATFORM = "windows"
OLD_CONTENT = b"old archive"
NEW_CONTENT = b"new archive"


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


@pytest.fixture
def archives_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "archives"
    directory.mkdir()
    return directory


@pytest.fixture
def server_dir(tmp_path: Path) -> Path:
    """Directory served through file:// URLs, standing for the mod host."""
    directory = tmp_path / "server"
    directory.mkdir()
    return directory


def published(server_dir: Path, filename: str, content: bytes) -> str:
    path = server_dir / filename
    path.write_bytes(content)
    return path.as_uri()


def entry(filename: str, download: str, content: bytes, platforms: list[str] | None = None) -> dict:
    file_entry = {"filename": filename, "size": len(content), "sha256": sha256(content), "download": download}
    if platforms:
        file_entry["platforms"] = platforms
    return file_entry


# ----------------------------------------------------------------------
# ArchiveSource
# ----------------------------------------------------------------------


def test_matching_local_archive_is_used_without_download(archives_dir: Path) -> None:
    (archives_dir / "demo.zip").write_bytes(OLD_CONTENT)
    file_entry = entry("demo.zip", "file:///unreachable/demo.zip", OLD_CONTENT)

    archive = ArchiveSource(archives_dir, PLATFORM).process(file_entry)

    assert archive.apply_to(file_entry) == file_entry


def test_missing_archive_is_downloaded_and_hashed(archives_dir: Path, server_dir: Path) -> None:
    url = published(server_dir, "demo.zip", NEW_CONTENT)

    archive = ArchiveSource(archives_dir, PLATFORM).process({"filename": "demo.zip", "download": url})

    assert (archive.filename, archive.size, archive.sha256) == ("demo.zip", len(NEW_CONTENT), sha256(NEW_CONTENT))
    assert (archives_dir / "demo.zip").read_bytes() == NEW_CONTENT


def test_divergent_archive_is_downloaded_again_and_previous_one_retired(
    archives_dir: Path, server_dir: Path
) -> None:
    (archives_dir / "demo.zip").write_bytes(OLD_CONTENT)
    url = published(server_dir, "demo.zip", NEW_CONTENT)
    file_entry = entry("demo.zip", url, NEW_CONTENT)

    archive = ArchiveSource(archives_dir, PLATFORM).process(file_entry)

    assert archive.sha256 == sha256(NEW_CONTENT)
    assert (archives_dir / "demo.zip").read_bytes() == NEW_CONTENT
    retired = archives_dir / "_old" / f"demo.{sha256(OLD_CONTENT)[:8]}.zip"
    assert retired.read_bytes() == OLD_CONTENT


def test_failed_download_leaves_divergent_archive_in_place(archives_dir: Path, server_dir: Path) -> None:
    (archives_dir / "demo.zip").write_bytes(OLD_CONTENT)
    file_entry = entry("demo.zip", (server_dir / "missing.zip").as_uri(), NEW_CONTENT)

    assert ArchiveSource(archives_dir, PLATFORM).process(file_entry) is None
    assert (archives_dir / "demo.zip").read_bytes() == OLD_CONTENT
    assert sorted(path.name for path in archives_dir.iterdir()) == ["demo.zip"]


@pytest.mark.parametrize(
    ("platforms", "applies"),
    [(None, True), ([], True), (["windows"], True), (["linux", "windows"], True), (["macos"], False)],
)
def test_only_entries_of_the_machine_platform_are_processed(
    archives_dir: Path, platforms: list[str] | None, applies: bool
) -> None:
    file_entry = {"filename": "demo.zip", "platforms": platforms}

    assert ArchiveSource(archives_dir, PLATFORM).applies_to_platform(file_entry) is applies


# ----------------------------------------------------------------------
# Orchestrator
# ----------------------------------------------------------------------


class OfflineLCCSource:
    def __init__(self, force_refresh: bool = False) -> None:
        pass

    def fetch_all(self) -> bool:
        return True

    def get_all_mods(self) -> dict[str, dict]:
        return {"demo": {"tp2": "demo", "games": ["bg2ee"]}}


@pytest.fixture
def definitions_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(orchestrator_module, "LCCSource", OfflineLCCSource)
    directory = tmp_path / "mods"
    directory.mkdir()
    return directory


def test_sync_updates_machine_archive_and_keeps_other_platforms(
    definitions_dir: Path, archives_dir: Path, server_dir: Path
) -> None:
    (archives_dir / "win-demo.zip").write_bytes(OLD_CONTENT)
    linux = entry("lin-demo.zip", "https://example.org/lin-demo.zip", b"linux", ["linux"])
    windows = entry("win-demo.zip", published(server_dir, "win-demo.zip", NEW_CONTENT), OLD_CONTENT, ["windows"])
    windows["sha256"] = "stale-hash"
    definition_path = definitions_dir / "demo.json"
    definition_path.write_text(json.dumps({"tp2": "demo", "games": ["bg2ee"], "files": [linux, windows]}), encoding="utf-8")
    config = SyncConfig(
        definitions_dir=definitions_dir,
        archives_dir=archives_dir,
        calculate_hashes=True,
        mod_filter=ModFilter(),
    )

    orchestrator = ModSyncOrchestrator(config)
    orchestrator.archive_source = ArchiveSource(archives_dir, PLATFORM)

    stats = orchestrator.run()

    assert [result.status for result in stats.results] == [SyncStatus.UPDATED]
    files = json.loads(definition_path.read_text(encoding="utf-8"))["files"]
    assert files == [linux, {**windows, "size": len(NEW_CONTENT), "sha256": sha256(NEW_CONTENT)}]
    assert (archives_dir / "_old" / f"win-demo.{sha256(OLD_CONTENT)[:8]}.zip").exists()
