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
    MODS_DIR,
)
from core.ComponentReference import ComponentReference, IndexManager
from core.LocalizedCache import (
    CacheGroups,
    LocalizedCacheBuilder,
    LocalizedCacheManager,
    first_translation,
    is_cache_stale,
)
from core.Mod import Mod, MucComponent, SubComponent
from core.TranslationManager import SUPPORTED_LANGUAGES, get_translator, tr

logger = logging.getLogger(__name__)

# Single cache group: the mod cache file is a plain list of mods
MOD_CACHE_GROUP = "mods"


def list_json_files(directory: Path) -> list[Path]:
    """Return the JSON files of a directory, or nothing if it does not exist."""
    return list(directory.glob("*.json")) if directory.exists() else []


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

    def _load_sources(self) -> CacheGroups | None:
        """Load official and custom mods, reporting loading progress."""
        self.status_changed.emit(tr("app.searching_mod_files"))

        source_files = self._merge_mod_files(
            list_json_files(self.mods_dir), list_json_files(self.custom_mods_dir)
        )

        if not source_files:
            self.error.emit(f"No JSON files found in {self.mods_dir} or {self.custom_mods_dir}")
            return None

        self.status_changed.emit(tr("app.loading_count_mods", count=len(source_files)))

        mods_data = []
        for i, (file_path, is_custom) in enumerate(source_files):
            if self._should_stop:
                return None

            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    mods_data.append({"id": file_path.stem, "custom": is_custom, **json.load(f)})
            except Exception as e:
                logger.error(f"Error loading {file_path.name}: {e}")
                self.error.emit(f"Error loading {file_path.name}: {e}")
                continue

            self.progress.emit(
                int((i + 1) / len(source_files) * self.localization_progress_start)
            )

        if not mods_data:
            self.error.emit("No valid mods found")
            return None

        return {MOD_CACHE_GROUP: mods_data}

    def _localize_entry(self, entry: dict[str, Any], language: str) -> dict[str, Any]:
        return self._localize_mod(entry, language)

    def _cache_content(self, localized_groups: CacheGroups) -> object:
        return localized_groups[MOD_CACHE_GROUP]

    def _merge_mod_files(
        self, official_files: list[Path], custom_files: list[Path]
    ) -> list[tuple[Path, bool]]:
        """Merge official and custom mod files, resolving conflicts by modification time."""

        official_dict = {f.stem: f for f in official_files}
        custom_dict = {f.stem: f for f in custom_files}
        conflicting_ids = set(official_dict.keys()) & set(custom_dict.keys())

        if conflicting_ids:
            logger.info(
                f"Found {len(conflicting_ids)} mod conflicts, resolving by modification time"
            )

        result = []

        for mod_id, custom_path in custom_dict.items():
            if mod_id in conflicting_ids:
                official_path = official_dict[mod_id]
                custom_mtime = custom_path.stat().st_mtime
                official_mtime = official_path.stat().st_mtime

                if custom_mtime >= official_mtime:
                    result.append((custom_path, True))
                    logger.info(
                        f"Conflict resolved: using custom version of '{mod_id}' (newer)"
                    )
                else:
                    result.append((official_path, False))
                    logger.info(
                        f"Conflict resolved: using official version of '{mod_id}' (newer)"
                    )
            else:
                result.append((custom_path, True))

        for mod_id, official_path in official_dict.items():
            if mod_id not in conflicting_ids:
                result.append((official_path, False))

        logger.info(
            f"Merged {len(result)} mods ({len(custom_dict)} custom, {len(official_dict)} official, {len(conflicting_ids)} conflicts)"
        )

        return result

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
        mods_dir: Path = MODS_DIR,
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
        self._category_count_cache: Counter | None = None

        self.custom_mods_dir.mkdir(parents=True, exist_ok=True)

    # ========================================
    # CACHE MANAGEMENT
    # ========================================

    def needs_cache_rebuild(self) -> bool:
        """Check if cache needs to be rebuilt; no mod source at all always requires it."""
        source_files = list_json_files(self.mods_dir) + list_json_files(self.custom_mods_dir)

        if not source_files:
            logger.warning("No source files found")
            return True

        return is_cache_stale(self._cache_files(), source_files)

    def _create_cache_builder(self, languages: list[str]) -> CacheBuilderThread:
        return CacheBuilderThread(self.mods_dir, self.custom_mods_dir, self.cache_dir, languages)

    def _register_components(self, mod_data: dict[str, Any]) -> None:
        mod = Mod(mod_data)
        self.mods_data[mod.id.lower()] = mod

        indexes = IndexManager.get_indexes()
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
        cache_file = self._cache_file(self.current_language)

        if not cache_file.exists():
            logger.error(f"Cache file not found: {cache_file}")
            return False

        try:
            indexes = IndexManager.get_indexes()

            with open(cache_file, "r", encoding="utf-8") as f:
                mods_json = json.load(f)

            self.mods_data.clear()

            for data in mods_json:
                self._register_components(data)

            # Invalidate category cache
            self._category_count_cache = None

            logger.info(f"Loaded {len(self.mods_data)} mods for {self.current_language}")
            logger.info(f"Mod/Component index populated: {len(indexes.mod_component_index)}")
            logger.info(f"Children index populated: {len(indexes.children_index)} ")
            logger.info(f"Parent index populated: {len(indexes.parent_index)} ")
            return True

        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in cache: {e}")
            return False
        except Exception as e:
            logger.exception(f"Error loading cache: {e}")
            return False

    def reload_for_language(self, language: str) -> bool:
        """
        Reload cache for a new language.

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

        self.current_language = language
        return self.load_cache()

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
        """Add a single mod to the manager without full cache rebuild."""
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

            self._register_components(localized_mod_data)

            # Invalidate category cache
            self._category_count_cache = None

            logger.info(f"Successfully added mod: {mod_id}")
            return True

        except Exception as e:
            logger.error(f"Error adding mod {mod_id}: {e}", exc_info=True)
            return False
