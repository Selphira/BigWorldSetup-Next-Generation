"""Hybrid state management system for UI preferences and application state."""

import json
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from PySide6.QtCore import QSettings

logger = logging.getLogger(__name__)


@dataclass
class InstallationState:
    """Application state structure for installation configuration."""
    version: str = "1.0"
    configuration: Dict[str, Any] = field(default_factory=lambda: {
        "selected_game": None,
        "game_folders": {},
        "download_folder": None,
        "backup_folder": None,
        "languages_order": [],
    })
    installation: Dict[str, Any] = field(default_factory=lambda: {
        "current_step": None,
    })

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "version": self.version,
            "configuration": self.configuration,
            "installation": self.installation,
        }


def _load_from_dict(data: Dict[str, Any]) -> InstallationState:
    """Load InstallationState from dictionary.

    Args:
        data: Dictionary with state data

    Returns:
        Reconstructed InstallationState
    """
    state = InstallationState()
    state.version = data.get("version", "1.0")
    state.configuration = state.configuration | data.get("configuration", {}) or {}
    state.installation = data.get("installation", state.installation)
    return state


class StateManager:
    """Manages application state with QSettings for UI and JSON for installation config.

    Attributes:
        settings: Qt settings for UI preferences (lightweight, user-specific)
        installation_state: Application state for installation (complex, exportable, shareable)
    """

    STATE_FILE = Path("state.json")
    BACKUP_SUFFIX = ".backup"
    SUPPORTED_VERSION = "1.0"

    # Settings keys
    SETTINGS_ORG = "Selphira"
    SETTINGS_APP = "BigWorldSetupNextGen"

    def __init__(self) -> None:
        """Initialize state manager with QSettings and JSON state."""
        self.settings = QSettings(self.SETTINGS_ORG, self.SETTINGS_APP)
        self.installation_state = self._load_installation_state()

    # ========================================
    # UI PREFERENCES (QSettings)
    # ========================================

    def get_ui_language(self) -> str:
        """Get UI language code."""
        return self.settings.value("ui/language", "fr_FR", str)

    def set_ui_language(self, code: str) -> None:
        """Set UI language code."""
        self.settings.setValue("ui/language", code)
        logger.info(f"UI language set to: {code}")

    # ========================================
    # STATES CONFIGURATION (JSON)
    # ========================================

    def set_selected_game(self, game_code: str) -> None:
        self.installation_state.configuration["selected_game"] = game_code

    def get_selected_game(self) -> str:
        return self.installation_state.configuration["selected_game"]

    def set_game_folders(self, folders: Dict[str, Any]) -> None:
        self.installation_state.configuration["game_folders"] = folders

    def get_game_folders(self) -> Dict[str, Any]:
        return self.installation_state.configuration["game_folders"]

    def set_backup_folder(self, folder: str) -> None:
        self.installation_state.configuration["backup_folder"] = folder

    def get_backup_folder(self) -> str:
        return self.installation_state.configuration["backup_folder"]

    def set_download_folder(self, folder: str) -> None:
        self.installation_state.configuration["download_folder"] = folder

    def get_download_folder(self) -> str:
        return self.installation_state.configuration["download_folder"]

    def set_languages_order(self, languages: List[str]) -> None:
        self.installation_state.configuration["languages_order"] = languages

    def get_languages_order(self) -> List[str]:
        return self.installation_state.configuration["languages_order"]

    def _load_installation_state(self) -> InstallationState:
        """Load installation configuration from JSON file.

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
                logger.warning(f"Unsupported state version: {version}")
                return InstallationState()

            state = _load_from_dict(data)

            logger.info("Installation state loaded successfully")
            return state

        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in state file: {e}")
            return InstallationState()
        except Exception as e:
            logger.error(f"Error loading state: {e}")
            return InstallationState()

    def save_state(self) -> bool:
        """Save installation configuration to JSON file with backup.

        Returns:
            True if saved successfully, False otherwise
        """
        try:
            # Create backup if file exists
            if self.STATE_FILE.exists():
                backup_path = self.STATE_FILE.with_suffix(
                    self.STATE_FILE.suffix + self.BACKUP_SUFFIX
                )
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
            logger.error(f"Error saving state: {e}")
            return False

    def export_configuration(self, filepath: Path) -> bool:
        """Export configuration to a file.

        Args:
            filepath: Target file path

        Returns:
            True if exported successfully, False otherwise
        """
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(
                    self.installation_state.to_dict(),
                    f,
                    indent=2,
                    ensure_ascii=False
                )
            logger.info(f"Configuration exported to: {filepath}")
            return True
        except Exception as e:
            logger.error(f"Error exporting configuration: {e}")
            return False

    def import_configuration(self, filepath: Path) -> bool:
        """Import configuration from a file.

        Args:
            filepath: Source file path

        Returns:
            True if imported successfully, False otherwise
        """
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # Check version compatibility
            version = data.get("version", "1.0")
            if version != self.SUPPORTED_VERSION:
                logger.error(f"Unsupported configuration version: {version}")
                return False

            # Load into current state
            self.installation_state = _load_from_dict(data)

            # Save immediately
            if self.save_state():
                logger.info(f"Configuration imported from: {filepath}")
                return True

            return False

        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in import file: {e}")
            return False
        except Exception as e:
            logger.error(f"Error importing configuration: {e}")
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

        backup_path = self.STATE_FILE.with_suffix(
            self.STATE_FILE.suffix + self.BACKUP_SUFFIX
        )
        if backup_path.exists():
            backup_path.unlink()
            logger.info("Backup file removed")