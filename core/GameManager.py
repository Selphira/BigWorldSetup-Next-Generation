import json
import logging
from pathlib import Path

from core.GameModels import GameDefinition

logger = logging.getLogger(__name__)


class GameManager:
    """Loads all game definitions from the games/*.json of the catalogue

    Provides access to:
    - Game list
    - Sequence definitions
    - Basic validation helpers
    """

    def __init__(self, games_dir: Path) -> None:
        self.games: dict[str, GameDefinition] = {}
        self.games_dir = games_dir

    # ----------------------------------------------------------------------
    # PUBLIC API
    # ----------------------------------------------------------------------

    def load_games(self) -> None:
        """Load all JSON definitions from the games directory"""
        if not self.games_dir.exists():
            raise RuntimeError(f"Game directory not found: {self.games_dir}")

        for file in self.games_dir.glob("*.json"):
            try:
                game_def = self._load_game_file(file)
                self.games[game_def.id] = game_def
                logger.info(f"Loaded game: {game_def.id}")
            except Exception as e:
                logger.error(f"Failed to load game file {file.name}: {e}")

    def get(self, game_id: str) -> GameDefinition | None:
        """Return GameDefinition or None."""
        return self.games.get(game_id)

    def get_all(self):
        """Return all loaded games."""
        return list(self.games.values())

    # ----------------------------------------------------------------------
    # INTERNALS
    # ----------------------------------------------------------------------

    @staticmethod
    def _load_game_file(path: Path) -> GameDefinition:
        """Parse one JSON file and return a GameDefinition instance."""
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)

        return GameDefinition.from_dict(raw)
