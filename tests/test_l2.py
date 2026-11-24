"""Backup integrity, interruption recovery and non-destructive path validation."""

from datetime import datetime
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core.BackupInfo import BackupInfo
from core.BackupManager import BackupManager
from core.BackupNames import BackupNames
from core.BackupSnapshot import BackupSnapshot
from core.validators.FolderRelationshipValidator import FolderRelationshipValidator
from core.validators.FolderValidator import (
    WritableFolderValidator,
)


@pytest.fixture
def backup(tmp_path: Path) -> tuple[BackupManager, Path, str]:
    game = tmp_path / "Jeu d'épreuve 日本"
    game.mkdir()
    for name in (
        "chitin.key",
        "root.bif",
        "Manuals/readme",
        "data/base.bif",
        "sod-dlc/base.zip",
        "mod/backup/0/original",
        "weidu.log",
        "save/slot/data",
        "portraits/custom.bmp",
    ):
        path = game / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(name.encode())
    (game / "empty").mkdir()
    manager = BackupManager()
    manager.set_backup_root(tmp_path / "backups")
    success, info, error = manager.create_backup("bg2ee", "BG2EE", game, Mock())
    assert success, error
    assert info is not None
    return manager, game, info.backup_id


def test_name_and_notes_given_at_creation_are_kept(tmp_path: Path) -> None:
    game = tmp_path / "game"
    game.mkdir()
    (game / "chitin.key").write_bytes(b"key")
    manager = BackupManager()
    manager.set_backup_root(tmp_path / "backups")
    success, info, error = manager.create_backup(
        "bg2ee", "BG2EE", game, Mock(), custom_name="Before BWS", notes="Clean install"
    )
    assert success, error
    assert info is not None
    stored = manager.load_backup_info(info.backup_id)
    assert stored is not None
    assert (stored.display_name, stored.notes) == ("Before BWS", "Clean install")


def _named_backup(backup_id: str, game_code: str, custom_name: str = "") -> BackupInfo:
    return BackupInfo(
        backup_id=backup_id,
        game_code=game_code,
        game_name=game_code,
        game_path=Path("game"),
        creation_date=datetime(2026, 11, 24),
        total_size=0,
        file_count=0,
        custom_name=custom_name,
    )


@pytest.fixture
def backup_names() -> list[BackupInfo]:
    return [
        _named_backup("BG2EE_1", "bg2ee", "Before BWS"),
        _named_backup("BG2EE_2", "bg2ee"),
        _named_backup("BGEE_1", "bgee", "Vanilla"),
    ]


@pytest.mark.parametrize("name", ["Before BWS", "  before bws ", "BG2EE_2"])
def test_name_shown_by_another_backup_of_the_game_is_used(
    backup_names: list[BackupInfo], name: str
) -> None:
    assert BackupNames(backup_names, ["bg2ee"]).contains(name)


@pytest.mark.parametrize("name", ["", "   ", "Vanilla", "After BWS"])
def test_empty_or_other_installation_name_is_free(
    backup_names: list[BackupInfo], name: str
) -> None:
    assert not BackupNames(backup_names, ["bg2ee"]).contains(name)


def test_installation_spanning_two_games_shares_its_names(
    backup_names: list[BackupInfo],
) -> None:
    eet_names = BackupNames(backup_names, ["bgee", "bg2ee"])
    assert eet_names.contains("Vanilla") and eet_names.contains("Before BWS")


def test_renamed_backup_may_keep_its_own_name(backup_names: list[BackupInfo]) -> None:
    assert not BackupNames(backup_names, ["bg2ee"], "BG2EE_1").contains("Before BWS")


def test_full_snapshot_and_current_user_data(backup: tuple[BackupManager, Path, str]) -> None:
    manager, game, identifier = backup
    before = BackupSnapshot(game).inventory()
    (game / "chitin.key").write_bytes(b"modified")
    (game / "root.bif").unlink()
    (game / "added_mod").mkdir()
    (game / "added_mod" / "extra").write_bytes(b"added")
    (game / "save" / "slot" / "data").write_bytes(b"new save")
    (game / "portraits" / "custom.bmp").unlink()
    current = BackupSnapshot(game).inventory()
    success, recovery, error = manager.restore_backup(identifier, game, Mock())
    assert success, error
    assert recovery is not None
    assert BackupSnapshot(game).inventory() == BackupSnapshot.restored_manifest(before, current)
    assert BackupSnapshot(recovery / "original").inventory() == current
    assert manager.validate_backup(identifier) == (True, "")
    assert json.loads((recovery / manager.JOURNAL_FILE).read_text())["phase"] == "complete"


