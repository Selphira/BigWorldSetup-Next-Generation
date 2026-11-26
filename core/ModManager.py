"""
Mod manager with multilingual cache support.

Handles loading, caching, and localization of mods with optimized
performance for large mod databases.
"""

from collections import Counter
import json
import logging
from pathlib import Path
from typing import Any, cast

from constants import (
    CACHE_DIR,
    CUSTOM_MODS_DIR,
    MOD_CACHE_LOCALIZATION_PROGRESS,
    MOD_CACHE_PREFIX,
)
from core.ComponentReference import ComponentReference, IndexManager, ReferenceIndexes
from core.LocalizedCache import (
    CacheBuildError,
    CacheGroups,
    LocalizedCacheBuilder,
    LocalizedCacheManager,
    first_translation,
)
from core.Mod import Mod, MucComponent, SubComponent
from core.TranslationManager import SUPPORTED_LANGUAGES, get_translator, tr

logger = logging.getLogger(__name__)

# Single cache group: the mod cache file is a plain list of mods
MOD_CACHE_GROUP = "mods"


def list_json_files(directory: Path) -> list[Path]:
    """Return the JSON files of a directory, or nothing if it does not exist."""
    return list(directory.glob("*.json")) if directory.exists() else []


def select_mod_files(mods_dir: Path, custom_mods_dir: Path) -> list[tuple[Path, bool]]:
    """Return the file of each mod and whether it is custom.

    A mod defined in both directories is read from its most recently modified file.
    """
    official_files = {path.stem: path for path in list_json_files(mods_dir)}
    custom_files = {path.stem: path for path in list_json_files(custom_mods_dir)}
    conflicting_ids = official_files.keys() & custom_files.keys()

    if conflicting_ids:
        logger.info(
            f"Found {len(conflicting_ids)} mod conflicts, resolving by modification time"
        )

    result = []
    for mod_id, custom_path in custom_files.items():
        if mod_id in conflicting_ids:
            official_path = official_files[mod_id]
            if custom_path.stat().st_mtime >= official_path.stat().st_mtime:
                result.append((custom_path, True))
                logger.info(f"Conflict resolved: using custom version of '{mod_id}' (newer)")
            else:
                result.append((official_path, False))
                logger.info(f"Conflict resolved: using official version of '{mod_id}' (newer)")
        else:
            result.append((custom_path, True))

    for mod_id, official_path in official_files.items():
        if mod_id not in conflicting_ids:
            result.append((official_path, False))

    logger.info(
        f"Merged {len(result)} mods ({len(custom_files)} custom, {len(official_files)} official, "
        f"{len(conflicting_ids)} conflicts)"
    )

    return result


class CacheBuilderThread(LocalizedCacheBuilder):
    """Thread building the mod cache without blocking UI."""

    cache_prefix = MOD_CACHE_PREFIX
    cache_label = "mod cache"
    localization_progress_start = MOD_CACHE_LOCALIZATION_PROGRESS

    def __init__(
        self, mods_dir: Path, custom_mods_dir: Path, cache_dir: Path, languages: list[str]
    ) -> None:
        """
        Initialize cache builder thread.

        Args:
            mods_dir: Directory containing mod JSON files
            custom_mods_dir: Directory containing custom mod JSON files
            cache_dir: Directory for cache output
            languages: Languages to build cache for
        """
        super().__init__(cache_dir, languages)
        self.mods_dir = mods_dir
        self.custom_mods_dir = custom_mods_dir

    def _load_sources(self) -> CacheGroups:
        """Load official and custom mods, reporting loading progress."""
        self.status_changed.emit(tr("app.searching_mod_files"))

        source_files = select_mod_files(self.mods_dir, self.custom_mods_dir)

        if not source_files:
            raise CacheBuildError(
                f"No JSON files found in {self.mods_dir} or {self.custom_mods_dir}"
            )

        self.status_changed.emit(tr("app.loading_count_mods", count=len(source_files)))

        mods_data = []
        for i, (file_path, is_custom) in enumerate(source_files):
            self._raise_if_stopped()

            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    mods_data.append(
                        {"id": file_path.stem, "custom": is_custom, **json.load(f)}
                    )
            except Exception as e:
                self._report_skipped_source(f"Error loading {file_path.name}: {e}")
                continue

            self.progress.emit(
                int((i + 1) / len(source_files) * self.localization_progress_start)
            )

        if not mods_data:
            raise CacheBuildError("No valid mods found")

        return {MOD_CACHE_GROUP: mods_data}

    def _localize_entry(self, entry: dict[str, Any], language: str) -> dict[str, Any]:
        return self._localize_mod(entry, language)

    def _cache_content(self, localized_groups: CacheGroups) -> object:
        return localized_groups[MOD_CACHE_GROUP]

    @staticmethod
    def _localize_mod(mod: dict[str, Any], target_lang: str) -> dict[str, Any]:
        """
        Create localized version of mod for target language.

        Applies fallback system:
        target_language → other languages → empty key

        Args:
            mod: Mod data dictionary
            target_lang: Target language code

        Returns:
            Localized mod dictionary
        """
        result = mod.copy()
        translations_all = mod.get("translations", {})
        result.pop("translations", None)

        description = first_translation(
            translations_all, target_lang, lambda texts: texts.get("description", "")
        )

        def resolve_component_text(key: str) -> str:
            """Find best translation for a component key."""
            return first_translation(
                translations_all,
                target_lang,
                lambda texts: texts.get("components", {}).get(key, ""),
            )

        localized_components = {}

        for comp_key, comp_data in mod.get("components", {}).items():
            comp_type = comp_data.get("type", "std")

            # Main component text
            localized_components[comp_key] = resolve_component_text(comp_key)

            # MUC components (mutually exclusive choices)
            if comp_type == "muc":
                for sub_key in comp_data.get("components", []):
                    localized_components[sub_key] = resolve_component_text(sub_key)

            # SUB components (with prompts)
            elif comp_type == "sub":
                for prompt_key, prompt_data in comp_data.get("prompts", {}).items():
                    # Prompt text
                    full_key = f"{comp_key}.{prompt_key}"
                    localized_components[full_key] = resolve_component_text(full_key)

                    # Option texts
                    for option in prompt_data.get("options", []):
                        full_key = f"{comp_key}.{prompt_key}.{option}"
                        localized_components[full_key] = resolve_component_text(full_key)

        result["translations"] = {
            "description": description,
            "components": localized_components,
        }

        return result


