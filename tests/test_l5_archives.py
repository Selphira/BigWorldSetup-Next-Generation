"""L5: archive identity, manual associations and their proof on disk."""

import hashlib
import json
import os
from pathlib import Path

from PySide6.QtCore import QByteArray, QObject, Qt, QTimer, Signal
from PySide6.QtNetwork import QNetworkReply, QNetworkRequest
import pytest
from pytestqt.qtbot import QtBot

from constants import ARCHIVE_REGISTRY_FORMAT, DOWNLOAD_MAX_ATTEMPTS
from core import DownloadManager as download_module
from core.ArchiveRegistry import ArchiveRecord, ArchiveRegistry, ArchiveSource
from core.ArchiveService import ArchiveService
from core.DownloadManager import ArchiveInfo, ArchiveStatus, DownloadWorker
from core.Mod import Mod
from core.StateManager import StateManager
from core.TranslationManager import tr
from tests.conftest import ModCatalogue
from ui.pages.DownloadPage import (
    COL_MOD_NAME,
    COL_ORDER,
    COL_STATUS,
    DownloadPage,
    VerificationJob,
)

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

    assert (
        service.verify(mod, path, ArchiveSource.DOWNLOAD, PLATFORM).status
        is ArchiveStatus.VALID
    )
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


def test_deleted_download_needs_download_again(
    service: ArchiveService, downloads: Path
) -> None:
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

    reviewed = service.associate_manual(mod, manual, PLATFORM)
    assert reviewed.status is ArchiveStatus.INVALID_HASH
    assert registry.get("demo") is None

    accepted = service.accept_mismatch(mod, reviewed.record, PLATFORM)
    assert accepted.status is ArchiveStatus.MISMATCH_ACCEPTED
    assert registry.get("demo").accepted_mismatch


def test_acceptance_does_not_cover_a_later_content(
    service: ArchiveService, tmp_path: Path
) -> None:
    mod = archive_mod(size=0)
    manual = write_archive(tmp_path / "demo-fr.zip", CONTENT_V2, mtime_ns=1_000_000_000)
    reviewed = service.associate_manual(mod, manual, PLATFORM)
    service.accept_mismatch(mod, reviewed.record, PLATFORM)

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


def test_replayed_migration_keeps_existing_proof(
    registry: ArchiveRegistry, tmp_path: Path
) -> None:
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


def test_acceptance_requires_the_reviewed_content(
    service: ArchiveService, tmp_path: Path
) -> None:
    mod = archive_mod(size=0)
    manual = write_archive(tmp_path / "demo-fr.zip", CONTENT_V2, mtime_ns=1_000_000_000)
    reviewed = service.associate_manual(mod, manual, PLATFORM)

    write_archive(manual, b"changed after review", mtime_ns=2_000_000_000)

    assert (
        service.accept_mismatch(mod, reviewed.record, PLATFORM).status is ArchiveStatus.UNKNOWN
    )


# ----------------------------------------------------------------------
# Download worker against a local reply
# ----------------------------------------------------------------------


class LocalReply(QObject):
    """Stand-in for QNetworkReply delivering a scripted response."""

    readyRead = Signal()
    downloadProgress = Signal(object, object)
    finished = Signal()

    def __init__(
        self,
        body: bytes = b"",
        status: int | None = 200,
        content_type: str = "application/zip",
        error: QNetworkReply.NetworkError = QNetworkReply.NetworkError.NoError,
    ) -> None:
        super().__init__()
        self._body = body
        self._status = status
        self._content_type = content_type
        self._error = error
        self.aborted = False

    def deliver(self) -> None:
        if self._body and not self.aborted:
            self.readyRead.emit()
        self.finished.emit()

    def readAll(self) -> QByteArray:
        body, self._body = self._body, b""
        return QByteArray(body)

    def abort(self) -> None:
        self.aborted = True
        if self._error == QNetworkReply.NetworkError.NoError:
            self._error = QNetworkReply.NetworkError.OperationCanceledError

    def error(self) -> QNetworkReply.NetworkError:
        return self._error

    def errorString(self) -> str:
        return self._error.name

    def attribute(self, attribute: QNetworkRequest.Attribute) -> int | None:
        return self._status

    def header(self, header: QNetworkRequest.KnownHeaders) -> str:
        return self._content_type


def run_worker(
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
    downloads_dir: Path,
    replies: list[LocalReply],
    expected_hash: str = "",
) -> tuple[list[str], list[str]]:
    monkeypatch.setattr(download_module, "DOWNLOAD_RETRY_BASE_DELAY_MS", 1)
    info = ArchiveInfo("demo", "demo.zip", "https://example.invalid/demo.zip", expected_hash)
    worker = DownloadWorker(info, downloads_dir)

    def send(request: QNetworkRequest) -> LocalReply:
        reply = replies.pop(0)
        QTimer.singleShot(0, reply.deliver)
        return reply

    monkeypatch.setattr(worker, "_send_request", send)
    finished, errors = [], []
    worker.finished.connect(finished.append)
    worker.error.connect(errors.append)
    worker.start_download()
    qtbot.waitUntil(lambda: bool(finished or errors))
    return finished, errors


