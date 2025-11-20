"""Translation management system with fallback support and caching."""

import json
import logging
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from PySide6.QtCore import QObject, Signal

logger = logging.getLogger(__name__)

# Constants
SUPPORTED_LANGUAGES: tuple[tuple[str, str], ...] = (
    ("de_DE", "Deutsch"),
    ("en_US", "English"),
    ("es_ES", "Español"),
    ("fr_FR", "Français"),
    ("it_IT", "Italiano"),
    ("ko_KR", "한국어"),
    ("pl_PL", "Polski"),
    ("ru_RU", "Русский"),
    ("zh_CN", "中文"),
)


def get_supported_languages() -> list[tuple[str, str]]:
    """Return list of all supported language codes and names."""
    return list(SUPPORTED_LANGUAGES)


def get_supported_language_codes() -> list[str]:
    """Return list of all supported language codes."""
    return [code for code, _ in SUPPORTED_LANGUAGES]


class TranslationManager(QObject):
    """
    Manages application translations with automatic fallback support.

    Features:
    - Lazy loading of translation files
    - LRU cache for resolved translations
    - Thread-safe operations
    - Automatic fallback chain
    """

    language_changed = Signal(str)

    LOCALES_DIR = Path("i18n")
    DEFAULT_LANGUAGE = "fr_FR"
    FALLBACK_CHAIN = ("en_US", "fr_FR")
    CACHE_SIZE = 1024  # Max cached translations

    def __init__(self) -> None:
        """Initialize translation manager."""
        super().__init__()
        self._current_language = self.DEFAULT_LANGUAGE
        self._translations: dict[str, dict[str, Any]] = {}
        self._language_names = dict(SUPPORTED_LANGUAGES)
        self._lock = threading.RLock()

        self._load_all_languages()

    def _load_all_languages(self) -> None:
        """Load all available translation files from locales directory."""
        if not self.LOCALES_DIR.exists():
            logger.warning(f"Locales directory not found: {self.LOCALES_DIR}")
            return

        for filepath in self.LOCALES_DIR.glob("*.json"):
            code = filepath.stem
            self._load_language(code, filepath)

    def _load_language(
            self,
            code: str,
            filepath: Optional[Path] = None
    ) -> bool:
        """
        Load a specific language translation file.

        Args:
            code: Language code (e.g., 'en_US')
            filepath: Optional path to translation file

        Returns:
            True if loaded successfully, False otherwise
        """
        if filepath is None:
            filepath = self.LOCALES_DIR / f"{code}.json"

        if not filepath.exists():
            logger.error(f"Translation file not found: {filepath}")
            return False

        try:
            with filepath.open('r', encoding='utf-8') as f:
                with self._lock:
                    self._translations[code] = json.load(f)

            logger.info(f"Loaded language: {code}")
            return True

        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in {code}: {e}")
            return False
        except Exception as e:
            logger.exception(f"Error loading {code}: {e}")
            return False

    @property
    def current_language(self) -> str:
        """Get current language code (thread-safe)."""
        with self._lock:
            return self._current_language

    def get_available_languages(self) -> list[str]:
        """Return list of loaded language codes."""
        with self._lock:
            return list(self._translations.keys())

    def get_language_name(self, code: str) -> str:
        """
        Get display name for a language code.

        Args:
            code: Language code (e.g., 'en_US')

        Returns:
            Display name or code itself if not found
        """
        return self._language_names.get(code, code)

    def set_language(self, code: str) -> bool:
        """
        Set current language.

        Args:
            code: Language code to set

        Returns:
            True if language was changed, False otherwise
        """
        with self._lock:
            if code not in self._translations:
                logger.warning(f"Language not available: {code}")
                return False

            if self._current_language != code:
                old_language = self._current_language
                self._current_language = code

                # Clear cache on language change
                self._clear_translation_cache()

                self.language_changed.emit(code)
                logger.info(f"Language changed from {old_language} to {code}")
                return True

        return False

    def get(self, key: str, **kwargs) -> str:
        """
        Get translated text for a key with optional formatting.

        Args:
            key: Translation key (supports dot notation for nested keys)
            **kwargs: Variables for string formatting

        Returns:
            Translated and formatted text
        """
        # Use cached resolution for better performance
        text = self._resolve_translation_cached(key)

        if text is None:
            logger.warning(f"Missing translation: {key}")
            return key

        return self._format_translation(text, key, kwargs)

    def _resolve_translation_cached(self, key: str) -> Optional[str]:
        """
        Resolve translation with LRU caching.

        Args:
            key: Translation key

        Returns:
            Translated text or None
        """
        # Cache key includes current language for cache invalidation
        cache_key = f"{self.current_language}:{key}"
        return self._get_cached_translation(cache_key, key)

    @lru_cache(maxsize=CACHE_SIZE)
    def _get_cached_translation(self, cache_key: str, key: str) -> Optional[str]:
        """
        Internal cached translation resolver.

        Args:
            cache_key: Cache key (language:key)
            key: Translation key

        Returns:
            Translated text or None
        """
        # Try current language
        text = self._get_from_language(self.current_language, key)
        if text is not None:
            return text

        # Try fallback languages
        for fallback_lang in self.FALLBACK_CHAIN:
            if fallback_lang == self.current_language:
                continue

            text = self._get_from_language(fallback_lang, key)
            if text is not None:
                logger.debug(f"Using fallback {fallback_lang} for key: {key}")
                return text

        return None

    def _get_from_language(self, code: str, key: str) -> Optional[str]:
        """
        Get translation from a specific language.

        Args:
            code: Language code
            key: Translation key (supports dot notation)

        Returns:
            Translated text or None
        """
        with self._lock:
            if code not in self._translations:
                return None

            # Navigate nested dictionary structure
            value: Any = self._translations[code]
            for key_part in key.split('.'):
                if not isinstance(value, dict) or key_part not in value:
                    return None
                value = value[key_part]

            return value if isinstance(value, str) else None

    @staticmethod
    def _format_translation(
            text: str,
            key: str,
            kwargs: dict[str, Any]
    ) -> str:
        """
        Format translation text with provided variables.

        Args:
            text: Translation text to format
            key: Original key (for error reporting)
            kwargs: Formatting variables

        Returns:
            Formatted text
        """
        if not kwargs:
            return text

        try:
            return text.format(**kwargs)
        except KeyError as e:
            logger.warning(f"Missing variable in '{key}': {e}")
            return text
        except Exception as e:
            logger.error(f"Error formatting '{key}': {e}")
            return text

    def _clear_translation_cache(self) -> None:
        """Clear the LRU cache for translations."""
        self._get_cached_translation.cache_clear()
        logger.debug("Translation cache cleared")

    def get_cache_info(self) -> dict[str, Any]:
        """
        Get information about translation cache.

        Returns:
            Dictionary with cache statistics
        """
        cache_info = self._get_cached_translation.cache_info()
        return {
            "hits": cache_info.hits,
            "misses": cache_info.misses,
            "size": cache_info.currsize,
            "maxsize": cache_info.maxsize,
        }


# Singleton pattern (thread-safe)
_translator_instance: Optional[TranslationManager] = None
_translator_lock = threading.Lock()


def get_translator() -> TranslationManager:
    """Get or create singleton TranslationManager instance (thread-safe)."""
    global _translator_instance

    if _translator_instance is None:
        with _translator_lock:
            # Double-check locking pattern
            if _translator_instance is None:
                _translator_instance = TranslationManager()

    return _translator_instance


def tr(key: str, **kwargs) -> str:
    """
    Convenience function for getting translations.

    Args:
        key: Translation key
        **kwargs: Variables for string formatting

    Returns:
        Translated and formatted text
    """
    return get_translator().get(key, **kwargs)