@pytest.mark.parametrize(
    "damage", ["content", "missing", "extra", "directory", "metadata", "manifest"]
)
def test_corrupt_backup_never_moves_game(
    backup: tuple[BackupManager, Path, str],
    damage: str,
) -> None:
    manager, game, identifier = backup
    directory = manager._backup_directory(identifier)
    stored = directory / manager.GAME_FILES_DIR
    if damage == "content":
        path = stored / "chitin.key"
        path.write_bytes(b"x" * path.stat().st_size)
    elif damage == "missing":
        (stored / "chitin.key").unlink()
    elif damage == "extra":
        (stored / "extra").write_bytes(b"extra")
    elif damage == "directory":
        (stored / "empty").rmdir()
    elif damage == "manifest":
        (directory / manager.MANIFEST_FILE).unlink()
    else:
        path = directory / manager.METADATA_FILE
        data = json.loads(path.read_text())
        data["game_code"] = "other"
        path.write_text(json.dumps(data))
    before = BackupSnapshot(game).inventory()
    assert not manager.validate_backup(identifier)[0]
    success, recovery, error = manager.restore_backup(identifier, game, Mock())
    assert not success and error and recovery is None
    assert BackupSnapshot(game).inventory() == before


@pytest.mark.parametrize("boundary", ["prepared", "original_saved", "complete", "publish"])
def test_interrupted_restore_retains_original_and_can_roll_back(
    backup: tuple[BackupManager, Path, str],
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    import importlib

    module = importlib.import_module("core.RestoreTransaction")
    manager, game, identifier = backup
    (game / "chitin.key").write_bytes(b"current modified game")
    original = BackupSnapshot(game).inventory()
    write = module.write_json_atomic
    rename = Path.rename

    def write_fault(path: Path, value: dict) -> None:
        if value.get("phase") == boundary:
            raise OSError("injected journal failure")
        write(path, value)

    def rename_fault(path: Path, target: Path) -> Path:
        if boundary == "publish" and path.name == "staged":
            raise OSError("injected publication failure")
        return rename(path, target)

    with monkeypatch.context() as patch:
        patch.setattr(module, "write_json_atomic", write_fault)
        patch.setattr(Path, "rename", rename_fault)
        success, recovery, error = manager.restore_backup(identifier, game, Mock())
    assert not success and recovery is not None and str(recovery) in error
    if (recovery / "original").exists():
        assert BackupSnapshot(recovery / "original").inventory() == original
    else:
        assert BackupSnapshot(game).inventory() == original
    assert manager.recover_restore(recovery) == (True, "")
    assert BackupSnapshot(game).inventory() == original
    assert manager.recover_restore(recovery) == (True, "")


def test_copy_failure_keeps_game_and_does_not_publish_backup(
    backup: tuple[BackupManager, Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, game, identifier = backup
    original = BackupSnapshot(game).inventory()
    monkeypatch.setattr(manager, "_copy_files", Mock(side_effect=OSError("disk full")))
    assert not manager.create_backup("bg2ee", "BG2EE", game, Mock())[0]
    assert [info.backup_id for info in manager.list_backups()] == [identifier]
    success, recovery, _ = manager.restore_backup(identifier, game, Mock())
    assert not success and recovery is not None
    assert BackupSnapshot(game).inventory() == original


def test_initial_journal_failure_reports_directory_and_keeps_game(
    backup: tuple[BackupManager, Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, game, identifier = backup
    original = BackupSnapshot(game).inventory()
    monkeypatch.setattr(
        "core.RestoreTransaction.write_json_atomic",
        Mock(side_effect=OSError("journal unavailable")),
    )

    success, recovery_dir, error = manager.restore_backup(identifier, game, Mock())

    assert not success
    assert recovery_dir is not None and recovery_dir.is_dir()
    assert str(recovery_dir) in error
    assert BackupSnapshot(game).inventory() == original


def test_space_preflight_on_both_destination_volumes(
    backup: tuple[BackupManager, Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, game, identifier = backup
    usage = Mock(return_value=SimpleNamespace(free=0))
    monkeypatch.setattr("shutil.disk_usage", usage)
    before = BackupSnapshot(game).inventory()
    assert not manager.create_backup("bg2ee", "BG2EE", game, Mock())[0]
    assert usage.call_args.args[0] == manager._backup_root
    assert not manager.restore_backup(identifier, game, Mock())[0]
    assert usage.call_args.args[0] == game.parent
    assert BackupSnapshot(game).inventory() == before


@pytest.mark.parametrize(
    "identifier", ["..", "../outside", "a/b", "a\\b", "C:game", ".candidate_x", "game."]
)
def test_backup_id_cannot_escape_root(tmp_path: Path, identifier: str) -> None:
    manager = BackupManager()
    manager.set_backup_root(tmp_path)
    assert not manager.delete_backup(identifier)[0]
    assert manager.load_backup_info(identifier) is None


@pytest.mark.parametrize("relation", ["same", "inside", "outside"])
def test_nested_paths_are_rejected(tmp_path: Path, relation: str) -> None:
    game = tmp_path / "game"
    game.mkdir()
    (game / "chitin.key").write_bytes(b"game")
    manager = BackupManager()
    manager.set_backup_root(
        {"same": game, "inside": game / "backup", "outside": tmp_path}[relation]
    )
    assert not manager.create_backup("game", "Game", game, Mock())[0]
    assert (game / "chitin.key").read_bytes() == b"game"


def test_other_target_is_rejected(
    backup: tuple[BackupManager, Path, str], tmp_path: Path
) -> None:
    manager, _, identifier = backup
    other = tmp_path / "other"
    other.mkdir()
    (other / "chitin.key").write_bytes(b"other version")
    before = BackupSnapshot(other).inventory()
    assert not manager.restore_backup(identifier, other, Mock())[0]
    assert BackupSnapshot(other).inventory() == before


def test_write_probe_preserves_existing_file(tmp_path: Path) -> None:
    sentinel = tmp_path / ".write_test"
    sentinel.write_bytes(b"keep")
    assert WritableFolderValidator().validate(str(tmp_path)) == (True, "")
    assert sentinel.read_bytes() == b"keep"
    assert list(tmp_path.iterdir()) == [sentinel]


def test_archive_and_game_roots_must_be_independent(tmp_path: Path) -> None:
    game, backups, archives = (tmp_path / name for name in ("game", "backups", "archives"))
    assert FolderRelationshipValidator().validate([str(game), str(backups), str(archives)])[0]
    assert not FolderRelationshipValidator().validate(
        [str(game), str(backups), str(game / "archives")]
    )[0]
    assert not FolderRelationshipValidator().validate([str(game), str(backups), str(backups)])[
        0
    ]


def test_junction_is_rejected_without_touching_target(
    backup: tuple[BackupManager, Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, game, identifier = backup
    original = BackupSnapshot(game).inventory()
    junction = game / "data"
    with monkeypatch.context() as patch:
        patch.setattr(Path, "is_junction", lambda path: path == junction)
        assert not manager.create_backup("bg2ee", "BG2EE", game, Mock())[0]
        assert not manager.restore_backup(identifier, game, Mock())[0]
    assert BackupSnapshot(game).inventory() == original


def test_corrupted_user_save_copy_never_replaces_game(
    backup: tuple[BackupManager, Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, game, identifier = backup
    original = BackupSnapshot(game).inventory()
    transfer = manager._transfer

    def corrupt(source: Path, destination: Path, *, move: bool) -> None:
        transfer(source, destination, move=move)
        if source.name == "save":
            (destination / "slot" / "data").write_bytes(b"corrupt")

    monkeypatch.setattr(manager, "_transfer", corrupt)
    assert not manager.restore_backup(identifier, game, Mock())[0]
    assert BackupSnapshot(game).inventory() == original


def test_source_changes_during_creation_are_not_published(
    backup: tuple[BackupManager, Path, str],
) -> None:
    manager, game, identifier = backup

    def change_source(message: str, current: int, total: int) -> None:
        if current == 1:
            (game / "chitin.key").write_bytes(b"changed while copying")

    assert not manager.create_backup("bg2ee", "BG2EE", game, change_source)[0]
    assert [info.backup_id for info in manager.list_backups()] == [identifier]


def test_backup_table_sorts_dates_and_sizes_on_their_value(qtbot) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QTableWidget

    from ui.widgets.SortValueTableItem import SortValueTableItem

    table = QTableWidget(0, 2)
    qtbot.addWidget(table)
    rows = [
        ("24/11/2026", datetime(2026, 11, 24), "900.0 MB", 900 * 1024**2),
        ("25/10/2026", datetime(2026, 10, 25), "1.2 GB", 1288490188),
        ("01/12/2025", datetime(2025, 12, 1), "50.0 KB", 50 * 1024),
    ]
    for row, (date_text, date, size_text, size) in enumerate(rows):
        table.insertRow(row)
        table.setItem(row, 0, SortValueTableItem(date_text, date))
        table.setItem(row, 1, SortValueTableItem(size_text, size))

    table.sortItems(0, Qt.SortOrder.DescendingOrder)
    assert [table.item(row, 0).text() for row in range(3)] == [
        "24/11/2026",
        "25/10/2026",
        "01/12/2025",
    ]
    table.sortItems(1, Qt.SortOrder.AscendingOrder)
    assert [table.item(row, 1).text() for row in range(3)] == ["50.0 KB", "900.0 MB", "1.2 GB"]


def test_restore_worker_keeps_recovery_path(tmp_path: Path) -> None:
    from ui.pages.BackupPage import BackupRestorationWorker

    manager = Mock(spec=BackupManager)
    manager.restore_backup.return_value = (False, tmp_path, "interrupted")
    worker = BackupRestorationWorker(manager, "backup", tmp_path / "game")
    results: list[tuple[bool, Path, str]] = []
    worker.completed.connect(lambda *result: results.append(result))
    worker.run()
    assert results == [(False, tmp_path, "interrupted")]


def test_denied_game_rename_preserves_prepared_copy_and_original_location(
    backup: tuple[BackupManager, Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, game, identifier = backup
    before = BackupSnapshot(game).inventory()
    with monkeypatch.context() as patch:
        patch.setattr(Path, "rename", Mock(side_effect=PermissionError("denied")))
        success, directory, error = manager.restore_backup(identifier, game, Mock())
    assert not success and str(game) in error and "denied" in error
    assert directory and (directory / "staged").is_dir()
    assert not (directory / "original").exists()
    assert (
        json.loads((directory / manager.JOURNAL_FILE).read_text(encoding="utf-8"))["phase"]
        == "prepared"
    )
    assert BackupSnapshot(game).inventory() == before


@pytest.mark.parametrize(
    ("content", "modded"),
    [
        (None, False),
        ("", False),
        ("// Log of Currently Installed WeiDU Mods\n// ~OLD.TP2~ #0 #1\n", False),
        ("~unfinished.tp2~ #0\n~notes.txt~ #0 #1", False),
        ("\ufeff~MOD/SETUP-MOD.TP2~ #0 #0 // Composant installé", True),
    ],
)
def test_backup_detects_only_installed_weidu_components(
    tmp_path: Path,
    content: str | None,
    modded: bool,
) -> None:
    if content is not None:
        (tmp_path / "weidu.log").write_text(content, encoding="utf-8")
    assert BackupManager().is_game_modded(tmp_path) is modded


@pytest.mark.parametrize(
    ("boundary", "game_moved"),
    [("prepared", False), ("original_saved", True), ("publish", True)],
)
def test_restore_failure_says_whether_the_game_folder_was_touched(
    backup: tuple[BackupManager, Path, str],
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
    game_moved: bool,
) -> None:
    import importlib

    from core.TranslationManager import tr

    module = importlib.import_module("core.RestoreTransaction")
    manager, game, identifier = backup
    write = module.write_json_atomic
    rename = Path.rename

    def write_fault(path: Path, value: dict) -> None:
        if value.get("phase") == boundary:
            raise OSError("injected journal failure")
        write(path, value)

    def rename_fault(path: Path, target: Path) -> Path:
        if boundary == "publish" and path.name == "staged":
            raise OSError("injected publication failure")
        return rename(path, target)

    with monkeypatch.context() as patch:
        patch.setattr(module, "write_json_atomic", write_fault)
        patch.setattr(Path, "rename", rename_fault)
        success, recovery, error = manager.restore_backup(identifier, game, Mock())

    assert not success and recovery is not None
    untouched = tr("backup_manager.error.restore_game_untouched")
    if game_moved:
        assert str(recovery / "original") in error and untouched not in error
    else:
        assert untouched in error and str(recovery / "original") not in error
