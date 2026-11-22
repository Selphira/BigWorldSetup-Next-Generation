"""
Localized caches: one JSON file per supported language, built in a background thread.

Shared by the mod and rule managers: they only describe their source files, how
sources are loaded and how one entry is localized.

A cache is fresh when its fingerprint, the SHA-256 of the cache format and of
the paths and contents of its sources, matches the current sources: an added,
removed or replaced file is detected whatever its modification date.
"""

from collections.abc import Callable
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal

from constants import LOCALIZED_CACHE_FINGERPRINT_SUFFIX, LOCALIZED_CACHE_FORMAT_VERSION
from core.atomic_io import write_json_atomic
from core.TranslationManager import SUPPORTED_LANGUAGES, tr

logger = logging.getLogger(__name__)

# Entries to localize, grouped by the key they are written under in the cache
CacheGroups = dict[str, list[dict[str, Any]]]


def localized_cache_path(cache_dir: Path, prefix: str, language: str) -> Path:
    """Return the cache file of a language."""
    return cache_dir / f"{prefix}_{language}.json"


def localized_cache_fingerprint_path(cache_dir: Path, prefix: str) -> Path:
    """Return the file holding the fingerprint of the sources of a cache."""
    return cache_dir / f"{prefix}{LOCALIZED_CACHE_FINGERPRINT_SUFFIX}"


def source_fingerprint(source_files: list[Path]) -> str:
    """Return the SHA-256 of the cache format and of the paths and contents of the sources.

    Raises:
        OSError: If a source file cannot be read
    """
    digest = hashlib.sha256(f"format:{LOCALIZED_CACHE_FORMAT_VERSION}\0".encode())

    for source_file in sorted(source_files):
        digest.update(source_file.as_posix().encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(source_file.read_bytes()).digest())

    return digest.hexdigest()


def first_translation(
    translations: dict[str, Any], language: str, read: Callable[[Any], str]
) -> str:
    """
    Return the first non-empty text, trying the target language before the others.

    Args:
        translations: Translations indexed by language code
        language: Target language code
        read: Extracts the wanted text from the translation of one language

    Returns:
        Translated text, or an empty string when no language provides it
    """
    fallback_order = [language] + [code for code in translations if code != language]

    for code in fallback_order:
        if code in translations:
            text = read(translations[code])
            if text:
                return text

    return ""


class LocalizedCacheBuilder(QThread):
    """
    Thread writing one localized cache file per language.

    Subclasses load their sources and localize one entry; this class drives the
    languages, the stop requests, the progress and the cache writes.
    """

    progress = Signal(int)  # Progress 0-100
    status_changed = Signal(str)  # Status message
    completed = Signal(bool)  # True if success, False if error
    error = Signal(str)  # Error message

    cache_prefix: str
    cache_label: str
    # Progress reached once sources are loaded; localization fills the rest
    localization_progress_start: int

    def __init__(self, cache_dir: Path, languages: list[str]) -> None:
        """
        Initialize cache builder thread.

        Args:
            cache_dir: Directory for cache output
            languages: Languages to build cache for
        """
        super().__init__()
        self.cache_dir = cache_dir
        self.languages = languages
        self._should_stop = False
        self._total_steps = 1
        self._current_step = 0

    def run(self) -> None:
        """Build cache for all languages."""
        try:
            groups = self._load_sources()
            if groups is None:
                self.completed.emit(False)
                return

            self.progress.emit(self.localization_progress_start)
            self._total_steps = max(len(self.languages) * self._count_entries(groups), 1)
            self._current_step = 0

            for language in self.languages:
                if not self._write_language_cache(groups, language):
                    self.completed.emit(False)
                    return

            self.status_changed.emit(tr("app.cache_generated_successfully"))
            self.progress.emit(100)
            self.completed.emit(True)

        except Exception as e:
            logger.exception(f"Error during {self.cache_label} generation: {e}")
            self.error.emit(f"Error during {self.cache_label} generation: {e}")
            self.completed.emit(False)

    def stop(self) -> None:
        """Request thread to stop gracefully."""
        self._should_stop = True

    # ========================================
    # STEPS PROVIDED BY SUBCLASSES
    # ========================================

    def _load_sources(self) -> CacheGroups | None:
        """Load entries to localize; None aborts the build (after emitting any error)."""
        raise NotImplementedError

    def _localize_entry(self, entry: dict[str, Any], language: str) -> dict[str, Any]:
        """Return the entry localized for a language."""
        raise NotImplementedError

    def _cache_content(self, localized_groups: CacheGroups) -> object:
        """Return the JSON value written to a language cache file."""
        return localized_groups

    # ========================================
    # LOCALIZATION
    # ========================================

    def _write_language_cache(self, groups: CacheGroups, language: str) -> bool:
        """Localize all entries for a language and write its cache; False if stopped."""
        if self._should_stop:
            return False

        self.status_changed.emit(tr("app.generating_cache_for_lang", lang=language))

        localized_groups: CacheGroups = {}
        for group, entries in groups.items():
            localized_entries = []

            for entry in entries:
                if self._should_stop:
                    return False

                localized_entries.append(self._localize_entry(entry, language))
                self._advance_progress()

            localized_groups[group] = localized_entries

        cache_path = localized_cache_path(self.cache_dir, self.cache_prefix, language)
        write_json_atomic(cache_path, self._cache_content(localized_groups))
        logger.info(f"{self.cache_label.capitalize()} generated for {language}: {cache_path}")

        return True

    def _advance_progress(self) -> None:
        """Emit progress after one localized entry."""
        self._current_step += 1
        start = self.localization_progress_start
        self.progress.emit(start + int(self._current_step / self._total_steps * (100 - start)))

    @staticmethod
    def _count_entries(groups: CacheGroups) -> int:
        return sum(len(entries) for entries in groups.values())


