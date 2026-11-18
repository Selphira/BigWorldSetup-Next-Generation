"""
Folder validation system for game installations.

Provides a hierarchy of validators:
- FolderValidator: Base abstract validator
- ExistingFolderValidator: Validates folder existence
- WritableFolderValidator: Validates write permissions
- GameFolderValidator: Validates game-specific requirements
"""

from abc import ABC, abstractmethod
import logging
from pathlib import Path
import re
import tempfile

from constants import MAX_LUA_FILE_SIZE
from core.GameModels import FileGroup, FileGroupOperator, GameValidationRule
from core.TranslationManager import tr

logger = logging.getLogger(__name__)

# Type alias for validation result
ValidationResult = tuple[bool, str]


# ============================================================================
# BASE VALIDATORS
# ============================================================================


class FolderValidator(ABC):
    """
    Base abstract validator for folder paths.

    All folder validators should inherit from this class and implement
    the validate method.
    """

    @abstractmethod
    def validate(self, path: str) -> ValidationResult:
        """
        Validate a folder path.

        Args:
            path: Path to validate

        Returns:
            Tuple of (is_valid, error_message).
            error_message is empty string if valid.
        """
        pass


class ExistingFolderValidator(FolderValidator):
    """Validates that a folder exists and is a directory."""

    def validate(self, path: str) -> ValidationResult:
        """
        Validate that folder exists.

        Args:
            path: Path to validate

        Returns:
            (True, "") if valid, (False, error_message) otherwise
        """
        if not path:
            return False, tr("validation.folder_required")

        folder = Path(path)

        if not folder.exists():
            return False, tr("validation.folder_not_exist")

        if not folder.is_dir():
            return False, tr("validation.not_a_folder")

        return True, ""


class WritableFolderValidator(ExistingFolderValidator):
    """Validates that a folder exists and is writable."""

    TEST_FILE_PREFIX = ".write_test_"

    def validate(self, path: str) -> ValidationResult:
        """
        Validate that folder exists and is writable.

        Args:
            path: Path to validate

        Returns:
            (True, "") if valid, (False, error_message) otherwise
        """
        # First check existence
        valid, message = super().validate(path)
        if not valid:
            return valid, message

        # Test write permission
        folder = Path(path)

        try:
            with tempfile.NamedTemporaryFile(dir=folder, prefix=self.TEST_FILE_PREFIX):
                pass
            logger.debug(f"Folder is writable: {path}")
            return True, ""

        except PermissionError:
            logger.warning(f"Folder not writable: {path}")
            return False, tr("validation.folder_not_writable")

        except OSError as e:
            logger.warning(f"Error testing write permission for {path}: {e}")
            return False, tr("validation.folder_not_writable")


# ============================================================================
# GAME FOLDER VALIDATOR
# ============================================================================


