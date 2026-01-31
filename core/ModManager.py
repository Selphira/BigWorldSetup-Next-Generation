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

from PySide6.QtCore import QObject, QThread, Signal

from constants import CACHE_DIR, CUSTOM_MODS_DIR, MODS_DIR
from core.ComponentReference import ComponentReference, IndexManager
from core.Mod import Mod, MucComponent, SubComponent
from core.TranslationManager import SUPPORTED_LANGUAGES, get_translator, tr

logger = logging.getLogger(__name__)


class CacheBuilderThread(QThread):
    """Thread for building cache without blocking UI."""

    progress = Signal(int)  # Progress 0-100
    status_changed = Signal(str)  # Status message
    finished = Signal(bool)  # True if success, False if error
    error = Signal(str)  # Error message

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
        super().__init__()
        self.mods_dir = mods_dir
        self.custom_mods_dir = custom_mods_dir
        self.cache_dir = cache_dir
        self.languages = languages
        self._should_stop = False

    def run(self) -> None:
        """Build cache for all languages."""
        try:
            self.status_changed.emit(tr("app.searching_mod_files"))

            # Find all JSON files
            official_files = (
                list(self.mods_dir.glob("*.json")) if self.mods_dir.exists() else []
            )
            custom_files = (
                list(self.custom_mods_dir.glob("*.json"))
                if self.custom_mods_dir.exists()
                else []
            )

            source_files = self._merge_mod_files(official_files, custom_files)

            if not source_files:
                self.error.emit(
                    f"No JSON files found in {self.mods_dir} or {self.custom_mods_dir}"
                )
                self.finished.emit(False)
                return

            self.status_changed.emit(tr("app.loading_count_mods", count=len(source_files)))

            # Load all mods
            mods_data = []
            for i, (file_path, is_custom) in enumerate(source_files):
                if self._should_stop:
                    self.finished.emit(False)
                    return

                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        mods_data.append(
                            {"id": file_path.stem, "custom": is_custom, **json.load(f)}
                        )
                except Exception as e:
                    logger.error(f"Error loading {file_path.name}: {e}")
                    self.error.emit(f"Error loading {file_path.name}: {e}")
                    continue

                # Loading progress (0-20%)
                self.progress.emit(int((i + 1) / len(source_files) * 20))

            if not mods_data:
                self.error.emit("No valid mods found")
                self.finished.emit(False)
                return

            # Generate cache for each language
            total_steps = len(self.languages) * len(mods_data)
            current_step = 0

            for lang in self.languages:
                if self._should_stop:
                    self.finished.emit(False)
                    return

                self.status_changed.emit(tr("app.generating_cache_for_lang", lang=lang))

                localized_data = []
                for mod in mods_data:
                    if self._should_stop:
                        self.finished.emit(False)
                        return

                    localized_mod = self._localize_mod(mod, lang)
                    localized_data.append(localized_mod)

                    current_step += 1
                    # Progress 20-100%
                    progress_value = 20 + int((current_step / total_steps) * 80)
                    self.progress.emit(progress_value)

                # Save cache
                cache_path = self.cache_dir / f"mods_{lang}.json"
                with open(cache_path, "w", encoding="utf-8") as f:
                    json.dump(localized_data, f, indent=2, ensure_ascii=False)

                logger.info(f"Cache generated for {lang}: {cache_path}")

            self.status_changed.emit(tr("app.cache_generated_successfully"))
            self.progress.emit(100)
            self.finished.emit(True)

        except Exception as e:
            logger.exception(f"Error during cache generation: {e}")
            self.error.emit(f"Error during cache generation: {e}")
            self.finished.emit(False)

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

    def stop(self) -> None:
        """Request thread to stop gracefully."""
        self._should_stop = True

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

        # Fallback order: target language first, then others
        fallback_order = [target_lang] + [
            lang for lang in translations_all.keys() if lang != target_lang
        ]

        # Resolve mod description with fallback
        description = ""
        for lang in fallback_order:
            if lang in translations_all:
                desc = translations_all[lang].get("description", "")
                if desc:
                    description = desc
                    break

        # Localized components
        localized_components = {}

        def resolve_component_text(key: str) -> str:
            """Find best translation for a component key."""
            for lang in fallback_order:
                trans = translations_all.get(lang, {}).get("components", {})
                if key in trans and trans[key]:
                    return trans[key]
            return ""

        # Process all components
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

        # Final structure
        result["translations"] = {
            "description": description,
            "components": localized_components,
        }

        return result