class ModManager(LocalizedCacheManager):
    """
    Centralized mod manager.

    Responsibilities:
    - Loading mods from cache
    - Managing multilingual cache
    - Providing access to localized data
    - Filtering and searching mods
    """

    cache_prefix = MOD_CACHE_PREFIX
    cache_label = "mod cache"

    def __init__(
        self,
        mods_dir: Path,
        custom_mods_dir: Path = CUSTOM_MODS_DIR,
        cache_dir: Path = CACHE_DIR,
    ) -> None:
        """
        Initialize mod manager.

        Args:
            mods_dir: Directory containing mod source files
            custom_mods_dir: Directory containing custom mods
            cache_dir: Directory for cache storage
        """
        super().__init__(cache_dir)
        self.mods_dir = mods_dir
        self.custom_mods_dir = custom_mods_dir

        self.current_language = get_translator().current_language
        self.mods_data: dict[str, Mod] = {}
        self.revision: int = 0

        self.custom_mods_dir.mkdir(parents=True, exist_ok=True)

    # ========================================
    # CACHE MANAGEMENT
    # ========================================

    def needs_cache_rebuild(self) -> bool:
        """Check if cache needs to be rebuilt; no mod source at all always requires it."""
        if not self._source_files():
            logger.warning("No source files found")
            return True

        return self._is_cache_stale()

    def _create_cache_builder(self, languages: list[str]) -> CacheBuilderThread:
        return CacheBuilderThread(
            self.mods_dir, self.custom_mods_dir, self.cache_dir, languages
        )

    def _source_files(self) -> list[Path]:
        return [path for path, _ in select_mod_files(self.mods_dir, self.custom_mods_dir)]

    @staticmethod
    def _register_components(
        mod_data: dict[str, Any], mods: dict[str, Mod], indexes: ReferenceIndexes
    ) -> None:
        """Register a mod, its components and their parent-child links."""
        mod = Mod(mod_data)
        mods[mod.id.lower()] = mod

        mod_ref = indexes.register_mod(mod)

        comp_refs = []
        for component in mod.get_components():
            comp_ref = indexes.register_component(component)
            comp_refs.append(comp_ref)

            if component.is_muc():
                component = cast(MucComponent, component)
                children_refs = []
                for muc_component in component.components.values():
                    muc_comp_ref = indexes.register_component(muc_component)
                    children_refs.append(muc_comp_ref)
                indexes.register_parent_child(comp_ref, children_refs)
            elif component.is_sub():
                component = cast(SubComponent, component)
                prompt_children_refs = []
                for prompt_key, prompt in component.prompts.items():
                    option_children_refs = []
                    prompt_ref = ComponentReference.for_component(
                        mod.id, f"{component.key}.{prompt_key}"
                    )
                    prompt_children_refs.append(prompt_ref)

                    for option in prompt.options:
                        option_children_refs.append(
                            ComponentReference.for_component(
                                mod.id, f"{component.key}.{prompt_key}.{option}"
                            )
                        )
                    indexes.register_parent_child(prompt_ref, option_children_refs)

                comp_ref = indexes.register_component(component)
                indexes.register_parent_child(comp_ref, prompt_children_refs)

        indexes.register_parent_child(mod_ref, comp_refs)

    def load_cache(self) -> bool:
        """
        Load cache for current language.

        Returns:
            True if successful, False otherwise
        """
        return self._load_language(self.current_language)

    def reload_for_language(self, language: str) -> bool:
        """
        Reload cache for a new language.

        The language becomes the current one only once its catalogue is loaded.

        Args:
            language: Language code (e.g., "fr_FR")

        Returns:
            True if successful
        """
        if not any(lang == language for lang, _ in SUPPORTED_LANGUAGES):
            logger.warning(f"Unsupported language: {language}")
            return False

        if self.current_language == language:
            return True

        if not self._load_language(language):
            return False

        self.current_language = language
        return True

    def _load_language(self, language: str) -> bool:
        """Load and publish the catalogue of a language; a failure keeps the loaded one."""
        cache_file = self._cache_file(language)

        if not cache_file.exists():
            logger.error(f"Cache file not found: {cache_file}")
            return False

        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                mods_json = json.load(f)

            # The catalogue is rebuilt aside and substituted once complete: a
            # failed load keeps the previous one, a removed mod leaves no reference
            mods: dict[str, Mod] = {}
            catalogue_indexes = ReferenceIndexes()
            for data in mods_json:
                self._register_components(data, mods, catalogue_indexes)

        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in cache: {e}")
            return False
        except Exception as e:
            logger.exception(f"Error loading cache: {e}")
            return False

        indexes = IndexManager.get_indexes()
        indexes.replace_catalogue(catalogue_indexes)
        self.mods_data.clear()
        self.mods_data.update(mods)
        self.revision += 1

        logger.info(f"Loaded {len(self.mods_data)} mods for {language}")
        logger.info(f"Mod/Component index populated: {len(indexes.mod_component_index)}")
        logger.info(f"Children index populated: {len(indexes.children_index)} ")
        logger.info(f"Parent index populated: {len(indexes.parent_index)} ")
        return True

    # ========================================
    # MOD ACCESS
    # ========================================

    def get_all_mods(self) -> dict[str, Mod]:
        """Return all mods (returns reference, do not modify)."""
        return self.mods_data

    def get_mod_by_id(self, mod_id: str) -> Mod | None:
        """
        Find mod by ID.

        Args:
            mod_id: Mod identifier

        Returns:
            Mod instance or None
        """
        return self.mods_data.get(mod_id.lower())

    # ========================================
    # CATEGORIES & STATISTICS
    # ========================================

    def get_count(self) -> int:
        """Return total number of mods."""
        return len(self.mods_data)

    def get_count_by_categories(self) -> Counter:
        """
        Return number of mods by category.

        Returns:
            Number of mods by category
        """
        return Counter(
            category for mod in self.mods_data.values() for category in mod.categories
        )

    def get_count_by_languages(self) -> Counter:
        """
        Return number of mods by language.

        Returns:
            Number of mods by language
        """
        return Counter(
            language for mod in self.mods_data.values() for language in mod.languages
        )

    def get_count_by_games(self) -> Counter:
        """
        Return number of mods by game.

        Returns:
            Number of mods by game
        """
        return Counter(game for mod in self.mods_data.values() for game in mod.games)

    # ========================================
    # ADD CUSTOM MOD
    # ========================================

    def add_mod(self, mod_id: str) -> bool:
        """Add or replace a single mod without full cache rebuild.

        A replaced mod is removed whole first: none of its former components
        or links survives. A failure keeps the catalogue unchanged.
        """
        mod_file = self.custom_mods_dir / f"{mod_id}.json"

        if not mod_file.exists():
            logger.error(f"Mod file not found: {mod_file}")
            return False

        try:
            with open(mod_file, "r", encoding="utf-8") as f:
                mod_data = json.load(f)

            mod_data["id"] = mod_id

            localized_mod_data = CacheBuilderThread._localize_mod(
                mod_data, self.current_language
            )

            added_mods: dict[str, Mod] = {}
            mod_indexes = ReferenceIndexes()
            self._register_components(localized_mod_data, added_mods, mod_indexes)

        except Exception as e:
            logger.error(f"Error adding mod {mod_id}: {e}", exc_info=True)
            return False

        indexes = IndexManager.get_indexes()
        indexes.remove_mod(mod_id)
        indexes.add_catalogue(mod_indexes)
        self.mods_data.update(added_mods)
        self.revision += 1

        logger.info(f"Successfully added mod: {mod_id}")
        return True
