"""WeiDU TP2 file parser for Infinity Engine mod installations.

This module provides parsing functionality for WeiDU TP2 (mod metadata) files,
extracting mod information including version, supported languages, components,
and translations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from pathlib import Path
import platform
import re

from core.File import safe_read

# ------------------------------
#         CONSTANTS
# ------------------------------

# Language mapping from WeiDU codes to ISO codes
LANG_MAP: dict[str, str] = {
    "american": "en_US",
    "english": "en_US",
    "en-us": "en_US",
    "francais": "fr_FR",
    "french": "fr_FR",
    "frenchee": "fr_FR",
    "deutsch": "de_DE",
    "dutch": "de_DE",
    "german": "de_DE",
    "italian": "it_IT",
    "polish": "pl_PL",
    "polski": "pl_PL",
    "castellano": "es_ES",
    "castilian": "es_ES",
    "espanol": "es_ES",
    "spanish": "es_ES",
    "russian": "ru_RU",
    "chinese": "zh_CN",
    "chinese(simplified)": "zh_CN",
    "chs": "zh_CN",
    "schinese": "zh_CN",
    "chineset": "zh_TW",
    "tchinese": "zh_TW",
    "korean": "ko_KR",
    "cesky": "cs_CZ",
    "czech": "cs_CZ",
    "brazilianportuguese": "pt_BR",
    "brazilian_portuguese": "pt_BR",
    "ptbr": "pt_BR",
    "portuguese": "pt_PT",
    "faroese": "fo_FO",
    "latin": "la_LA",
    "swedish": "sv_SE",
}

# OS code mapping for WeiDU %WEIDU_OS% variable
OS_CODE_MAP: dict[str, str] = {
    "Windows": "win32",
    "Darwin": "osx",
}
DEFAULT_OS_CODE: str = "unix"

# Regex patterns (compiled once for performance)
RE_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
RE_LINE_COMMENT = re.compile(r"^\s*//.*(?:\n|$)", re.MULTILINE)
RE_VERSION = re.compile(r'VERSION\s+[~"]?v?([0-9][0-9A-Za-z\.\-_]*)[~"]?')
RE_LANGUAGE_BLOCK = re.compile(r"LANGUAGE\s+((?:[~\"].*?[~\"]\s*)+)", re.DOTALL)
RE_QUOTED_STRING = re.compile(r'[~"]([^~"]+)[~"]')
RE_BEGIN_BLOCK = re.compile(r"(^\s*BEGIN\s+.*?)(?=BEGIN|\Z|^\s*$)", re.DOTALL | re.MULTILINE)
RE_BEGIN_REF = re.compile(r"BEGIN\s+@(\d+)")
RE_BEGIN_TEXT = re.compile(r'BEGIN\s+[~"]([^~"]+)[~"]')
RE_DESIGNATED = re.compile(r"DESIGNATED\s+(\d+)")
RE_SUBCOMPONENT_REF = re.compile(r"(?<![\/#])SUBCOMPONENT\s+@(\d+)")
RE_SUBCOMPONENT_TEXT = re.compile(r'SUBCOMPONENT\s+[~"]([^~"]+)[~"]')
RE_TRA_TRANSLATION = re.compile(
    r"""
    @\s*(?P<id>-?\d+)          # @ + identifiant (positif ou négatif)
    \s*=\s*
    (?:
        ~(?P<text_tilde>.*?)~ # Texte entre ~ ~ (multiligne)
      |
        "(?P<text_quote>.*?)" # Texte entre " "
    )
    """,
    re.DOTALL | re.VERBOSE,
)

# WeiDU variable placeholder
WEIDU_OS_VAR: str = "%WEIDU_OS%"
MOD_FOLDER_VAR: str = "%MOD_FOLDER%"

logger = logging.getLogger(__name__)


# ------------------------------
#         EXCEPTIONS
# ------------------------------


class Tp2ParseError(Exception):
    """Base exception for TP2 parsing errors."""

    pass


class Tp2FileNotFoundError(Tp2ParseError):
    """Raised when a TP2 or TRA file is not found."""

    pass


class Tp2InvalidFormatError(Tp2ParseError):
    """Raised when TP2 format is invalid or corrupted."""

    pass


# ------------------------------
#         UTILITIES
# ------------------------------


def normalize_language_code(code: str) -> str:
    """Normalize a language code to ISO format.

    Args:
        code: Raw language code from TP2 file

    Returns:
        Normalized ISO language code (e.g., 'en_US')
    """
    if "/" in code:
        code = code.split("/")[-1]
    code_lower = code.lower().strip()
    normalized = LANG_MAP.get(code_lower, code)
    logger.debug(f"Normalized language code '{code}' -> '{normalized}'")
    return normalized


def get_os_code() -> str:
    """Get the current OS code for WeiDU compatibility.

    Returns:
        OS code string ('win32', 'osx', or 'unix')
    """
    system = platform.system()
    os_code = OS_CODE_MAP.get(system, DEFAULT_OS_CODE)
    logger.debug(f"Detected OS: {system} -> {os_code}")
    return os_code


# ------------------------------
#         DATA STRUCTURES
# ------------------------------


@dataclass
class LanguageDeclaration:
    """Represents a language declaration in a TP2 file.

    Attributes:
        display_name: Human-readable language name
        language_code: Normalized ISO language code
        tra_files: List of translation file paths
        index: Position in language declaration order
    """

    display_name: str
    language_code: str
    tra_files: list[str]
    index: int

    def __post_init__(self) -> None:
        """Normalize language code after initialization."""
        self.language_code = normalize_language_code(self.language_code)


@dataclass
class Component:
    """Represents a mod component.

    Attributes:
        designated: Unique component identifier
        text_ref: Reference to translation string ID
        text: Direct text (if not using translation reference)
    """

    designated: str
    text_ref: str | None = None
    text: str | None = None

    def __post_init__(self) -> None:
        """Validate component data after initialization."""
        if not self.designated:
            raise ValueError("Component designated cannot be empty")


@dataclass
class MucComponent(Component):
    """Represents a mutually exclusive choice component.

    A MucComponent contains multiple sub-components where only one
    can be selected at installation time.

    Attributes:
        components: List of sub-components (choices)
    """

    components: list[Component] = field(default_factory=list)


@dataclass
class WeiDUTp2:
    """Represents a complete parsed TP2 file.

    Attributes:
        version: Mod version string
        languages: Available language declarations
        components: List of mod components
        component_translations: Nested dict {language_code: {designated: translated_text}}
    """

    name: str | None = None
    version: str | None = None
    languages: list[LanguageDeclaration] = field(default_factory=list)
    components: list[Component] = field(default_factory=list)
    translations: dict[str, dict[str, str]] = field(default_factory=dict)
    component_translations: dict[str, dict[str, str]] = field(default_factory=dict)

    def get_translation(self, designated: str, language_code: str) -> str | None:
        """Retrieve translation for a component in a specific language.

        Args:
            designated: Component identifier
            language_code: ISO language code

        Returns:
            Translated text or None if not found
        """
        return self.component_translations.get(language_code, {}).get(designated)

    def get_all_translations_for_language(self, language_code: str) -> dict[str, str]:
        """Retrieve all translations for a given language.

        Args:
            language_code: ISO language code

        Returns:
            Dictionary mapping component designated to translated text
        """
        return self.component_translations.get(language_code, {})

    def get_language_by_code(self, language_code: str) -> LanguageDeclaration | None:
        """Find language declaration by code.

        Args:
            language_code: ISO language code to search for

        Returns:
            LanguageDeclaration if found, None otherwise
        """
        normalized_code = normalize_language_code(language_code)
        for lang in self.languages:
            if lang.language_code == normalized_code:
                return lang
        return None


# ------------------------------
#        MAIN PARSER
# ------------------------------


class WeiDUTp2Parser:
    """Parser for WeiDU TP2 mod metadata files.

    This parser extracts mod information including version, languages,
    components, and translations from TP2 files used by the WeiDU
    installer for Infinity Engine games.

    Attributes:
        base_dir: Base directory containing the TP2 file and related resources
    """

    def __init__(self, base_dir: Path) -> None:
        """Initialize parser with base directory.

        Args:
            base_dir: Directory containing TP2 and related files
        """
        self.base_dir = Path(base_dir).resolve()
        if not self.base_dir.is_dir():
            raise ValueError(f"Base directory does not exist: {self.base_dir}")

        logger.info(f"Initialized TP2 parser with base_dir: {self.base_dir}")

    def parse_file(self, path: str | Path) -> WeiDUTp2:
        """Parse a TP2 file from disk.

        Args:
            path: Path to TP2 file (absolute or relative to base_dir)

        Returns:
            Parsed WeiDUTp2 object

        Raises:
            Tp2FileNotFoundError: If file doesn't exist
            Tp2ParseError: If parsing fails
        """
        file_path = Path(path)
        if not file_path.is_absolute():
            file_path = self.base_dir / file_path

        file_path = file_path.resolve()

        if not file_path.exists():
            raise Tp2FileNotFoundError(f"TP2 file not found: {file_path}")

        logger.info(f"Parsing TP2 file: {file_path}")

        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            return self.parse_string(content, str(path.stem))
        except Exception as e:
            logger.error(f"Failed to parse TP2 file: {file_path}", exc_info=True)
            raise Tp2ParseError(f"Error parsing {file_path}: {e}") from e

    def parse_string(self, tp2_content: str, tp2_name: str) -> WeiDUTp2:
        """Parse TP2 content from a string.

        Args:
            tp2_content: Raw TP2 file content

        Returns:
            Parsed WeiDUTp2 object

        Raises:
            Tp2ParseError: If parsing fails
        """
        logger.debug("Starting TP2 string parsing")

        try:
            # Strip comments first
            clean_content = self._strip_comments(tp2_content)

            # Initialize data structure
            tp2 = WeiDUTp2()
            tp2.name = tp2_name

            # Extract sections
            self._extract_version(clean_content, tp2)
            self._extract_languages(clean_content, tp2)
            self._parse_components(clean_content, tp2)

            # Build translation mappings
            self._build_translations(tp2)

            logger.info(
                f"Successfully parsed TP2: version={tp2.version}, "
                f"languages={len(tp2.languages)}, components={len(tp2.components)}"
            )

            return tp2

        except Exception as e:
            logger.error("Failed to parse TP2 string", exc_info=True)
            raise Tp2ParseError(f"Error parsing TP2 content: {e}") from e

    # ------------------------------------------------------
    # ------------ LOW LEVEL PARSING METHODS --------------
    # ------------------------------------------------------

    def _strip_comments(self, text: str) -> str:
        """Remove C-style and C++-style comments from text.

        Args:
            text: Raw text with comments

        Returns:
            Text with comments removed
        """
        # Remove block comments /* */
        text = RE_BLOCK_COMMENT.sub("", text)
        # Remove line comments //
        text = RE_LINE_COMMENT.sub("", text)
        return text

    def _extract_version(self, text: str, tp2: WeiDUTp2) -> None:
        """Extract VERSION declaration from TP2 content.

        Args:
            text: Clean TP2 content
            tp2: WeiDUTp2 object to populate
        """
        match = RE_VERSION.search(text)
        if match:
            tp2.version = match.group(1)
            logger.debug(f"Extracted version: {tp2.version}")
        else:
            logger.warning("No VERSION declaration found in TP2")

    def _extract_languages(self, text: str, tp2: WeiDUTp2) -> None:
        """Extract LANGUAGE declarations from TP2 content.

        Args:
            text: Clean TP2 content
            tp2: WeiDUTp2 object to populate
        """
        for block in RE_LANGUAGE_BLOCK.finditer(text):
            raw = block.group(1)
            parts = RE_QUOTED_STRING.findall(raw)

            if len(parts) < 2:
                logger.warning(
                    f"Invalid LANGUAGE block (need at least 2 strings): {raw[:50]}..."
                )
                continue

            os_code = get_os_code()
            tra_files = [
                tra_file.replace(WEIDU_OS_VAR, os_code).replace(MOD_FOLDER_VAR, tp2.name)
                for tra_file in parts[2:]
            ]

            lang = LanguageDeclaration(
                display_name=parts[0],
                language_code=parts[1],
                tra_files=tra_files,
                index=len(tp2.languages),
            )
            tp2.languages.append(lang)
            logger.debug(f"Added language: {lang.display_name} ({lang.language_code})")

        if not tp2.languages:
            logger.warning("No LANGUAGE declarations found in TP2")

    def _split_begin_blocks(self, text: str) -> list[str]:
        """Split TP2 content into BEGIN component blocks.

        Args:
            text: Clean TP2 content

        Returns:
            List of BEGIN block strings
        """
        blocks = [match.group(1) for match in RE_BEGIN_BLOCK.finditer(text)]
        logger.debug(f"Found {len(blocks)} BEGIN blocks")
        return blocks

    def _extract_tra_translations(self, tra_files: list[str]) -> dict[int, str]:
        """Extract translations from TRA files.

        Args:
            tra_files: List of TRA file paths (may contain %WEIDU_OS% variable)

        Returns:
            Dictionary mapping reference ID to translated text
        """
        translations: dict[int, str] = {}

        for tra_file in tra_files:
            tra_path = (self.base_dir / tra_file).resolve()

            logger.debug(f"Reading TRA file: {tra_path}")

            try:
                content = safe_read(tra_path)
                for match in RE_TRA_TRANSLATION.finditer(content):
                    ref_id = match.group("id")
                    if match.group("text_tilde") or match.group("text_quote"):
                        text = (
                            match.group("text_tilde").strip()
                            if match.group("text_tilde")
                            else match.group("text_quote").strip()
                        )
                    else:
                        text = ""
                    translations[ref_id] = text

                logger.debug(f"Loaded {len(translations)} translations from {tra_path.name}")

            except Exception as e:
                logger.error(f"Error reading TRA file {tra_path}: {e}", exc_info=True)

        return translations

    def _build_translations(self, tp2: WeiDUTp2) -> None:
        """Build translation dictionary for all languages.

        Args:
            tp2: WeiDUTp2 object to populate with translations
        """
        translations = {}
        for lang in tp2.languages:
            logger.debug(f"Building translations for language: {lang.language_code}")

            # Load TRA file translations
            translations[lang.language_code] = self._extract_tra_translations(lang.tra_files)

            # Map component designated to translated text
            components: dict[str, str] = {}

            def process_component(comp: Component, lang_code: str) -> None:
                """Recursively process component and sub-components."""
                designated = comp.designated

                # Priority: text_ref > text
                if comp.text_ref is not None and comp.text_ref in translations[lang_code]:
                    components[designated] = translations[lang_code][comp.text_ref]
                elif comp.text is not None:
                    components[designated] = comp.text

                # Handle sub-components for MucComponent
                if isinstance(comp, MucComponent):
                    for sub_comp in comp.components:
                        process_component(sub_comp, lang_code)

            for component in tp2.components:
                process_component(component, lang.language_code)

            tp2.component_translations[lang.language_code] = components
            logger.debug(f"Loaded {len(components)} translations for {lang.language_code}")
        tp2.translations = translations

    # ------------------------------------------------------
    # ---------------- COMPONENT PARSING -------------------
    # ------------------------------------------------------

    def _parse_components(self, text: str, tp2: WeiDUTp2) -> None:
        """Parse all component declarations from TP2 content.

        Args:
            text: Clean TP2 content
            tp2: WeiDUTp2 object to populate
        """
        blocks = self._split_begin_blocks(text)
        prev_designated = -1
        subcomponents: dict[str, MucComponent] = {}

        for block in blocks:
            try:
                component_data = self._parse_single_component(block, prev_designated)
                if component_data is None:
                    continue
                prev_designated = component_data["designated"]

                # Handle subcomponents (mutually exclusive choices)
                if component_data["subcomponent_key"]:
                    muc = self._get_or_create_muc_component(
                        component_data["subcomponent_key"],
                        component_data["subcomponent_ref"],
                        component_data["subcomponent_text"],
                        subcomponents,
                        tp2,
                    )
                    muc.components.append(component_data["component"])
                else:
                    tp2.components.append(component_data["component"])

            except Exception as e:
                logger.error(f"Error parsing component block: {e}", exc_info=True)
                # Continue parsing other blocks
                continue

        logger.info(f"Parsed {len(tp2.components)} top-level components")

    def _parse_single_component(self, block: str, prev_designated: int) -> dict | None:
        """Parse a single BEGIN component block.

        Args:
            block: Single BEGIN block text
            prev_designated: Previous component's designated number

        Returns:
            Dictionary containing parsed component data
        """
        # Extract BEGIN text/reference
        text_ref: str | None = None
        text: str | None = None

        ref_match = RE_BEGIN_REF.search(block)
        if ref_match:
            text_ref = ref_match.group(1)
        else:
            text_match = RE_BEGIN_TEXT.search(block)
            if text_match:
                text = text_match.group(1)

        if not text_ref and not text:
            return None

        # Extract DESIGNATED
        designated_match = RE_DESIGNATED.search(block)
        if designated_match:
            designated = int(designated_match.group(1))
        else:
            designated = prev_designated + 1

        # Extract SUBCOMPONENT
        subcomponent_ref: str | None = None
        subcomponent_text: str | None = None
        subcomponent_key: str | None = None

        sub_ref_match = RE_SUBCOMPONENT_REF.search(block)
        if sub_ref_match:
            subcomponent_ref = sub_ref_match.group(1)
            subcomponent_key = f"ref_{subcomponent_ref}"
        else:
            sub_text_match = RE_SUBCOMPONENT_TEXT.search(block)
            if sub_text_match:
                subcomponent_text = sub_text_match.group(1)
                subcomponent_key = f"text_{subcomponent_text}"

        component = Component(designated=str(designated), text_ref=text_ref, text=text)

        return {
            "component": component,
            "designated": designated,
            "subcomponent_key": subcomponent_key,
            "subcomponent_ref": subcomponent_ref,
            "subcomponent_text": subcomponent_text,
        }

    def _get_or_create_muc_component(
        self,
        key: str,
        text_ref: str | None,
        text: str | None,
        subcomponents: dict[str, MucComponent],
        tp2: WeiDUTp2,
    ) -> MucComponent:
        """Get existing or create new MucComponent.

        Args:
            key: Unique key for the subcomponent group
            text_ref: Translation reference ID
            text: Direct text
            subcomponents: Cache of existing MucComponents
            tp2: WeiDUTp2 object to add new components to

        Returns:
            MucComponent (existing or newly created)
        """

        if key not in subcomponents:
            designated = f"choice_{len(subcomponents)}"
            muc = MucComponent(designated=designated, text_ref=text_ref, text=text)
            tp2.components.append(muc)
            subcomponents[key] = muc
            logger.debug(f"Created MucComponent: {designated}")

        return subcomponents[key]