def test_html_page_is_rejected_and_previous_archive_kept(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, downloads: Path
) -> None:
    existing = write_archive(downloads / "demo.zip", CONTENT_V1)
    reply = LocalReply(b"<html>login</html>", content_type="text/html; charset=utf-8")

    finished, errors = run_worker(qtbot, monkeypatch, downloads, [reply])

    assert not finished and errors == [tr("page.download.error.html")]
    assert existing.read_bytes() == CONTENT_V1
    assert not (downloads / "demo.zip.part").exists()


def test_not_found_is_an_explicit_error_without_retry(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, downloads: Path
) -> None:
    replies = [
        LocalReply(status=404, error=QNetworkReply.NetworkError.ContentNotFoundError),
        LocalReply(CONTENT_V1),
    ]

    _, errors = run_worker(qtbot, monkeypatch, downloads, replies)

    assert errors == [tr("page.download.error.http_status", status=404)]
    assert len(replies) == 1


def test_timeout_is_retried_then_succeeds(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, downloads: Path
) -> None:
    replies = [
        LocalReply(status=None, error=QNetworkReply.NetworkError.TimeoutError),
        LocalReply(CONTENT_V1),
    ]

    finished, errors = run_worker(qtbot, monkeypatch, downloads, replies)

    assert finished == [str(downloads / "demo.zip")] and not errors
    assert (downloads / "demo.zip").read_bytes() == CONTENT_V1


def test_retries_are_bounded(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, downloads: Path
) -> None:
    replies = [
        LocalReply(status=None, error=QNetworkReply.NetworkError.TimeoutError)
        for _ in range(DOWNLOAD_MAX_ATTEMPTS + 1)
    ]

    _, errors = run_worker(qtbot, monkeypatch, downloads, replies)

    assert errors == ["TimeoutError"]
    assert len(replies) == 1


def test_download_differing_from_catalogue_keeps_previous_archive(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, downloads: Path
) -> None:
    existing = write_archive(downloads / "demo.zip", CONTENT_V1)

    finished, errors = run_worker(
        qtbot,
        monkeypatch,
        downloads,
        [LocalReply(CONTENT_V2)],
        expected_hash=sha256(CONTENT_V1),
    )

    assert not finished and errors == [tr("page.download.error.kept_previous")]
    assert existing.read_bytes() == CONTENT_V1


def test_matching_download_replaces_previous_archive(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, downloads: Path
) -> None:
    write_archive(downloads / "demo.zip", b"corrupted")

    finished, _ = run_worker(
        qtbot,
        monkeypatch,
        downloads,
        [LocalReply(CONTENT_V1)],
        expected_hash=sha256(CONTENT_V1),
    )

    assert finished and (downloads / "demo.zip").read_bytes() == CONTENT_V1


