"""L5: archive identity, manual associations and their proof on disk."""

import hashlib
import json
import os
from pathlib import Path

import pytest

from constants import ARCHIVE_REGISTRY_FORMAT
from core.ArchiveRegistry import ArchiveRecord, ArchiveRegistry, ArchiveSource
from core.ArchiveService import ArchiveService
from core.DownloadManager import ArchiveStatus
from core.Mod import Mod
from core.StateManager import StateManager

PLATFORM = "windows"
CONTENT_V1 = b"archive version 1"
CONTENT_V2 = b"archive version 2"


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def archive_mod(
    sha: str | None = sha256(CONTENT_V1),
    url: str | None = "https://example.invalid/demo.zip",
    size: int = len(CONTENT_V1),
) -> Mod:
    return Mod(
        {
            "id": "demo",
            "name": "Demo",
            "links": {},
            "tp2": "demo",
            "games": ["eet"],
            "components": {"0": {"type": "std"}},
            "files": [{"filename": "demo.zip", "size": size, "sha256": sha, "download": url}],
        }
    )


def write_archive(path: Path, content: bytes, mtime_ns: int | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    if mtime_ns is not None:
        os.utime(path, ns=(mtime_ns, mtime_ns))
    return path


@pytest.fixture
def registry(tmp_path: Path) -> ArchiveRegistry:
    return ArchiveRegistry.load(tmp_path / "archives.json")


@pytest.fixture
def service(registry: ArchiveRegistry) -> ArchiveService:
    return ArchiveService(registry)


@pytest.fixture
def downloads(tmp_path: Path) -> Path:
    folder = tmp_path / "downloads"
    folder.mkdir()
    return folder


# ----------------------------------------------------------------------
# Proof follows the file on disk
# ----------------------------------------------------------------------


def test_verified_download_is_valid_without_rehash(
    service: ArchiveService, downloads: Path
) -> None:
    mod = archive_mod()
    path = write_archive(downloads / "demo.zip", CONTENT_V1)

    assert service.verify(mod, path, ArchiveSource.DOWNLOAD, PLATFORM).status is ArchiveStatus.VALID
    assert service.resolve(mod, downloads, PLATFORM).status is ArchiveStatus.VALID


def test_replaced_file_with_same_name_loses_its_proof(
    service: ArchiveService, downloads: Path
) -> None:
    mod = archive_mod()
    path = write_archive(downloads / "demo.zip", CONTENT_V1, mtime_ns=1_000_000_000)
    service.verify(mod, path, ArchiveSource.DOWNLOAD, PLATFORM)

    write_archive(path, CONTENT_V2, mtime_ns=2_000_000_000)

    resolution = service.resolve(mod, downloads, PLATFORM)
    assert resolution.status is ArchiveStatus.UNKNOWN
    assert service.verify(mod, path, ArchiveSource.DOWNLOAD, PLATFORM).status is (
        ArchiveStatus.INVALID_HASH
    )


def test_deleted_download_needs_download_again(service: ArchiveService, downloads: Path) -> None:
    mod = archive_mod()
    path = write_archive(downloads / "demo.zip", CONTENT_V1)
    service.verify(mod, path, ArchiveSource.DOWNLOAD, PLATFORM)

    path.unlink()

    assert service.resolve(mod, downloads, PLATFORM).status is ArchiveStatus.MISSING


def test_archive_without_url_is_expected_manually(
    service: ArchiveService, downloads: Path
) -> None:
    resolution = service.resolve(archive_mod(url=None), downloads, PLATFORM)

    assert resolution.status is ArchiveStatus.MANUAL_EXPECTED
    assert resolution.path is None


def test_archive_without_reference_hash_is_a_weaker_proof(
    service: ArchiveService, downloads: Path
) -> None:
    mod = archive_mod(sha=None)
    path = write_archive(downloads / "demo.zip", CONTENT_V2, mtime_ns=1_000_000_000)

    status = service.verify(mod, path, ArchiveSource.DOWNLOAD, PLATFORM).status

    assert status is ArchiveStatus.NO_REFERENCE
    assert status.is_usable and not status.is_available


# ----------------------------------------------------------------------
# Manual associations
# ----------------------------------------------------------------------


def test_manual_archive_with_other_name_takes_precedence(
    service: ArchiveService, downloads: Path, tmp_path: Path
) -> None:
    mod = archive_mod(url=None)
    manual = write_archive(tmp_path / "elsewhere" / "Demo-v1 (final).zip", CONTENT_V1)

    associated = service.associate_manual(mod, manual, PLATFORM)
    resolved = service.resolve(mod, downloads, PLATFORM)

    assert associated.status is ArchiveStatus.VALID
    assert resolved.path == manual
    assert resolved.status is ArchiveStatus.VALID


def test_mismatching_manual_archive_requires_explicit_acceptance(
    service: ArchiveService, registry: ArchiveRegistry, tmp_path: Path
) -> None:
    mod = archive_mod(size=0)
    manual = write_archive(tmp_path / "demo-fr.zip", CONTENT_V2)

    refused = service.associate_manual(mod, manual, PLATFORM)
    assert refused.status is ArchiveStatus.INVALID_HASH
    assert registry.get("demo") is None

    accepted = service.associate_manual(mod, manual, PLATFORM, accept_mismatch=True)
    assert accepted.status is ArchiveStatus.MISMATCH_ACCEPTED
    assert registry.get("demo").accepted_mismatch


def test_acceptance_does_not_cover_a_later_content(
    service: ArchiveService, tmp_path: Path
) -> None:
    mod = archive_mod(size=0)
    manual = write_archive(tmp_path / "demo-fr.zip", CONTENT_V2, mtime_ns=1_000_000_000)
    service.associate_manual(mod, manual, PLATFORM, accept_mismatch=True)

    write_archive(manual, b"another unreviewed content", mtime_ns=2_000_000_000)

    status = service.verify(mod, manual, ArchiveSource.MANUAL, PLATFORM).status
    assert status is ArchiveStatus.INVALID_HASH


def test_moved_manual_archive_is_expected_again(
    service: ArchiveService, downloads: Path, tmp_path: Path
) -> None:
    mod = archive_mod(url=None)
    manual = write_archive(tmp_path / "manual.zip", CONTENT_V1)
    service.associate_manual(mod, manual, PLATFORM)

    manual.rename(tmp_path / "moved.zip")

    assert service.resolve(mod, downloads, PLATFORM).status is ArchiveStatus.MANUAL_EXPECTED


# ----------------------------------------------------------------------
# Persistence and migration
# ----------------------------------------------------------------------


def test_proof_survives_a_new_session(registry: ArchiveRegistry, downloads: Path) -> None:
    mod = archive_mod()
    path = write_archive(downloads / "demo.zip", CONTENT_V1)
    ArchiveService(registry).verify(mod, path, ArchiveSource.DOWNLOAD, PLATFORM)

    reloaded = ArchiveService(ArchiveRegistry.load(registry.path))

    assert reloaded.resolve(mod, downloads, PLATFORM).status is ArchiveStatus.VALID


@pytest.mark.parametrize("corruption", ['{"format": ', '{"format": 99, "archives": {}}'])
def test_unreadable_registry_is_set_aside_and_restarts_empty(
    tmp_path: Path, corruption: str
) -> None:
    path = tmp_path / "archives.json"
    path.write_text(corruption, encoding="utf-8")

    registry = ArchiveRegistry.load(path)

    assert registry.get("demo") is None
    assert not path.exists()
    [kept] = tmp_path.glob("archives.json.unreadable-*")
    assert kept.read_text(encoding="utf-8") == corruption


@pytest.fixture
def state_manager(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> StateManager:
    monkeypatch.setattr(StateManager, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(StateManager, "ARCHIVE_REGISTRY_FILE", tmp_path / "archives.json")
    return StateManager()


def test_legacy_custom_archives_are_migrated_unverified(
    state_manager: StateManager, tmp_path: Path
) -> None:
    legacy = tmp_path / "legacy.zip"
    state_manager.installation_state.configuration["custom_archives"] = {"demo": str(legacy)}

    assert state_manager.get_custom_archive("demo") == legacy
    record = state_manager.get_archive_registry().get("demo")
    assert record.source is ArchiveSource.MANUAL
    assert not record.is_verified
    assert state_manager.installation_state.configuration["custom_archives"] == {}

    saved = json.loads((tmp_path / "archives.json").read_text(encoding="utf-8"))
    assert saved["format"] == ARCHIVE_REGISTRY_FORMAT
    assert saved["archives"]["demo"]["path"] == str(legacy)


def test_replayed_migration_keeps_existing_proof(registry: ArchiveRegistry, tmp_path: Path) -> None:
    path = tmp_path / "manual.zip"
    proven = ArchiveRecord.unverified("demo", path, ArchiveSource.MANUAL).with_hash(
        sha256(CONTENT_V1), len(CONTENT_V1), 1, None, False
    )
    registry.put(proven)

    assert registry.migrate_custom_archives({"demo": str(path)}) == 0
    assert registry.get("demo") == proven


def test_removing_custom_archive_keeps_download_proofs(
    state_manager: StateManager, downloads: Path
) -> None:
    path = write_archive(downloads / "demo.zip", CONTENT_V1)
    ArchiveService(state_manager.get_archive_registry()).verify(
        archive_mod(), path, ArchiveSource.DOWNLOAD, PLATFORM
    )

    assert not state_manager.has_custom_archive("demo")
    state_manager.remove_custom_archive("demo")

    assert state_manager.get_archive_registry().get("demo") is not None
