"""
Localized caches: one JSON file per supported language, built in a background thread.

Shared by the mod and rule managers: they only describe their source files, how
sources are loaded and how one entry is localized.

A cache is fresh when its fingerprint, the SHA-256 of the cache format and of
the paths and contents of its sources, matches the current sources: an added,
removed or replaced file is detected whatever its modification date.
"""

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal

from constants import LOCALIZED_CACHE_FINGERPRINT_SUFFIX, LOCALIZED_CACHE_FORMAT_VERSION
from core.atomic_io import write_json_atomic
from core.ProgressEmitter import ProgressEmitter
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


def fallback_translations(translations: dict[str, Any], language: str) -> list[Any]:
    """
    Return the translations in fallback order: target language first, then the others.

    Args:
        translations: Translations indexed by language code
        language: Target language code

    Returns:
        Translations of the languages present, in the order they are tried
    """
    ordered = [translations[language]] if language in translations else []
    ordered.extend(text for code, text in translations.items() if code != language)
    return ordered


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
    for translation in fallback_translations(translations, language):
        text = read(translation)
        if text:
            return text

    return ""


class CacheBuildError(Exception):
    """Expected end of a cache build without a usable cache: no valid source, or stopped."""


@dataclass(frozen=True)
class CacheBuildResult:
    """Outcome of a successful cache build.

    Attributes:
        diagnostics: Sources skipped because they could not be read, one message each
    """

    diagnostics: tuple[str, ...] = ()


class LocalizedCacheBuilder(QThread):
    """
    Thread writing one localized cache file per language, then the sources fingerprint.

    Subclasses load their sources and localize one entry; this class drives the
    languages, the stop requests, the progress and the cache writes.

    A build ends with exactly one of `build_succeeded` and `build_failed`, sent
    as the last action of the thread.
    """

    progress = Signal(int)  # Progress 0-100
    status_changed = Signal(str)  # Status message
    build_succeeded = Signal(object)  # CacheBuildResult
    build_failed = Signal(str)  # Error message

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
        # Written once every language cache is; None leaves the caches stale
        self.sources_fingerprint: str | None = None
        self._diagnostics: list[str] = []
        self._should_stop = False
        self._progress = ProgressEmitter(self.progress.emit)
        self._total_steps = 1
        self._current_step = 0

    def run(self) -> None:
        """Build cache for all languages and publish the single outcome of the build."""
        try:
            result = self._build()
        except CacheBuildError as e:
            logger.error(f"{self.cache_label.capitalize()} not generated: {e}")
            self.build_failed.emit(str(e))
            return
        except Exception as e:
            logger.exception(f"Error during {self.cache_label} generation: {e}")
            self.build_failed.emit(f"Error during {self.cache_label} generation: {e}")
            return

        self.build_succeeded.emit(result)

    def stop(self) -> None:
        """Request thread to stop gracefully."""
        self._should_stop = True

    def _build(self) -> CacheBuildResult:
        """Write every language cache, then the fingerprint that makes them fresh."""
        self._diagnostics = []
        self._progress.restart()
        groups = self._load_sources()
        self._raise_if_stopped()

        self._progress.force(self.localization_progress_start)
        self._total_steps = max(len(self.languages) * self._count_entries(groups), 1)
        self._current_step = 0

        for language in self.languages:
            self._write_language_cache(groups, language)

        self._write_fingerprint()

        self.status_changed.emit(tr("app.cache_generated_successfully"))
        self._progress.force(100)
        return CacheBuildResult(tuple(self._diagnostics))

    # ========================================
    # STEPS PROVIDED BY SUBCLASSES
    # ========================================

    def _load_sources(self) -> CacheGroups:
        """
        Load entries to localize, reporting each skipped source with `_report_skipped_source`.

        Raises:
            CacheBuildError: If no usable source is left
        """
        raise NotImplementedError

    def _localize_entry(self, entry: dict[str, Any], language: str) -> dict[str, Any]:
        """Return the entry localized for a language."""
        raise NotImplementedError

    def _cache_content(self, localized_groups: CacheGroups) -> object:
        """Return the JSON value written to a language cache file."""
        return localized_groups

    def _report_skipped_source(self, message: str) -> None:
        """Record a source left out of the cache; the build goes on without it."""
        self._diagnostics.append(message)

    def _raise_if_stopped(self) -> None:
        if self._should_stop:
            raise CacheBuildError(f"{self.cache_label.capitalize()} build stopped")

    # ========================================
    # LOCALIZATION
    # ========================================

    def _write_language_cache(self, groups: CacheGroups, language: str) -> None:
        """Localize all entries for a language and write its cache."""
        self._raise_if_stopped()
        self.status_changed.emit(tr("app.generating_cache_for_lang", lang=language))

        localized_groups: CacheGroups = {}
        for group, entries in groups.items():
            localized_entries = []

            for entry in entries:
                self._raise_if_stopped()
                localized_entries.append(self._localize_entry(entry, language))
                self._advance_progress()

            localized_groups[group] = localized_entries

        cache_path = localized_cache_path(self.cache_dir, self.cache_prefix, language)
        write_json_atomic(cache_path, self._cache_content(localized_groups))
        logger.info(f"{self.cache_label.capitalize()} generated for {language}: {cache_path}")

    def _write_fingerprint(self) -> None:
        """Mark the written caches as built from the fingerprinted sources."""
        if self.sources_fingerprint is None:
            return

        fingerprint_path = localized_cache_fingerprint_path(self.cache_dir, self.cache_prefix)
        write_json_atomic(fingerprint_path, {"sources": self.sources_fingerprint})

    def _advance_progress(self) -> None:
        """Report progress after one localized entry."""
        self._current_step += 1
        start = self.localization_progress_start
        self._progress.report(
            start + int(self._current_step / self._total_steps * (100 - start))
        )

    @staticmethod
    def _count_entries(groups: CacheGroups) -> int:
        return sum(len(entries) for entries in groups.values())


