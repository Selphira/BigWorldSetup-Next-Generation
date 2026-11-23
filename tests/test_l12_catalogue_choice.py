"""L12: the shipped catalogue in data/ and the downloaded one in var/catalogue/."""

import importlib
import json
from pathlib import Path
import shutil
from unittest.mock import Mock

import pytest
from pytestqt.qtbot import QtBot
import requests

from constants import DATA_SCHEMA_VERSION
from core.CatalogueLocation import CatalogueLocation
from core.CatalogueUpdateTask import CatalogueUpdateOutcome, CatalogueUpdateTask
from core.DataUpdater import DataUpdater
import main

updater_module = importlib.import_module("core.DataUpdater")

SIGNAL_TIMEOUT_MS = 10_000
SHIPPED_TIMESTAMP = 2_000
OLDER_TIMESTAMP = 1_000
NEWER_TIMESTAMP = 3_000


def write_catalogue(root: Path, timestamp: int, schema: str = DATA_SCHEMA_VERSION) -> Path:
    for part in ("mods", "games", "rules"):
        (root / part).mkdir(parents=True, exist_ok=True)
    version = {
        "data_schema_version": schema,
        "commit_hash": f"commit-{timestamp}",
        "timestamp": timestamp,
    }
    (root / "data_version.json").write_text(json.dumps(version), encoding="utf-8")
    return root


@pytest.fixture
def shipped(tmp_path: Path) -> Path:
    return write_catalogue(tmp_path / "data", SHIPPED_TIMESTAMP)


@pytest.fixture
def downloaded_dir(tmp_path: Path) -> Path:
    return tmp_path / "var" / "catalogue"


