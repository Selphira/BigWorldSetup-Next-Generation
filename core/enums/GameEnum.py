"""Game enumeration with validation rules for Infinity Engine games."""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class GameValidationRule:
    """
    Validation rules for a single game folder sequence.

    Attributes:
        game_folder: Optional reference to another game's folder (for widget reuse)
                    e.g., "sod" means this sequence shares SOD's folder widget
        required_files: List of files that must exist
        lua_checks: Dictionary of Lua variable checks {var_name: expected_value}
    """
    required_files: tuple[str, ...]
    lua_checks: Dict[str, Any]
    game_folder: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'GameValidationRule':
        """
        Create validation sequence from dictionary.

        Args:
            data: Dictionary with validation rules

        Returns:
            GameValidationSequence instance
        """
        return cls(
            required_files=tuple(data.get("required_files", [])),
            lua_checks=data.get("lua_checks", {}),
            game_folder=data.get("game_folder")
        )


class GameEnum(Enum):
    """
    Enumeration of supported Infinity Engine games.

    Each game has:
    - code: Short identifier (bgee, bg2ee, etc.)
    - display_name: Human-readable name
    - sequence_count: Number of folder sequences (1 for most, 2 for EET)
    - validation_rules: List of validation rules for each sequence

    The 'game_folder' attribute in sequences enables folder widget reuse:
    When a sequence has game_folder="sod", the UI will:
    1. Use the same folder selector widget as the SOD game
    2. Automatically update all games referencing "sod" when the path changes
    3. Share validation state across all references
    """

    # Enhanced Edition Trilogy (requires 2 folders)
    # First folder is SOD installation, second is BG2EE installation
    EET = (
        "eet",
        "Enhanced Edition Trilogy",
        2,
        [
            GameValidationRule(
                game_folder="sod",  # Reuses SOD's folder widget
                required_files=("chitin.key", "Baldur.exe"),
                lua_checks={"engine_mode": 0}
            ),
            GameValidationRule(
                game_folder="bg2ee",  # Reuses BG2EE's folder widget
                required_files=("chitin.key", "Baldur.exe"),
                lua_checks={"engine_mode": 1}
            )
        ]
    )

    # Baldur's Gate: Enhanced Edition
    BGEE = (
        "bgee",
        "Baldur's Gate: Enhanced Edition",
        1,
        [
            GameValidationRule(
                required_files=("chitin.key", "Baldur.exe"),
                lua_checks={"engine_mode": 0}
            )
        ]
    )

    # Baldur's Gate: Siege of Dragonspear
    SOD = (
        "sod",
        "Baldur's Gate: Siege of Dragonspear",
        1,
        [
            GameValidationRule(
                required_files=("chitin.key", "Baldur.exe", "sod-dlc.zip"),
                lua_checks={"engine_mode": 0}
            )
        ]
    )

    # Baldur's Gate II: Enhanced Edition
    BG2EE = (
        "bg2ee",
        "Baldur's Gate II: Enhanced Edition",
        1,
        [
            GameValidationRule(
                required_files=("chitin.key", "Baldur.exe"),
                lua_checks={"engine_mode": 1}
            )
        ]
    )

    # Icewind Dale: Enhanced Edition
    IWDEE = (
        "iwdee",
        "Icewind Dale: Enhanced Edition",
        1,
        [
            GameValidationRule(
                required_files=("chitin.key", "Baldur.exe"),
                lua_checks={"engine_mode": 2}
            )
        ]
    )

    # Planescape: Torment: Enhanced Edition
    PSTEE = (
        "pstee",
        "Planescape: Torment: Enhanced Edition",
        1,
        [
            GameValidationRule(
                required_files=("chitin.key", "Torment.exe"),
                lua_checks={}
            )
        ]
    )

    def __init__(
            self,
            code: str,
            display_name: str,
            sequence_count: int,
            validation_rules: List[GameValidationRule]
    ) -> None:
        """
        Initialize game enum value.

        Args:
            code: Short game code (bgee, bg2ee, etc.)
            display_name: Human-readable game name
            sequence_count: Number of folder sequences
            validation_rules: List of validation rules per sequence
        """
        self.code = code
        self.display_name = display_name
        self.sequence_count = sequence_count
        self.validation_rules = validation_rules

    @classmethod
    def from_code(cls, code: str) -> Optional['GameEnum']:
        """
        Get GameEnum from code string.

        Args:
            code: Game code (e.g., "bgee")

        Returns:
            GameEnum instance or None if not found
        """
        for game in cls:
            if game.code == code:
                return game
        raise ValueError(f"Unknown game code: {code}")

    def __str__(self) -> str:
        """String representation."""
        return f"{self.display_name} ({self.code})"

    def __repr__(self) -> str:
        """Developer representation."""
        return f"<GameEnum.{self.name}: {self.code}>"
