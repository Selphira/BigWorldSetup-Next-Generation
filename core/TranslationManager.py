"""Translation management system with fallback support and caching."""

from functools import lru_cache
import json
import logging
from pathlib import Path
import threading
from typing import Any

from PySide6.QtCore import QLibraryInfo, QObject, QTranslator, Signal
from PySide6.QtWidgets import QApplication

from constants import LOCALES_DIR

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

    LOCALES_DIR: Path = LOCALES_DIR
    DEFAULT_LANGUAGE = "fr_FR"
    FALLBACK_CHAIN = ("en_US", "fr_FR")
    CACHE_SIZE = 1024  # Max cached translations

    def __init__(self, app: QApplication | None) -> None:
        """Initialize translation manager."""
        super().__init__()
        self._app = app
        self._qt_translator = QTranslator()
        self._app_translator = QTranslator()
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

    def _load_language(self, code: str, filepath: Path | None = None) -> bool:
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
            with filepath.open("r", encoding="utf-8") as f, self._lock:
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

            self._set_ui_language(code)

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
        text = self._get_cached_translation(self.current_language, key)

        if text is None:
            logger.warning(f"Missing translation: {key}")
            return key

        return self._format_translation(text, key, kwargs)

    @lru_cache(maxsize=CACHE_SIZE)
    def _get_cached_translation(self, language: str, key: str) -> str | None:
        """
        Internal cached translation resolver.

        Args:
            language: Language key
            key: Translation key

        Returns:
            Translated text or None
        """
        # Try primary language
        text = self._get_from_language(language, key)
        if text is not None:
            return text

        # Try fallback languages
        for fallback_lang in self.FALLBACK_CHAIN:
            if fallback_lang == language:
                continue

            text = self._get_from_language(fallback_lang, key)
            if text is not None:
                logger.debug(f"Using fallback {fallback_lang} for key: {key}")
                return text

        return None

    def _get_from_language(self, code: str, key: str) -> str | None:
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
            for key_part in key.split("."):
                if not isinstance(value, dict) or key_part not in value:
                    return None
                value = value[key_part]

            return value if isinstance(value, str) else None

    @staticmethod
    def _format_translation(text: str, key: str, kwargs: dict[str, Any]) -> str:
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

    def _set_ui_language(self, code: str) -> None:
        self._app.removeTranslator(self._qt_translator)
        self._app.removeTranslator(self._app_translator)

        self._qt_translator.load(
            f"qt_{code}", QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
        )
        self._app_translator.load(f"app_{code}", "translations")

        self._app.installTranslator(self._qt_translator)
        self._app.installTranslator(self._app_translator)


# Singleton pattern (thread-safe)
_translator_instance: TranslationManager | None = None
_translator_lock = threading.Lock()


def get_translator(app: QApplication | None = None) -> TranslationManager:
    """Get or create singleton TranslationManager instance (thread-safe)."""
    global _translator_instance

    if _translator_instance is None:
        with _translator_lock:
            # Double-check locking pattern
            if _translator_instance is None:
                _translator_instance = TranslationManager(app)

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
