"""Relationship errors use the same visual feedback as local folder validation."""

from pathlib import Path
from unittest.mock import Mock

from PySide6.QtCore import QPointF
from PySide6.QtGui import QEnterEvent
from PySide6.QtWidgets import QApplication, QToolTip
import pytest
from pytestqt.qtbot import QtBot

from core.GameModels import GameDefinition
from core.validators.FolderRelationshipValidator import FolderRelationshipValidator
from ui.pages.InstallationType import InstallationTypePage


@pytest.fixture
def page(qtbot: QtBot, tmp_path: Path) -> InstallationTypePage:
    games = {
        key: GameDefinition.from_dict(
            {
                "id": key,
                "name": key,
                "sequences": [{"game": key}],
            }
        )
        for key in ("bg2ee", "sod")
    }
    state = Mock()
    state.get_game_manager.return_value.get.side_effect = games.get
    state.get_game_manager.return_value.get_all.return_value = list(games.values())

    page = InstallationTypePage(state)
    qtbot.addWidget(page)
    page._on_game_selected(games["bg2ee"])

    selectors = {
        **page.folder_widgets,
        "downloads": page.download_folder,
        "backups": page.backup_folder,
    }
    for name, selector in selectors.items():
        directory = tmp_path / name
        directory.mkdir()
        selector.set_path(str(directory))

    assert page.can_go_to_next_page()
    return page


def test_conflicting_folders_show_error_icon_and_immediate_tooltip(
    page: InstallationTypePage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    game_selector = page.folder_widgets["bg2ee"]
    success_icon = game_selector.icon_action.icon().pixmap(16, 16).toImage()
    page.backup_folder.set_path(game_selector.get_path())

    assert not page.can_go_to_next_page()
    assert page.download_folder.is_valid()
    for selector in (game_selector, page.backup_folder):
        assert not selector.is_valid()
        assert selector.is_path_valid()
        assert selector.get_error_message()
        assert selector.icon_action.toolTip() == selector.get_error_message()
        assert selector.path_input.toolTip() == selector.get_error_message()
        assert selector.icon_action.icon().pixmap(16, 16).toImage() != success_icon

    show_tooltip = Mock()
    monkeypatch.setattr(QToolTip, "showText", show_tooltip)
    QApplication.sendEvent(
        game_selector.path_input, QEnterEvent(QPointF(), QPointF(), QPointF())
    )
    assert show_tooltip.call_args.args[1] == game_selector.get_error_message()


def test_fixing_conflict_clears_both_fields_without_recursive_signals(
    page: InstallationTypePage,
    tmp_path: Path,
) -> None:
    game_selector = page.folder_widgets["bg2ee"]
    page.download_folder.set_path(game_selector.get_path())
    notifications = Mock()
    page.navigation_changed.connect(notifications)

    page.download_folder.set_path(str(tmp_path / "downloads"))

    assert notifications.call_count == 1
    assert page.can_go_to_next_page()
    for selector in (game_selector, page.download_folder):
        assert selector.is_valid()
        assert selector.get_error_message() == ""
        assert selector.path_input.toolTip() == ""
        assert selector.icon_action.toolTip() == ""


def test_local_error_is_not_hidden_by_relationship_feedback(
    page: InstallationTypePage,
    tmp_path: Path,
) -> None:
    game_selector = page.folder_widgets["bg2ee"]
    page.backup_folder.set_path(game_selector.get_path())
    game_selector.set_path(str(tmp_path / "missing_game"))

    assert not game_selector.is_path_valid()
    assert not game_selector.is_valid()
    assert (
        game_selector.get_error_message()
        == game_selector.validator.validate(game_selector.get_path())[1]
    )
    assert page.backup_folder.is_valid()
    assert not page.can_go_to_next_page()


def test_switching_game_clears_conflicts_with_inactive_folder(
    page: InstallationTypePage,
) -> None:
    previous_selector = page.folder_widgets["bg2ee"]
    page.backup_folder.set_path(previous_selector.get_path())

    page._on_game_selected(page.folder_widgets["sod"].game)

    assert page.can_go_to_next_page()
    assert previous_selector.is_valid()
    assert page.backup_folder.is_valid()
    assert previous_selector.get_error_message() == ""


def test_validator_reports_every_affected_folder(tmp_path: Path) -> None:
    roots = [
        tmp_path / "game",
        tmp_path / "game" / "backup",
        tmp_path / "archives",
        tmp_path / "archives",
    ]
    errors = FolderRelationshipValidator().find_errors([str(path) for path in roots])
    assert set(errors) == {0, 1, 2, 3}