class LocalizedCacheManager(QObject):
    """
    Manager whose data is read from a localized cache built asynchronously.

    Subclasses create their builder thread, tell whether the cache is stale and
    load the cache of the current language.

    Signals:
        cache_ready: The cache is loaded
        cache_building: A cache build starts
        cache_error: Error message; the cache could not be built or loaded
        build_progress: Progress 0-100 of the running build
        build_status: Status message of the running build
    """

    cache_ready = Signal(bool)
    cache_building = Signal()
    cache_error = Signal(str)
    build_progress = Signal(int)
    build_status = Signal(str)

    cache_prefix: str
    cache_label: str

    def __init__(self, cache_dir: Path) -> None:
        """Initialize manager with the cache directory."""
        super().__init__()
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Kept until the thread has ended, so that it is never destroyed while running
        self.builder_thread: LocalizedCacheBuilder | None = None

    # ========================================
    # CACHE MANAGEMENT
    # ========================================

    def prepare_cache(self) -> None:
        """
        Load the cache, building it first when stale or unreadable.

        Always ends with `cache_ready` or `cache_error`, possibly before returning.
        """
        if not self.needs_cache_rebuild():
            if self.load_cache():
                logger.info(f"{self.cache_label.capitalize()} loaded from existing cache")
                self.cache_ready.emit(True)
                return

            logger.warning(f"{self.cache_label.capitalize()} unreadable, rebuilding it")

        try:
            started = self.build_cache_async()
        except Exception as e:
            logger.exception(f"Unable to start {self.cache_label} build")
            self.cache_error.emit(str(e))
            return

        if not started:
            self.cache_error.emit(f"{self.cache_label.capitalize()} build not started")

    def build_cache_async(self) -> bool:
        """
        Start asynchronous cache build.

        Returns:
            False if a build is already running; True once the build has started, which
            then ends with exactly one `cache_ready` or `cache_error`
        """
        if self.builder_thread is not None and self.builder_thread.isRunning():
            logger.warning(f"{self.cache_label.capitalize()} build already in progress")
            return False

        # Taken before the build reads the sources: a source changed during the
        # build makes the cache stale afterwards
        try:
            fingerprint = source_fingerprint(self._source_files())
        except OSError as e:
            logger.error(f"Error reading {self.cache_label} sources: {e}")
            fingerprint = None

        # Caches being rewritten match no sources until the build completes
        self._fingerprint_file().unlink(missing_ok=True)
        self.cache_building.emit()

        builder = self._create_cache_builder([code for code, _ in SUPPORTED_LANGUAGES])
        builder.sources_fingerprint = fingerprint
        builder.build_succeeded.connect(self._on_cache_build_succeeded)
        builder.build_failed.connect(self._on_cache_build_failed)
        builder.progress.connect(self.build_progress)
        builder.status_changed.connect(self.build_status)
        builder.finished.connect(partial(self._on_builder_thread_finished, builder))

        self.builder_thread = builder
        builder.start()

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

    # ========================================
    # BUILD OUTCOME
    # ========================================

    def _on_cache_build_succeeded(self, result: CacheBuildResult) -> None:
        """Load the built cache; the build may have skipped unreadable sources."""
        self._end_build_run()

        for diagnostic in result.diagnostics:
            logger.warning(f"{self.cache_label.capitalize()} source skipped: {diagnostic}")

        if self.load_cache():
            logger.info(f"{self.cache_label.capitalize()} loaded successfully after build")
            self.cache_ready.emit(True)
            return

        error_msg = f"Error loading {self.cache_label} after build"
        logger.error(error_msg)
        self.cache_error.emit(error_msg)

    def _on_cache_build_failed(self, error_message: str) -> None:
        self._end_build_run()

        logger.error(f"{self.cache_label.capitalize()} build error: {error_message}")
        self.cache_error.emit(error_message)

    def _end_build_run(self) -> None:
        """Wait for the builder to return from `run()`, which ends right after its outcome.

        A slot of `cache_ready` or `cache_error` may then start the next build at once.
        """
        if self.builder_thread is not None:
            self.builder_thread.wait()

    def _on_builder_thread_finished(self, builder: LocalizedCacheBuilder) -> None:
        """Release an ended builder, unless a newer build already replaced it."""
        # The connection holds the builder: dropping it lets the builder be freed
        builder.finished.disconnect()
        if self.builder_thread is builder:
            self.builder_thread = None
