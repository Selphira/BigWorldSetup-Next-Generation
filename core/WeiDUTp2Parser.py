"""WeiDU TP2 file parser for Infinity Engine mod installations.

This module provides parsing functionality for WeiDU TP2 (mod metadata) files,
extracting mod information including version, supported languages, components,
and translations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
import logging
from pathlib import Path
import platform
import re
from typing import Any, Iterator

from core.File import safe_read

logger = logging.getLogger(__name__)


# ============================================================================
# Constants
# ============================================================================

LANG_MAP: dict[str, str] = {
    "american": "en_US",
    "english": "en_US",
    "en-us": "en_US",
    "en_us": "en_US",
    "francais": "fr_FR",
    "french": "fr_FR",
    "frenchbg2": "fr_FR",
    "frenchee": "fr_FR",
    "deutsch": "de_DE",
    "dutch": "de_DE",
    "german": "de_DE",
    "german-sh": "de_DE",
    "italian": "it_IT",
    "italiano": "it_IT",
    "polish": "pl_PL",
    "polski": "pl_PL",
    "castellano": "es_ES",
    "castilian": "es_ES",
    "espanol": "es_ES",
    "spanish": "es_ES",
    "russian": "ru_RU",
    "ru_ru": "ru_RU",
    "chinese": "zh_CN",
    "chinese(simplified)": "zh_CN",
    "chs": "zh_CN",
    "schinese": "zh_CN",
    "chineset": "zh_TW",
    "cht": "zh_TW",
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
    "japan": "ja_JP",
    "japanese": "ja_JP",
    "turkce": "tr_TR",
}

OS_CODE_MAP: dict[str, str] = {
    "Windows": "win32",
    "Darwin": "osx",
}
DEFAULT_OS_CODE: str = "unix"

_WEIDU_OS_VAR: str = "%WEIDU_OS%"
_MOD_FOLDER_VAR: str = "%MOD_FOLDER%"

RE_VERSION = re.compile(r'VERSION\s+[~"]?v?([0-9][0-9A-Za-z.\-_]*)[~"]?')
RE_TRA_TRANSLATION = re.compile(
    r"""
    @\s*(?P<id>-?\d+)
    \s*=\s*
    (?:
        ~~~~~(?P<text_tilde5>.*?)~~~~~
      |
        ~(?P<text_tilde>.*?)~
      |
        "(?P<text_quote>.*?)"
    )
    """,
    re.DOTALL | re.VERBOSE,
)


# ============================================================================
# Exceptions
# ============================================================================


class Tp2ParseError(Exception):
    """Base exception for TP2 parsing errors."""

    pass


class Tp2FileNotFoundError(Tp2ParseError):
    """Raised when a TP2 or TRA file is not found."""

    pass


class Tp2InvalidFormatError(Tp2ParseError):
    """Raised when TP2 format is invalid or corrupted."""

    pass


# ============================================================================
# Utilities
# ============================================================================


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
    return LANG_MAP.get(code_lower, code)


def get_os_code() -> str:
    """Get the current OS code for WeiDU compatibility.

    Returns:
        OS code string ('win32', 'osx', or 'unix')
    """
    system = platform.system()
    return OS_CODE_MAP.get(system, DEFAULT_OS_CODE)


# ============================================================================
# Data Structures
# ============================================================================


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
        self.language_code = normalize_language_code(self.language_code)


@dataclass
class Component:
    """Represents a mod component.

    Attributes:
        designated: Unique component identifier
        text_ref: Reference to translation string ID
        text: Direct text (if not using translation reference)
        metadata: Extensible metadata for future features (e.g., predicates)
    """

    designated: str
    text_ref: str | None = None
    text: str | None = None
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
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
        name: Mod name
        version: Mod version string
        languages: Available language declarations
        components: List of mod components
        translations: Raw TRA translations {language_code: {ref_id: text}}
        component_translations: Resolved translations {language_code: {designated: text}}
    """

    name: str | None = None
    version: str | None = None
    languages: list[LanguageDeclaration] = field(default_factory=list)
    components: list[Component] = field(default_factory=list)
    translations: dict[str, dict[str, str]] = field(default_factory=dict)
    component_translations: dict[str, dict[str, str]] = field(default_factory=dict)

    def get_translation(self, designated: str, language_code: str) -> str | None:
        """Retrieve translation for a component in a specific language."""
        return self.component_translations.get(language_code, {}).get(designated)

    def get_all_translations_for_language(self, language_code: str) -> dict[str, str]:
        """Retrieve all translations for a given language."""
        return self.component_translations.get(language_code, {})

    def get_language_by_code(self, language_code: str) -> LanguageDeclaration | None:
        """Find language declaration by code."""
        normalized_code = normalize_language_code(language_code)
        for lang in self.languages:
            if lang.language_code == normalized_code:
                return lang
        return None