class ModManager(QObject):
    """
    Centralized mod manager.

    Responsibilities:
    - Loading mods from cache
    - Managing multilingual cache
    - Providing access to localized data
    - Filtering and searching mods
    """

    # Signals
    cache_ready = Signal(bool)  # Emitted when cache is ready
    cache_building = Signal()  # Emitted when cache building starts
    cache_error = Signal(str)  # Emitted on error

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
        super().__init__()
        self.mods_dir = mods_dir
        self.custom_mods_dir = custom_mods_dir
        self.cache_dir = cache_dir

        self.current_language = get_translator().current_language
        self.mods_data: dict[str, Mod] = {}
        self._category_count_cache: Counter | None = None

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.custom_mods_dir.mkdir(parents=True, exist_ok=True)

        self.builder_thread: CacheBuilderThread | None = None

    # ========================================
    # CACHE MANAGEMENT
    # ========================================

    def needs_cache_rebuild(self) -> bool:
        """Check if cache needs to be rebuilt."""
        return self._should_rebuild_cache()

    def build_cache_async(self) -> bool:
        """Start asynchronous cache build."""
        if self.builder_thread and self.builder_thread.isRunning():
            logger.warning("Cache build already in progress")
            return False

        self.cache_building.emit()

        languages = [code for code, _ in SUPPORTED_LANGUAGES]

        self.builder_thread = CacheBuilderThread(
            self.mods_dir, self.custom_mods_dir, self.cache_dir, languages
        )

        self.builder_thread.finished.connect(self._on_cache_build_finished)
        self.builder_thread.error.connect(self._on_cache_build_error)
        self.builder_thread.start()
        logger.info("Cache build thread started")
        return True

    def _on_cache_build_finished(self, success: bool) -> None:
        """Called when cache building finishes."""
        if success:
            # Load freshly created cache
            if self.load_cache():
                logger.info("Cache loaded successfully after build")
                self.cache_ready.emit(True)
            else:
                error_msg = "Error loading cache after build"
                logger.error(error_msg)
                self.cache_error.emit(error_msg)
        else:
            error_msg = "Cache build failed"
            logger.error(error_msg)
            self.cache_error.emit(error_msg)

        self.builder_thread = None

    def _on_cache_build_error(self, error_message: str) -> None:
        """Called on cache build error."""
        logger.error(f"Cache build error: {error_message}")
        self.cache_error.emit(error_message)
        self.builder_thread = None

    def _should_rebuild_cache(self) -> bool:
        """
        Check if cache should be rebuilt.

        Returns:
            True if rebuild needed
        """
        # Check if all cache files exist
        for lang, _ in SUPPORTED_LANGUAGES:
            cache_file = self.cache_dir / f"mods_{lang}.json"
            if not cache_file.exists():
                logger.info(f"Cache missing for {lang}")
                return True

        # Check if sources are newer than cache
        official_files = list(self.mods_dir.glob("*.json")) if self.mods_dir.exists() else []
        custom_files = (
            list(self.custom_mods_dir.glob("*.json")) if self.custom_mods_dir.exists() else []
        )
        source_files = official_files + custom_files

        if not source_files:
            logger.warning("No source files found")
            return True

        try:
            last_source_mod = max(f.stat().st_mtime for f in source_files)

            # Compare with oldest cache
            oldest_cache = min(
                (self.cache_dir / f"mods_{lang}.json").stat().st_mtime
                for lang, _ in SUPPORTED_LANGUAGES
            )

            if last_source_mod > oldest_cache:
                logger.info("Source files newer than cache")
                return True

        except OSError as e:
            logger.error(f"Error checking file timestamps: {e}")
            return True

        return False

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
        cache_file = self.cache_dir / f"mods_{self.current_language}.json"

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

    def get_stats(self) -> dict[str, Any]:
        """Return statistics about loaded mods."""
        return {
            "total_mods": len(self.mods_data),
            "categories": self.get_count_by_categories(),
            "games": self.get_count_by_games(),
            "languages": self.get_count_by_languages(),
        }

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
