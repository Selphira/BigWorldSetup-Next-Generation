import logging
from pathlib import Path
import shutil

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
        "{game_dir}/setup-{tp2}.exe",  # Most common: setup executable
        "{game_dir}/{tp2}.tp2",  # Rare: TP2 in root
        "{game_dir}/setup-{tp2}.tp2",  # Alternative: setup TP2 in root
        "{game_dir}/{tp2}/{tp2}.tp2",  # Standard: TP2 in mod folder
        "{game_dir}/{tp2}/setup-{tp2}.tp2",  # Alternative: setup TP2 in folder
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
            if tp2_path.exists():
                return True, tp2_path

        logger.warning(f"No valid TP2 found for mod '{tp2_name}' in {game_dir}")
        return False, None

    @staticmethod
    def normalize_structure(extraction_dir: Path, tp2_name: str | None = None) -> bool:
        """Normalize mod directory structure after extraction.

        This method handles two scenarios:
        1. If tp2_name is known: Ensures proper WeiDU structure
        2. If tp2_name is None: Flattens unnecessary nested directories

        Args:
            extraction_dir: Directory where mod was extracted
            tp2_name: Mod identifier, or None to just flatten

        Returns:
            True if structure was normalized successfully
        """
        if tp2_name is None:
            logger.info(f"Flattening directory structure: {extraction_dir}")
            StructureValidator.flatten_single_child_directories(extraction_dir)
            return True

        # Check if already valid
        is_valid, _ = StructureValidator.validate_structure(extraction_dir, tp2_name)
        if is_valid:
            return True

        logger.info(f"Normalizing mod structure: {tp2_name}")

        try:
            return StructureValidator._fix_mod_structure(extraction_dir, tp2_name)
        except Exception as e:
            logger.error(
                f"Failed to normalize mod structure for '{tp2_name}': {e}", exc_info=True
            )
            return False

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

    # ========================================================================
    # Internal Methods
    # ========================================================================

    @staticmethod
    def _fix_mod_structure(game_dir: Path, tp2_name: str) -> bool:
        """Fix incorrect mod directory structure.

        Steps:
        1. Find the mod directory (contains TP2 file)
        2. Find the anchor point (where companion files are located)
        3. Move mod directory to correct location
        4. Move companion files (setup.exe, readme, etc.) to game directory
        5. Clean up empty directories

        Args:
            game_dir: Game directory
            tp2_name: Mod identifier

        Returns:
            True if structure was fixed successfully
        """
        mod_dir = StructureValidator._find_mod_directory(game_dir, tp2_name)
        if mod_dir is None:
            logger.error(
                f"Cannot fix structure: mod directory '{tp2_name}' not found in {game_dir}"
            )
            return False

        anchor_dir = StructureValidator._find_anchor_directory(mod_dir, game_dir, tp2_name)

        logger.info(f"Fixing mod structure: mod_dir={mod_dir}, anchor={anchor_dir}")

        target_mod_dir = game_dir / tp2_name
        if mod_dir.resolve() != target_mod_dir.resolve():
            logger.debug(f"Moving mod directory: {mod_dir} -> {target_mod_dir}")
            try:
                if target_mod_dir.exists():
                    logger.warning(f"Target mod directory already exists: {target_mod_dir}")
                    return False
                shutil.move(str(mod_dir), str(target_mod_dir))
            except (PermissionError, OSError) as e:
                logger.error(f"Failed to move mod directory: {e}")
                return False

        if anchor_dir != game_dir:
            try:
                for item in anchor_dir.iterdir():
                    if item.name.lower() == tp2_name:
                        continue

                    destination = game_dir / item.name
                    if destination.exists():
                        logger.debug(f"Skipping existing file: {destination}")
                        continue

                    logger.debug(f"Moving companion file: {item.name}")
                    shutil.move(str(item), str(destination))

            except (PermissionError, OSError) as e:
                logger.error(f"Failed to move companion files: {e}")
                # Not a critical error, continue

        if anchor_dir != game_dir:
            StructureValidator._remove_empty_directories(anchor_dir)

        logger.info(f"Successfully normalized structure for '{tp2_name}'")
        return True

    @staticmethod
    def _find_mod_directory(search_root: Path, tp2_name: str) -> Path | None:
        """Find the mod directory containing the TP2 file.

        Searches recursively for a directory named {tp2_name} that contains
        either {tp2_name}.tp2 or setup-{tp2_name}.tp2.

        Args:
            search_root: Root directory to search from
            tp2_name: Mod identifier

        Returns:
            Path to mod directory, or None if not found
        """
        for candidate_dir in search_root.rglob(tp2_name):
            if not candidate_dir.is_dir():
                continue

            # Check for TP2 file in this directory
            tp2_variants = [
                candidate_dir / f"{tp2_name}.tp2",
                candidate_dir / f"setup-{tp2_name}.tp2",
            ]

            for tp2_path in tp2_variants:
                if tp2_path.exists():
                    logger.debug(f"Found mod directory: {candidate_dir}")
                    return candidate_dir

        logger.debug(f"Mod directory not found for: {tp2_name}")
        return None

    @staticmethod
    def _find_anchor_directory(mod_dir: Path, game_dir: Path, tp2_name: str) -> Path:
        """Find the anchor directory containing companion files.

        The anchor is the directory that contains files that should be
        alongside the mod directory (like setup.exe, readme.txt, etc.).

        Traverses up from mod_dir looking for:
        1. setup-{tp2_name}.exe (strong indicator of anchor)
        2. A directory containing a {tp2_name} subdirectory with valid TP2

        Args:
            mod_dir: Mod directory path
            game_dir: Game directory (fallback anchor)
            tp2_name: Mod identifier

        Returns:
            Path to anchor directory
        """
        current = mod_dir.parent

        while current != game_dir.parent:  # Don't go above game_dir
            # Look for setup executable (strongest indicator)
            setup_exe = current / f"setup-{tp2_name}.exe"
            if setup_exe.exists():
                logger.debug(f"Anchor found via setup.exe: {current}")
                return current

            # Check if this directory contains a valid mod subdirectory
            mod_subdir = current / tp2_name
            if mod_subdir.is_dir():
                tp2_in_subdir = (mod_subdir / f"{tp2_name}.tp2").exists() or (
                    mod_subdir / f"setup-{tp2_name}.tp2"
                ).exists()
                if tp2_in_subdir:
                    logger.debug(f"Anchor found via mod subdirectory: {current}")
                    return current

            # Move up one level
            if current == game_dir:
                break
            current = current.parent

        logger.debug(f"Using game directory as anchor: {game_dir}")
        return game_dir

    @staticmethod
    def _remove_empty_directories(root_dir: Path) -> None:
        """Recursively remove empty directories.

        Performs depth-first traversal to remove directories bottom-up.

        Args:
            root_dir: Root directory to clean
        """
        if not root_dir.is_dir():
            return

        # Recursively clean subdirectories first (depth-first)
        try:
            for subdir in list(root_dir.iterdir()):
                if subdir.is_dir():
                    StructureValidator._remove_empty_directories(subdir)
        except PermissionError as e:
            logger.warning(f"Cannot access directory for cleanup: {root_dir}: {e}")
            return

        try:
            if not any(root_dir.iterdir()):  # Check if empty
                logger.debug(f"Removing empty directory: {root_dir}")
                root_dir.rmdir()
        except (PermissionError, OSError) as e:
            logger.debug(f"Cannot remove directory {root_dir}: {e}")
