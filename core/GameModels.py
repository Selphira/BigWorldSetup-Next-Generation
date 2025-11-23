from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class GameValidationRule:
    """Validation rules for a single game folder sequence.

    Attributes:
        game_folder: Optional reference to another game's folder (for widget reuse)
                    e.g., "sod" means this sequence shares SOD's folder widget
        required_files: List of files that must exist
        lua_checks: Dictionary of Lua variable checks {var_name: expected_value}
    """
    required_files: tuple[str, ...]
    lua_checks: dict[str, Any]
    game_folder: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'GameValidationRule':
        """Create validation sequence from dictionary.

        Args:
            data: Dictionary with validation rules

        Returns:
            GameValidationRule instance
        """
        return cls(
            required_files=tuple(data.get("required_files", [])),
            lua_checks=data.get("lua_checks", {}),
            game_folder=data.get("game_folder")
        )


@dataclass
class GameSequence:
    """One installation sequence of a game (BG1, BG2, SoD, …)

    - game_folder: Folder key used by UI
    - required_files: files that must exist
    - lua_checks: Lua engine variables
    - allowed_mods / ignored_mods: filtering rules
    - allowed_components: per-mod component filtering
    """

    game_folder: str
    required_files: list[str]
    lua_checks: dict[str, Any]

    allowed_mods: Optional[list[str]] = None
    ignored_mods: Optional[list[str]] = None
    allowed_components: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> "GameSequence":
        return cls(
            game_folder=data.get("game_folder"),
            required_files=data.get("required_files", []),
            lua_checks=data.get("lua_checks", {}),

            allowed_mods=data.get("allowed_mods"),
            ignored_mods=data.get("ignored_mods"),
            allowed_components=data.get("allowed_components", {})
        )


@dataclass
class GameDefinition:
    """Definition of a full game (EET, BG2EE, IWDEE, …)"""
    id: str
    name: str
    sequences: list[GameSequence]

    def get_sequence(self, index: int) -> Optional[GameSequence]:
        if 0 <= index < len(self.sequences):
            return self.sequences[index]
        return None

    def get_folder_keys(self) -> list[str]:
        """ Get list of unique folder keys needed for UI widgets.

        For standalone sequences: uses game id
        For shared sequences: uses referenced game id

        Example:
            EET returns ["sod", "bg2ee"] (not ["eet", "eet"])
            BGEE returns ["bgee"]

        Returns:
            List of folder keys for widget creation
        """
        folder_keys = []
        for sequence in self.sequences:
            if sequence.game_folder:
                folder_keys.append(sequence.game_folder)
            else:
                folder_keys.append(self.id)

        return folder_keys
