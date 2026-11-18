"""Validate relationships between configured filesystem roots."""

from itertools import combinations
from pathlib import Path

from core.PathSafety import PathSafety


class FolderRelationshipValidator:
    """Check that game, archive and backup roots do not overlap."""

    def validate(self, paths: list[str]) -> tuple[bool, str]:
        errors = self.find_errors(paths)
        return (False, next(iter(errors.values()))) if errors else (True, "")

    def find_errors(self, paths: list[str]) -> dict[int, str]:
        """Return errors indexed by the affected paths, including both sides of a conflict."""
        errors: dict[int, str] = {}

        for first_index, second_index in combinations(range(len(paths)), 2):
            first, second = paths[first_index], paths[second_index]
            try:
                PathSafety.independent_paths(Path(first), Path(second))
            except (OSError, RuntimeError) as error:
                errors.setdefault(first_index, str(error))
                errors.setdefault(second_index, str(error))

        return errors
