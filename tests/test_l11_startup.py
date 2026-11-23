"""Startup without blocking: catalogue update off the UI thread and self-healing caches."""

from collections.abc import Callable, Iterator
import hashlib
import importlib
import json
from pathlib import Path
import threading
from unittest.mock import Mock
import zipfile

import pytest
from pytestqt.qtbot import QtBot
import requests

from core.CatalogueUpdateTask import CatalogueUpdateOutcome, CatalogueUpdateTask
from core.DataUpdater import DataUpdater
from core.ModManager import ModManager

updater_module = importlib.import_module("core.DataUpdater")

SHORT_TIMEOUT_MS = 50
SIGNAL_TIMEOUT_MS = 10_000


def _snapshot(path: Path) -> dict[str, bytes]:
    return {
        str(item.relative_to(path)): item.read_bytes()
        for item in path.rglob("*")
        if item.is_file()
    }


def _blocking(release: threading.Event, result: Callable[..., object]) -> Callable[..., object]:
    """Return a call that waits for `release`, like a slow server, before answering."""

    def call(*args: object, **kwargs: object) -> object:
        assert release.wait(SIGNAL_TIMEOUT_MS / 1000)
        return result(*args, **kwargs)

    return call


@pytest.fixture
def release() -> Iterator[threading.Event]:
    """Unblocks the simulated slow network, even when a test fails."""
    event = threading.Event()
    yield event
    event.set()