def test_unwritable_destination_is_reported(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, errors = run_worker(qtbot, monkeypatch, tmp_path / "absent", [LocalReply(CONTENT_V1)])

    prefix = tr("page.download.error.disk", error="")
    assert len(errors) == 1 and errors[0].startswith(prefix)


def test_archive_without_url_or_name_fails_cleanly(tmp_path: Path) -> None:
    worker = DownloadWorker(ArchiveInfo("demo", None, None, ""), tmp_path)
    errors = []
    worker.error.connect(errors.append)

    worker.start_download()

    assert errors == [tr("page.download.error.no_url")]


def test_progress_above_two_gibibytes_crosses_threads(qtbot: QtBot, tmp_path: Path) -> None:
    worker = DownloadWorker(ArchiveInfo("demo", "demo.zip", "u", ""), tmp_path)
    received = []
    worker.progress.connect(
        lambda done, total, speed: received.append((done, total)),
        Qt.ConnectionType.QueuedConnection,
    )
    size = 5 * 2**30

    worker.progress.emit(size - 1, size, 1.0)

    qtbot.waitUntil(lambda: received == [(size - 1, size)])


# ----------------------------------------------------------------------
# Download page
# ----------------------------------------------------------------------


@pytest.fixture
def download_page(
    qtbot: QtBot, state_manager: StateManager, downloads: Path, monkeypatch: pytest.MonkeyPatch
) -> DownloadPage:
    mods = [
        archive_mod(),
        Mod(
            {
                "id": "manual",
                "name": "Manual",
                "links": {"homepage": "https://example.invalid/manual"},
                "files": [{"filename": "manual.zip", "size": 0, "sha256": None}],
            }
        ),
    ]
    return make_download_page(
        qtbot, state_manager, downloads, monkeypatch, mods, ["demo:0", "manual:0"]
    )


def make_download_page(
    qtbot: QtBot,
    state_manager: StateManager,
    downloads: Path,
    monkeypatch: pytest.MonkeyPatch,
    mods: list[Mod],
    selection: list[str],
) -> DownloadPage:
    catalogue = ModCatalogue(mods)
    monkeypatch.setattr(state_manager, "get_mod_manager", lambda: catalogue)
    state_manager.set_selected_components(selection)
    state_manager.set_download_folder(str(downloads))
    page = DownloadPage(state_manager)
    qtbot.addWidget(page)
    page.retranslate_ui()
    return page


def test_provided_mod_requires_its_unselected_provider_archive(
    qtbot: QtBot,
    state_manager: StateManager,
    downloads: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundled = Mod({"id": "bundled", "name": "Bundled", "links": {}, "provided_by": "Demo"})
    page = make_download_page(
        qtbot, state_manager, downloads, monkeypatch, [archive_mod(), bundled], ["bundled:0"]
    )

    page.on_page_shown()

    assert set(page._archives) == {"demo"}
    assert page._archive_status["demo"] is ArchiveStatus.MISSING
    provided_row = page._find_row("bundled")
    status_text = page._archive_table.item(provided_row, COL_STATUS).text()
    assert status_text == tr("page.download.status.provided", provider="Demo")
    assert page._selected_mod_id(page._archive_table.item(provided_row, COL_MOD_NAME)) == "demo"
    assert not page.can_go_to_next_page()

    write_archive(downloads / "demo.zip", CONTENT_V1)
    page._verify_archives(["demo"])
    qtbot.waitUntil(lambda: not page._is_verifying)

    assert page.can_go_to_next_page()


def test_archives_are_listed_in_install_order_with_provided_mods_below_provider(
    qtbot: QtBot,
    state_manager: StateManager,
    downloads: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other = Mod({"id": "aaa", "name": "Zulu", "links": {}})
    bundled = Mod({"id": "bundled", "name": "Bundled", "links": {}, "provided_by": "Demo"})
    page = make_download_page(
        qtbot,
        state_manager,
        downloads,
        monkeypatch,
        [archive_mod(), other, bundled],
        ["aaa:0", "bundled:0", "demo:0"],
    )
    state_manager.set_install_order({0: ["demo:0", "bundled:0", "aaa:0"]})

    page.on_page_shown()

    table = page._archive_table
    rows = [
        (table.item(row, COL_ORDER).text(), table.item(row, COL_MOD_NAME).text())
        for row in range(table.rowCount())
    ]
    assert rows == [("1", "Demo"), ("", "Bundled"), ("2", "Zulu")]
    assert list(page._archives) == ["demo", "aaa"]


def test_install_order_sort_puts_weidu_first_and_unordered_mods_last(
    state_manager: StateManager,
) -> None:
    state_manager.set_install_order({1: ["late:0"], 0: ["Early:0", "late:1", "early:1"]})

    ordered = state_manager.sort_by_install_order({"zeta", "early", "late", "weidu64", "alpha"})

    assert ordered == ["weidu64", "early", "late", "alpha", "zeta"]


def test_mod_without_url_is_listed_and_blocks_navigation(download_page: DownloadPage) -> None:
    download_page.on_page_shown()

    assert download_page._archive_status["manual"] is ArchiveStatus.MANUAL_EXPECTED
    assert download_page._find_row("manual") is not None
    assert not download_page.can_go_to_next_page()


def test_page_hashes_in_a_worker_and_accepts_weaker_proofs(
    qtbot: QtBot, download_page: DownloadPage, downloads: Path, tmp_path: Path
) -> None:
    write_archive(downloads / "demo.zip", CONTENT_V1)
    manual = write_archive(tmp_path / "Manual v2.zip", CONTENT_V2)
    download_page.on_page_shown()
    assert download_page._archive_status["demo"] is ArchiveStatus.UNKNOWN

    download_page._verify_archives(["demo"])
    manual_mod = download_page._mod_manager.get_mod_by_id("manual")
    download_page._queue_verifications(
        {"manual": VerificationJob(manual_mod, manual, ArchiveSource.MANUAL, associate=True)},
        then_download=False,
    )
    qtbot.waitUntil(lambda: not download_page._is_verifying)

    assert download_page._archive_status["demo"] is ArchiveStatus.VALID
    assert download_page._archive_status["manual"] is ArchiveStatus.NO_REFERENCE
    assert download_page._displayed_filename("manual") == "Manual v2.zip"
    assert download_page.can_go_to_next_page()


def test_replaced_archive_is_unknown_on_next_visit(
    qtbot: QtBot, download_page: DownloadPage, downloads: Path
) -> None:
    path = write_archive(downloads / "demo.zip", CONTENT_V1, mtime_ns=1_000_000_000)
    download_page.on_page_shown()
    download_page._verify_archives(["demo"])
    qtbot.waitUntil(lambda: not download_page._is_verifying)
    assert download_page._archive_status["demo"] is ArchiveStatus.VALID

    write_archive(path, CONTENT_V2, mtime_ns=2_000_000_000)
    download_page.on_page_shown()

    assert download_page._archive_status["demo"] is ArchiveStatus.UNKNOWN
