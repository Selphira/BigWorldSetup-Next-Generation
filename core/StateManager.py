"""Hybrid state management system for UI preferences and application state."""

from dataclasses import asdict, dataclass, field
import json
import logging
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QSettings

from constants import ARCHIVE_REGISTRY_FILE, CACHE_DIR, MODS_DIR, RULES_DIR, STATE_FILE
from core.ArchiveRegistry import ArchiveRecord, ArchiveRegistry, ArchiveSource
from core.atomic_io import write_json_atomic
from core.GameManager import GameManager
from core.ModManager import ModManager
from core.RuleManager import RuleManager
from core.validators.FolderRelationshipValidator import FolderRelationshipValidator

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
    configuration: dict[str, Any] = field(
        default_factory=lambda: {
            "selected_game": None,
            "selected_components": {},
            "game_folders": {},
            "download_folder": None,
            "backup_folder": None,
            "languages_order": [],
            "install_order": {},
            "custom_archives": {},
        }
    )
    installation: dict[str, Any] = field(
        default_factory=lambda: {
            "current_step": None,
        }
    )

    def to_dict(self) -> dict[str, Any]:
        """
        Convert to dictionary for JSON serialization.

        Returns:
            Dictionary representation
        """
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "InstallationState":
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

    STATE_FILE: Path = STATE_FILE
    ARCHIVE_REGISTRY_FILE: Path = ARCHIVE_REGISTRY_FILE
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
        self._archive_registry: ArchiveRegistry | None = None
        self._reset_workflow_callback: Callable[[], None] | None = None

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

    def set_selected_components(self, components: list[str]) -> None:
        """
        Set selected components.

        Args:
            components: Dictionary of component lists
        """
        self.installation_state.configuration["selected_components"] = components
        logger.debug(f"Selected components: {components}")

    def get_selected_components(self) -> list[str]:
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

        return {int(seq_idx): order_list for seq_idx, order_list in install_order.items()}

    def get_archive_registry(self) -> ArchiveRegistry:
        """Return the archive registry, importing legacy `custom_archives` first.

        The legacy mapping is emptied only after the registry has saved it, and
        an imported or older state file is migrated again on the next access.
        """
        if self._archive_registry is None:
            self._archive_registry = ArchiveRegistry.load(self.ARCHIVE_REGISTRY_FILE)

        legacy = self.installation_state.configuration.get("custom_archives")
        if legacy:
            self._archive_registry.migrate_custom_archives(legacy)
            legacy.clear()
        return self._archive_registry

    def add_custom_archive(self, mod_id: str, archive_path: Path) -> None:
        """Associate a user archive with a mod; its hash is checked before use."""
        self.get_archive_registry().put(
            ArchiveRecord.unverified(mod_id, archive_path, ArchiveSource.MANUAL)
        )

    def get_custom_archive(self, mod_id: str) -> Path | None:
        """Get custom archive path for a mod."""
        record = self._manual_record(mod_id)
        return record.archive_path if record else None

    def has_custom_archive(self, mod_id: str) -> bool:
        """Check if mod has a custom archive."""
        return self._manual_record(mod_id) is not None

    def remove_custom_archive(self, mod_id: str) -> None:
        """Remove custom archive association."""
        if self._manual_record(mod_id) is not None:
            self.get_archive_registry().remove(mod_id)

    def _manual_record(self, mod_id: str) -> ArchiveRecord | None:
        record = self.get_archive_registry().get(mod_id)
        if record is None or record.source is not ArchiveSource.MANUAL:
            return None
        return record

    def set_page_option(self, page: str, option: str, value: Any) -> None:
        """Set page-specific boolean option.

        Args:
            page: Page identifier
            option: Option name
            value: Option value
        """
        key = f"{page}_{option}"
        if value is not None:
            self.installation_state.configuration[key] = value
        else:
            self.installation_state.configuration.pop(key, None)

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
        for path in (self.STATE_FILE, self.STATE_FILE.with_name(self.STATE_FILE.name + self.BACKUP_SUFFIX)):
            try:
                state = self._read_state(path)
                logger.info("Installation state loaded from %s", path)
                return state
            except FileNotFoundError:
                continue
            except (OSError, ValueError, TypeError) as error:
                logger.warning("Cannot load state from %s: %s", path, error)
        return InstallationState()

    @classmethod
    def _read_state(cls, path: Path) -> InstallationState:
        data = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(data, dict)
            or data.get("version") != cls.SUPPORTED_VERSION
            or not isinstance(data.get("configuration"), dict)
            or not isinstance(data.get("installation"), dict)
        ):
            raise ValueError("Invalid or unsupported installation state")
        return InstallationState.from_dict(data)

    def save_state(self) -> bool:
        """Atomically persist state, retaining the last readable version."""
        try:
            try:
                previous = self._read_state(self.STATE_FILE)
            except (OSError, ValueError, TypeError):
                previous = None
            if previous is not None:
                backup = self.STATE_FILE.with_name(self.STATE_FILE.name + self.BACKUP_SUFFIX)
                write_json_atomic(backup, previous.to_dict())
            write_json_atomic(self.STATE_FILE, self.installation_state.to_dict())
            logger.info("State saved successfully")
            return True
        except Exception:
            logger.exception("Error saving state")
            return False

    # ========================================
    # UTILITIES
    # ========================================

    def set_reset_workflow_callback(self, callback: Callable[[], None]) -> None:
        """Set callback for resetting the entire workflow.

        Args:
            callback: Function to call when workflow reset is requested
        """
        self._reset_workflow_callback = callback

    def reset_workflow(self) -> None:
        """Reset the entire workflow (clear state + reload all pages)."""
        logger.info("Workflow reset requested")

        # Clear all workflow data
        self.installation_state.configuration["selected_components"].clear()
        self.installation_state.configuration["install_order"].clear()

        # Clear page-specific options
        prefixes = ("mod_selection_", "install_order_", "installation_")
        configuration = self.installation_state.configuration
        for key in [k for k in configuration if k.startswith(prefixes)]:
            configuration.pop(key)

        # Trigger MainWindow reset callback
        if self._reset_workflow_callback:
            self._reset_workflow_callback()
        else:
            logger.warning("No reset workflow callback registered")

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
            self._mod_manager = ModManager(mods_dir=MODS_DIR, cache_dir=CACHE_DIR)
            logger.debug("ModManager initialized")

        return self._mod_manager

    def get_rule_manager(self) -> RuleManager:
        """Get rule manager instance."""
        if self._rule_manager is None:
            self._rule_manager = RuleManager(
                self.get_mod_manager(), rules_dir=RULES_DIR, cache_dir=CACHE_DIR
            )
            logger.debug("RuleManager initialized")
        return self._rule_manager

    def is_valid_state(self) -> bool:
        game_def = self.get_game_manager().get(self.get_selected_game())
        is_valid = True

        if not game_def:
            is_valid = False
        else:
            folders = [
                self.get_backup_folder(),
                self.get_download_folder(),
            ]

            game_folders = self.get_game_folders()
            game_manager = self.get_game_manager()
            game_codes = {seq.game for seq in game_def.sequences if seq.game}

            for game_code in game_codes:
                game = game_manager.get(game_code)
                if not game:
                    is_valid = False
                    break

                for folder_key in game.get_folder_keys():
                    path = game_folders.get(folder_key)
                    if not path:
                        is_valid = False
                        break
                    folders.append(path)

            if is_valid:
                is_valid = all(path and Path(path).exists() for path in folders)
            if is_valid:
                is_valid = FolderRelationshipValidator().validate(folders)[0]

        return is_valid