@pytest.fixture
def published_update(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[DataUpdater, Path, Path]:
    """Updater whose remote catalogue is a valid package newer than the local one."""
    data, schemas = request.getfixturevalue("small_catalogue")
    archive = tmp_path / "published.zip"
    with zipfile.ZipFile(archive, "w") as package:
        for path in data.rglob("*.json"):
            package.write(path, path.relative_to(data))

    monkeypatch.setattr(updater_module, "DATA_DIR", data)
    monkeypatch.setattr(updater_module, "DATA_VERSION_FILE", data / "data_version.json")
    monkeypatch.setattr(updater_module, "SCHEMAS_DIR", schemas)

    updater = DataUpdater()
    version = {"sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}
    monkeypatch.setattr(updater, "_fetch_remote_version", Mock(return_value=version))
    return updater, data, archive


def _copy_archive(archive: Path) -> Callable[[str, Path], bool]:
    def download(url: str, dest: Path) -> bool:
        dest.write_bytes(archive.read_bytes())
        return True

    return download


def _run(task: CatalogueUpdateTask, qtbot: QtBot) -> CatalogueUpdateOutcome:
    with qtbot.waitSignal(task.finished, timeout=SIGNAL_TIMEOUT_MS) as finished:
        task.start()
    return finished.args[0]


# ========================================
# CATALOGUE UPDATE
# ========================================


def test_absent_network_continues_with_the_current_catalogue(
    monkeypatch: pytest.MonkeyPatch, qtbot: QtBot
) -> None:
    monkeypatch.setattr(
        updater_module.requests, "get", Mock(side_effect=requests.ConnectionError("offline"))
    )
    task = CatalogueUpdateTask(DataUpdater())

    assert _run(task, qtbot) is CatalogueUpdateOutcome.NOT_UPDATED
    assert task.errors == []
    task.wait_for_thread()


def test_slow_availability_check_times_out_and_never_updates(
    published_update: tuple[DataUpdater, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    qtbot: QtBot,
    release: threading.Event,
) -> None:
    updater, data, archive = published_update
    before = _snapshot(data)
    fetch = updater._fetch_remote_version
    monkeypatch.setattr(updater, "_fetch_remote_version", _blocking(release, fetch))
    download = Mock(side_effect=_copy_archive(archive))
    monkeypatch.setattr(updater, "_download_file", download)
    task = CatalogueUpdateTask(updater, check_timeout_ms=SHORT_TIMEOUT_MS)

    assert _run(task, qtbot) is CatalogueUpdateOutcome.CANCELLED

    release.set()
    task.wait_for_thread()
    download.assert_not_called()
    assert _snapshot(data) == before


def test_skipping_during_download_keeps_the_current_catalogue(
    published_update: tuple[DataUpdater, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    qtbot: QtBot,
    release: threading.Event,
) -> None:
    updater, data, archive = published_update
    before = _snapshot(data)
    downloading = threading.Event()

    def slow_download(url: str, dest: Path) -> bool:
        downloading.set()
        return _blocking(release, _copy_archive(archive))(url, dest)

    monkeypatch.setattr(updater, "_download_file", slow_download)
    task = CatalogueUpdateTask(updater)
    task.start()
    qtbot.waitUntil(downloading.is_set, timeout=SIGNAL_TIMEOUT_MS)

    with qtbot.waitSignal(task.finished, timeout=SIGNAL_TIMEOUT_MS) as finished:
        assert task.cancel()
    assert finished.args[0] is CatalogueUpdateOutcome.CANCELLED

    release.set()
    task.wait_for_thread()
    assert _snapshot(data) == before
    assert not data.with_name("data_backup").exists()


def test_skipping_is_refused_once_the_activation_started(
    published_update: tuple[DataUpdater, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    qtbot: QtBot,
    release: threading.Event,
) -> None:
    updater, data, archive = published_update
    activating = threading.Event()
    monkeypatch.setattr(updater, "_download_file", _copy_archive(archive))
    create_backup = updater._create_backup

    def slow_backup() -> Path | None:
        activating.set()
        return _blocking(release, create_backup)()

    monkeypatch.setattr(updater, "_create_backup", slow_backup)
    task = CatalogueUpdateTask(updater)
    task.start()
    qtbot.waitUntil(activating.is_set, timeout=SIGNAL_TIMEOUT_MS)

    assert not task.cancel()

    with qtbot.waitSignal(task.finished, timeout=SIGNAL_TIMEOUT_MS) as finished:
        release.set()
    assert finished.args[0] is CatalogueUpdateOutcome.UPDATED
    assert (data / "data_version.json").exists()


# ========================================
# CACHE PREPARATION
# ========================================


@pytest.fixture
def mods(tmp_path: Path) -> ModManager:
    definition = {"name": "alpha", "tp2": "alpha", "components": {"0": {"type": "std"}}}
    path = tmp_path / "mods" / "alpha.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({**definition, "links": {}}), encoding="utf-8")
    return ModManager(tmp_path / "mods", tmp_path / "custom", tmp_path / "cache")


def _prepare(manager: ModManager, qtbot: QtBot) -> None:
    with qtbot.waitSignal(manager.cache_ready, timeout=SIGNAL_TIMEOUT_MS):
        manager.prepare_cache()


def test_fresh_cache_is_loaded_without_rebuild(mods: ModManager, qtbot: QtBot) -> None:
    _prepare(mods, qtbot)
    rebuilt = Mock()
    mods.cache_building.connect(rebuilt)

    _prepare(mods, qtbot)

    rebuilt.assert_not_called()
    assert set(mods.get_all_mods()) == {"alpha"}


def test_corrupted_cache_is_rebuilt(mods: ModManager, qtbot: QtBot) -> None:
    _prepare(mods, qtbot)
    cache_file = mods._cache_file(mods.current_language)
    cache_file.write_text("{ truncated", encoding="utf-8")
    assert not mods.needs_cache_rebuild()

    _prepare(mods, qtbot)

    assert json.loads(cache_file.read_text(encoding="utf-8"))
    assert set(mods.get_all_mods()) == {"alpha"}


def test_builder_that_cannot_start_reports_a_terminal_error(
    mods: ModManager, monkeypatch: pytest.MonkeyPatch, qtbot: QtBot
) -> None:
    monkeypatch.setattr(
        mods, "_create_cache_builder", Mock(side_effect=RuntimeError("no thread"))
    )

    with qtbot.waitSignal(mods.cache_error, timeout=SIGNAL_TIMEOUT_MS):
        mods.prepare_cache()
