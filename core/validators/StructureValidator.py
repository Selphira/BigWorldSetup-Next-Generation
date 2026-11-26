import logging
import os
from pathlib import Path
import shutil

from constants import TP2_EXTENSION, WEIDU_SETUP_PREFIX

logger = logging.getLogger(__name__)


class StructureValidator:
    """Validates and normalizes extracted mod directory structures.

    WeiDU mods require a specific directory structure:
    - game_dir/setup-{mod}.exe (or .tp2)
    - game_dir/{mod}/{mod}.tp2 (or setup-{mod}.tp2)

    This validator detects and fixes common extraction issues like:
    - Extra nested directories from archive structure
    - Misplaced mod directories
    - Missing parallel files (setup executables, readme, etc.)
    """

    # Possible TP2 locations in order of preference and reliability
    TP2_SEARCH_PATTERNS: list[str] = [
        f"{{game_dir}}/{{tp2}}{TP2_EXTENSION}",  # Rare: TP2 in root
        f"{{game_dir}}/{WEIDU_SETUP_PREFIX}{{tp2}}{TP2_EXTENSION}",  # Setup TP2 in root
        f"{{game_dir}}/{{tp2}}/{{tp2}}{TP2_EXTENSION}",  # Standard: TP2 in mod folder
        f"{{game_dir}}/{{tp2}}/{WEIDU_SETUP_PREFIX}{{tp2}}{TP2_EXTENSION}",  # Setup TP2 in folder
    ]

    @staticmethod
    def validate_structure(game_dir: Path, tp2_name: str) -> tuple[bool, Path | None]:
        """Validate that mod has correct directory structure.

        Checks if the mod's TP2 file can be found in any expected location.

        Args:
            game_dir: Game directory where mod should be installed
            tp2_name: Mod identifier

        Returns:
            Tuple of (is_valid, tp2_path_if_found)
        """
        for pattern in StructureValidator.TP2_SEARCH_PATTERNS:
            tp2_path = Path(pattern.format(game_dir=game_dir, tp2=tp2_name))
            if tp2_path.is_file():
                return True, tp2_path

        logger.warning(f"No valid TP2 found for mod '{tp2_name}' in {game_dir}")
        return False, None

    @staticmethod
    def normalize_structure(temp_dir: Path, tp2_name: str | None = None) -> bool:
        """Normalize extracted mod structure inside the temp directory.

        After normalization, structure will be either:
        - temp_dir/setup-{tp2_name}.tp2
        - temp_dir/{tp2_name}/setup-{tp2_name}.tp2
        """
        if not tp2_name:
            logger.info(f"Flattening directory structure: {temp_dir}")
            StructureValidator.flatten_single_child_directories(temp_dir)
            return True

        tp2_path = StructureValidator._find_tp2(temp_dir, tp2_name)
        if tp2_path is None:
            logger.error(f"TP2 not found for '{tp2_name}' in {temp_dir}")
            return False

        tp2_dir = tp2_path.parent

        if tp2_dir in (temp_dir, temp_dir / tp2_name):
            return True

        logger.info(f"Normalizing mod structure: {tp2_name}")

        try:
            if tp2_dir.name.lower() == tp2_name.lower():
                shutil.move(str(tp2_dir), str(temp_dir / tp2_name))
                intermediate = tp2_dir.parent
            else:
                target = temp_dir if (tp2_dir / tp2_name).is_dir() else temp_dir / tp2_name
                target.mkdir(exist_ok=True)
                for item in list(tp2_dir.iterdir()):
                    if not (dest := target / item.name).exists():
                        shutil.move(str(item), str(dest))
                intermediate = tp2_dir

            # Move remaining siblings then delete intermediate
            for item in list(intermediate.iterdir()):
                if not (dest := temp_dir / item.name).exists():
                    shutil.move(str(item), str(dest))
            shutil.rmtree(str(intermediate), ignore_errors=True)

        except Exception as e:
            logger.error(
                f"Failed to normalize mod structure for '{tp2_name}': {e}", exc_info=True
            )
            return False

        return True

    @staticmethod
    def _find_tp2(temp_dir: Path, tp2_name: str) -> Path | None:
        """Return the preferred TP2 for a mod, walking the tree only once.

        A plain ``{tp2}.tp2`` wins over ``setup-{tp2}.tp2``; within a name,
        the deepest path wins and the first one walked breaks ties. Names
        are compared like the platform compares paths, as ``rglob`` does.
        """
        candidate_names = [
            os.path.normcase(f"{name}{TP2_EXTENSION}")
            for name in (tp2_name, f"{WEIDU_SETUP_PREFIX}{tp2_name}")
        ]
        best_paths: list[Path | None] = [None] * len(candidate_names)

        for path in temp_dir.rglob(f"*{TP2_EXTENSION}"):
            name = os.path.normcase(path.name)
            if name not in candidate_names:
                continue

            rank = candidate_names.index(name)
            best_path = best_paths[rank]
            if best_path is None or len(path.parts) > len(best_path.parts):
                best_paths[rank] = path

        return next((path for path in best_paths if path is not None), None)

    @staticmethod
    def flatten_single_child_directories(root_dir: Path) -> None:
        """Recursively flatten directory trees with single-child directories.

        Removes unnecessary nesting by moving contents up when a directory
        contains only one subdirectory and no files.

        This handles common archive extraction issues where the archive
        contains a root folder that just wraps the actual content.

        Args:
            root_dir: Directory to flatten
        """
        root_dir = Path(root_dir)

        if not root_dir.is_dir():
            logger.warning(f"Cannot flatten non-directory: {root_dir}")
            return

        iteration_count = 0
        max_iterations = 100  # Prevent infinite loops

        while iteration_count < max_iterations:
            iteration_count += 1

            try:
                entries = list(root_dir.iterdir())
            except PermissionError as e:
                logger.error(f"Permission denied accessing {root_dir}: {e}")
                break

            # Separate files and directories
            subdirs = [e for e in entries if e.is_dir()]
            files = [e for e in entries if e.is_file()]

            # Stop if multiple items or any files exist
            if len(files) > 0 or len(subdirs) != 1:
                logger.debug(
                    f"Flattening stopped at {root_dir}: {len(files)} files, {len(subdirs)} dirs"
                )
                break

            single_subdir = subdirs[0]
            logger.debug(f"Flattening: moving contents of {single_subdir.name} up to parent")

            # Move all contents of the single subdirectory up
            try:
                for item in single_subdir.iterdir():
                    target = root_dir / item.name
                    if target.exists():
                        logger.warning(
                            f"Conflict during flattening: {target} already exists, skipping"
                        )
                        continue
                    shutil.move(str(item), str(root_dir))

                # Remove now-empty subdirectory
                single_subdir.rmdir()

            except (PermissionError, OSError) as e:
                logger.error(f"Error moving contents from {single_subdir}: {e}")
                break

        if iteration_count >= max_iterations:
            logger.warning(f"Flattening stopped: max iterations reached for {root_dir}")