@pytest.fixture
def updater(
    shipped: Path, downloaded_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> DataUpdater:
    monkeypatch.setattr(updater_module, "BASE_CATALOGUE_DIR", shipped)
    monkeypatch.setattr(updater_module, "DOWNLOADED_CATALOGUE_DIR", downloaded_dir)
    return DataUpdater()


def active_root(downloaded_dir: Path, shipped: Path, enabled: bool = True) -> Path:
    return CatalogueLocation.active(downloaded_dir, shipped, downloaded_enabled=enabled).root


# ========================================
# CHOICE OF THE CATALOGUE READ
# ========================================


def test_first_launch_offline_reads_the_shipped_catalogue(
    updater: DataUpdater,
    shipped: Path,
    downloaded_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    qtbot: QtBot,
) -> None:
    monkeypatch.setattr(
        updater_module.requests, "get", Mock(side_effect=requests.ConnectionError("offline"))
    )
    task = CatalogueUpdateTask(updater)

    with qtbot.waitSignal(task.finished, timeout=SIGNAL_TIMEOUT_MS) as finished:
        task.start()
    task.wait_for_thread()

    assert finished.args[0] is CatalogueUpdateOutcome.NOT_UPDATED
    assert not downloaded_dir.exists()
    assert active_root(downloaded_dir, shipped) == shipped


def test_newer_download_is_read(shipped: Path, downloaded_dir: Path) -> None:
    write_catalogue(downloaded_dir, NEWER_TIMESTAMP)

    assert active_root(downloaded_dir, shipped) == downloaded_dir


def test_shipped_catalogue_newer_than_the_download_is_read(
    shipped: Path, downloaded_dir: Path
) -> None:
    write_catalogue(downloaded_dir, OLDER_TIMESTAMP)

    assert active_root(downloaded_dir, shipped) == shipped


def test_download_of_the_same_version_as_the_shipped_one_is_not_read(
    shipped: Path, downloaded_dir: Path
) -> None:
    write_catalogue(downloaded_dir, SHIPPED_TIMESTAMP)

    assert active_root(downloaded_dir, shipped) == shipped


def test_download_is_read_over_a_shipped_catalogue_without_version(
    tmp_path: Path, downloaded_dir: Path
) -> None:
    unversioned = write_catalogue(tmp_path / "unversioned", SHIPPED_TIMESTAMP)
    (unversioned / "data_version.json").unlink()
    write_catalogue(downloaded_dir, OLDER_TIMESTAMP)

    assert active_root(downloaded_dir, unversioned) == downloaded_dir


@pytest.mark.parametrize(
    "damage",
    [
        "deleted",
        "version_deleted",
        "part_deleted",
        "version_unreadable",
        "version_not_an_object",
        "schema_incompatible",
    ],
)
def test_invalid_or_deleted_download_falls_back_to_the_shipped_catalogue(
    shipped: Path, downloaded_dir: Path, damage: str
) -> None:
    write_catalogue(downloaded_dir, NEWER_TIMESTAMP)
    version_file = downloaded_dir / "data_version.json"
    match damage:
        case "deleted":
            shutil.rmtree(downloaded_dir)
        case "version_deleted":
            version_file.unlink()
        case "part_deleted":
            shutil.rmtree(downloaded_dir / "rules")
        case "version_unreadable":
            version_file.write_text("{truncated", encoding="utf-8")
        case "version_not_an_object":
            version_file.write_text("[]", encoding="utf-8")
        case "schema_incompatible":
            write_catalogue(downloaded_dir, NEWER_TIMESTAMP, schema="999.0")

    assert active_root(downloaded_dir, shipped) == shipped


def test_run_from_the_sources_reads_only_the_shipped_catalogue(
    shipped: Path, downloaded_dir: Path
) -> None:
    write_catalogue(downloaded_dir, NEWER_TIMESTAMP)

    assert active_root(downloaded_dir, shipped, enabled=False) == shipped


# ========================================
# UPDATE DECISION
# ========================================


@pytest.mark.parametrize(
    ("remote_timestamp", "expected"),
    [(OLDER_TIMESTAMP, False), (SHIPPED_TIMESTAMP, False), (NEWER_TIMESTAMP, True)],
)
def test_only_a_published_version_newer_than_the_shipped_one_is_downloaded(
    updater: DataUpdater,
    monkeypatch: pytest.MonkeyPatch,
    remote_timestamp: int,
    expected: bool,
) -> None:
    remote = {"commit_hash": "published", "timestamp": remote_timestamp}
    monkeypatch.setattr(updater, "_fetch_remote_version", Mock(return_value=remote))

    assert updater.check_for_updates() is expected


def test_published_version_already_shipped_is_not_downloaded(
    updater: DataUpdater, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Published after the commit of its data: same catalogue, later timestamp
    remote = {"commit_hash": f"commit-{SHIPPED_TIMESTAMP}", "timestamp": NEWER_TIMESTAMP}
    monkeypatch.setattr(updater, "_fetch_remote_version", Mock(return_value=remote))

    assert not updater.check_for_updates()


def test_update_is_compared_with_the_newer_download(
    updater: DataUpdater, downloaded_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_catalogue(downloaded_dir, NEWER_TIMESTAMP)
    remote = {"commit_hash": f"commit-{NEWER_TIMESTAMP}", "timestamp": NEWER_TIMESTAMP}
    monkeypatch.setattr(updater, "_fetch_remote_version", Mock(return_value=remote))

    assert not updater.check_for_updates()


# ========================================
# STARTUP
# ========================================


@pytest.fixture
def initializer(qapp: object, monkeypatch: pytest.MonkeyPatch) -> main.ApplicationInitializer:
    initializer = main.ApplicationInitializer(qapp)
    initializer.splash = Mock()
    for stage in (
        "_recover_catalogue",
        "_load_catalogue",
        "_start_catalogue_update",
        "_is_installation_in_progress",
    ):
        monkeypatch.setattr(initializer, stage, Mock(return_value=False))
    return initializer


@pytest.mark.parametrize("enabled", [False, True])
def test_startup_updates_the_catalogue_only_when_enabled(
    initializer: main.ApplicationInitializer,
    monkeypatch: pytest.MonkeyPatch,
    enabled: bool,
) -> None:
    monkeypatch.setattr(main, "CATALOGUE_UPDATE_ENABLED", enabled)

    initializer._prepare_catalogue()

    assert initializer._start_catalogue_update.called is enabled
    assert initializer._load_catalogue.called is not enabled
