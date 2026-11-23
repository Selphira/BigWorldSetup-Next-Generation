"""Failure injection for catalogue transactions, durable state and portable paths."""

import hashlib
import importlib
import json
from pathlib import Path
import runpy
import sys
from unittest.mock import Mock
import zipfile

import pytest
from pytestqt.qtbot import QtBot

import constants
from core import atomic_io
from core.DataUpdater import DataUpdater
from core.StateManager import InstallationState, StateManager
from core.TranslationManager import TranslationManager
from ui.pages.InstallationPage import InstallationPage

updater_module = importlib.import_module("core.DataUpdater")


@pytest.fixture
def update_package(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[DataUpdater, Path]:
    data, schemas = request.getfixturevalue("small_catalogue")
    archive = tmp_path / "published.zip"
    with zipfile.ZipFile(archive, "w") as package:
        for path in data.rglob("*.json"):
            package.write(path, path.relative_to(data))
    version = {"sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}
    monkeypatch.setattr(updater_module, "DOWNLOADED_CATALOGUE_DIR", data)
    monkeypatch.setattr(updater_module, "SCHEMAS_DIR", schemas)
    updater = DataUpdater()
    monkeypatch.setattr(updater, "_fetch_remote_version", Mock(return_value=version))

    def download(url: str, dest: Path) -> bool:
        dest.write_bytes(archive.read_bytes())
        return True

    monkeypatch.setattr(updater, "_download_file", download)
    return updater, data


def snapshot(path: Path) -> dict[str, bytes]:
    return {
        str(item.relative_to(path)): item.read_bytes()
        for item in path.rglob("*")
        if item.is_file()
    }


def test_update_leaves_only_the_downloaded_catalogue(
    update_package: tuple[DataUpdater, Path],
) -> None:
    updater, data = update_package
    siblings = {path.name for path in data.parent.iterdir()}
    assert updater.update_data()
    assert json.loads((data / "data_version.json").read_text())["sha256"]
    assert {path.name for path in data.parent.iterdir()} == siblings


@pytest.mark.parametrize(
    "phase", ["_download_file", "_validate_zip", "_extract_zip", "_replace_data_directory"]
)
def test_b01_failed_catalogue_replacement_retains_recovery(
    update_package: tuple[DataUpdater, Path],
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    updater, data = update_package
    before = snapshot(data)
    failure = Mock(return_value=False)
    monkeypatch.setattr(updater, phase, failure)
    assert not updater.update_data()
    failure.assert_called_once()
    assert snapshot(data) == before


@pytest.mark.parametrize("phase", ["_create_backup", "_replace_data_directory"])
def test_catalogue_exception_rolls_back(
    update_package: tuple[DataUpdater, Path],
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    updater, data = update_package
    before = snapshot(data)
    monkeypatch.setattr(updater, phase, Mock(side_effect=OSError("disk failure")))
    assert not updater.update_data()
    assert snapshot(data) == before


def test_rollback_failure_is_recovered_on_next_launch(
    update_package: tuple[DataUpdater, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updater, data = update_package
    before = snapshot(data)
    original = Path.rename

    def fail_activation_and_restore(path: Path, target: Path) -> Path:
        if target == data:
            raise PermissionError("locked")
        return original(path, target)

    monkeypatch.setattr(Path, "rename", fail_activation_and_restore)
    assert not updater.update_data()
    assert snapshot(data.with_name("data_backup")) == before
    monkeypatch.setattr(Path, "rename", original)
    assert DataUpdater().recover_pending_update()
    assert snapshot(data) == before


@pytest.mark.parametrize("invalid", ["checksum", "schema", "reference", "metadata"])
def test_invalid_package_never_activates(
    update_package: tuple[DataUpdater, Path],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    invalid: str,
) -> None:
    updater, data = update_package
    before = snapshot(data)
    archive = tmp_path / "published.zip"
    if invalid == "metadata":
        monkeypatch.setattr(updater, "_fetch_remote_version", Mock(return_value=None))
    elif invalid == "checksum":
        archive.write_bytes(b"corrupt")
    else:
        with zipfile.ZipFile(archive, "a") as package:
            content = (
                {}
                if invalid == "schema"
                else {
                    "id": "other",
                    "name": "Other",
                    "sequences": [{"game": "bg2ee", "order": ["missing:0"]}],
                }
            )
            package.writestr("games/other.json", json.dumps(content))
        updater._fetch_remote_version.return_value["sha256"] = hashlib.sha256(
            archive.read_bytes()
        ).hexdigest()
    assert not updater.update_data()
    assert snapshot(data) == before


@pytest.fixture
def state_manager(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> StateManager:
    monkeypatch.setattr(StateManager, "STATE_FILE", tmp_path / "state.json")
    manager = StateManager.__new__(StateManager)
    manager.installation_state = InstallationState()
    return manager


def test_state_atomic_write_failure_retains_previous(
    state_manager: StateManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert state_manager.save_state()
    before = state_manager.STATE_FILE.read_bytes()
    state_manager.installation_state.installation["current_step"] = "new"
    monkeypatch.setattr(atomic_io.os, "fsync", Mock(side_effect=OSError("disk full")))
    assert not state_manager.save_state()
    assert state_manager.STATE_FILE.read_bytes() == before
    assert not list(state_manager.STATE_FILE.parent.glob("*.tmp"))


@pytest.mark.parametrize("corruption", ["{", "[]", '{"version":"future"}', None])
def test_state_recovers_last_valid_backup(
    state_manager: StateManager, corruption: str | None
) -> None:
    state_manager.installation_state.installation["current_step"] = "previous"
    assert state_manager.save_state()
    state_manager.installation_state.installation["current_step"] = "new"
    assert state_manager.save_state()
    if corruption is None:
        state_manager.STATE_FILE.unlink()
    else:
        state_manager.STATE_FILE.write_text(corruption)
    recovered = state_manager._load_installation_state()
    assert recovered.installation["current_step"] == "previous"
    state_manager.installation_state = recovered
    assert state_manager.save_state()
    backup = state_manager.STATE_FILE.with_name("state.json.backup")
    assert StateManager._read_state(backup).installation["current_step"] == "previous"


def test_paths_do_not_depend_on_cwd(tmp_path: Path) -> None:
    for path in (
        constants.BASE_CATALOGUE_DIR,
        constants.VAR_DIR,
        constants.TOOLS_DIR,
        constants.CACHE_DIR,
        constants.LOG_DIR,
        constants.RESOURCES_DIR,
        constants.STATE_FILE,
        TranslationManager.LOCALES_DIR,
    ):
        assert path.is_absolute()
        assert not path.is_relative_to(tmp_path)
    assert (TranslationManager.LOCALES_DIR / "en_US.json").is_file()


def test_frozen_paths_use_executable_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    executable = tmp_path / "portable" / "BigWorldSetup.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "bundle"), raising=False)
    monkeypatch.delenv(constants.VAR_DIR_ENVIRONMENT_VARIABLE, raising=False)
    paths = runpy.run_path(constants.__file__)
    assert paths["RESOURCE_ROOT"] == executable.parent
    assert paths["VAR_DIR"] == executable.parent / "var"
    assert paths["STATE_FILE"].is_relative_to(paths["VAR_DIR"])


def test_interrupted_activation_recovers_old_catalogue(
    update_package: tuple[DataUpdater, Path],
) -> None:
    updater, data = update_package
    before = snapshot(data)
    updater._create_backup()
    data.mkdir()
    (data / "uncommitted").write_bytes(b"new")
    assert DataUpdater().recover_pending_update()
    assert snapshot(data) == before


@pytest.mark.parametrize("failure", ["version", "commit"])
def test_preparation_and_commit_failures_preserve_catalogue(
    update_package: tuple[DataUpdater, Path],
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    updater, data = update_package
    before = snapshot(data)
    if failure == "version":
        monkeypatch.setattr(
            updater_module, "write_json_atomic", Mock(side_effect=OSError("disk full"))
        )
    else:
        rename = Path.rename

        def fail_commit(path: Path, target: Path) -> Path:
            if target.name == "previous":
                raise PermissionError("locked")
            return rename(path, target)

        monkeypatch.setattr(Path, "rename", fail_commit)
    assert not updater.update_data()
    assert snapshot(data) == before


def test_state_replace_failure_retains_valid_files(
    state_manager: StateManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert state_manager.save_state()
    before = state_manager.STATE_FILE.read_bytes()
    state_manager.installation_state.installation["current_step"] = "new"
    replace = Path.replace

    def fail_state_replace(path: Path, target: Path) -> Path:
        if target == state_manager.STATE_FILE:
            raise PermissionError("locked")
        return replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_state_replace)
    assert not state_manager.save_state()
    assert state_manager.STATE_FILE.read_bytes() == before
    assert state_manager.STATE_FILE.with_name("state.json.backup").read_bytes() == before


def test_page_reports_persistence_failure(
    state_manager: StateManager,
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_manager._mod_manager = Mock()
    page = InstallationPage(state_manager)
    qtbot.addWidget(page)
    monkeypatch.setattr(state_manager, "save_state", Mock(return_value=False))
    dialog = Mock()
    monkeypatch.setattr("ui.pages.InstallationPage.QMessageBox.critical", dialog)
    page.save_state()
    dialog.assert_called_once()
