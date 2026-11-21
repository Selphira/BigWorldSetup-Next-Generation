"""Mods downloaded from a GitHub commit rather than from their latest release."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
import json
import logging
from pathlib import Path
import re

from util.mod_sync.processors.github_processor import GitHubCommit

logger = logging.getLogger(__name__)

# Abbreviated commit hash shown in versions ("master@a1b2c3d")
SHORT_SHA_LENGTH = 7

# A pin reference: "branch", "sha" or "branch@sha"
COMMIT_SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{7,40}$")
REF_SEPARATOR = "@"


class RecipeMode(Enum):
    """How the commit of a pinned mod is chosen."""

    BRANCH = "branch"  # follows the last commit of a branch
    COMMIT = "commit"  # stays on a commit chosen by hand


@dataclass(frozen=True, slots=True)
class GitHubRecipe:
    """Commit a mod is downloaded from, and how it moves."""

    mode: RecipeMode
    commit: str
    date: datetime
    branch: str | None = None

    @property
    def version(self) -> str:
        return f"{self.branch or 'commit'}{REF_SEPARATOR}{self.commit[:SHORT_SHA_LENGTH]}"

    def moved_to(self, commit: GitHubCommit) -> GitHubRecipe:
        return replace(self, commit=commit.sha, date=commit.date)

    @staticmethod
    def parse_ref(ref: str) -> tuple[str | None, str | None]:
        """Split a pin reference into (branch, commit); either may be missing."""
        branch, separator, commit = ref.partition(REF_SEPARATOR)
        if separator:
            return branch or None, commit or None
        if COMMIT_SHA_PATTERN.match(ref):
            return None, ref
        return ref, None

    def to_json(self) -> dict[str, str]:
        data = {"mode": self.mode.value, "commit": self.commit, "date": self.date.isoformat()}
        if self.branch:
            data["branch"] = self.branch
        return data

    @classmethod
    def from_json(cls, data: dict[str, str]) -> GitHubRecipe:
        return cls(
            mode=RecipeMode(data["mode"]),
            commit=data["commit"],
            date=datetime.fromisoformat(data["date"]),
            branch=data.get("branch"),
        )


class GitHubRecipes:
    """Maintainer file of the mods pinned to a GitHub commit.

    The file stays outside ``data/``: it drives mod_sync only and is never
    published with the catalogue. Keys are lowercase tp2 names.
    """

    def __init__(self, recipes_path: Path):
        self.recipes_path = Path(recipes_path)
        self._recipes = self._load()

    def get(self, tp2_name: str) -> GitHubRecipe | None:
        return self._recipes.get(tp2_name.lower())

    def set(self, tp2_name: str, recipe: GitHubRecipe) -> None:
        """Record the recipe of a mod and save the file."""
        self._recipes[tp2_name.lower()] = recipe
        self._save()

    def remove(self, tp2_name: str) -> bool:
        """Forget the recipe of a mod; tell whether it had one."""
        if self._recipes.pop(tp2_name.lower(), None) is None:
            return False
        self._save()
        return True

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> dict[str, GitHubRecipe]:
        """Load the recipes; an unreadable file stops the run rather than unpinning mods."""
        try:
            stored = json.loads(self.recipes_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}

        try:
            return {tp2.lower(): GitHubRecipe.from_json(data) for tp2, data in stored.items()}
        except (AttributeError, KeyError, TypeError, ValueError) as e:
            raise ValueError(f"Invalid GitHub recipe file {self.recipes_path}: {e}") from e

    def _save(self) -> None:
        stored = {tp2: recipe.to_json() for tp2, recipe in sorted(self._recipes.items())}
        self.recipes_path.parent.mkdir(parents=True, exist_ok=True)
        partial_path = self.recipes_path.with_suffix(".part")
        partial_path.write_text(json.dumps(stored, indent=2) + "\n", encoding="utf-8")
        partial_path.replace(self.recipes_path)