# ============================================================================
# Tokenizer
# ============================================================================


class TokenType(Enum):
    """Types of tokens in TP2 component declarations."""

    ACTION_IF = auto()
    BEGIN = auto()
    COPY_EXISTING = auto()
    DEPRECATED = auto()
    DESIGNATED = auto()
    FORCED_SUBCOMPONENT = auto()
    GAME_IS = auto()
    GROUP = auto()
    IDENTIFIER = auto()
    LABEL = auto()
    MOD_IS_INSTALLED = auto()
    NUMBER = auto()
    PRINT = auto()
    REQUIRE_COMPONENT = auto()
    REQUIRE_PREDICATE = auto()
    STRING_LITERAL = auto()  # ~text~ or "text"
    STRING_REF = auto()  # @123
    SUBCOMPONENT = auto()
    EOF = auto()


@dataclass
class Token:
    """Represents a parsed token from TP2 content."""

    type: TokenType
    value: str
    line: int
    column: int

    def __repr__(self) -> str:
        return f"Token({self.type.name}, {self.value!r}, L{self.line}:C{self.column})"


class Tokenizer:
    """Tokenizes TP2 component blocks into a stream of tokens."""

    KEYWORDS = {
        "ACTION_IF": TokenType.ACTION_IF,
        "BEGIN": TokenType.BEGIN,
        "COPY_EXISTING": TokenType.COPY_EXISTING,
        "DEPRECATED": TokenType.DEPRECATED,
        "DESIGNATED": TokenType.DESIGNATED,
        "FORCED_SUBCOMPONENT": TokenType.FORCED_SUBCOMPONENT,
        "GAME_IS": TokenType.GAME_IS,
        "GROUP": TokenType.GROUP,
        "LABEL": TokenType.LABEL,
        "MOD_IS_INSTALLED": TokenType.MOD_IS_INSTALLED,
        "PRINT": TokenType.PRINT,
        "REQUIRE_COMPONENT": TokenType.REQUIRE_COMPONENT,
        "REQUIRE_PREDICATE": TokenType.REQUIRE_PREDICATE,
        "SUBCOMPONENT": TokenType.SUBCOMPONENT,
    }

    PATTERNS = [
        (re.compile(r"@\s*(-?\d+)"), TokenType.STRING_REF),
        (re.compile(r"\b(\d+)\b"), TokenType.NUMBER),
        (re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\b"), TokenType.IDENTIFIER),
    ]

    def tokenize(self, text: str) -> list[Token]:
        """Tokenize TP2 text into a list of tokens."""
        tokens: list[Token] = []
        i = 0
        length = len(text)
        line = 1
        column = 0

        while i < length:
            char = text[i]

            # Track line/column
            if char == "\n":
                line += 1
                column = 0
                i += 1
                continue

            # Skip whitespace (except newlines)
            if char in " \t\r":
                column += 1
                i += 1
                continue

            # String literals
            if char in ("~", '"', "%"):
                token = self._extract_string(text, i, line, column, char)
                if token:
                    tokens.append(token)
                    # Update position
                    string_content = char + token.value + char
                    newlines = string_content.count("\n")
                    if newlines > 0:
                        line += newlines
                        # Column is position after last newline
                        last_newline = string_content.rfind("\n")
                        column = len(string_content) - last_newline - 1
                    else:
                        column += len(string_content)
                    i += len(string_content)
                    continue

            # String references @123 or @-123
            if char == "@":
                token = self._extract_string_ref(text, i, line, column)
                if token:
                    tokens.append(token)
                    column += len(token.value) + 1  # +1 for @
                    i += len(token.value) + 1
                    continue

            # Numbers
            if char.isdigit():
                token = self._extract_number(text, i, line, column)
                if token:
                    tokens.append(token)
                    column += len(token.value)
                    i += len(token.value)
                    continue

            # Identifiers and keywords
            if char.isalpha() or char == "_":
                token = self._extract_identifier(text, i, line, column)
                if token:
                    tokens.append(token)
                    column += len(token.value)
                    i += len(token.value)
                    continue

            # Unknown character, skip
            column += 1
            i += 1

        tokens.append(Token(TokenType.EOF, "", line, column))
        return tokens

    @staticmethod
    def _extract_string(
        text: str, start: int, line: int, column: int, delimiter: str
    ) -> Token | None:
        """Extract a string literal with given delimiter.

        Supports multi-line strings.
        """
        i = start + 1  # Skip opening delimiter
        end = text.find(delimiter, i)

        if end == -1:
            # Unclosed string
            return None

        value = text[i:end]
        return Token(TokenType.STRING_LITERAL, value, line, column)

    @staticmethod
    def _extract_string_ref(text: str, start: int, line: int, column: int) -> Token | None:
        """Extract a string reference like @123 or @-123."""
        pattern = re.compile(r"@\s*(-?\d+)")
        match = pattern.match(text, start)

        if not match:
            return None

        value = match.group(1)
        return Token(TokenType.STRING_REF, value, line, column)

    @staticmethod
    def _extract_number(text: str, start: int, line: int, column: int) -> Token | None:
        """Extract a number."""
        i = start
        while i < len(text) and text[i].isdigit():
            i += 1

        value = text[start:i]
        return Token(TokenType.NUMBER, value, line, column)

    def _extract_identifier(
        self, text: str, start: int, line: int, column: int
    ) -> Token | None:
        """Extract an identifier or keyword."""
        i = start
        while i < len(text) and (text[i].isalnum() or text[i] in ["_", "-"]):
            i += 1

        value = text[start:i]
        token_type = self.KEYWORDS.get(value.upper(), TokenType.IDENTIFIER)
        return Token(token_type, value, line, column)


# ============================================================================
# Language Parser
# ============================================================================


class LanguageParser:
    """Extracts and parses LANGUAGE declarations from TP2 content."""

    LANGUAGE_START = re.compile(r"^\s*LANGUAGE\b", re.MULTILINE)

    def __init__(self):
        self.tokenizer = Tokenizer()

    def extract_languages(self, text: str, mod_name: str) -> list[LanguageDeclaration]:
        """Extract all LANGUAGE declarations from TP2 content.

        Args:
            text: Cleaned TP2 content (comments already removed)
            mod_name: Name of the mod (for %MOD_FOLDER% substitution)

        Returns:
            List of LanguageDeclaration objects
        """
        # Find all LANGUAGE keyword positions
        language_positions = [match.start() for match in self.LANGUAGE_START.finditer(text)]

        if not language_positions:
            return []

        languages = []

        for i, lang_start in enumerate(language_positions):
            # Determine end position (next LANGUAGE or BEGIN, or EOF)
            if i + 1 < len(language_positions):
                lang_end = language_positions[i + 1]
            else:
                begin_match = re.search(r"^\s*BEGIN\b", text[lang_start:], re.MULTILINE)
                lang_end = lang_start + begin_match.start() if begin_match else len(text)

            # Extract block content (skip "LANGUAGE" keyword itself)
            block_text = text[lang_start:lang_end]
            block_content = re.sub(r"^\s*LANGUAGE\s+", "", block_text)

            lang_decl = self._parse_block(block_content, mod_name, len(languages))
            if lang_decl:
                languages.append(lang_decl)

        return languages

    def _parse_block(
        self, block_content: str, mod_name: str, index: int
    ) -> LanguageDeclaration | None:
        """Parse a single LANGUAGE block.

        Args:
            block_content: Content after LANGUAGE keyword
            mod_name: Mod name for variable substitution
            index: Position in language declaration order

        Returns:
            LanguageDeclaration or None if invalid
        """
        tokens = self.tokenizer.tokenize(block_content)

        values = []
        for token in tokens:
            if token.type in [TokenType.STRING_LITERAL, TokenType.IDENTIFIER]:
                values.append(token.value)
            else:
                break

        # Need at least display_name and language_code
        if len(values) < 2:
            logger.warning(f"Invalid LANGUAGE block (need ≥2 values): {block_content[:50]}...")
            return None

        display_name = values[0]
        language_code = values[1]
        tra_files_raw = values[2:]
        os_code = get_os_code()

        tra_files = [
            tra_file.replace(_WEIDU_OS_VAR, os_code).replace(_MOD_FOLDER_VAR, mod_name)
            for tra_file in tra_files_raw
        ]

        return LanguageDeclaration(
            display_name=display_name,
            language_code=language_code,
            tra_files=tra_files,
            index=index,
        )


# ============================================================================
# Component Parser
# ============================================================================


class ComponentParser:
    """Extracts and parses component blocks from TP2 content."""

    BEGIN_PATTERN = re.compile(
        r"^\s*BEGIN\s+(?:@\s*-?\d+|[~\"]|/\*.*?\*/\s*(?:@|[~\"]))",
        re.MULTILINE | re.IGNORECASE,
    )

    def __init__(self):
        self.tokenizer = Tokenizer()

    def extract_blocks(self, text: str) -> list[str]:
        """Extract component blocks from TP2 content.

        Args:
            text: Cleaned TP2 content (comments already removed)

        Returns:
            List of component block strings
        """
        begin_positions = [match.start() for match in self.BEGIN_PATTERN.finditer(text)]

        if not begin_positions:
            return []

        blocks = []
        for i, start in enumerate(begin_positions):
            end = begin_positions[i + 1] if i + 1 < len(begin_positions) else len(text)
            block = text[start:end].strip()
            if block:
                blocks.append(block)

        return blocks

    def parse_block(self, block_text: str) -> dict | None:
        """Parse a single component block into structured data.

        Returns:
            Dictionary with component data, or None if invalid
        """
        tokens = self.tokenizer.tokenize(block_text)

        if not tokens or tokens[0].type != TokenType.BEGIN:
            return None

        component: dict[str, Any] = {
            "text_ref": None,
            "text": None,
            "designated": None,
            "deprecated": None,
            "subcomponent_ref": None,
            "subcomponent_text": None,
            "label": None,
            "group": None,
            "metadata": {},
        }

        i = 0
        while i < len(tokens):
            token = tokens[i]

            if token.type == TokenType.BEGIN:
                i += 1
                if i < len(tokens):
                    next_token = tokens[i]
                    if next_token.type == TokenType.STRING_REF:
                        component["text_ref"] = next_token.value
                    elif next_token.type == TokenType.STRING_LITERAL:
                        component["text"] = next_token.value

            elif token.type == TokenType.DESIGNATED:
                i += 1
                if i < len(tokens) and tokens[i].type == TokenType.NUMBER:
                    component["designated"] = tokens[i].value

            elif token.type in (TokenType.SUBCOMPONENT, TokenType.FORCED_SUBCOMPONENT):
                i += 1
                if i < len(tokens):
                    next_token = tokens[i]
                    if next_token.type == TokenType.STRING_REF:
                        component["subcomponent_ref"] = next_token.value
                    elif next_token.type == TokenType.STRING_LITERAL:
                        component["subcomponent_text"] = next_token.value

            elif token.type == TokenType.LABEL:
                i += 1
                if i < len(tokens):
                    next_token = tokens[i]
                    if next_token.type in (TokenType.STRING_LITERAL, TokenType.IDENTIFIER):
                        component["label"] = next_token.value

            elif token.type == TokenType.GROUP:
                i += 1
                if i < len(tokens):
                    next_token = tokens[i]
                    if next_token.type in (TokenType.STRING_LITERAL, TokenType.IDENTIFIER):
                        component["group"] = next_token.value

            elif token.type == TokenType.DEPRECATED:
                component["deprecated"] = True
                i += 1

            # TODO: Future extension
            elif token.type in (
                TokenType.GAME_IS,
                TokenType.IDENTIFIER,
                TokenType.MOD_IS_INSTALLED,
                TokenType.NUMBER,
                TokenType.REQUIRE_COMPONENT,
                TokenType.REQUIRE_PREDICATE,
                TokenType.STRING_LITERAL,
                TokenType.STRING_REF,
            ):
                pass  # Placeholder for future implementation

            else:
                break

            i += 1

        return component


# ============================================================================
# Main Parser
# ============================================================================


class WeiDUTp2Parser:
    """Parser for WeiDU TP2 mod metadata files.

    This parser extracts mod information including version, languages,
    components, and translations from TP2 files used by the WeiDU
    installer for Infinity Engine games.
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
            return self.parse_string(content, file_path.stem)
        except Exception as e:
            logger.error(f"Failed to parse TP2 file: {file_path}", exc_info=True)
            raise Tp2ParseError(f"Error parsing {file_path}: {e}") from e

    def parse_string(self, tp2_content: str, tp2_name: str) -> WeiDUTp2:
        """Parse TP2 content from a string.

        Args:
            tp2_content: Raw TP2 file content
            tp2_name: Name of the mod

        Returns:
            Parsed WeiDUTp2 object

        Raises:
            Tp2ParseError: If parsing fails
        """
        try:
            clean_content = self._strip_comments(tp2_content)

            tp2 = WeiDUTp2()
            tp2.name = tp2_name

            self._extract_version(clean_content, tp2)
            self._extract_languages(clean_content, tp2)
            self._parse_components(clean_content, tp2)
            self._build_translations(tp2)

            logger.info(
                f"Successfully parsed TP2: version={tp2.version}, "
                f"languages={len(tp2.languages)}, components={len(tp2.components)}"
            )

            return tp2

        except Exception as e:
            logger.error("Failed to parse TP2 string", exc_info=True)
            raise Tp2ParseError(f"Error parsing TP2 content: {e}") from e

    def _strip_comments(self, text: str) -> str:
        """Remove C-style and C++-style comments from text."""
        return "".join(
            content
            for token_type, content in self._tokenize_for_comment_removal(text)
            if token_type != "comment"
        )

    @staticmethod
    def _tokenize_for_comment_removal(text: str) -> Iterator[tuple[str, str]]:
        """Tokenize text into strings, comments, and code segments."""
        i = 0
        length = len(text)

        while i < length:
            # Check for string literals (must check before comments)
            for delimiter in ("~", '"', "'"):
                if text[i] == delimiter:
                    end = text.find(delimiter, i + 1)
                    if end != -1:
                        yield "string", text[i : end + 1]
                        i = end + 1
                        break
            else:
                # Check for line comment
                if i + 1 < length and text[i : i + 2] == "//":
                    end = text.find("\n", i)
                    if end != -1:
                        yield "comment", text[i:end]
                        i = end
                    else:
                        # Comment extends to EOF
                        yield "comment", text[i:]
                        break
                    continue

                # Check for block comment
                if i + 1 < length and text[i : i + 2] == "/*":
                    end = text.find("*/", i + 2)
                    if end != -1:
                        yield "comment", text[i : end + 2]
                        i = end + 2
                    else:
                        # Unclosed block comment, consume to EOF
                        yield "comment", text[i:]
                        logger.warning("Unclosed block comment in TP2 file")
                        break
                    continue

                # Check for inline file block
                if i + 7 < length and text[i : i + 8] == "<<<<<<<<":
                    end = text.find(">>>>>>>>", i + 8)
                    if end != -1:
                        yield "comment", text[i : end + 8]
                        i = end + 8
                    else:
                        # Unclosed inline file, consume to EOF
                        yield "comment", text[i:]
                        logger.warning("Unclosed inline file block in TP2 file")
                        break
                    continue

                # Regular code character
                yield "code", text[i]
                i += 1

    @staticmethod
    def _extract_version(text: str, tp2: WeiDUTp2) -> None:
        """Extract VERSION declaration from TP2 content."""
        match = RE_VERSION.search(text)
        if match:
            tp2.version = match.group(1)
        else:
            logger.warning("No VERSION declaration found in TP2")

    @staticmethod
    def _extract_languages(text: str, tp2: WeiDUTp2) -> None:
        """Extract LANGUAGE declarations from TP2 content."""
        parser = LanguageParser()
        tp2.languages = parser.extract_languages(text, tp2.name)

        if not tp2.languages:
            logger.warning("No LANGUAGE declarations found in TP2")

    @staticmethod
    def _parse_components(text: str, tp2: WeiDUTp2) -> None:
        """Parse all component declarations from TP2 content using robust token-based approach."""
        parser = ComponentParser()
        blocks = parser.extract_blocks(text)

        logger.info(f"Extracted {len(blocks)} component blocks")

        deprecated_count = 0
        prev_designated = "-1"
        muc_groups: dict[str, MucComponent] = {}

        for block in blocks:
            try:
                component_data = parser.parse_block(block)

                if component_data is None:
                    logger.warning("Failed to parse component block")
                    continue

                if component_data["designated"] is not None:
                    designated = component_data["designated"]
                else:
                    # Ex: DESIGNATED 0010 (trap_overhaul)
                    width = len(prev_designated) if prev_designated.startswith("0") else None
                    value = int(prev_designated) + 1
                    designated = f"{value:0{width}d}" if width else str(value)

                prev_designated = designated

                if component_data["deprecated"] is not None:
                    deprecated_count += 1
                    logger.debug("Skipping deprecated component block")
                    continue

                component = Component(
                    designated=str(designated),
                    text_ref=component_data["text_ref"],
                    text=component_data["text"],
                )

                if component_data["subcomponent_ref"] or component_data["subcomponent_text"]:
                    subcomponent_key = (
                        f"ref_{component_data['subcomponent_ref']}"
                        if component_data["subcomponent_ref"]
                        else f"text_{component_data['subcomponent_text']}"
                    )

                    if subcomponent_key not in muc_groups:
                        muc = MucComponent(
                            designated=f"choice_{len(muc_groups)}",
                            text_ref=component_data["subcomponent_ref"],
                            text=component_data["subcomponent_text"],
                        )
                        tp2.components.append(muc)
                        muc_groups[subcomponent_key] = muc

                    if component not in muc_groups[subcomponent_key].components:
                        muc_groups[subcomponent_key].components.append(component)
                else:
                    tp2.components.append(component)

            except Exception as e:
                logger.error(f"Error parsing component: {e}", exc_info=True)
                continue

        logger.info(f"Parsed {len(tp2.components)} top-level components")
        if deprecated_count > 0:
            logger.info(f"Skipped {deprecated_count} deprecated component(s)")

    def _extract_tra_translations(self, tra_files: list[str]) -> dict[str, str]:
        """Extract translations from TRA files.

        Args:
            tra_files: List of TRA file paths

        Returns:
            Dictionary mapping reference ID to translated text
        """
        translations: dict[str, str] = {}

        for tra_file in tra_files:
            tra_path = (self.base_dir / tra_file).resolve()

            try:
                content = safe_read(tra_path)
                for match in RE_TRA_TRANSLATION.finditer(content):
                    ref_id = match.group("id")
                    text = (
                        match.group("text_tilde").strip()
                        if match.group("text_tilde")
                        else match.group("text_quote").strip()
                        if match.group("text_quote")
                        else match.group("text_tilde5").strip()
                        if match.group("text_tilde5")
                        else ""
                    )
                    translations[ref_id] = text

            except Exception as e:
                logger.error(f"Error reading TRA file {tra_path}: {e}", exc_info=True)

        return translations

    def _build_translations(self, tp2: WeiDUTp2) -> None:
        """Build translation dictionary for all languages."""
        translations: dict[str, dict[str, str]] = {
            lang.language_code: self._extract_tra_translations(lang.tra_files)
            for lang in tp2.languages
        }

        fallback_langs = [code for code in ["en_US", "fr_FR", "de_DE"] if code in translations]

        for lang in tp2.languages:
            lang_code = lang.language_code
            lang_codes = [lang_code] + fallback_langs
            components: dict[str, str] = {}

            for component in tp2.components:
                self._process_component(component, components, lang_codes, translations)

            if components:
                tp2.component_translations[lang_code] = components

        tp2.translations = translations

    def _process_component(
        self,
        comp: Component,
        components: dict[str, str],
        lang_codes: list[str],
        translations: dict[str, dict[str, str]],
    ) -> None:
        """Process a single component and its sub-components recursively.

        Args:
            comp: Component to process
            components: Dictionary to populate with translations
            lang_codes: Ordered list of language codes for fallback
            translations: Dictionary of all translations
        """
        designated = comp.designated

        text = None
        if comp.text_ref is not None:
            for code in lang_codes:
                value = translations.get(code, {}).get(comp.text_ref)
                if value:
                    text = value
                    break

        if text is None:
            text = comp.text

        if text is not None:
            components[designated] = text

        if isinstance(comp, MucComponent):
            for sub_comp in comp.components:
                self._process_component(sub_comp, components, lang_codes, translations)
