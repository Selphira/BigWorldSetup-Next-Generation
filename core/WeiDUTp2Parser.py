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
from typing import Iterator

from core.ComponentRequirement import (
    ComponentRequirement,
    ComponentTarget,
    RequirementKind,
    RequirementSource,
)
from core.File import TEXT_ENCODINGS, safe_read

logger = logging.getLogger(__name__)


# ============================================================================
# Constants
# ============================================================================

LANG_MAP: dict[str, str] = {
    "american": "en_US",
    "english": "en_US",
    "english_original": "en_US",
    "english_revision": "en_US",
    "en-us": "en_US",
    "en_us": "en_US",
    "franais": "fr_FR",
    "francais": "fr_FR",
    "french": "fr_FR",
    "frenchbg2": "fr_FR",
    "frenchee": "fr_FR",
    "deutsch": "de_DE",
    "dutch": "nl_NL",
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
    "schineseee": "zh_CN",
    "simchinese": "zh_CN",
    "chineset": "zh_TW",
    "cht": "zh_TW",
    "tchinese": "zh_TW",
    "korean": "ko_KR",
    "cesky": "cs_CZ",
    "czech": "cs_CZ",
    "bportuguese": "pt_BR",
    "brazilianportuguese": "pt_BR",
    "brazilian_portuguese": "pt_BR",
    "ptbr": "pt_BR",
    "portugues": "pt_PT",
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

SUPPORTED_GAMES: set[str] = {"bgee", "sod", "bg2ee", "eet", "iwdee", "pstee"}

# Label of a translation made for the Enhanced Editions, when the mod also
# declares one for the classic games ("Francais pour BG2EE", "pour les EE",
# "BG2EE/EET", "l'edition Enhanced"); "EE" is matched in capitals only.
RE_ENHANCED_EDITION_LABEL = re.compile(r"EE\b|\bEET\b|(?i:\benhanced\b)")

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


# Legacy Chinese TRA files are in GBK or Big5, which cp1252 and latin-1 would
# turn into mojibake without error: these codecs are tried before latin-1.
# gb18030 is a superset of GBK.
LANGUAGE_TEXT_ENCODINGS: dict[str, tuple[str, ...]] = {
    "zh_CN": ("utf-8-sig", "gb18030", "latin-1"),
    "zh_TW": ("utf-8-sig", "big5", "latin-1"),
}


# ============================================================================
# Exceptions
# ============================================================================


class Tp2ParseError(Exception):
    """Base exception for TP2 parsing errors."""


class Tp2FileNotFoundError(Tp2ParseError):
    """Raised when a TP2 or TRA file is not found."""


class Tp2InvalidFormatError(Tp2ParseError):
    """Raised when TP2 format is invalid or corrupted."""


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

    @property
    def is_enhanced_edition(self) -> bool:
        """Whether the label marks a translation made for the Enhanced Editions."""
        return RE_ENHANCED_EDITION_LABEL.search(self.display_name) is not None


@dataclass
class Component:
    """Represents a mod component.

    Attributes:
        designated: Unique component identifier
        text_ref: Reference to translation string ID
        text: Direct text (if not using translation reference)
        games: List of supported game codes (lowercase)
        requirements: Requirements on other components, in declaration order
        metadata: Extensible metadata for future features (e.g., predicates)
    """

    designated: str
    text_ref: str | None = None
    text: str | None = None
    games: list[str] | None = None
    requirements: list[ComponentRequirement] = field(default_factory=list)
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
        untranslated_component_labels: Literal labels of a TP2 declaring no
            LANGUAGE, whose language is unknown {designated: text}
    """

    name: str | None = None
    version: str | None = None
    languages: list[LanguageDeclaration] = field(default_factory=list)
    components: list[Component] = field(default_factory=list)
    translations: dict[str, dict[str, str]] = field(default_factory=dict)
    component_translations: dict[str, dict[str, str]] = field(default_factory=dict)
    untranslated_component_labels: dict[str, str] = field(default_factory=dict)

    def unlogged_components(self) -> set[str]:
        """Identify installable component numbers explicitly excluded from WeiDU.log."""
        return {
            item.designated
            for component in self.components
            for item in (
                component.components if isinstance(component, MucComponent) else [component]
            )
            if item.metadata.get("no_log_record")
        }

    def preferred_languages(self) -> list[LanguageDeclaration]:
        """Keep one declaration per language code, in declaration order.

        Among several translations of a language, the one made for the
        Enhanced Editions wins; otherwise the first declared one, which
        is the order of preference of the author.
        """
        preferred: dict[str, LanguageDeclaration] = {}
        for language in self.languages:
            kept = preferred.get(language.language_code)
            if kept is None or (language.is_enhanced_edition and not kept.is_enhanced_edition):
                preferred[language.language_code] = language

        return sorted(preferred.values(), key=lambda language: language.index)


class GamePredicateParser:
    """Parser for GAME_IS and GAME_INCLUDES."""

    def __init__(self, supported_games: set[str] | None = None):
        """Initialize parser with supported games."""
        self.supported_games = (
            supported_games if supported_games is not None else SUPPORTED_GAMES.copy()
        )
        if "bg" in self.supported_games:
            self.supported_games.add("bg1")
        self.tokenizer = Tokenizer()

    def parse_from_tokens(self, tokens: list[Token], pos: int) -> tuple[list[str] | None, int]:
        """Parse game predicate from a token stream."""
        games, pos = self._parse_or_expression(tokens, pos)

        if games is None or len(games) == 0:
            return None, pos

        # If all games are supported, return None
        if games == self.supported_games:
            return None, pos

        return sorted(games), pos

    def _parse_or_expression(self, tokens: list[Token], pos: int) -> tuple[set[str], int]:
        """Parse OR expression (union of conditions)."""
        games, pos = self._parse_and_expression(tokens, pos)

        while pos < len(tokens):
            token = tokens[pos]

            if token.type == TokenType.OR or (
                token.type == TokenType.IDENTIFIER and token.value.upper() == "OR"
            ):
                pos += 1
                right_games, pos = self._parse_and_expression(tokens, pos)
                games = games.union(right_games)
            else:
                break

        return games, pos

    def _parse_and_expression(self, tokens: list[Token], pos: int) -> tuple[set[str], int]:
        """Parse AND expression (intersection of conditions)."""
        games, pos = self._parse_primary_expression(tokens, pos)

        while pos < len(tokens):
            token = tokens[pos]

            if token.type == TokenType.AND or (
                token.type == TokenType.IDENTIFIER and token.value.upper() == "AND"
            ):
                pos += 1
                right_games, pos = self._parse_primary_expression(tokens, pos)
                games = games.intersection(right_games)
            else:
                break

        return games, pos

    def _parse_primary_expression(self, tokens: list[Token], pos: int) -> tuple[set[str], int]:
        """Parse primary expression (NOT, parentheses, or condition)."""
        if pos >= len(tokens):
            return self.supported_games.copy(), pos

        token = tokens[pos]

        if token.type == TokenType.NOT or (
            token.type == TokenType.IDENTIFIER and token.value.upper() == "NOT"
        ):
            pos += 1
            games, pos = self._parse_primary_expression(tokens, pos)
            # Negate: all games except those in the set
            return self.supported_games - games, pos

        if token.type == TokenType.LPAREN:
            pos += 1
            games, pos = self._parse_or_expression(tokens, pos)
            # Skip closing parenthesis if present
            if pos < len(tokens) and tokens[pos].type == TokenType.RPAREN:
                pos += 1
            return games, pos

        if token.type == TokenType.GAME_IS:
            return self._parse_game_is(tokens, pos)

        if token.type == TokenType.GAME_INCLUDES:
            return self._parse_game_includes(tokens, pos)

        if token.type in (
            TokenType.BEGIN,
            TokenType.NO_LOG_RECORD,
            TokenType.DESIGNATED,
            TokenType.SUBCOMPONENT,
            TokenType.FORCED_SUBCOMPONENT,
            TokenType.LABEL,
            TokenType.GROUP,
            TokenType.DEPRECATED,
            TokenType.REQUIRE_PREDICATE,
            TokenType.REQUIRE_COMPONENT,
            TokenType.EOF,
        ):
            return self.supported_games.copy(), pos

        while pos < len(tokens):
            t = tokens[pos]
            if t.type in (TokenType.AND, TokenType.OR, TokenType.RPAREN):
                break
            if t.type in (
                TokenType.BEGIN,
                TokenType.NO_LOG_RECORD,
                TokenType.DESIGNATED,
                TokenType.SUBCOMPONENT,
                TokenType.FORCED_SUBCOMPONENT,
                TokenType.LABEL,
                TokenType.GROUP,
                TokenType.DEPRECATED,
                TokenType.REQUIRE_PREDICATE,
                TokenType.REQUIRE_COMPONENT,
                TokenType.EOF,
            ):
                break
            pos += 1

        return self.supported_games.copy(), pos

    def _parse_game_is(self, tokens: list[Token], pos: int) -> tuple[set[str], int]:
        """Parse GAME_IS ~games~."""
        pos += 1

        if pos >= len(tokens):
            return set(), pos

        if tokens[pos].type == TokenType.STRING_LITERAL:
            game_list = tokens[pos].value.strip()
            games = set()
            for game in game_list.split():
                game = game.lower()
                if game == "bgee" and "sod" in self.supported_games:
                    games.add("sod")
                if game in self.supported_games:
                    games.add(game)
            return games, pos + 1

        return set(), pos

    def _parse_game_includes(self, tokens: list[Token], pos: int) -> tuple[set[str], int]:
        """Parse GAME_INCLUDES ~game~."""
        pos += 1

        if pos >= len(tokens):
            return set(), pos

        if tokens[pos].type == TokenType.STRING_LITERAL:
            game = tokens[pos].value.strip().lower()
            if game == "bg1":
                game = "bgee"
            if game == "bg2":
                game = "bg2ee"
            if game in self.supported_games:
                if game == "bgee":
                    return {"bgee", "eet", "sod"}, pos + 1
                if game == "bg2ee":
                    return {"bg2ee", "eet"}, pos + 1
                return {game}, pos + 1

        return set(), pos


# ============================================================================
# Tokenizer
# ============================================================================


class TokenType(Enum):
    """Types of tokens in TP2 component declarations."""

    ACTION_IF = auto()
    AND = auto()
    BEGIN = auto()
    COPY_EXISTING = auto()
    DEPRECATED = auto()
    NO_LOG_RECORD = auto()
    DESIGNATED = auto()
    FORBID_COMPONENT = auto()
    FORBID_FILE = auto()
    FORCED_SUBCOMPONENT = auto()
    GAME_INCLUDES = auto()
    GAME_IS = auto()
    GROUP = auto()
    IDENTIFIER = auto()
    INSTALL_BY_DEFAULT = auto()
    LABEL = auto()
    LPAREN = auto()
    METADATA = auto()
    MOD_IS_INSTALLED = auto()
    NOT = auto()
    NUMBER = auto()
    OR = auto()
    PRINT = auto()
    REQUIRE_COMPONENT = auto()
    REQUIRE_FILE = auto()
    REQUIRE_PREDICATE = auto()
    RPAREN = auto()
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
    delimiter: str = ""  # Delimiter of a string literal: ~, " or %

    def __repr__(self) -> str:
        return f"Token({self.type.name}, {self.value!r}, L{self.line}:C{self.column})"

    def source_text(self) -> str:
        """Return the token as written in the TP2, delimiters included."""
        if self.type == TokenType.STRING_REF:
            return f"@{self.value}"
        return f"{self.delimiter}{self.value}{self.delimiter}"


class Tokenizer:
    """Tokenizes TP2 component blocks into a stream of tokens."""

    KEYWORDS = {
        "ACTION_IF": TokenType.ACTION_IF,
        "AND": TokenType.AND,
        "BEGIN": TokenType.BEGIN,
        "COPY_EXISTING": TokenType.COPY_EXISTING,
        "DEPRECATED": TokenType.DEPRECATED,
        "NO_LOG_RECORD": TokenType.NO_LOG_RECORD,
        "DESIGNATED": TokenType.DESIGNATED,
        "FORBID_COMPONENT": TokenType.FORBID_COMPONENT,
        "FORBID_FILE": TokenType.FORBID_FILE,
        "FORCED_SUBCOMPONENT": TokenType.FORCED_SUBCOMPONENT,
        "GAME_INCLUDES": TokenType.GAME_INCLUDES,
        "GAME_IS": TokenType.GAME_IS,
        "GROUP": TokenType.GROUP,
        "INSTALL_BY_DEFAULT": TokenType.INSTALL_BY_DEFAULT,
        "LABEL": TokenType.LABEL,
        "METADATA": TokenType.METADATA,
        "MOD_IS_INSTALLED": TokenType.MOD_IS_INSTALLED,
        "NOT": TokenType.NOT,
        "OR": TokenType.OR,
        "PRINT": TokenType.PRINT,
        "REQUIRE_COMPONENT": TokenType.REQUIRE_COMPONENT,
        "REQUIRE_FILE": TokenType.REQUIRE_FILE,
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

            # Operators: || (OR) and && (AND)
            if i + 1 < length:
                two_char = text[i : i + 2]
                if two_char == "||":
                    tokens.append(Token(TokenType.OR, "||", line, column))
                    column += 2
                    i += 2
                    continue
                if two_char == "&&":
                    tokens.append(Token(TokenType.AND, "&&", line, column))
                    column += 2
                    i += 2
                    continue

            # Single character operators
            if char == "|":
                tokens.append(Token(TokenType.OR, "|", line, column))
                column += 1
                i += 1
                continue

            if char == "&":
                tokens.append(Token(TokenType.AND, "&", line, column))
                column += 1
                i += 1
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

            # Parentheses
            if char == "(":
                tokens.append(Token(TokenType.LPAREN, "(", line, column))
                column += 1
                i += 1
                continue

            if char == ")":
                tokens.append(Token(TokenType.RPAREN, ")", line, column))
                column += 1
                i += 1
                continue

            # Exclamation mark for negation
            if char == "!":
                tokens.append(Token(TokenType.NOT, "!", line, column))
                column += 1
                i += 1
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
        return Token(TokenType.STRING_LITERAL, value, line, column, delimiter)

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
        # Bare WeiDU words may hold "#" and "." (c#label, setup-mod.tp2).
        while i < len(text) and (text[i].isalnum() or text[i] in ["_", "-", "#", "."]):
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

    def extract_languages(
        self, text: str, mod_name: str, base_dir: str
    ) -> list[LanguageDeclaration]:
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
            block_content = re.sub(r"^\s*LANGUAGE\s*", "", block_text)

            lang_decl = self._parse_block(block_content, base_dir, len(languages))
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


@dataclass
class ComponentHeader:
    """Component flags declared between BEGIN and the first action of a block.

    Attributes:
        body_start: Index, in the block tokens, of the first token after the
            flags (the EOF token when the block has no body)
        text_ref: Reference to the label translation string ID
        text: Direct label text
        designated: Explicit DESIGNATED number
        deprecated: Whether the component is DEPRECATED
        subcomponent_ref: Reference to the SUBCOMPONENT label translation string ID
        subcomponent_text: Direct SUBCOMPONENT label text
        label: LABEL value
        group: GROUP value, when given as text
        games: Games kept by the REQUIRE_PREDICATE, None when unrestricted
        no_log_record: Whether the component is excluded from WeiDU.log
        requirements: REQUIRE_COMPONENT and FORBID_COMPONENT flags, in order
        unresolved_requirements: Source of the component flags whose target
            cannot be resolved, left for the caller to report
    """

    body_start: int
    text_ref: str | None = None
    text: str | None = None
    designated: str | None = None
    deprecated: bool = False
    subcomponent_ref: str | None = None
    subcomponent_text: str | None = None
    label: str | None = None
    group: str | None = None
    games: list[str] | None = None
    no_log_record: bool = False
    requirements: list[ComponentRequirement] = field(default_factory=list)
    unresolved_requirements: list[str] = field(default_factory=list)


class ComponentParser:
    """Extracts component blocks from TP2 content and reads their header.

    The header grammar is strict: WeiDU defines a closed list of component
    flags, each read with its arguments. The first token that is not a flag
    starts the body, which is left to callers.
    """

    BEGIN_PATTERN = re.compile(
        r"^\s*BEGIN\s+(?:@\s*-?\d+|~[^~]*~(?!\s*=)|\"[^\"]*\"(?!\s*=)|/\*.*?\*/\s*(?:@|[~\"]))",
        re.MULTILINE | re.IGNORECASE,
    )

    # Token types a WeiDU String argument can take, bare words included.
    STRING_ARGUMENT_TYPES = frozenset(
        {TokenType.STRING_LITERAL, TokenType.STRING_REF, TokenType.IDENTIFIER, TokenType.NUMBER}
    )

    # Flags followed by a fixed number of String arguments.
    STRING_ARGUMENT_COUNTS = {
        TokenType.FORBID_COMPONENT: 3,
        TokenType.FORBID_FILE: 2,
        TokenType.INSTALL_BY_DEFAULT: 0,
        TokenType.METADATA: 1,
        TokenType.NO_LOG_RECORD: 0,
        TokenType.REQUIRE_COMPONENT: 3,
        TokenType.REQUIRE_FILE: 2,
    }

    # Component flags naming a component of another or of the same mod.
    COMPONENT_REQUIREMENT_KINDS = {
        TokenType.FORBID_COMPONENT: RequirementKind.FORBIDDEN,
        TokenType.REQUIRE_COMPONENT: RequirementKind.REQUIRED,
    }

    # Predicate functions by number of arguments. The arguments of other
    # functions cannot be counted.
    PREDICATE_ARGUMENT_COUNTS = {
        "ENGINE_IS": 1,
        "FILE_CONTAINS": 2,
        "FILE_CONTAINS_EVALUATED": 2,
        "FILE_EXISTS": 1,
        "FILE_EXISTS_IN_GAME": 1,
        "FILE_MD5": 2,
        "FILE_SIZE": 2,
        "GAME_INCLUDES": 1,
        "GAME_IS": 1,
        "IS_AN_INT": 1,
        "MOD_IS_INSTALLED": 2,
        "RESOURCE_CONTAINS": 2,
        "VARIABLE_IS_SET": 1,
    }

    # Binary operators written as words, between two predicate operands.
    PREDICATE_OPERATOR_NAMES = frozenset(
        {
            "BAND",
            "BOR",
            "BXOR",
            "BLSL",
            "BLSR",
            "BASR",
            "STR_CMP",
            "STR_EQ",
            "STRING_COMPARE",
            "STRING_COMPARE_CASE",
            "STRING_COMPARE_REGEXP",
            "STRING_CONTAINS_REGEXP",
            "STRING_EQUAL",
            "STRING_EQUAL_CASE",
            "STRING_MATCHES_REGEXP",
        }
    )

    def __init__(self, supported_games: set[str] | None = None):
        self.tokenizer = Tokenizer()
        self.game_parser = GamePredicateParser(supported_games)

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

    def parse_block(self, block_text: str) -> ComponentHeader | None:
        """Tokenize a component block and read its header.

        Returns:
            The component header, or None if the block does not start with BEGIN
        """
        return self.parse_header(self.tokenizer.tokenize(block_text))

    def parse_header(self, tokens: list[Token]) -> ComponentHeader | None:
        """Read the component flags of a tokenized block.

        Returns:
            The component header, or None if the tokens do not start with BEGIN
        """
        if not tokens or tokens[0].type != TokenType.BEGIN:
            return None

        header = ComponentHeader(body_start=0)
        header.text_ref, header.text, pos = self._read_label(tokens, 1)
        # Some mods follow the label with a second, unused string.
        _, _, pos = self._read_label(tokens, pos)

        while (next_pos := self._read_flag(tokens, pos, header)) is not None:
            pos = next_pos

        header.body_start = pos
        return header

    # ------------------------------------------------------------------
    # Flags
    # ------------------------------------------------------------------

    def _read_flag(self, tokens: list[Token], pos: int, header: ComponentHeader) -> int | None:
        """Read the flag at pos into the header.

        Returns:
            The position after the flag, or None if pos is not a flag
        """
        flag = tokens[pos].type
        pos += 1

        if flag == TokenType.DESIGNATED:
            if tokens[pos].type == TokenType.NUMBER:
                header.designated = tokens[pos].value
            return self._skip_strings(tokens, pos, 1)

        if flag == TokenType.DEPRECATED:
            header.deprecated = True
            return self._skip_strings(tokens, pos, 1)

        if flag in (TokenType.SUBCOMPONENT, TokenType.FORCED_SUBCOMPONENT):
            header.subcomponent_ref, header.subcomponent_text, pos = self._read_label(
                tokens, pos
            )
            return self._skip_optional_predicate(tokens, pos)

        if flag == TokenType.GROUP:
            header.group, pos = self._read_name(tokens, pos)
            return self._skip_optional_predicate(tokens, pos)

        if flag == TokenType.LABEL:
            header.label, pos = self._read_name(tokens, pos)
            return pos

        if flag == TokenType.REQUIRE_PREDICATE:
            games, _ = self.game_parser.parse_from_tokens(tokens, pos)
            if games:
                header.games = games
            # A literal message may already be taken as an argument of the
            # predicate; a bare word is the body, not the message.
            pos = self._predicate_end(tokens, pos)
            if tokens[pos].type in (TokenType.STRING_REF, TokenType.STRING_LITERAL):
                pos += 1
            return pos

        if flag in self.COMPONENT_REQUIREMENT_KINDS:
            return self._read_component_requirement(tokens, pos, flag, header)

        if flag == TokenType.NO_LOG_RECORD:
            header.no_log_record = True

        if flag in self.STRING_ARGUMENT_COUNTS:
            return self._skip_strings(tokens, pos, self.STRING_ARGUMENT_COUNTS[flag])

        return None

    @staticmethod
    def _read_label(tokens: list[Token], pos: int) -> tuple[str | None, str | None, int]:
        """Read a label given as a string reference or a literal.

        Returns:
            The string reference, the literal text and the position after the label
        """
        token = tokens[pos]
        if token.type == TokenType.STRING_REF:
            return token.value, None, pos + 1
        if token.type == TokenType.STRING_LITERAL:
            return None, token.value, pos + 1
        return None, None, pos

    def _read_component_requirement(
        self, tokens: list[Token], pos: int, flag: TokenType, header: ComponentHeader
    ) -> int:
        """Read the tp2, component and message of a component flag into the header.

        A target given by a variable or a component that is not a number
        cannot be resolved: the flag is kept as unresolved.

        Returns:
            The position after the flag arguments
        """
        end = self._skip_strings(tokens, pos, self.STRING_ARGUMENT_COUNTS[flag])
        arguments = tokens[pos:end]

        target = self._read_component_target(arguments)
        if target is None:
            values = " ".join(token.source_text() for token in arguments)
            header.unresolved_requirements.append(f"{flag.name} {values}")
            return end

        text_ref, text = None, None
        if len(arguments) > 2:
            text_ref, text, _ = self._read_label(tokens, pos + 2)

        header.requirements.append(
            ComponentRequirement(
                kind=self.COMPONENT_REQUIREMENT_KINDS[flag],
                alternatives=(target,),
                source=RequirementSource.HEADER,
                text_ref=text_ref,
                text=text,
            )
        )
        return end

    @staticmethod
    def _read_component_target(arguments: list[Token]) -> ComponentTarget | None:
        """Read the tp2 and component number heading the arguments of a flag.

        Returns:
            The target, or None if either is missing or given by a variable
        """
        if len(arguments) < 2:
            return None

        tp2, component = arguments[0], arguments[1]
        if any(token.delimiter == "%" or "%" in token.value for token in (tp2, component)):
            return None
        if tp2.type == TokenType.STRING_REF or not tp2.value.strip():
            return None
        if not component.value.strip().isdigit():
            return None

        return ComponentTarget(tp2=tp2.value, component=component.value.strip())

    def _read_name(self, tokens: list[Token], pos: int) -> tuple[str | None, int]:
        """Read a name given as a literal or a bare word; a string reference is skipped."""
        token = tokens[pos]
        if token.type in (TokenType.STRING_LITERAL, TokenType.IDENTIFIER):
            return token.value, pos + 1
        return None, self._skip_strings(tokens, pos, 1)

    def _skip_strings(self, tokens: list[Token], pos: int, count: int) -> int:
        """Skip up to count String arguments."""
        for _ in range(count):
            if tokens[pos].type not in self.STRING_ARGUMENT_TYPES:
                break
            pos += 1
        return pos

    # ------------------------------------------------------------------
    # Predicate bounds
    # ------------------------------------------------------------------

    def _skip_optional_predicate(self, tokens: list[Token], pos: int) -> int:
        """Skip the predicate that may follow a SUBCOMPONENT or GROUP argument."""
        token = tokens[pos]
        starts_predicate = (
            token.type in (TokenType.LPAREN, TokenType.NOT)
            or self._predicate_argument_count(token) is not None
        )
        return self._predicate_end(tokens, pos) if starts_predicate else pos

    def _predicate_end(self, tokens: list[Token], pos: int) -> int:
        """Find the end of the predicate starting at pos, without evaluating it."""
        pos = self._term_end(tokens, pos)
        while tokens[pos].type in (TokenType.AND, TokenType.OR):
            pos = self._term_end(tokens, pos + 1)
        return pos

    def _term_end(self, tokens: list[Token], pos: int) -> int:
        """Find the end of a predicate term: a negation, a parenthesized group or a call."""
        while tokens[pos].type == TokenType.NOT:
            pos += 1

        token = tokens[pos]
        if token.type == TokenType.EOF:
            return pos

        if token.type == TokenType.LPAREN:
            return self._closing_parenthesis_end(tokens, pos)

        pos += 1
        argument_count = self._predicate_argument_count(token)
        if argument_count is not None:
            pos = self._skip_strings(tokens, pos, argument_count)

        return self._operands_end(tokens, pos)

    def _predicate_argument_count(self, token: Token) -> int | None:
        """Return the number of arguments of a predicate function, None if unknown."""
        if token.type in (TokenType.STRING_LITERAL, TokenType.STRING_REF, TokenType.NUMBER):
            return None
        return self.PREDICATE_ARGUMENT_COUNTS.get(token.value.upper())

    def _operands_end(self, tokens: list[Token], pos: int) -> int:
        """Skip the arguments and comparison operands that follow a term.

        The tokenizer drops symbolic operators ("=", "<", ">"), so an operand
        directly follows its term; "!=" leaves a NOT. Arguments that cannot
        be counted are taken as literals. String references are left out, as
        they are the message closing a REQUIRE_PREDICATE.
        """
        while True:
            token = tokens[pos]
            if token.type in (TokenType.STRING_LITERAL, TokenType.NUMBER):
                pos += 1
            elif token.type == TokenType.LPAREN:
                pos = self._closing_parenthesis_end(tokens, pos)
            elif token.type == TokenType.NOT or (
                token.type == TokenType.IDENTIFIER
                and token.value.upper() in self.PREDICATE_OPERATOR_NAMES
            ):
                pos = self._term_end(tokens, pos + 1)
            else:
                return pos

    @staticmethod
    def _closing_parenthesis_end(tokens: list[Token], pos: int) -> int:
        """Return the position after the parenthesis closing the one at pos."""
        depth = 0
        while tokens[pos].type != TokenType.EOF:
            if tokens[pos].type == TokenType.LPAREN:
                depth += 1
            elif tokens[pos].type == TokenType.RPAREN:
                depth -= 1
                if depth == 0:
                    return pos + 1
            pos += 1
        return pos


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

    def parse_file(self, path: str | Path, supported_games: set[str] | None = None) -> WeiDUTp2:
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
            return self.parse_string(content, file_path.stem, supported_games, file_path.parent)
        except Exception as e:
            logger.error(f"Failed to parse TP2 file: {file_path}", exc_info=True)
            raise Tp2ParseError(f"Error parsing {file_path}: {e}") from e

    def parse_string(
        self,
        tp2_content: str,
        tp2_name: str,
        supported_games: set[str] | None = None,
        parent: str | None = None,
    ) -> WeiDUTp2:
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
            self.supported_games = (
                supported_games if supported_games is not None else SUPPORTED_GAMES.copy()
            )
            clean_content = self._strip_comments(tp2_content)

            tp2 = WeiDUTp2()
            tp2.name = tp2_name

            self._extract_version(clean_content, tp2)
            self._extract_languages(clean_content, tp2, parent)
            self._parse_components(clean_content, tp2, self.supported_games)
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

    def _extract_languages(self, text: str, tp2: WeiDUTp2, parent: str) -> None:
        """Extract LANGUAGE declarations from TP2 content."""
        parser = LanguageParser()
        tp2.languages = parser.extract_languages(text, tp2.name, str(parent))

        if not tp2.languages:
            logger.warning("No LANGUAGE declarations found in TP2")

    @staticmethod
    def _parse_components(text: str, tp2: WeiDUTp2, supported_games: set[str]) -> None:
        """Parse all component declarations from TP2 content using robust token-based approach."""
        parser = ComponentParser(supported_games)
        blocks = parser.extract_blocks(text)

        logger.info(f"Extracted {len(blocks)} component blocks")

        deprecated_count = 0
        prev_designated = "-1"
        muc_groups: dict[str, MucComponent] = {}

        for block in blocks:
            try:
                header = parser.parse_block(block)

                if header is None:
                    logger.warning("Failed to parse component block")
                    continue

                if header.designated is not None:
                    designated = header.designated
                else:
                    # Ex: DESIGNATED 0010 (trap_overhaul)
                    width = len(prev_designated) if prev_designated.startswith("0") else None
                    value = int(prev_designated) + 1
                    designated = f"{value:0{width}d}" if width else str(value)

                prev_designated = designated

                if header.deprecated:
                    deprecated_count += 1
                    logger.debug("Skipping deprecated component block")
                    continue

                for flag in header.unresolved_requirements:
                    logger.warning(
                        f"{tp2.name}:{designated}: Skipping unresolved target: {flag}"
                    )

                component = Component(
                    designated=str(designated),
                    text_ref=header.text_ref,
                    text=header.text,
                    games=header.games,
                    requirements=header.requirements,
                    metadata={"no_log_record": True} if header.no_log_record else {},
                )

                if header.subcomponent_ref or header.subcomponent_text:
                    subcomponent_key = (
                        f"ref_{header.subcomponent_ref}"
                        if header.subcomponent_ref
                        else f"text_{header.subcomponent_text}"
                    )

                    if subcomponent_key not in muc_groups:
                        muc = MucComponent(
                            designated=f"choice_{len(muc_groups)}",
                            text_ref=header.subcomponent_ref,
                            text=header.subcomponent_text,
                            games=header.games,
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

    def _extract_tra_translations(
        self, tra_files: list[str], language_code: str
    ) -> dict[str, str]:
        """Extract translations from TRA files.

        Args:
            tra_files: List of TRA file paths
            language_code: Language of the files, which selects their encodings

        Returns:
            Dictionary mapping reference ID to translated text
        """
        translations: dict[str, str] = {}
        encodings = LANGUAGE_TEXT_ENCODINGS.get(language_code, TEXT_ENCODINGS)

        for tra_file in tra_files:
            tra_path = (self.base_dir / tra_file).resolve()

            try:
                content = safe_read(tra_path, encodings)
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
        languages = tp2.preferred_languages()
        translations: dict[str, dict[str, str]] = {
            lang.language_code: self._extract_tra_translations(
                lang.tra_files, lang.language_code
            )
            for lang in languages
        }

        fallback_langs = [code for code in ["en_US", "fr_FR", "de_DE"] if code in translations]

        for lang in languages:
            lang_code = lang.language_code
            lang_codes = [lang_code] + fallback_langs
            components: dict[str, str] = {}

            for component in tp2.components:
                self._process_component(component, components, lang_codes, translations)

            if components:
                tp2.component_translations[lang_code] = components

        # Without LANGUAGE, WeiDU shows the literal labels as they are
        if not tp2.languages:
            for component in tp2.components:
                self._process_component(component, tp2.untranslated_component_labels, [], {})

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
