"""Hybrid state management system for UI preferences and application state."""

import json
import logging
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSettings

from constants import CACHE_DIR, MODS_DIR, RULES_DIR
from core.GameManager import GameManager, GameDefinition
from core.ModManager import ModManager
from core.RuleManager import RuleManager

logger = logging.getLogger(__name__)


@dataclass
class InstallationState:
    """
    Application state structure for installation configuration.

    Attributes:
        version: State format version
        configuration: Installation configuration settings
        installation: Current installation state
    """
    version: str = "1.0"
    configuration: dict[str, Any] = field(default_factory=lambda: {
        "selected_game": None,
        "selected_components": {},
        "game_folders": {},
        "download_folder": None,
        "backup_folder": None,
        "languages_order": [],
        "install_order": [],
    })
    installation: dict[str, Any] = field(default_factory=lambda: {
        "current_step": None,
    })

    def to_dict(self) -> dict[str, Any]:
        """
        Convert to dictionary for JSON serialization.

        Returns:
            Dictionary representation
        """
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'InstallationState':
        """
        Load InstallationState from dictionary.

        Args:
            data: Dictionary with state data

        Returns:
            Reconstructed InstallationState
        """
        state = cls()
        state.version = data.get("version", "1.0")

        # Merge configurations (preserving defaults)
        if "configuration" in data:
            state.configuration.update(data["configuration"])

        if "installation" in data:
            state.installation = data["installation"]

        return state


