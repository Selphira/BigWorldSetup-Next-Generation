import json
import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from constants import CUSTOM_MODS_DIR
from core.enums.CategoryEnum import CategoryEnum
from core.StateManager import StateManager
from core.TranslationManager import SUPPORTED_LANGUAGES, tr
from core.WeiDUTp2Parser import WeiDUTp2Parser
from ui.widgets.MultiSelectComboBox import MultiSelectComboBox

logger = logging.getLogger(__name__)


class AddModDialog(QDialog):
    """Dialog for adding a custom mod from a .tp2 file."""

    mod_added = Signal(str)  # mod_id

    def __init__(self, state_manager: StateManager, parent: QWidget | None = None):
        """Initialize the dialog."""
        super().__init__(parent)
        self.setWindowTitle(tr("page.selection.custom_mod.title"))
        self.setMinimumWidth(600)
        self.setMinimumHeight(700)

        self.state_manager: StateManager = state_manager
        self.tp2_path: Path | None = None
        self.parsed_data: dict[str, Any] | None = None

        self._tp2_path_edit: QLineEdit | None = None
        self._name_edit: QLineEdit | None = None
        self._version_edit: QLineEdit | None = None
        self._games_combo: MultiSelectComboBox | None = None
        self._categories_combo: MultiSelectComboBox | None = None
        self._languages_combo: MultiSelectComboBox | None = None
        self._description_edit: QTextEdit | None = None
        self._components_edit: QTextEdit | None = None
        self._button_box: QDialogButtonBox | None = None

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Setup the user interface."""
        main_layout = QVBoxLayout(self)

        file_group = self._create_file_selection_group()
        main_layout.addWidget(file_group)

        form_group = self._create_form_group()
        main_layout.addWidget(form_group)

        self._button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._button_box.accepted.connect(self._on_accept)
        self._button_box.rejected.connect(self.reject)
        self._button_box.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        main_layout.addWidget(self._button_box)

    def _create_file_selection_group(self) -> QGroupBox:
        """Create file selection group."""
        group = QGroupBox(tr("page.selection.custom_mod.select_tp2_file"))
        layout = QHBoxLayout()

        self._tp2_path_edit = QLineEdit()
        self._tp2_path_edit.setReadOnly(True)
        self._tp2_path_edit.setPlaceholderText(tr("page.selection.custom_mod.no_file_selected"))
        layout.addWidget(self._tp2_path_edit)

        browse_btn = QPushButton(tr("page.selection.custom_mod.browse"))
        browse_btn.clicked.connect(self._browse_tp2_file)
        layout.addWidget(browse_btn)

        group.setLayout(layout)
        return group

    def _create_form_group(self) -> QGroupBox:
        """Create form for mod data."""
        group = QGroupBox(tr("page.selection.custom_mod.mod_information"))
        form = QFormLayout()

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText(tr("page.selection.custom_mod.required"))
        self._name_edit.textChanged.connect(self._validate_form)
        form.addRow(tr("page.selection.custom_mod.name") + " *:", self._name_edit)

        self._version_edit = QLineEdit()
        self._version_edit.setPlaceholderText(tr("page.selection.custom_mod.optional"))
        form.addRow(tr("page.selection.custom_mod.version") + ":", self._version_edit)

        games = {
            game.id: (game.name, str(game.get_icon()))
            for game in self.state_manager.get_game_manager().get_all()
        }
        self._games_combo = MultiSelectComboBox(show_text_selection=False)
        self._games_combo.set_items(games)
        self._games_combo.set_selected_keys([self.state_manager.get_selected_game()])
        self._games_combo.selection_changed.connect(self._validate_form)
        form.addRow(tr("page.selection.custom_mod.games") + " *:", self._games_combo)

        categories = {
            str(cat.value): str(cat.value) for cat in CategoryEnum if cat != CategoryEnum.ALL
        }
        self._categories_combo = MultiSelectComboBox(separator=",")
        self._categories_combo.set_items(categories)
        self._categories_combo.set_selected_keys(["custom"])
        self._categories_combo.selection_changed.connect(self._validate_form)
        form.addRow(tr("page.selection.custom_mod.categories") + " *:", self._categories_combo)

        languages = {code: name for code, name in SUPPORTED_LANGUAGES}
        self._languages_combo = MultiSelectComboBox(separator=",", min_selection_count=0)
        self._languages_combo.set_items(languages)
        self._languages_combo.setEnabled(False)
        self._languages_combo.selection_changed.connect(self._validate_form)
        form.addRow(tr("page.selection.custom_mod.languages") + " *:", self._languages_combo)

        self._description_edit = QTextEdit()
        self._description_edit.setMaximumHeight(150)
        self._description_edit.setPlaceholderText(tr("page.selection.custom_mod.optional"))
        form.addRow(tr("page.selection.custom_mod.description") + ":", self._description_edit)

        self._components_edit = QTextEdit()
        self._components_edit.setReadOnly(True)
        self._components_edit.setMinimumHeight(150)
        form.addRow(tr("page.selection.custom_mod.components") + ":", self._components_edit)

        group.setLayout(form)
        return group

    def _browse_tp2_file(self) -> None:
        """Open file dialog to select .tp2 file."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            tr("page.selection.custom_mod.select_tp2_file"),
            "",  # TODO: current game folder
            "TP2 Files (*.tp2);;All Files (*.*)",
        )

        if not file_path:
            return

        self.tp2_path = Path(file_path)
        self._tp2_path_edit.setText(str(self.tp2_path))
        self._parse_tp2_file()

    def _parse_tp2_file(self) -> None:
        """Parse the selected .tp2 file."""
        if not self.tp2_path or not self.tp2_path.exists():
            return

        try:
            base_dir = self.tp2_path.parent
            if self.tp2_path.parent.stem.lower() == self.tp2_path.stem.lower().removeprefix(
                "setup-"
            ):
                base_dir = base_dir.parent

            parser = WeiDUTp2Parser(base_dir)
            tp2_data = parser.parse_file(self.tp2_path)

            self.parsed_data = {
                "id": self.tp2_path.stem.lower(),
                "tp2": self.tp2_path.stem.lower(),
                "name": tp2_data.name or self.tp2_path.stem,
                "version": tp2_data.version,
                "languages": self._extract_languages(tp2_data),
                "components": self._extract_components(tp2_data),
                "translations": self._extract_translations(tp2_data),
            }

            # TODO: parse ini file if exsists (name, description, links, categories, order)

            self._populate_form(tp2_data)
            self._validate_form()

        except Exception as e:
            logger.error(f"Error parsing .tp2 file: {e}", exc_info=True)
            QMessageBox.critical(
                self,
                tr("page.selection.custom_mod.error_title"),
                tr("page.selection.custom_mod.error_parsing", error=str(e)),
            )

    @staticmethod
    def _extract_languages(tp2_data) -> dict[str, int]:
        """Extract languages from parsed TP2 data."""
        languages = {}
        for lang_decl in tp2_data.languages:
            languages[lang_decl.language_code] = lang_decl.index
        return languages if languages else {"all": 0}

    @staticmethod
    def _extract_components(tp2_data) -> dict[str, Any]:
        """Extract components from parsed TP2 data."""
        components = {}

        for component in tp2_data.components:
            comp_data = {"type": "std"}

            if hasattr(component, "components") and component.components:
                comp_data["type"] = "muc"
                comp_data["components"] = [c.designated for c in component.components]
                comp_data["default"] = (
                    component.components[0].designated if component.components else ""
                )

            if component.games:
                comp_data["games"] = component.games

            components[component.designated] = comp_data

        return components

    @staticmethod
    def _extract_translations(tp2_data) -> dict[str, Any]:
        """Extract translations from parsed TP2 data."""
        translations = {}

        for lang_code, comp_translations in tp2_data.component_translations.items():
            translations[lang_code] = {
                "description": "",
                "components": comp_translations,
            }

        if not translations:
            translations["en_US"] = {"description": "", "components": {}}

        return translations

    def _populate_form(self, tp2_data) -> None:
        """Populate form with parsed data."""
        self._name_edit.setText(str(tp2_data.name or self.tp2_path.stem))
        self._version_edit.setText(str(tp2_data.version))
        self._components_edit.setPlainText(self._get_components_list(tp2_data))
        self._languages_combo.set_selected_keys(
            [language for language in self.parsed_data["languages"]]
        )

    def _get_components_list(self, tp2_data) -> str:
        components_count = len(tp2_data.components)
        current_lang = self.state_manager.get_ui_language()
        default_text = tr("page.selection.custom_mod.no_translation")
        summary_lines = [
            tr("page.selection.custom_mod.detected_components", total=components_count),
            "",
        ]

        for component in tp2_data.components:
            comp_text = default_text
            translations = tp2_data.component_translations.get(current_lang)
            if translations and component.designated in translations:
                comp_text = translations[component.designated]
            else:
                for translations in tp2_data.component_translations.values():
                    if component.designated in translations:
                        comp_text = translations[component.designated]
                        break

            summary_lines.append(f"[{component.designated}] {comp_text}")

            if hasattr(component, "components") and component.components:
                for sub in component.components:
                    sub_text = default_text
                    translations = tp2_data.component_translations.get(current_lang)
                    if translations and sub.designated in translations:
                        sub_text = translations[sub.designated]
                    else:
                        for translations in tp2_data.component_translations.values():
                            if sub.designated in translations:
                                sub_text = translations[sub.designated]
                                break
                    summary_lines.append(f"    [{sub.designated}] {sub_text}")

        return "\n".join(summary_lines)

    def _validate_form(self) -> None:
        """Validate form and enable/disable OK button."""
        is_valid = (
            self.tp2_path is not None
            and self.parsed_data is not None
            and bool(self._name_edit.text().strip())
            and len(self._games_combo.selected_keys()) > 0
            and len(self._categories_combo.selected_keys()) > 0
        )

        self._button_box.button(QDialogButtonBox.StandardButton.Ok).setEnabled(is_valid)

    def _on_accept(self) -> None:
        """Called when OK button is clicked."""
        if not self._validate_and_create_mod():
            return

        self.accept()

    def _validate_and_create_mod(self) -> bool:
        """Validate data and create mod JSON file."""
        try:
            mod_id = self.parsed_data["id"]
            mod_data = {
                "id": mod_id,
                "name": self._name_edit.text().strip(),
                "tp2": self.parsed_data["tp2"],
                "version": self._version_edit.text().strip() or "unknown",
                "games": self._games_combo.selected_keys(),
                "categories": self._categories_combo.selected_keys(),
                "languages": self.parsed_data["languages"],
                "components": self.parsed_data["components"],
                "translations": self.parsed_data["translations"],
                "authors": [],
                "links": {},
                "safe": True,
                "custom": True,
            }

            description = self._description_edit.toPlainText().strip()
            if description:
                for lang_code in mod_data["translations"]:
                    mod_data["translations"][lang_code]["description"] = description

            json_path = CUSTOM_MODS_DIR / f"{mod_id}.json"

            if json_path.exists():
                reply = QMessageBox.question(
                    self,
                    tr("page.selection.custom_mod.confirm_overwrite_title"),
                    tr("page.selection.custom_mod.confirm_overwrite_message", mod_id=mod_id),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return False

            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(mod_data, f, indent=2, ensure_ascii=False)
                print("SAVED")

            logger.info(f"Custom mod created: {json_path}")

            self.mod_added.emit(mod_id)

            QMessageBox.information(
                self,
                tr("page.selection.custom_mod.success_title"),
                tr("page.selection.custom_mod.success_message", name=mod_data["name"]),
            )

            return True

        except Exception as e:
            logger.error(f"Error creating mod file: {e}", exc_info=True)
            QMessageBox.critical(
                self,
                tr("page.selection.custom_mod.error_title"),
                tr("page.selection.custom_mod.error_creating", error=str(e)),
            )
            return False