class LocalizedCacheManager(QObject):
    """
    Manager whose data is read from a localized cache built asynchronously.

    Subclasses create their builder thread, tell whether the cache is stale and
    load the cache of the current language.
    """

    cache_ready = Signal(bool)  # Emitted when cache is ready
    cache_building = Signal()  # Emitted when cache building starts
    cache_error = Signal(str)  # Emitted on error

    cache_prefix: str
    cache_label: str

    def __init__(self, cache_dir: Path) -> None:
        """Initialize manager with the cache directory."""
        super().__init__()
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.builder_thread: LocalizedCacheBuilder | None = None
        # Fingerprint of the sources taken before the running build reads them:
        # a source changed during the build makes the cache stale afterwards
        self._building_fingerprint: str | None = None

    # ========================================
    # CACHE MANAGEMENT
    # ========================================

    def build_cache_async(self) -> bool:
        """Start asynchronous cache build."""
        if self.builder_thread is not None and self.builder_thread.isRunning():
            logger.warning(f"{self.cache_label.capitalize()} build already in progress")
            return False

        try:
            self._building_fingerprint = source_fingerprint(self._source_files())
        except OSError as e:
            logger.error(f"Error reading {self.cache_label} sources: {e}")
            self._building_fingerprint = None

        # Caches being rewritten match no sources until the build completes
        self._fingerprint_file().unlink(missing_ok=True)
        self.cache_building.emit()

        self.builder_thread = self._create_cache_builder(
            [code for code, _ in SUPPORTED_LANGUAGES]
        )
        self.builder_thread.completed.connect(self._on_cache_build_finished)
        self.builder_thread.error.connect(self._on_cache_build_error)
        self.builder_thread.start()

        logger.info(f"{self.cache_label.capitalize()} build thread started")
        return True

    def needs_cache_rebuild(self) -> bool:
        """Check if cache needs to be rebuilt."""
        return self._is_cache_stale()

    def load_cache(self) -> bool:
        """Load cache for current language."""
        raise NotImplementedError

    def _create_cache_builder(self, languages: list[str]) -> LocalizedCacheBuilder:
        """Create the thread building the cache for the given languages."""
        raise NotImplementedError

    def _source_files(self) -> list[Path]:
        """Return the files the cache builder reads."""
        raise NotImplementedError

    # ========================================
    # FRESHNESS
    # ========================================

    def _is_cache_stale(self) -> bool:
        """Tell whether a language cache is missing or was built from other sources."""
        for cache_file in self._cache_files():
            if not cache_file.exists():
                logger.info(f"Cache missing: {cache_file}")
                return True

        stored_fingerprint = self._read_stored_fingerprint()
        if stored_fingerprint is None:
            logger.info(f"{self.cache_label.capitalize()} has no source fingerprint")
            return True

        try:
            current_fingerprint = source_fingerprint(self._source_files())
        except OSError as e:
            logger.error(f"Error reading {self.cache_label} sources: {e}")
            return True

        if current_fingerprint != stored_fingerprint:
            logger.info(f"{self.cache_label.capitalize()} sources changed")
            return True

        return False

    def _fingerprint_file(self) -> Path:
        return localized_cache_fingerprint_path(self.cache_dir, self.cache_prefix)

    def _read_stored_fingerprint(self) -> str | None:
        """Return the fingerprint of the last complete build, None if unknown."""
        try:
            stored = json.loads(self._fingerprint_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

        fingerprint = stored.get("sources") if isinstance(stored, dict) else None
        return fingerprint if isinstance(fingerprint, str) else None

    def _cache_file(self, language: str) -> Path:
        """Return the cache file of a language."""
        return localized_cache_path(self.cache_dir, self.cache_prefix, language)

    def _cache_files(self) -> list[Path]:
        """Return the cache files of all supported languages."""
        return [self._cache_file(code) for code, _ in SUPPORTED_LANGUAGES]

    def _on_cache_build_finished(self, success: bool) -> None:
        """Called when cache building finishes."""
        if success and self._building_fingerprint is not None:
            write_json_atomic(self._fingerprint_file(), {"sources": self._building_fingerprint})

        if not success:
            error_msg = f"{self.cache_label.capitalize()} build failed"
            logger.error(error_msg)
            self.cache_error.emit(error_msg)
        elif self.load_cache():
            logger.info(f"{self.cache_label.capitalize()} loaded successfully after build")
            self.cache_ready.emit(True)
        else:
            error_msg = f"Error loading {self.cache_label} after build"
            logger.error(error_msg)
            self.cache_error.emit(error_msg)

        self.builder_thread = None

    def _on_cache_build_error(self, error_message: str) -> None:
        """Called on cache build error."""
        logger.error(f"{self.cache_label.capitalize()} build error: {error_message}")
        self.cache_error.emit(error_message)
        self.builder_thread = None