class StateManager:
    """
    Manages application state with QSettings for UI and JSON for installation config.

    Responsibilities:
    - UI preferences (QSettings): lightweight, user-specific
    - Installation state (JSON): complex, exportable, shareable
    - ModManager lifecycle management

    Attributes:
        settings: Qt settings for UI preferences
        installation_state: Application state for installation
    """

    STATE_FILE = Path("state.json")
    BACKUP_SUFFIX = ".backup"
    SUPPORTED_VERSION = "1.0"

    # QSettings configuration
    SETTINGS_ORG = "Selphira"
    SETTINGS_APP = "BigWorldSetupEnhanced"

    def __init__(self) -> None:
        """Initialize state manager with QSettings and JSON state."""
        self.settings = QSettings(self.SETTINGS_ORG, self.SETTINGS_APP)
        self.installation_state = self._load_installation_state()
        self._game_manager: GameManager | None = None
        self._mod_manager: ModManager | None = None
        self._rule_manager: RuleManager | None = None

    # ========================================
    # UI PREFERENCES (QSettings)
    # ========================================

    def get_ui_language(self) -> str:
        """
        Get UI language code.

        Returns:
            Language code (e.g., "fr_FR")
        """
        return str(self.settings.value("ui/language", "fr_FR", str))

    def set_ui_language(self, code: str) -> None:
        """
        Set UI language code.

        Args:
            code: Language code (e.g., "fr_FR")
        """
        self.settings.setValue("ui/language", code)
        logger.info(f"UI language set to: {code}")

    def get_ui_current_page(self) -> str:
        """
        Get current page identifier.

        Returns:
            Page identifier
        """
        return str(self.settings.value("ui/current_page", "", str))

    def set_ui_current_page(self, page_id: str) -> None:
        """
        Set current page identifier.

        Args:
            page_id: Page identifier
        """
        self.settings.setValue("ui/current_page", page_id)
        logger.info(f"UI current page set to: {page_id}")

    # ========================================
    # INSTALLATION CONFIGURATION (JSON)
    # ========================================

    def set_selected_game(self, game_code: str) -> None:
        """
        Set selected game.

        Args:
            game_code: Game code (e.g., "bgee")
        """
        self.installation_state.configuration["selected_game"] = game_code
        logger.debug(f"Selected game: {game_code}")

    def get_selected_game(self) -> str | None:
        """
        Get selected game.

        Returns:
            Game code or None
        """
        return self.installation_state.configuration.get("selected_game")

    def set_selected_components(self, components: dict[str, list[Any]]) -> None:
        """
        Set selected components.

        Args:
            components: Dictionary of component lists
        """
        self.installation_state.configuration["selected_components"] = components
        logger.debug(f"Selected components: {components}")

    def get_selected_components(self) -> dict[str, list[Any]]:
        """
        Get selected components configuration.

        Returns:
            Dictionary of component lists
        """
        return self.installation_state.configuration.get("selected_components", {}).copy()

    def set_install_order(self, install_order: dict[int, list[str]]) -> None:
        """
        Set installation order for all sequences.

        The installation order maps sequence indices to ordered lists of component IDs.
        Each component ID follows the format "mod_id:comp_key".

        Args:
            install_order: Dictionary mapping sequence index to ordered component IDs.
                          Example: {0: ["mod1:comp1", "mod2:comp3"], 1: ["mod3:comp2"]}
        """
        self.installation_state.configuration["install_order"] = install_order.copy()
        logger.debug(f"Install order set for {len(install_order)} sequence(s)")

    def get_install_order(self) -> dict[int, list[str]]:
        """
        Get installation order for all sequences.

        Returns ordered lists of component IDs for each sequence. Component IDs
        follow the format "mod_id:comp_key".

        Returns:
            Dictionary mapping sequence index to ordered component IDs.
            Returns empty dict if no order has been set.
            Example: {0: ["mod1:comp1", "mod2:comp3"], 1: ["mod3:comp2"]}
        """
        install_order = self.installation_state.configuration.get("install_order", {}).copy()

        return {
            int(seq_idx): order_list
            for seq_idx, order_list in install_order.items()
        }

    def set_page_option(self, page: str, option: str, value: bool) -> None:
        """Set page-specific boolean option.

        Args:
            page: Page identifier
            option: Option name
            value: Option value
        """
        key = f"{page}_{option}"
        self.installation_state.configuration[key] = value

    def get_page_option(self, page: str, option: str, default: Any) -> Any:
        """Get page-specific boolean option.

        Args:
            page: Page identifier
            option: Option name
            default: Default value if not set

        Returns:
            Option value
        """
        key = f"{page}_{option}"
        return self.installation_state.configuration.get(key, default)

    def set_game_folders(self, folders: dict[str, Any]) -> None:
        """
        Set game folders configuration.

        Args:
            folders: Dictionary of game folders
        """
        self.installation_state.configuration["game_folders"] = folders.copy()
        logger.debug(f"Game folders updated: {folders}")

    def get_game_folders(self) -> dict[str, Any]:
        """
        Get game folders configuration.

        Returns:
            Dictionary of game folders
        """
        return self.installation_state.configuration.get("game_folders", {}).copy()

    def set_backup_folder(self, folder: str) -> None:
        """
        Set backup folder path.

        Args:
            folder: Backup folder path
        """
        self.installation_state.configuration["backup_folder"] = folder
        logger.debug(f"Backup folder: {folder}")

    def get_backup_folder(self) -> str | None:
        """
        Get backup folder path.

        Returns:
            Backup folder path or None
        """
        return self.installation_state.configuration.get("backup_folder")

    def set_download_folder(self, folder: str) -> None:
        """
        Set download folder path.

        Args:
            folder: Download folder path
        """
        self.installation_state.configuration["download_folder"] = folder
        logger.debug(f"Download folder: {folder}")

    def get_download_folder(self) -> str | None:
        """
        Get download folder path.

        Returns:
            Download folder path or None
        """
        return self.installation_state.configuration.get("download_folder")

    def set_languages_order(self, languages: list[str]) -> None:
        """
        Set language preference order.

        Args:
            languages: Ordered list of language codes
        """
        self.installation_state.configuration["languages_order"] = languages.copy()
        logger.debug(f"Languages order: {languages}")

    def get_languages_order(self) -> list[str]:
        """
        Get language preference order.

        Returns:
            Ordered list of language codes
        """
        return self.installation_state.configuration.get("languages_order", []).copy()

    # ========================================
    # STATE PERSISTENCE
    # ========================================

    def _load_installation_state(self) -> InstallationState:
        """
        Load installation configuration from JSON file.

        Returns:
            Loaded or default InstallationState
        """
        if not self.STATE_FILE.exists():
            logger.info("No state file found, using defaults")
            return InstallationState()

        try:
            with self.STATE_FILE.open('r', encoding='utf-8') as f:
                data = json.load(f)

            # Validate version
            version = data.get("version", "1.0")
            if version != self.SUPPORTED_VERSION:
                logger.warning(
                    f"Unsupported state version: {version}, "
                    f"expected {self.SUPPORTED_VERSION}"
                )
                return InstallationState()

            state = InstallationState.from_dict(data)
            logger.info("Installation state loaded successfully")
            return state

        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in state file: {e}")
            return InstallationState()
        except Exception as e:
            logger.exception(f"Error loading state: {e}")
            return InstallationState()

    def save_state(self) -> bool:
        """
        Save installation configuration to JSON file with backup.

        Returns:
            True if saved successfully, False otherwise
        """
        try:
            # Create backup if file exists
            if self.STATE_FILE.exists():
                backup_path = Path(str(self.STATE_FILE) + self.BACKUP_SUFFIX)
                shutil.copy2(self.STATE_FILE, backup_path)
                logger.debug(f"Backup created: {backup_path}")

            # Save with indentation for readability
            with self.STATE_FILE.open('w', encoding='utf-8') as f:
                json.dump(
                    self.installation_state.to_dict(),
                    f,
                    indent=2,
                    ensure_ascii=False
                )

            logger.info("State saved successfully")
            return True

        except Exception as e:
            logger.exception(f"Error saving state: {e}")
            return False

    def export_configuration(self, filepath: Path) -> bool:
        """
        Export configuration to a file.

        Args:
            filepath: Target file path

        Returns:
            True if exported successfully, False otherwise
        """
        try:
            with filepath.open('w', encoding='utf-8') as f:
                json.dump(
                    self.installation_state.to_dict(),
                    f,
                    indent=2,
                    ensure_ascii=False
                )
            logger.info(f"Configuration exported to: {filepath}")
            return True

        except Exception as e:
            logger.exception(f"Error exporting configuration: {e}")
            return False

    def import_configuration(self, filepath: Path) -> bool:
        """
        Import configuration from a file with validation.

        Args:
            filepath: Source file path

        Returns:
            True if imported successfully, False otherwise
        """
        try:
            with filepath.open('r', encoding='utf-8') as f:
                data = json.load(f)

            # Validate version compatibility
            version = data.get("version", "1.0")
            if version != self.SUPPORTED_VERSION:
                logger.error(
                    f"Unsupported configuration version: {version}, "
                    f"expected {self.SUPPORTED_VERSION}"
                )
                return False

            # Validate required structure
            if not isinstance(data.get("configuration"), dict):
                logger.error("Invalid configuration structure")
                return False

            # Load into current state
            self.installation_state = InstallationState.from_dict(data)

            # Save immediately
            if self.save_state():
                logger.info(f"Configuration imported from: {filepath}")
                return True

            return False

        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in import file: {e}")
            return False
        except Exception as e:
            logger.exception(f"Error importing configuration: {e}")
            return False

    # ========================================
    # UTILITIES
    # ========================================

    def clear_all_settings(self) -> None:
        """Clear ALL data (UI preferences + installation state)."""
        # Clear QSettings
        self.settings.clear()
        self.settings.sync()
        logger.info("QSettings cleared")

        # Reset JSON state
        self.installation_state = InstallationState()

        # Remove state files
        if self.STATE_FILE.exists():
            self.STATE_FILE.unlink()
            logger.info("State file removed")

        backup_path = Path(str(self.STATE_FILE) + self.BACKUP_SUFFIX)
        if backup_path.exists():
            backup_path.unlink()
            logger.info("Backup file removed")

    def get_game_manager(self) -> GameManager:
        """
        Get GameManager instance (lazy initialization).

        Returns:
            GameManager instance
        """
        if self._game_manager is None:
            self._game_manager = GameManager()
            logger.debug("GameManager initialized")

        return self._game_manager

    def get_mod_manager(self) -> ModManager:
        """
        Get ModManager instance (lazy initialization).

        Returns:
            ModManager instance
        """
        if self._mod_manager is None:
            self._mod_manager = ModManager(
                mods_dir=MODS_DIR,
                cache_dir=CACHE_DIR
            )
            logger.debug("ModManager initialized")

        return self._mod_manager

    def get_rule_manager(self) -> RuleManager:
        """Get rule manager instance."""
        if self._rule_manager is None:
            self._rule_manager = RuleManager(Path(RULES_DIR))
            logger.debug("RuleManager initialized")
        return self._rule_manager

    def get_game_definition(self) -> GameDefinition | None:
        """Get the currently selected game definition."""
        game_code = self.get_selected_game()
        if not game_code:
            return None
        return self.get_game_manager().get(game_code)
