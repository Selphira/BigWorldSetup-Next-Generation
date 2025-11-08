from enum import Enum
from typing import Dict, Any


class GameEnum(Enum):
    """Enumeration of supported Infinity Engine games with validation rules."""

    # Format: (code, display_name, sequence_count, validation_rules)

    EET = (
        "eet",
        "Enhanced Edition Trilogy",
        2,
        {
            "sequences": [
                {
                    "label_key": "installation.bgee_source_folder",
                    "required_files": ["chitin.key", "Baldur.exe"],
                    "lua_checks": {"engine_mode": 0}
                },
                {
                    "label_key": "installation.bg2ee_target_folder",
                    "required_files": ["chitin.key", "Baldur.exe"],
                    "lua_checks": {"engine_mode": 1}
                }
            ]
        }
    )

    BGEE = (
        "bgee",
        "Baldur's Gate: Enhanced Edition",
        1,
        {
            "sequences": [{
                "required_files": ["chitin.key", "Baldur.exe"],
                "lua_checks": {"engine_mode": 0}
            }]
        }
    )

    BG2EE = (
        "bg2ee",
        "Baldur's Gate II: Enhanced Edition",
        1,
        {
            "sequences": [{
                "required_files": ["chitin.key", "Baldur.exe"],
                "lua_checks": {"engine_mode": 1}
            }]
        }
    )

    IWDEE = (
        "iwdee",
        "Icewind Dale: Enhanced Edition",
        1,
        {
            "sequences": [{
                "required_files": ["chitin.key", "Baldur.exe"],
                "lua_checks": {"engine_mode": 2}
            }]
        }
    )

    PSTEE = (
        "pstee",
        "Planescape: Torment: Enhanced Edition",
        1,
        {
            "sequences": [{
                "required_files": ["chitin.key", "Torment.exe"],
                "lua_checks": {}
            }]
        }
    )

    def __init__(
            self,
            code: str,
            display_name: str,
            sequence_count: int,
            validation_rules: Dict[str, Any]
    ):
        """Initialize game enum value.

        Args:
            code: Short game code (bgee, bg2ee, etc.)
            display_name: Human-readable game name
            sequence_count: Number of folder sequences (1 for most, 2 for EET)
            validation_rules: Dictionary containing validation rules for each sequence
        """
        self.code = code
        self.display_name = display_name
        self.sequence_count = sequence_count
        self.validation_rules = validation_rules

    @classmethod
    def from_code(cls, code: str) -> 'GameEnum':
        """Get GameEnum from code string.

        Args:
            code: Game code (e.g., "bgee")

        Returns:
            GameEnum instance

        Raises:
            ValueError: If code not found
        """
        for game in cls:
            if game.code == code:
                return game
        raise ValueError(f"Unknown game code: {code}")

    @classmethod
    def get_all_codes(cls) -> list:
        """Get list of all game codes.

        Returns:
            List of game code strings
        """
        return [game.code for game in cls]

    def __str__(self) -> str:
        """String representation."""
        return f"{self.display_name} ({self.code})"

    def __repr__(self) -> str:
        """Developer representation."""
        return f"<GameEnum.{self.name}: {self.code}>"