class GameFolderValidator(ExistingFolderValidator):
    """
    Validates a game folder against specific game requirements.

    Checks:
    - Folder existence (via parent class)
    - Required file groups (all groups must be satisfied)
    - Lua variable conditions in engine.lua
    """

    # Regex to parse Lua numeric variables (compiled once at class level)
    # Matches: variable_name = numeric_value
    _LUA_NUMBER_PATTERN = re.compile(r"(\w+)\s*=\s*(-?\d+(?:\.\d+)?)", re.MULTILINE)

    def __init__(self, validation_rules: GameValidationRule) -> None:
        """
        Initialize validator with game-specific rules.

        Args:
            validation_rules: GameValidationRule instance
        """
        self.validation_rules = validation_rules
        logger.debug(f"GameFolderValidator initialized: {validation_rules}")

    def validate(self, path: str) -> ValidationResult:
        """
        Validate game folder against rules.

        Args:
            path: Path to game folder

        Returns:
            (True, "") if valid, (False, error_message) otherwise
        """
        # Check folder existence
        valid, message = super().validate(path)
        if not valid:
            return valid, message

        # Validate game requirements
        is_valid, error_msg = self._validate_game_requirements(path)
        if not is_valid:
            return False, error_msg

        logger.info(f"Game folder validated successfully: {path}")
        return True, ""

    def _validate_game_requirements(self, folder_path: str) -> ValidationResult:
        """
        Validate folder against all game requirements.

        Args:
            folder_path: Path to game folder

        Returns:
            (True, "") if valid, (False, error_message) otherwise
        """
        folder = Path(folder_path)

        if not folder.is_dir():
            logger.debug(f"Invalid folder: {folder_path}")
            return False, tr("validation.invalid_game_folder")

        # Check required file groups
        if self.validation_rules.required_files:
            result = self._check_required_files(folder)
            if not result[0]:
                return result

        # Check Lua conditions
        if self.validation_rules.lua_checks:
            if not self._check_lua_conditions(folder):
                return False, tr("validation.invalid_game_folder")

        return True, ""

    # ========================================
    # FILE OPERATIONS
    # ========================================

    @staticmethod
    def _find_file_case_insensitive(folder: Path, filename: str) -> Path | None:
        """
        Find a file in folder by name (case-insensitive).

        Supports both simple filenames and relative paths with subdirectories.

        Args:
            folder: Folder to search in
            filename: File name or relative path to find (e.g., "dlc/mod.zip")

        Returns:
            Path to file if found, None otherwise
        """
        file_path = Path(filename)
        full_path = folder / file_path

        try:
            if full_path.is_file():
                return full_path

            parent_dir = full_path.parent

            if not parent_dir.exists():
                parts = file_path.parts
                current = folder

                for part in parts[:-1]:
                    matches = [
                        d
                        for d in current.iterdir()
                        if d.is_dir() and d.name.lower() == part.lower()
                    ]
                    if not matches:
                        return None
                    current = matches[0]

                parent_dir = current

            filename_lower = file_path.name.lower()
            for item in parent_dir.iterdir():
                if item.is_file() and item.name.lower() == filename_lower:
                    return item

            return None

        except (PermissionError, OSError) as e:
            logger.warning(f"Cannot access folder {folder}: {e}")
            return None

    # ========================================
    # VALIDATION CHECKS
    # ========================================

    def _check_required_files(self, folder: Path) -> ValidationResult:
        """
        Validate all required file groups.

        All groups must be satisfied for validation to pass.

        Args:
            folder: Folder to check

        Returns:
            (True, "") if all groups valid, (False, error_message) otherwise
        """
        for group in self.validation_rules.required_files:
            result = self._validate_file_group(folder, group)
            if not result[0]:
                return result

        logger.debug("All file groups validated in {folder}")
        return True, ""

    def _validate_file_group(self, folder: Path, group: FileGroup) -> ValidationResult:
        """
        Validate a file group against its operator logic.

        Args:
            folder: Folder to check
            group: FileGroup to validate

        Returns:
            (True, "") if valid, (False, error_message) otherwise
        """
        found_files = []

        for filename in group.files:
            if self._find_file_case_insensitive(folder, filename):
                found_files.append(filename)

        found_count = len(found_files)
        total_count = len(group.files)

        # Apply operator logic
        is_valid = False

        if group.operator == FileGroupOperator.ALL:
            is_valid = found_count == total_count

        elif group.operator == FileGroupOperator.ANY:
            is_valid = found_count >= 1

        # Log and return result
        if is_valid:
            logger.debug("File group validated: {group.description} (found: {ound_files})")
            return True, ""
        else:
            logger.debug(
                "File group validation failed: {group.description} (found {found_count}/{total_count}: {found_files})"
            )
            return False, tr("validation.file_group_failed", description=group.description)

    def _check_lua_conditions(self, folder: Path) -> bool:
        """
        Verify numeric variable conditions in engine.lua.

        Args:
            folder: Game folder

        Returns:
            True if all conditions met
        """
        # Find engine.lua (case-insensitive)
        engine_path = self._find_file_case_insensitive(folder, "engine.lua")

        if not engine_path:
            logger.debug(f"engine.lua not found in {folder}")
            return False

        # Parse Lua variables
        lua_vars = self._parse_lua_file(engine_path)

        # Check each condition
        for var_name, expected_value in self.validation_rules.lua_checks.items():
            actual_value = lua_vars.get(var_name)

            if actual_value != expected_value:
                logger.debug(
                    f"Lua variable mismatch in {engine_path}: "
                    f"{var_name} (expected={expected_value}, actual={actual_value})"
                )
                return False

        logger.debug(f"All Lua conditions met in {engine_path}")
        return True

    # ========================================
    # LUA PARSING
    # ========================================

    @classmethod
    def _parse_lua_file(cls, file_path: Path) -> dict[str, float | int]:
        """
        Parse a Lua file and extract numeric variables.

        Extracts variables in the format: variable_name = numeric_value
        Supports integers and floats.

        Args:
            file_path: Path to Lua file

        Returns:
            Dictionary of variable_name -> value
        """
        try:
            # Check file size
            file_size = file_path.stat().st_size
            if file_size > MAX_LUA_FILE_SIZE:
                logger.warning(f"Lua file too large ({file_size} bytes): {file_path}")
                return {}

            # Read file content
            content = file_path.read_text(encoding="utf-8", errors="ignore")

        except (PermissionError, OSError) as e:
            logger.warning(f"Failed to read Lua file {file_path}: {e}")
            return {}

        # Extract all numeric variables with regex
        matches = cls._LUA_NUMBER_PATTERN.findall(content)

        # Convert to appropriate numeric type
        variables = {}
        for name, value in matches:
            # Use float if decimal point present, otherwise int
            try:
                variables[name] = float(value) if "." in value else int(value)
            except ValueError:
                logger.warning(f"Failed to parse Lua value: {name}={value}")
                continue

        logger.debug(f"Parsed {len(variables)} variables from {file_path}")
        return variables

    # ========================================
    # UTILITY METHODS
    # ========================================

    def get_validation_rules(self) -> GameValidationRule:
        """
        Get current validation rules.

        Returns:
            GameValidationRule instance
        """
        return self.validation_rules

    def __repr__(self) -> str:
        """String representation for debugging."""
        return f"<GameFolderValidator rule={self.validation_rules}>"
