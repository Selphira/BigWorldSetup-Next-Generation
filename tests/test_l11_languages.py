"""The UI language never rewrites the mod languages order nor leaves stale SUB texts."""

from pathlib import Path
from unittest.mock import Mock

from PySide6.QtCore import QSettings
import pytest
from pytestqt.qtbot import QtBot

from core.ComponentReference import ComponentReference, IndexManager
from core.GameModels import GameDefinition
from core.Mod import Mod
from core.RuleManager import RuleManager
from core.StateManager import InstallationState, StateManager
from tests.conftest import ModCatalogue
from ui.pages.InstallationType import InstallationTypePage
from ui.pages.mod_selection.ComponentSelector import ComponentSelector
from ui.pages.mod_selection.SelectionController import SelectionController

LANGUAGES = ["de_DE", "en_US", "fr_FR", "it_IT"]
ENGLISH_THEN_FRENCH = ["en_US", "fr_FR"]


@pytest.fixture
def state_manager(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> StateManager:
    monkeypatch.setattr(StateManager, "STATE_FILE", tmp_path / "state.json")
    manager = StateManager.__new__(StateManager)
    manager.settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    manager.installation_state = InstallationState()
    return manager


# ========================================
# MOD LANGUAGES ORDER
# ========================================


def test_first_launch_has_no_ui_language(state_manager: StateManager) -> None:
    assert not state_manager.has_ui_language()

    state_manager.set_ui_language("it_IT")

    assert state_manager.has_ui_language()


def test_languages_order_starts_with_the_chosen_ui_language(
    state_manager: StateManager,
) -> None:
    state_manager.set_ui_language("fr_FR")

    state_manager.initialize_languages_order(LANGUAGES)

    assert state_manager.get_languages_order() == ["fr_FR", "de_DE", "en_US", "it_IT"]
    assert StateManager._read_state(state_manager.STATE_FILE).configuration[
        "languages_order"
    ] == ["fr_FR", "de_DE", "en_US", "it_IT"]


def test_languages_order_survives_a_ui_language_change(state_manager: StateManager) -> None:
    state_manager.set_ui_language("en_US")
    state_manager.initialize_languages_order(LANGUAGES)
    user_order = ["en_US", "fr_FR", "de_DE", "it_IT"]
    state_manager.set_languages_order(user_order)

    state_manager.set_ui_language("fr_FR")
    state_manager.initialize_languages_order(LANGUAGES)

    assert state_manager.get_languages_order() == user_order


# ========================================
# INSTALLATION TYPE PAGE
# ========================================


@pytest.fixture
def french_ui_page(qtbot: QtBot) -> InstallationTypePage:
    game = GameDefinition.from_dict({"id": "bg2ee", "name": "BG2EE", "sequences": [{"game": "bg2ee"}]})
    state = Mock()
    state.get_game_manager.return_value.get.side_effect = {"bg2ee": game}.get
    state.get_game_manager.return_value.get_all.return_value = [game]
    state.get_selected_game.return_value = None
    state.get_game_folders.return_value = {}
    state.get_download_folder.return_value = None
    state.get_backup_folder.return_value = None
    state.get_languages_order.return_value = ENGLISH_THEN_FRENCH
    state.get_ui_language.return_value = "fr_FR"

    page = InstallationTypePage(state)
    qtbot.addWidget(page)
    return page


def test_page_registration_keeps_english_before_french(
    french_ui_page: InstallationTypePage,
) -> None:
    # Same sequence as MainWindow.register_page.
    french_ui_page.load_state()
    french_ui_page.retranslate_ui()

    assert french_ui_page.languages_order.get_order()[:2] == ENGLISH_THEN_FRENCH


def test_switching_the_ui_to_french_keeps_english_before_french(
    french_ui_page: InstallationTypePage,
) -> None:
    french_ui_page.state_manager.get_ui_language.return_value = "en_US"
    french_ui_page.load_state()
    french_ui_page.retranslate_ui()

    french_ui_page.state_manager.get_ui_language.return_value = "fr_FR"
    french_ui_page.retranslate_ui()

    assert french_ui_page.languages_order.get_order()[:2] == ENGLISH_THEN_FRENCH


# ========================================
# SUB TEXTS
# ========================================


def _sub_mod(prompt_text: str, option_text: str) -> Mod:
    return Mod(
        {
            "id": "demo",
            "name": "Demo",
            "links": {},
            "tp2": "demo",
            "games": ["eet"],
            "components": {
                "1": {"type": "sub", "prompts": {"1": {"options": ["A", "B"], "default": "A"}}}
            },
            "translations": {
                "components": {"1": "Sub", "1.1": prompt_text, "1.1.A": option_text}
            },
        }
    )


def _item_text(reference: str) -> str:
    item = IndexManager.get_indexes().tree_item_index[ComponentReference.from_string(reference)]
    return item.text()


def test_sub_texts_follow_the_reloaded_catalogue(qtbot: QtBot, tmp_path: Path) -> None:
    catalogue = ModCatalogue([_sub_mod("Question", "Answer")])
    rules = RuleManager(
        catalogue,
        rules_dir=tmp_path / "rules",
        cache_dir=tmp_path / "cache",
        mods_dir=tmp_path / "mods",
        custom_mods_dir=tmp_path / "custom",
    )
    selector = ComponentSelector(catalogue, SelectionController(rules))
    qtbot.addWidget(selector)
    selector.retranslate_ui()

    # A language reload replaces every Mod object of the catalogue.
    catalogue.mods = {"demo": _sub_mod("Question FR", "Réponse")}
    selector.retranslate_ui()

    assert _item_text("demo:1.1") == "[1.1] Question FR"
    assert _item_text("demo:1.1.A") == "[1.1.A] Réponse"
