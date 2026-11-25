"""WeiDU TP2 file parser for Infinity Engine mod installations.

This module provides parsing functionality for WeiDU TP2 (mod metadata) files,
extracting mod information including version, supported languages, components,
and translations.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum, auto
import logging
from pathlib import Path, PurePosixPath, PureWindowsPath
import platform
import re
import unicodedata

from constants import (
    TP2_FOLDER_LISTINGS_CACHE_SIZE,
    TP2_INCLUDE_MAX_DEPTH,
    TP2_PREDICATE_MAX_CLAUSES,
)
from core.ComponentRequirement import (
    ComponentRequirement,
    ComponentTarget,
    RequirementKind,
    RequirementSource,
    normalize_tp2_name,
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

_WEIDU_OS_NAME: str = "WEIDU_OS"
_MOD_FOLDER_NAME: str = "MOD_FOLDER"

SUPPORTED_GAMES: set[str] = {"bgee", "sod", "bg2ee", "eet", "iwdee", "pstee"}

# BWS names the classic BG1 "bg", WeiDU GAME_IS names it "bg1".
_BWS_BG1_GAME: str = "bg"
_WEIDU_BG1_GAME: str = "bg1"

# Games holding the content a GAME_INCLUDES value names, among the Enhanced
# Editions and the classic games BWS knows. EE names, which WeiDU does not
# document there, stand for the content of their base game.
_BG1_CONTENT_GAMES = frozenset({"bgee", "sod", "eet", "bg1", "bgt", "tutu"})
_BG2_CONTENT_GAMES = frozenset({"bg2ee", "eet", "bg2", "bgt"})
_IWD_CONTENT_GAMES = frozenset({"iwdee", "iwd"})
GAME_INCLUDES_GAMES: dict[str, frozenset[str]] = {
    "bg1": _BG1_CONTENT_GAMES,
    "totsc": _BG1_CONTENT_GAMES,
    "bgee": _BG1_CONTENT_GAMES,
    "sod": frozenset({"sod", "eet"}),
    "bg2": _BG2_CONTENT_GAMES,
    "soa": _BG2_CONTENT_GAMES,
    "tob": _BG2_CONTENT_GAMES,
    "bg2ee": _BG2_CONTENT_GAMES,
    "iwd": _IWD_CONTENT_GAMES,
    "iwd1": _IWD_CONTENT_GAMES,
    "how": _IWD_CONTENT_GAMES,
    "totlm": _IWD_CONTENT_GAMES,
    "pst": frozenset({"pstee", "pst"}),
}

# Label of a translation made for the Enhanced Editions, when the mod also
# declares one for the classic games ("Francais pour BG2EE", "pour les EE",
# "BG2EE/EET", "l'edition Enhanced"); "EE" is matched in capitals only.
RE_ENHANCED_EDITION_LABEL = re.compile(r"EE\b|\bEET\b|(?i:\benhanced\b)")

RE_VARIABLE_REFERENCE = re.compile(r'%([^%\s~"]+)%')
RE_VERSION_KEYWORD = re.compile(r"\bVERSION\b")
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
    # LANG_MAP spells names without accents: "français" is "francais".
    unaccented = unicodedata.normalize("NFKD", code_lower).encode("ascii", "ignore").decode()
    return LANG_MAP.get(code_lower) or LANG_MAP.get(unaccented, code)


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
        label: LABEL of the component, by which ID_OF_LABEL designates it
        games: Game codes (lowercase) the component installs on, None when
            unrestricted, empty when on no known game
        requirements: Requirements on other components, in declaration order
        metadata: Extensible metadata for future features (e.g., predicates)
    """

    designated: str
    text_ref: str | None = None
    text: str | None = None
    label: str | None = None
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

    def installable_components(self) -> list[Component]:
        """List the components that can be installed, the choices of a MUC in its place."""
        return [
            item
            for component in self.components
            for item in (
                component.components if isinstance(component, MucComponent) else [component]
            )
        ]

    def unlogged_components(self) -> set[str]:
        """Identify installable component numbers explicitly excluded from WeiDU.log."""
        return {
            item.designated
            for item in self.installable_components()
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


@dataclass(frozen=True, slots=True)
class GameRestriction:
    """Games on which a predicate can hold, and those on which it can fail.

    A condition on anything but the game can hold or fail on every game.
    Keeping both bounds lets a negation swap them instead of complementing
    a bound, which would exclude games where the predicate holds.

    Attributes:
        holds: Games on which the predicate can be true
        fails: Games on which the predicate can be false
    """

    holds: frozenset[str]
    fails: frozenset[str]


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
    INCLUDE = auto()
    INSTALL_BY_DEFAULT = auto()
    LABEL = auto()
    LPAREN = auto()
    METADATA = auto()
    MOD_IS_INSTALLED = auto()
    NOT = auto()
    NUMBER = auto()
    OR = auto()
    PATCH_IF = auto()
    PATCH_INCLUDE = auto()
    PRINT = auto()
    REQUIRE_COMPONENT = auto()
    REQUIRE_FILE = auto()
    REQUIRE_PREDICATE = auto()
    RPAREN = auto()
    SPRINT = auto()  # OUTER_SPRINT, OUTER_TEXT_SPRINT, SPRINT, TEXT_SPRINT
    STRING_LITERAL = auto()  # ~text~ or "text"
    STRING_REF = auto()  # @123
    SUBCOMPONENT = auto()
    THEN = auto()
    EOF = auto()


@dataclass(slots=True)
class Token:
    """Represents a parsed token from TP2 content."""

    type: TokenType
    value: str
    line: int
    column: int
    delimiter: str = ""  # Delimiter of a string literal: ~, ", % or ~~~~~

    def __repr__(self) -> str:
        return f"Token({self.type.name}, {self.value!r}, L{self.line}:C{self.column})"

    def source_text(self) -> str:
        """Return the token as written in the TP2, delimiters included."""
        if self.type == TokenType.STRING_REF:
            return f"@{self.value}"
        return f"{self.delimiter}{self.value}{self.delimiter}"


class Tokenizer:
    """Tokenizes TP2 component blocks into a stream of tokens.

    Symbols the parsers do not use ("=", "<", "+", ...) are skipped, as is
    an unclosed string delimiter.
    """

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
        "INCLUDE": TokenType.INCLUDE,
        "INSTALL_BY_DEFAULT": TokenType.INSTALL_BY_DEFAULT,
        "LABEL": TokenType.LABEL,
        "METADATA": TokenType.METADATA,
        "MOD_IS_INSTALLED": TokenType.MOD_IS_INSTALLED,
        "NOT": TokenType.NOT,
        "OR": TokenType.OR,
        "OUTER_SPRINT": TokenType.SPRINT,
        "OUTER_TEXT_SPRINT": TokenType.SPRINT,
        "PATCH_IF": TokenType.PATCH_IF,
        "PATCH_INCLUDE": TokenType.PATCH_INCLUDE,
        "PRINT": TokenType.PRINT,
        "REQUIRE_COMPONENT": TokenType.REQUIRE_COMPONENT,
        "REQUIRE_FILE": TokenType.REQUIRE_FILE,
        "REQUIRE_PREDICATE": TokenType.REQUIRE_PREDICATE,
        "SPRINT": TokenType.SPRINT,
        "SUBCOMPONENT": TokenType.SUBCOMPONENT,
        "TEXT_SPRINT": TokenType.SPRINT,
        "THEN": TokenType.THEN,
    }

    # A bare word may hold "#", "." and "-" (c#label, setup-mod.tp2); one
    # starting with digits is a word as soon as it holds a letter, "_" or "#"
    # (1pp.tp2), a number of its leading digits otherwise.
    TOKEN_PATTERN = re.compile(
        r"""
        ~~~~~(?P<long>.*?)~~~~~
      | ~(?P<tilde>[^~]*)~
      | "(?P<quote>[^"]*)"
      | %(?P<percent>[^%]*)%
      | @\s*(?P<reference>-?\d+)
      | (?P<or>\|\|?)
      | (?P<and>&&?)
      | (?P<digit_word>\d[\d.\-]*(?:[^\W\d]|\#)[\w.\-\#]*)
      | (?P<number>\d+)
      | (?P<word>[^\W\d][\w.\-\#]*)
      | (?P<lparen>\()
      | (?P<rparen>\))
      | (?P<not>!)
        """,
        re.DOTALL | re.VERBOSE,
    )
    STRING_DELIMITERS = {"long": "~~~~~", "tilde": "~", "quote": '"', "percent": "%"}
    SYMBOL_TYPES = {
        "or": TokenType.OR,
        "and": TokenType.AND,
        "digit_word": TokenType.IDENTIFIER,
        "number": TokenType.NUMBER,
        "lparen": TokenType.LPAREN,
        "rparen": TokenType.RPAREN,
        "not": TokenType.NOT,
    }

    def tokenize(self, text: str) -> list[Token]:
        """Tokenize TP2 text into a list of tokens, ended by an EOF token."""
        tokens: list[Token] = []
        line, line_start, scanned = 1, 0, 0

        for match in self.TOKEN_PATTERN.finditer(text):
            start = match.start()
            line, line_start = self._advance_lines(text, scanned, start, line, line_start)
            tokens.append(self._token(match, line, start - line_start))
            scanned = start

        line, line_start = self._advance_lines(text, scanned, len(text), line, line_start)
        tokens.append(Token(TokenType.EOF, "", line, len(text) - line_start))
        return tokens

    def _token(self, match: re.Match[str], line: int, column: int) -> Token:
        """Build the token of a match of TOKEN_PATTERN."""
        group = match.lastgroup
        if group in self.STRING_DELIMITERS:
            delimiter = self.STRING_DELIMITERS[group]
            return Token(TokenType.STRING_LITERAL, match[group], line, column, delimiter)
        if group == "reference":
            return Token(TokenType.STRING_REF, match[group], line, column)
        if group == "word":
            value = match[group]
            return Token(self.KEYWORDS.get(value, TokenType.IDENTIFIER), value, line, column)
        return Token(self.SYMBOL_TYPES[group], match[group], line, column)

    @staticmethod
    def _advance_lines(
        text: str, start: int, end: int, line: int, line_start: int
    ) -> tuple[int, int]:
        """Return the line and line start position after the newlines of text[start:end]."""
        newlines = text.count("\n", start, end)
        if newlines:
            line += newlines
            line_start = text.rfind("\n", start, end) + 1
        return line, line_start


class GamePredicateParser:
    """Reads the games GAME_IS and GAME_INCLUDES conditions restrict a component to.

    Conditions are evaluated over every game BWS knows, those of the mod
    included: a component made for a game the mod does not list keeps that
    game, instead of looking unrestricted.
    """

    # Tokens that end the predicate of a component flag.
    FLAG_TYPES = frozenset(
        {
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
        }
    )
    OPERATOR_TYPES = frozenset({TokenType.AND, TokenType.OR, TokenType.RPAREN})

    def __init__(self, supported_games: set[str] | None = None):
        """Initialize the parser with the games of the mod.

        Args:
            supported_games: Games of the mod, every Enhanced Edition when None
        """
        games = set(supported_games if supported_games is not None else SUPPORTED_GAMES)
        if _BWS_BG1_GAME in games:
            games.add(_WEIDU_BG1_GAME)

        self.supported_games: frozenset[str] = frozenset(games)
        self.known_games: frozenset[str] = frozenset(SUPPORTED_GAMES) | self.supported_games

    def parse_from_tokens(self, tokens: list[Token], pos: int) -> tuple[list[str] | None, int]:
        """Read the games a predicate restricts a component to.

        Returns:
            The games (see component_games) and the position after the predicate
        """
        holds, pos = self.read_games(tokens, pos)
        return self.component_games(holds), pos

    def read_games(self, tokens: list[Token], pos: int) -> tuple[frozenset[str], int]:
        """Read the known games on which a predicate can hold.

        Returns:
            The games and the position after the predicate
        """
        restriction, pos = self._parse_or_expression(tokens, pos)
        return restriction.holds, pos

    def component_games(self, holds: frozenset[str]) -> list[str] | None:
        """Turn the games on which the predicates of a component hold into its games.

        Returns:
            None when the component is unrestricted on the games of the mod;
            otherwise its games among those of the mod, or, when none of
            them, among the known games; an empty list when on no game
        """
        if holds >= self.supported_games:
            return None
        return sorted(holds & self.supported_games or holds)

    def _parse_or_expression(
        self, tokens: list[Token], pos: int
    ) -> tuple[GameRestriction, int]:
        """Parse OR expression (union of conditions)."""
        games, pos = self._parse_and_expression(tokens, pos)

        while pos < len(tokens) and tokens[pos].type == TokenType.OR:
            right_games, pos = self._parse_and_expression(tokens, pos + 1)
            games = GameRestriction(
                holds=games.holds | right_games.holds, fails=games.fails & right_games.fails
            )

        return games, pos

    def _parse_and_expression(
        self, tokens: list[Token], pos: int
    ) -> tuple[GameRestriction, int]:
        """Parse AND expression (intersection of conditions)."""
        games, pos = self._parse_primary_expression(tokens, pos)

        while pos < len(tokens) and tokens[pos].type == TokenType.AND:
            right_games, pos = self._parse_primary_expression(tokens, pos + 1)
            games = GameRestriction(
                holds=games.holds & right_games.holds, fails=games.fails | right_games.fails
            )

        return games, pos

    def _parse_primary_expression(
        self, tokens: list[Token], pos: int
    ) -> tuple[GameRestriction, int]:
        """Parse primary expression (NOT, parentheses, or condition)."""
        if pos >= len(tokens):
            return self._unrestricted(), pos

        token = tokens[pos]

        if token.type == TokenType.NOT:
            games, pos = self._parse_primary_expression(tokens, pos + 1)
            return GameRestriction(holds=games.fails, fails=games.holds), pos

        if token.type == TokenType.LPAREN:
            games, pos = self._parse_or_expression(tokens, pos + 1)
            # Skip closing parenthesis if present
            if pos < len(tokens) and tokens[pos].type == TokenType.RPAREN:
                pos += 1
            return games, pos

        if token.type in (TokenType.GAME_IS, TokenType.GAME_INCLUDES):
            games, pos = self.parse_game_condition(tokens, pos)
            if games is None:
                return self._unrestricted(), pos
            return self._game_condition(games), pos

        while pos < len(tokens):
            token_type = tokens[pos].type
            if token_type in self.OPERATOR_TYPES or token_type in self.FLAG_TYPES:
                break
            pos += 1

        return self._unrestricted(), pos

    def _game_condition(self, games: frozenset[str]) -> GameRestriction:
        """Return the bounds of a condition true on exactly these games."""
        return GameRestriction(holds=frozenset(games), fails=self.known_games - games)

    def _unrestricted(self) -> GameRestriction:
        """Return the bounds of a condition that does not depend on the game."""
        return GameRestriction(holds=self.known_games, fails=self.known_games)

    def parse_game_condition(
        self, tokens: list[Token], pos: int
    ) -> tuple[frozenset[str] | None, int]:
        """Read the GAME_IS or GAME_INCLUDES at pos into the known games satisfying it.

        Its argument is a literal or a bare word; one given by a variable
        cannot be read.

        Returns:
            The games, None if the argument cannot be read, and the position after it
        """
        keyword = tokens[pos].type
        if pos + 1 >= len(tokens):
            return None, pos + 1
        argument = tokens[pos + 1]
        if argument.type not in (TokenType.STRING_LITERAL, TokenType.IDENTIFIER):
            return None, pos + 1
        if argument.delimiter == "%" or "%" in argument.value:
            return None, pos + 2

        value = argument.value.lower()
        if keyword == TokenType.GAME_IS:
            return self._game_is_games(value), pos + 2
        game = value.strip()
        return GAME_INCLUDES_GAMES.get(game, frozenset({game})) & self.known_games, pos + 2

    def _game_is_games(self, value: str) -> frozenset[str]:
        """Return the known games a GAME_IS list names; bgee is BGEE with or without SoD."""
        games = set(value.split())
        if "bgee" in games:
            games.add("sod")
        return frozenset(games) & self.known_games


# ============================================================================
# Language Parser
# ============================================================================


class LanguageParser:
    """Extracts and parses LANGUAGE declarations from TP2 content."""

    LANGUAGE_START = re.compile(r"^\s*LANGUAGE\b", re.MULTILINE)
    BEGIN_START = re.compile(r"^\s*BEGIN\b", re.MULTILINE)

    def __init__(self):
        self.tokenizer = Tokenizer()

    def extract_languages(
        self, tp2_text: Tp2Text, variables: dict[str, str]
    ) -> list[LanguageDeclaration]:
        """Extract all LANGUAGE declarations from TP2 content.

        Args:
            tp2_text: TP2 content, whose code is searched for LANGUAGE
            variables: Variables WeiDU defines for every TP2, which the TRA
                paths may reference (%MOD_FOLDER%, %WEIDU_OS%)

        Returns:
            List of LanguageDeclaration objects
        """
        text, code = tp2_text.text, tp2_text.code
        language_positions = [match.start() for match in self.LANGUAGE_START.finditer(code)]

        if not language_positions:
            return []

        languages = []

        for i, lang_start in enumerate(language_positions):
            # Determine end position (next LANGUAGE or BEGIN, or EOF)
            if i + 1 < len(language_positions):
                lang_end = language_positions[i + 1]
            else:
                begin_match = self.BEGIN_START.search(code, lang_start)
                lang_end = begin_match.start() if begin_match else len(text)

            # Extract block content (skip "LANGUAGE" keyword itself)
            block_text = text[lang_start:lang_end]
            block_content = re.sub(r"^\s*LANGUAGE\s*", "", block_text)

            lang_decl = self._parse_block(block_content, variables, len(languages))
            if lang_decl:
                languages.append(lang_decl)

        return languages

    def _parse_block(
        self, block_content: str, variables: dict[str, str], index: int
    ) -> LanguageDeclaration | None:
        """Parse a single LANGUAGE block.

        Args:
            block_content: Content after LANGUAGE keyword
            variables: Variables the TRA paths may reference
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
            logger.warning("Invalid LANGUAGE block (need ≥2 values): %s...", block_content[:50])
            return None

        return LanguageDeclaration(
            display_name=values[0],
            language_code=values[1],
            tra_files=[expand_known_variables(tra_file, variables) for tra_file in values[2:]],
            index=index,
        )


# ============================================================================
# Requirement Predicate Parser
# ============================================================================


# WeiDU function giving the number of the component of a tp2 that declares a LABEL.
ID_OF_LABEL_FUNCTION = "ID_OF_LABEL"


def read_component_target(
    tokens: list[Token], pos: int, variables: dict[str, str] | None = None
) -> tuple[ComponentTarget | None, int]:
    """Read the tp2 and component starting at pos, as a flag or MOD_IS_INSTALLED names them.

    The component is a number, or "ID_OF_LABEL tp2 label", parenthesized or
    not, on the same tp2. A tp2 may reference the text variables known
    there; a component number or label may not.

    Args:
        tokens: Tokens holding the arguments
        pos: Index of the tp2 argument
        variables: Text variables known where the arguments are read

    Returns:
        The target, None if a tp2 references an unknown variable, the
        component is given by a variable or is not a number, or the label is
        that of another tp2; and the position after the arguments read
    """
    variables = variables or {}
    label_reference = _read_label_reference(tokens, pos + 1)
    if label_reference is not None:
        label_tp2, label, end = label_reference
        tp2 = _expand_tp2_name(tokens[pos], variables)
        label_tp2_name = _expand_tp2_name(label_tp2, variables)
        if tp2 is None or label_tp2_name is None or not _names_literal(label):
            return None, end
        if normalize_tp2_name(tp2) != normalize_tp2_name(label_tp2_name):
            return None, end
        return ComponentTarget(tp2=tp2, label=label.value.strip()), end

    end = min(pos + 2, len(tokens))
    if end - pos < 2:
        return None, end

    tp2, component = _expand_tp2_name(tokens[pos], variables), tokens[pos + 1]
    if tp2 is None or not _names_literal(component):
        return None, end
    if not component.value.strip().isdigit():
        return None, end

    return ComponentTarget(tp2=tp2, component=component.value.strip()), end


def _read_label_reference(tokens: list[Token], pos: int) -> tuple[Token, Token, int] | None:
    """Read the "ID_OF_LABEL tp2 label" at pos, possibly in parentheses.

    Returns:
        The tp2 and label tokens and the position after the reference, or
        None if pos does not start one
    """
    parenthesized = pos < len(tokens) and tokens[pos].type == TokenType.LPAREN
    start = pos + 1 if parenthesized else pos
    end = start + 3
    if end > len(tokens):
        return None
    function = tokens[start]
    if function.type != TokenType.IDENTIFIER or function.value.upper() != ID_OF_LABEL_FUNCTION:
        return None

    if parenthesized:
        if end >= len(tokens) or tokens[end].type != TokenType.RPAREN:
            return None
        end += 1

    return tokens[start + 1], tokens[start + 2], end


def _names_literal(token: Token) -> bool:
    """Tell whether a component or label argument is written out, not a variable."""
    if token.delimiter == "%" or "%" in token.value:
        return False
    return _is_name_argument(token) and bool(token.value.strip())


def _expand_tp2_name(token: Token, variables: dict[str, str]) -> str | None:
    """Return the tp2 name an argument gives, its variable references expanded.

    Returns:
        The tp2 name, or None if the argument is not a name or references an
        unknown variable
    """
    if not _is_name_argument(token):
        return None
    name = expand_variables(argument_text(token), variables)
    if name is None or not name.strip():
        return None
    return name


def _is_name_argument(token: Token) -> bool:
    """Tell whether a token can be a tp2, component or label argument."""
    return token.type in (TokenType.STRING_LITERAL, TokenType.IDENTIFIER, TokenType.NUMBER)


@dataclass(frozen=True, slots=True)
class ModCondition:
    """MOD_IS_INSTALLED on a resolved component."""

    target: ComponentTarget


@dataclass(frozen=True, slots=True)
class GameCondition:
    """GAME_IS or GAME_INCLUDES, as the supported games that satisfy it."""

    games: frozenset[str]


@dataclass(frozen=True, slots=True)
class OpaqueCondition:
    """A condition no requirement can express.

    Attributes:
        text: The condition as written in the TP2
        names_mod: Whether it tests a mod installation, so that leaving it
            out would lose a requirement
    """

    text: str
    names_mod: bool


@dataclass(frozen=True, slots=True)
class PredicateLiteral:
    """A condition, possibly negated; a negated game is stored as its complement."""

    condition: ModCondition | GameCondition | OpaqueCondition
    negated: bool = False


@dataclass(frozen=True, slots=True)
class PredicateConjunction:
    operands: tuple[PredicateNode, ...]


@dataclass(frozen=True, slots=True)
class PredicateDisjunction:
    operands: tuple[PredicateNode, ...]


PredicateNode = PredicateLiteral | PredicateConjunction | PredicateDisjunction
PredicateClause = tuple[PredicateLiteral, ...]


@dataclass
class PredicateRequirements:
    """Requirements read from a REQUIRE_PREDICATE.

    Attributes:
        requirements: Requirements of the component, in declaration order
        skipped: Reasons for the parts of the predicate left out
    """

    requirements: list[ComponentRequirement] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


class UntranslatablePredicateError(Exception):
    """A predicate, or one of its clauses, cannot be expressed as requirements."""


class RequirementPredicateParser:
    """Reads the MOD_IS_INSTALLED conditions of a REQUIRE_PREDICATE as requirements.

    The predicate is parsed with its negations pushed down to the conditions,
    then expanded into a conjunction of clauses. A clause of installed mods
    is a required requirement whose alternatives are the mods; a clause of a
    single absent mod is a forbidden one. A clause that also holds another
    condition only applies under that condition: it is kept when the games
    of the component never satisfy it, and skipped otherwise.
    """

    MESSAGE_TYPES = frozenset({TokenType.STRING_REF, TokenType.STRING_LITERAL})
    OPERATOR_TYPES = frozenset({TokenType.AND, TokenType.OR, TokenType.RPAREN})

    def __init__(self, game_parser: GamePredicateParser) -> None:
        self.game_parser = game_parser

    def read(
        self,
        tokens: list[Token],
        component_games: list[str] | None,
        variables: dict[str, str] | None = None,
    ) -> PredicateRequirements:
        """Read the requirements of a predicate.

        Args:
            tokens: The predicate, followed by its message when it has one
            component_games: Games of the component, None when unrestricted, empty
                when on no game
            variables: Text variables known before the component, which the
                tp2 of a MOD_IS_INSTALLED may reference

        Returns:
            The requirements and the reasons for the parts left out
        """
        result = PredicateRequirements()
        if not any(token.type == TokenType.MOD_IS_INSTALLED for token in tokens):
            return result

        source = "REQUIRE_PREDICATE " + " ".join(token.source_text() for token in tokens)
        games = (
            set(component_games)
            if component_games is not None
            else self.game_parser.supported_games
        )

        try:
            predicate, message = self._parse(tokens, variables or {})
            clauses = self._clauses(predicate)
        except UntranslatablePredicateError as error:
            result.skipped.append(f"{error}: {source}")
            return result

        text_ref, text = None, None
        if message is not None and message.type == TokenType.STRING_REF:
            text_ref = message.value
        elif message is not None:
            text = message.value

        for clause in clauses:
            try:
                requirement = self._clause_requirement(clause, games, text_ref, text)
            except UntranslatablePredicateError as error:
                result.skipped.append(f"{error}: {source}")
                continue
            if requirement is not None and requirement not in result.requirements:
                result.requirements.append(requirement)

        return result

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse(
        self, tokens: list[Token], variables: dict[str, str]
    ) -> tuple[PredicateNode, Token | None]:
        """Parse the predicate and find its message.

        Returns:
            The predicate and its message token, None when it has none
        """
        predicate, pos = self._parse_or(tokens, 0, variables, negated=False)

        if pos == len(tokens):
            return predicate, None
        if pos == len(tokens) - 1 and tokens[pos].type in self.MESSAGE_TYPES:
            return predicate, tokens[pos]
        raise UntranslatablePredicateError(f"unexpected {tokens[pos].source_text()!r}")

    def _parse_or(
        self, tokens: list[Token], pos: int, variables: dict[str, str], negated: bool
    ) -> tuple[PredicateNode, int]:
        operands = []
        operand, pos = self._parse_and(tokens, pos, variables, negated)
        operands.append(operand)

        while pos < len(tokens) and tokens[pos].type == TokenType.OR:
            operand, pos = self._parse_and(tokens, pos + 1, variables, negated)
            operands.append(operand)

        return self._combine(operands, conjunction=negated), pos

    def _parse_and(
        self, tokens: list[Token], pos: int, variables: dict[str, str], negated: bool
    ) -> tuple[PredicateNode, int]:
        operands = []
        operand, pos = self._parse_unary(tokens, pos, variables, negated)
        operands.append(operand)

        while pos < len(tokens) and tokens[pos].type == TokenType.AND:
            operand, pos = self._parse_unary(tokens, pos + 1, variables, negated)
            operands.append(operand)

        return self._combine(operands, conjunction=not negated), pos

    @staticmethod
    def _combine(operands: list[PredicateNode], conjunction: bool) -> PredicateNode:
        """Join operands; under a negation, De Morgan swaps AND and OR."""
        if len(operands) == 1:
            return operands[0]
        if conjunction:
            return PredicateConjunction(tuple(operands))
        return PredicateDisjunction(tuple(operands))

    def _parse_unary(
        self, tokens: list[Token], pos: int, variables: dict[str, str], negated: bool
    ) -> tuple[PredicateNode, int]:
        if pos >= len(tokens):
            raise UntranslatablePredicateError("incomplete predicate")

        token = tokens[pos]
        if token.type == TokenType.NOT:
            return self._parse_unary(tokens, pos + 1, variables, not negated)

        if token.type == TokenType.LPAREN:
            node, pos = self._parse_or(tokens, pos + 1, variables, negated)
            if pos >= len(tokens) or tokens[pos].type != TokenType.RPAREN:
                raise UntranslatablePredicateError("unbalanced parentheses")
            return node, pos + 1

        condition, end = self._read_condition(tokens, pos, variables)
        if not self._ends_term(tokens, end):
            # The condition is an operand of a comparison: "MOD_IS_INSTALLED a b = 0"
            end = self._opaque_term_end(tokens, end)
            condition = self._opaque(tokens, pos, end)

        if isinstance(condition, GameCondition) and negated:
            complement = self.game_parser.known_games - condition.games
            return PredicateLiteral(GameCondition(complement)), end
        return PredicateLiteral(condition, negated), end

    def _read_condition(
        self, tokens: list[Token], pos: int, variables: dict[str, str]
    ) -> tuple[ModCondition | GameCondition | OpaqueCondition, int]:
        """Read the condition at pos with its arguments.

        Returns:
            The condition and the position after it
        """
        token = tokens[pos]

        if token.type == TokenType.MOD_IS_INSTALLED:
            target, end = read_component_target(tokens, pos + 1, variables)
            if target is None:
                return self._opaque(tokens, pos, end), end
            return ModCondition(target), end

        if token.type in (TokenType.GAME_IS, TokenType.GAME_INCLUDES):
            games, end = self.game_parser.parse_game_condition(tokens, pos)
            if games is None:
                return self._opaque(tokens, pos, end), end
            return GameCondition(games), end

        end = self._opaque_term_end(tokens, pos)
        return self._opaque(tokens, pos, end), end

    def _ends_term(self, tokens: list[Token], pos: int) -> bool:
        """Tell whether a term may end at pos: an operator, the end or the message."""
        return (
            pos >= len(tokens)
            or tokens[pos].type in self.OPERATOR_TYPES
            or (pos == len(tokens) - 1 and tokens[pos].type in self.MESSAGE_TYPES)
        )

    def _opaque_term_end(self, tokens: list[Token], pos: int) -> int:
        """Find the end of a term the parser does not understand.

        A literal message after such a term cannot be told from its last
        argument: it is kept in the term, and the requirements get no message.
        """
        depth = 0
        while pos < len(tokens):
            token = tokens[pos]
            if depth == 0 and token.type in self.OPERATOR_TYPES:
                break
            if depth == 0 and token.type == TokenType.STRING_REF and pos == len(tokens) - 1:
                break
            if token.type == TokenType.LPAREN:
                depth += 1
            elif token.type == TokenType.RPAREN:
                depth -= 1
            pos += 1
        return pos

    @staticmethod
    def _opaque(tokens: list[Token], start: int, end: int) -> OpaqueCondition:
        term = tokens[start:end]
        return OpaqueCondition(
            text=" ".join(token.source_text() for token in term),
            names_mod=any(token.type == TokenType.MOD_IS_INSTALLED for token in term),
        )

    # ------------------------------------------------------------------
    # Clauses
    # ------------------------------------------------------------------

    def _clauses(self, node: PredicateNode) -> list[PredicateClause]:
        """Expand a predicate into a conjunction of clauses.

        Raises:
            UntranslatablePredicateError: If the expansion exceeds the clause limit
        """
        if isinstance(node, PredicateLiteral):
            return [(node,)]

        if isinstance(node, PredicateConjunction):
            clauses: list[PredicateClause] = []
            for operand in node.operands:
                clauses.extend(self._clauses(operand))
                self._check_clause_count(len(clauses))
            return clauses

        clauses = [()]
        for operand in node.operands:
            operand_clauses = self._clauses(operand)
            self._check_clause_count(len(clauses) * len(operand_clauses))
            clauses = [left + right for left in clauses for right in operand_clauses]
        return clauses

    @staticmethod
    def _check_clause_count(count: int) -> None:
        if count > TP2_PREDICATE_MAX_CLAUSES:
            raise UntranslatablePredicateError(f"more than {TP2_PREDICATE_MAX_CLAUSES} clauses")

    @staticmethod
    def _clause_requirement(
        clause: PredicateClause,
        component_games: set[str],
        text_ref: str | None,
        text: str | None,
    ) -> ComponentRequirement | None:
        """Turn a clause into a requirement.

        Returns:
            The requirement, or None if the clause tests no mod

        Raises:
            UntranslatablePredicateError: If the clause tests a mod in a way
                no requirement can express
        """
        conditions = [literal.condition for literal in clause]
        if any(isinstance(item, OpaqueCondition) and item.names_mod for item in conditions):
            raise UntranslatablePredicateError("unresolved MOD_IS_INSTALLED")

        mods = [literal for literal in clause if isinstance(literal.condition, ModCondition)]
        if not mods:
            return None

        clause_games = set().union(
            *(item.games for item in conditions if isinstance(item, GameCondition))
        )
        if clause_games & component_games:
            raise UntranslatablePredicateError("requirement depending on the game")
        if any(isinstance(item, OpaqueCondition) for item in conditions):
            raise UntranslatablePredicateError("requirement depending on another condition")

        targets = tuple(dict.fromkeys(literal.condition.target for literal in mods))
        negations = {literal.negated for literal in mods}

        if negations == {False}:
            kind = RequirementKind.REQUIRED
        elif negations == {True} and len(targets) == 1:
            kind = RequirementKind.FORBIDDEN
        else:
            raise UntranslatablePredicateError("alternative involving an absent mod")

        return ComponentRequirement(
            kind=kind,
            alternatives=targets,
            source=RequirementSource.PREDICATE,
            text_ref=text_ref,
            text=text,
        )


# ============================================================================
# Included Files
# ============================================================================


# Strings first, with the delimiters of WeiDU, so that a comment marker
# inside one is kept; an unclosed string is code, an unclosed comment or
# inline file runs to the end.
RE_COMMENT_OR_STRING = re.compile(
    r"""
    ~~~~~.*?~~~~~ | ~[^~]*~ | "[^"]*" | %[^%]*%
  | (?P<comment>
        //[^\n]*
      | /\*.*?(?:\*/|\Z)
      | <<<<<<<<.*?(?:>>>>>>>>|\Z)
    )
    """,
    re.DOTALL | re.VERBOSE,
)
_BLOCK_COMMENT_CLOSED_LENGTH = len("/**/")
_INLINE_FILE_CLOSED_LENGTH = len("<<<<<<<<>>>>>>>>")


def strip_comments(text: str) -> str:
    """Remove C-style and C++-style comments, and inline files, from TP2 code."""
    pieces = []
    code_start = 0
    for match in RE_COMMENT_OR_STRING.finditer(text):
        comment = match.group("comment")
        if comment is None:
            continue
        _warn_unclosed(comment)
        pieces.append(text[code_start : match.start()])
        code_start = match.end()

    pieces.append(text[code_start:])
    return "".join(pieces)


def _warn_unclosed(comment: str) -> None:
    """Warn about a block comment or an inline file running to the end of the TP2."""
    if comment.startswith("/*") and not (
        len(comment) >= _BLOCK_COMMENT_CLOSED_LENGTH and comment.endswith("*/")
    ):
        logger.warning("Unclosed block comment in WeiDU file")
    elif comment.startswith("<<<<<<<<") and not (
        len(comment) >= _INLINE_FILE_CLOSED_LENGTH and comment.endswith(">>>>>>>>")
    ):
        logger.warning("Unclosed inline file block in WeiDU file")


RE_STRING_LITERAL = re.compile(r'~~~~~.*?~~~~~|~[^~]*~|"[^"]*"|%[^%]*%', re.DOTALL)
RE_NOT_NEWLINE = re.compile(r"[^\n]")


@dataclass(frozen=True, slots=True)
class Tp2Text:
    """TP2 content without its comments, and the same code to search keywords in.

    In code, the contents of strings are blanked, their delimiters and
    newlines kept: a keyword found there is not inside a string (a PRINT
    message, a patched text), and its position is the same in text.

    Attributes:
        text: Content without comments
        code: Text with blanked string contents
    """

    text: str
    code: str

    @classmethod
    def from_content(cls, content: str) -> Tp2Text:
        """Remove the comments of content and blank its strings."""
        text = strip_comments(content)
        return cls(text, RE_STRING_LITERAL.sub(_blank_string, text))


def _blank_string(match: re.Match[str]) -> str:
    """Blank the contents of a string literal, keeping its delimiters and newlines."""
    literal = match.group()
    delimiter_length = len("~~~~~") if literal.startswith("~~~~~") else 1
    contents = RE_NOT_NEWLINE.sub(" ", literal[delimiter_length:-delimiter_length])
    return f"{literal[:delimiter_length]}{contents}{literal[-delimiter_length:]}"


def expand_variables(text: str, variables: dict[str, str]) -> str | None:
    """Replace the %name% references of text by the values of the variables.

    Returns:
        The expanded text, or None if it references an unknown variable
    """
    if any(name not in variables for name in RE_VARIABLE_REFERENCE.findall(text)):
        return None
    return RE_VARIABLE_REFERENCE.sub(lambda match: variables[match.group(1)], text)


def expand_known_variables(text: str, variables: dict[str, str]) -> str:
    """Replace the %name% references of text to known variables, keeping the others."""
    return RE_VARIABLE_REFERENCE.sub(
        lambda match: variables.get(match.group(1), match.group(0)), text
    )


class CaseInsensitivePathFinder:
    """Finds paths regardless of case as WeiDU on Windows, listing each folder once.

    Names absent with their own case are looked for in the listing of their
    folder. TP2 files look for many files that do not exist (generated at
    install time, or for another game) in a game folder that may hold
    thousands of mods: listing it again for each would dominate the parse.
    The listings are shared by all finders, each kept while the modification
    time of its folder, which an added or removed entry changes, is the same.
    """

    # Folder -> (modification time, lowercase name -> first spelling)
    _listings: dict[Path, tuple[int, dict[str, str]]] = {}

    def find(self, root: Path, relative: str) -> Path | None:
        """Find relative under root, matching each name regardless of case.

        Returns:
            The resolved path, or None if it does not exist, is absolute or
            cannot be reached (a name Windows rejects, an unreadable folder)
        """
        parts = PurePosixPath(relative.replace("\\", "/")).parts
        if not parts or parts[0] == "/" or PureWindowsPath(relative).drive:
            return None

        path = root
        try:
            for part in parts:
                candidate = path / part
                if not candidate.exists():
                    name = self._listing(path).get(part.lower())
                    if name is None:
                        return None
                    candidate = path / name
                path = candidate

            return path.resolve()
        except OSError as error:
            logger.debug("Cannot reach %s under %s: %s", relative, root, error)
            return None

    def _listing(self, folder: Path) -> dict[str, str]:
        """Map the lowercase names of the entries of folder to their first spelling.

        Raises:
            OSError: If folder is not a readable folder
        """
        modified = folder.stat().st_mtime_ns
        cached = self._listings.get(folder)
        if cached is not None and cached[0] == modified:
            return cached[1]

        listing: dict[str, str] = {}
        for entry in folder.iterdir():
            listing.setdefault(entry.name.lower(), entry.name)

        if len(self._listings) >= TP2_FOLDER_LISTINGS_CACHE_SIZE:
            self._listings.clear()
        self._listings[folder] = (modified, listing)
        return listing


def argument_text(token: Token) -> str:
    """Return the text of a String argument, keeping a %variable% reference whole."""
    if token.delimiter == "%":
        return f"%{token.value}%"
    return token.value


class IncludeReader:
    """Finds and reads the files the components of a TP2 include.

    WeiDU runs from the game folder: an included path is relative to it, and
    %MOD_FOLDER% names the mod folder inside it. The TP2 lies either in the
    mod folder (mymod/setup-mymod.tp2), or beside it in the game folder
    (setup-mymod.tp2 next to mymod/).
    """

    def __init__(self, tp2_dir: Path | None, tp2_name: str) -> None:
        """Initialize the reader of a TP2.

        Args:
            tp2_dir: Folder of the TP2, None when it was not read from disk
            tp2_name: Name of the TP2, with or without its setup- prefix
        """
        mod_name = normalize_tp2_name(tp2_name)
        self.game_dir: Path | None = None
        # Without the folder, the mod folder is taken to be named after the mod.
        self.variables: dict[str, str] = {
            _WEIDU_OS_NAME: get_os_code(),
            _MOD_FOLDER_NAME: mod_name,
        }
        self._tokenizer = Tokenizer()
        self._tokens: dict[Path, list[Token]] = {}
        self._path_finder = CaseInsensitivePathFinder()
        self._found: dict[str, Path | None] = {}

        if tp2_dir is None:
            return

        tp2_dir = tp2_dir.resolve()
        mod_dir = None
        if tp2_dir.name.lower() != mod_name:
            mod_dir = self._path_finder.find(tp2_dir, mod_name)

        if mod_dir is not None and mod_dir.is_dir():
            self.game_dir = tp2_dir
        else:
            self.game_dir, mod_dir = tp2_dir.parent, tp2_dir
        self.variables[_MOD_FOLDER_NAME] = mod_dir.name

    def find(self, path_text: str) -> Path | None:
        """Find the file an INCLUDE or a LANGUAGE names, its variables already expanded.

        Returns:
            The resolved file path, or None if there is no such file
        """
        if self.game_dir is None:
            return None
        if path_text not in self._found:
            path = self._path_finder.find(self.game_dir, path_text)
            self._found[path_text] = path if path is not None and path.is_file() else None
        return self._found[path_text]

    def read_tokens(self, path: Path) -> list[Token]:
        """Tokenize an included file once, without its comments.

        Raises:
            OSError: If the file cannot be read
        """
        if path not in self._tokens:
            self._tokens[path] = self._tokenizer.tokenize(strip_comments(safe_read(path)))
        return self._tokens[path]

    def describe(self, path: Path) -> str:
        """Return the path of an included file relative to the game folder."""
        if self.game_dir is not None and path.is_relative_to(self.game_dir):
            return path.relative_to(self.game_dir).as_posix()
        return path.as_posix()


# ============================================================================
# Component Body Scanner
# ============================================================================


@dataclass
class ComponentBody:
    """Requirements read from the code of a component.

    Attributes:
        requirements: Optional requirements, one per target, in order of appearance
        skipped: Reasons for the conditions and includes left out, each with its source
    """

    requirements: list[ComponentRequirement] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


class ComponentBodyScanner:
    """Finds the components the code of a component tests, without parsing it.

    Only the conditions of ACTION_IF and PATCH_IF are read, up to their THEN
    or BEGIN; the rest of the code is skipped. Each MOD_IS_INSTALLED there
    gives an optional requirement whatever its negation or branch: the code
    adapts to the target when it is installed, and does without otherwise.

    The files of INCLUDE and PATCH_INCLUDE are scanned the same way, where
    they are included. Their paths are expanded with the text variables
    defined so far by OUTER_SPRINT and the like; a variable set by other
    means is unknown, and the paths using it are skipped.
    """

    CONDITION_KEYWORDS = frozenset({TokenType.ACTION_IF, TokenType.PATCH_IF})
    CONDITION_ENDS = frozenset({TokenType.THEN, TokenType.BEGIN, TokenType.EOF})
    INCLUDE_KEYWORDS = frozenset({TokenType.INCLUDE, TokenType.PATCH_INCLUDE})
    VARIABLE_ARGUMENT_TYPES = frozenset(
        {TokenType.STRING_LITERAL, TokenType.IDENTIFIER, TokenType.NUMBER}
    )
    # Optional keyword between the name and the value of a SPRINT.
    EVALUATE_KEYWORD = "EVAL"

    def __init__(self, include_reader: IncludeReader | None = None) -> None:
        self.include_reader = include_reader

    def scan(
        self, tokens: list[Token], start: int, variables: dict[str, str] | None = None
    ) -> ComponentBody:
        """Read the optional requirements of the body starting at start.

        Args:
            tokens: Tokens of the component block
            start: Index of the first token of the body
            variables: Text variables known before the body; those the body
                defines are added to a copy
        """
        body = ComponentBody()
        self._scan(tokens, start, dict(variables or {}), (), body)
        body.requirements = list(dict.fromkeys(body.requirements))
        return body

    def define_variables(self, tokens: list[Token], variables: dict[str, str]) -> None:
        """Add to variables the text variables the tokens and their included files define.

        The conditions read on the way are left out: they apply to every
        component, not to one.
        """
        ignored = ComponentBody()
        self._scan(tokens, 0, variables, (), ignored)
        for reason in ignored.skipped:
            logger.debug("Variables not read from %s", reason)

    def _scan(
        self,
        tokens: list[Token],
        start: int,
        variables: dict[str, str],
        include_chain: tuple[Path, ...],
        body: ComponentBody,
    ) -> None:
        """Scan tokens reached through include_chain, defining variables as they come."""
        pos = start
        while pos < len(tokens):
            token_type = tokens[pos].type
            if token_type in self.CONDITION_KEYWORDS:
                end = self._condition_end(tokens, pos + 1)
                self._read_condition(
                    tokens[pos:end], variables, self._location(include_chain), body
                )
                pos = end
            elif token_type in self.INCLUDE_KEYWORDS:
                pos = self._follow_includes(tokens, pos, variables, include_chain, body)
            elif token_type == TokenType.SPRINT:
                pos = self._define_variable(tokens, pos + 1, variables)
            else:
                pos += 1

    def _condition_end(self, tokens: list[Token], pos: int) -> int:
        while tokens[pos].type not in self.CONDITION_ENDS:
            pos += 1
        return pos

    @staticmethod
    def _read_condition(
        condition: list[Token], variables: dict[str, str], location: str, body: ComponentBody
    ) -> None:
        """Add an optional requirement for each component the condition tests.

        A target tested several times is deduplicated once the body is scanned.
        """
        for pos, token in enumerate(condition):
            if token.type != TokenType.MOD_IS_INSTALLED:
                continue

            target, _ = read_component_target(condition, pos + 1, variables)
            if target is None:
                source = " ".join(item.source_text() for item in condition)
                body.skipped.append(f"unresolved MOD_IS_INSTALLED{location}: {source}")
                continue

            body.requirements.append(
                ComponentRequirement(
                    kind=RequirementKind.OPTIONAL,
                    alternatives=(target,),
                    source=RequirementSource.BODY,
                )
            )

    # ------------------------------------------------------------------
    # Included files
    # ------------------------------------------------------------------

    def _follow_includes(
        self,
        tokens: list[Token],
        pos: int,
        variables: dict[str, str],
        include_chain: tuple[Path, ...],
        body: ComponentBody,
    ) -> int:
        """Scan in turn each file of the INCLUDE at pos.

        Returns:
            The position after the list of files
        """
        keyword = tokens[pos].value
        location = self._location(include_chain)
        pos += 1

        if tokens[pos].type != TokenType.STRING_LITERAL:
            body.skipped.append(f"unresolved {keyword}{location}: {tokens[pos].source_text()}")
            return pos

        while tokens[pos].type == TokenType.STRING_LITERAL:
            reason = self._follow_include(tokens[pos], variables, include_chain, body)
            if reason is not None:
                body.skipped.append(
                    f"{reason} {keyword}{location}: {tokens[pos].source_text()}"
                )
            pos += 1

        return pos

    def _follow_include(
        self,
        path_token: Token,
        variables: dict[str, str],
        include_chain: tuple[Path, ...],
        body: ComponentBody,
    ) -> str | None:
        """Scan the file path_token names.

        Returns:
            Why the file was not scanned, or None once it is
        """
        if self.include_reader is None:
            return "unresolved"
        if len(include_chain) >= TP2_INCLUDE_MAX_DEPTH:
            return "too deeply nested"

        path_text = expand_variables(argument_text(path_token), variables)
        path = self.include_reader.find(path_text) if path_text is not None else None
        if path is None:
            return "unresolved"
        if path in include_chain:
            return "cyclic"

        try:
            tokens = self.include_reader.read_tokens(path)
        except OSError as error:
            logger.debug("Cannot read included file %s: %s", path, error)
            return "unreadable"

        self._scan(tokens, 0, variables, include_chain + (path,), body)
        return None

    def _location(self, include_chain: tuple[Path, ...]) -> str:
        """Describe the included file being scanned, empty in the TP2 itself."""
        if not include_chain or self.include_reader is None:
            return ""
        return f" in {self.include_reader.describe(include_chain[-1])}"

    # ------------------------------------------------------------------
    # Variables
    # ------------------------------------------------------------------

    def _define_variable(self, tokens: list[Token], pos: int, variables: dict[str, str]) -> int:
        """Read the name and value of the SPRINT whose arguments start at pos.

        A value referencing an unknown variable makes the variable unknown.

        Returns:
            The position after the arguments read
        """
        name = tokens[pos]
        if name.type not in self.VARIABLE_ARGUMENT_TYPES or name.delimiter == "%":
            return pos
        pos += 1

        if tokens[pos].value.upper() == self.EVALUATE_KEYWORD:
            pos += 1
        value = tokens[pos]
        if value.type not in self.VARIABLE_ARGUMENT_TYPES:
            variables.pop(name.value, None)
            return pos

        expanded = expand_variables(argument_text(value), variables)
        if expanded is None:
            variables.pop(name.value, None)
        else:
            variables[name.value] = expanded
        return pos + 1


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
        games: Games kept by all the REQUIRE_PREDICATE, None when unrestricted,
            empty when on no game
        no_log_record: Whether the component is excluded from WeiDU.log
        requirements: Requirements of the REQUIRE_COMPONENT and FORBID_COMPONENT
            flags and of the REQUIRE_PREDICATE, in declaration order
        skipped_requirements: Reasons for the requirements left out, each
            with its source, left for the caller to report
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
    skipped_requirements: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class HeaderPredicate:
    """A REQUIRE_PREDICATE of a header, read once the games of the component are known.

    Attributes:
        tokens: The predicate, followed by its message when it has one
        holds: Known games on which the predicate can hold
        requirement_index: Position of its requirements among those of the header
        skipped_index: Position of its skipped reasons among those of the header
    """

    tokens: list[Token]
    holds: frozenset[str]
    requirement_index: int
    skipped_index: int


@dataclass
class ComponentBlock:
    """A component block read from its header and its body.

    Attributes:
        header: Component flags of the block
        body: Optional requirements of the code, on targets the header does not name
    """

    header: ComponentHeader
    body: ComponentBody

    @property
    def requirements(self) -> list[ComponentRequirement]:
        """Requirements of the header, then those of the body."""
        return self.header.requirements + self.body.requirements

    @property
    def skipped_requirements(self) -> list[str]:
        """Reasons for the requirements left out in the header and the body."""
        return self.header.skipped_requirements + self.body.skipped


class ComponentParser:
    """Extracts component blocks from TP2 content and reads them.

    The header grammar is strict: WeiDU defines a closed list of component
    flags, each read with its arguments. The first token that is not a flag
    starts the body, which is only scanned for the components it tests.
    """

    BEGIN_PATTERN = re.compile(
        r"^\s*BEGIN\s+(?:@\s*-?\d+|~[^~]*~(?!\s*=)|\"[^\"]*\"(?!\s*=))",
        re.MULTILINE,
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

    def __init__(
        self,
        supported_games: set[str] | None = None,
        include_reader: IncludeReader | None = None,
    ):
        self.tokenizer = Tokenizer()
        self.game_parser = GamePredicateParser(supported_games)
        self.predicate_parser = RequirementPredicateParser(self.game_parser)
        self.body_scanner = ComponentBodyScanner(include_reader)

    def split_components(self, tp2_text: Tp2Text) -> tuple[str, list[str]]:
        """Split TP2 content into its prelude and its component blocks.

        A component starts with a BEGIN and its label at the start of a line
        of code; a BEGIN inside a string does not start one.

        Returns:
            The text before the first component, and each component block
            from its BEGIN
        """
        text = tp2_text.text
        starts = [match.start() for match in self.BEGIN_PATTERN.finditer(tp2_text.code)]
        if not starts:
            return text, []

        ends = [*starts[1:], len(text)]
        blocks = [text[start:end].strip() for start, end in zip(starts, ends, strict=True)]
        return text[: starts[0]], [block for block in blocks if block]

    def read_prelude_variables(self, prelude: str, variables: dict[str, str]) -> dict[str, str]:
        """Complete variables with those defined before the first component, as in ALWAYS.

        Args:
            prelude: TP2 content before the first component
            variables: Variables WeiDU defines for every TP2

        Returns:
            A new dictionary of the variables known to every component
        """
        prelude_variables = dict(variables)
        self.body_scanner.define_variables(self.tokenizer.tokenize(prelude), prelude_variables)
        return prelude_variables

    def parse_block(
        self, block_text: str, variables: dict[str, str] | None = None
    ) -> ComponentBlock | None:
        """Tokenize a component block and read its header and body.

        A target the header already requires or forbids is not repeated
        as an optional requirement of the body.

        Args:
            block_text: Component block, from its BEGIN
            variables: Text variables known before the block

        Returns:
            The component block, or None if it does not start with BEGIN
        """
        tokens = self.tokenizer.tokenize(block_text)
        header = self.parse_header(tokens, variables)
        if header is None:
            return None

        body = self.body_scanner.scan(tokens, header.body_start, variables)
        header_targets = {
            target for requirement in header.requirements for target in requirement.alternatives
        }
        body.requirements = [
            requirement
            for requirement in body.requirements
            if requirement.alternatives[0] not in header_targets
        ]

        return ComponentBlock(header, body)

    def parse_header(
        self, tokens: list[Token], variables: dict[str, str] | None = None
    ) -> ComponentHeader | None:
        """Read the component flags of a tokenized block.

        Args:
            tokens: Tokens of the block, from its BEGIN
            variables: Text variables known before the block, which the tp2
                of a requirement may reference

        Returns:
            The component header, or None if the tokens do not start with BEGIN
        """
        if not tokens or tokens[0].type != TokenType.BEGIN:
            return None

        header = ComponentHeader(body_start=0)
        predicates: list[HeaderPredicate] = []
        header.text_ref, header.text, pos = self._read_label(tokens, 1)
        # Some mods follow the label with a second, unused string.
        _, _, pos = self._read_label(tokens, pos)

        variables = variables or {}
        while (
            next_pos := self._read_flag(tokens, pos, header, predicates, variables)
        ) is not None:
            pos = next_pos

        header.body_start = pos
        self._read_predicates(header, predicates, variables)
        return header

    def _read_predicates(
        self,
        header: ComponentHeader,
        predicates: list[HeaderPredicate],
        variables: dict[str, str],
    ) -> None:
        """Restrict the component to the games of all its predicates, then read their requirements.

        A requirement depends on the games of the whole component, not only
        on those of its own predicate: the predicates are read last, their
        requirements put back in declaration order.
        """
        if not predicates:
            return

        holds = frozenset.intersection(*(predicate.holds for predicate in predicates))
        header.games = self.game_parser.component_games(holds)

        for predicate in reversed(predicates):
            result = self.predicate_parser.read(predicate.tokens, header.games, variables)
            index = predicate.requirement_index
            header.requirements[index:index] = result.requirements
            index = predicate.skipped_index
            header.skipped_requirements[index:index] = result.skipped

    # ------------------------------------------------------------------
    # Flags
    # ------------------------------------------------------------------

    def _read_flag(
        self,
        tokens: list[Token],
        pos: int,
        header: ComponentHeader,
        predicates: list[HeaderPredicate],
        variables: dict[str, str],
    ) -> int | None:
        """Read the flag at pos into the header, or into predicates for a REQUIRE_PREDICATE.

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
            holds, _ = self.game_parser.read_games(tokens, pos)
            # A literal message may already be taken as an argument of the
            # predicate; a bare word is the body, not the message.
            start, pos = pos, self._predicate_end(tokens, pos)
            if tokens[pos].type in (TokenType.STRING_REF, TokenType.STRING_LITERAL):
                pos += 1
            predicates.append(
                HeaderPredicate(
                    tokens=tokens[start:pos],
                    holds=holds,
                    requirement_index=len(header.requirements),
                    skipped_index=len(header.skipped_requirements),
                )
            )
            return pos

        if flag in self.COMPONENT_REQUIREMENT_KINDS:
            return self._read_component_requirement(tokens, pos, flag, header, variables)

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
        self,
        tokens: list[Token],
        pos: int,
        flag: TokenType,
        header: ComponentHeader,
        variables: dict[str, str],
    ) -> int:
        """Read the tp2, component and message of a component flag into the header.

        A target given by a variable or a component that is not a number
        cannot be resolved: the flag is kept as unresolved.

        Returns:
            The position after the flag arguments
        """
        end = self._skip_strings(tokens, pos, self.STRING_ARGUMENT_COUNTS[flag])
        arguments = tokens[pos:end]

        target, _ = read_component_target(arguments, 0, variables)
        if target is None:
            values = " ".join(token.source_text() for token in arguments)
            header.skipped_requirements.append(f"unresolved target: {flag.name} {values}")
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
            base_dir: Directory relative paths of TP2 files are given from,
                and TRA files are looked for in when not found in the game folder
        """
        self.base_dir = Path(base_dir).resolve()
        if not self.base_dir.is_dir():
            raise ValueError(f"Base directory does not exist: {self.base_dir}")

        logger.info(f"Initialized TP2 parser with base_dir: {self.base_dir}")

    def parse_file(self, path: str | Path, supported_games: set[str] | None = None) -> WeiDUTp2:
        """Parse a TP2 file from disk.

        Args:
            path: Path to TP2 file (absolute or relative to base_dir)
            supported_games: Games of the mod, every Enhanced Edition when None

        Returns:
            Parsed WeiDUTp2 object

        Raises:
            Tp2FileNotFoundError: If file doesn't exist
            Tp2ParseError: If the file cannot be read or parsed
        """
        file_path = Path(path)
        if not file_path.is_absolute():
            file_path = self.base_dir / file_path

        file_path = file_path.resolve()

        if not file_path.exists():
            raise Tp2FileNotFoundError(f"TP2 file not found: {file_path}")

        logger.info(f"Parsing TP2 file: {file_path}")

        try:
            content = safe_read(file_path)
        except OSError as e:
            logger.error(f"Failed to read TP2 file: {file_path}", exc_info=True)
            raise Tp2ParseError(f"Error reading {file_path}: {e}") from e

        return self.parse_string(content, file_path.stem, supported_games, file_path.parent)

    def parse_string(
        self,
        tp2_content: str,
        tp2_name: str,
        supported_games: set[str] | None = None,
        parent: str | Path | None = None,
    ) -> WeiDUTp2:
        """Parse TP2 content from a string.

        Args:
            tp2_content: Raw TP2 file content
            tp2_name: Name of the mod
            supported_games: Games of the mod, every Enhanced Edition when None
            parent: Folder of the TP2, from which included and TRA files are
                found; None when the TP2 was not read from disk

        Returns:
            Parsed WeiDUTp2 object

        Raises:
            Tp2ParseError: If parsing fails
        """
        try:
            tp2_text = Tp2Text.from_content(tp2_content)
            include_reader = IncludeReader(Path(parent) if parent else None, tp2_name)

            tp2 = WeiDUTp2()
            tp2.name = tp2_name

            self._extract_version(tp2_text, tp2)
            self._extract_languages(tp2_text, tp2, include_reader)
            self._parse_components(tp2_text, tp2, supported_games, include_reader)
            self._resolve_own_labels(tp2)
            self._build_translations(tp2, include_reader)

            logger.info(
                f"Successfully parsed TP2: version={tp2.version}, "
                f"languages={len(tp2.languages)}, components={len(tp2.components)}"
            )

            return tp2

        except Exception as e:
            logger.error(f"Failed to parse TP2 {tp2_name}", exc_info=True)
            raise Tp2ParseError(f"Error parsing {tp2_name}: {e}") from e

    @staticmethod
    def _extract_version(tp2_text: Tp2Text, tp2: WeiDUTp2) -> None:
        """Extract the first VERSION declaration of the TP2 code."""
        for keyword in RE_VERSION_KEYWORD.finditer(tp2_text.code):
            match = RE_VERSION.match(tp2_text.text, keyword.start())
            if match:
                tp2.version = match.group(1)
                return

        logger.warning("No VERSION declaration found in TP2")

    @staticmethod
    def _extract_languages(
        tp2_text: Tp2Text, tp2: WeiDUTp2, include_reader: IncludeReader
    ) -> None:
        """Extract LANGUAGE declarations from TP2 content."""
        parser = LanguageParser()
        tp2.languages = parser.extract_languages(tp2_text, include_reader.variables)

        if not tp2.languages:
            logger.warning("No LANGUAGE declarations found in TP2")

    @staticmethod
    def _parse_components(
        tp2_text: Tp2Text,
        tp2: WeiDUTp2,
        supported_games: set[str] | None,
        include_reader: IncludeReader,
    ) -> None:
        """Parse all component declarations from TP2 content using robust token-based approach."""
        parser = ComponentParser(supported_games, include_reader)
        prelude, blocks = parser.split_components(tp2_text)
        variables = parser.read_prelude_variables(prelude, include_reader.variables)

        logger.info(f"Extracted {len(blocks)} component blocks")

        deprecated_count = 0
        prev_designated = "-1"
        muc_groups: dict[str, MucComponent] = {}

        for block in blocks:
            try:
                parsed_block = parser.parse_block(block, variables)

                if parsed_block is None:
                    logger.warning("Failed to parse component block")
                    continue

                header = parsed_block.header

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

                for reason in parsed_block.skipped_requirements:
                    logger.warning("%s:%s: Skipping %s", tp2.name, designated, reason)

                component = Component(
                    designated=str(designated),
                    text_ref=header.text_ref,
                    text=header.text,
                    label=header.label,
                    games=header.games,
                    requirements=parsed_block.requirements,
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
                logger.error("Error parsing component: %s", e, exc_info=True)
                continue

        logger.info(f"Parsed {len(tp2.components)} top-level components")
        if deprecated_count > 0:
            logger.info(f"Skipped {deprecated_count} deprecated component(s)")

    @classmethod
    def _resolve_own_labels(cls, tp2: WeiDUTp2) -> None:
        """Name by their number the targets that a label of the mod itself designates.

        A requirement whose label no component declares is skipped. A target
        of another mod keeps its label, for the catalogue to resolve.
        """
        own_tp2 = normalize_tp2_name(tp2.name or "")
        components = tp2.installable_components()
        numbers: dict[str, str] = {}
        for component in components:
            if component.label is not None:
                numbers.setdefault(component.label, component.designated)

        for component in components:
            requirements = []
            for requirement in component.requirements:
                alternatives = cls._resolve_alternatives(requirement, own_tp2, numbers)
                if alternatives is None:
                    labels = [
                        target.label for target in requirement.alternatives if target.label
                    ]
                    logger.warning(
                        "%s:%s: Skipping %s requirement on a label no component declares: %s",
                        tp2.name,
                        component.designated,
                        requirement.kind.name.lower(),
                        ", ".join(labels),
                    )
                    continue
                requirements.append(replace(requirement, alternatives=alternatives))
            component.requirements = cls._without_repeated_optional(requirements)

    @staticmethod
    def _resolve_alternatives(
        requirement: ComponentRequirement, own_tp2: str, numbers: dict[str, str]
    ) -> tuple[ComponentTarget, ...] | None:
        """Return the alternatives of a requirement, those of the mod named by their number.

        Returns:
            The alternatives, or None if one names a label no component declares
        """
        alternatives = []
        for target in requirement.alternatives:
            if target.label is None or target.tp2 != own_tp2:
                alternatives.append(target)
            elif target.label in numbers:
                alternatives.append(target.with_component(numbers[target.label]))
            else:
                return None
        return tuple(alternatives)

    @staticmethod
    def _without_repeated_optional(
        requirements: list[ComponentRequirement],
    ) -> list[ComponentRequirement]:
        """Drop the optional requirements a label made identical to another requirement.

        A target named by its label in one place and by its number in another
        is only known to be the same once the labels are resolved.
        """
        named_targets = {
            target
            for requirement in requirements
            if requirement.kind != RequirementKind.OPTIONAL
            for target in requirement.alternatives
        }
        kept: list[ComponentRequirement] = []
        for requirement in requirements:
            if requirement.kind == RequirementKind.OPTIONAL and (
                requirement.alternatives[0] in named_targets or requirement in kept
            ):
                continue
            kept.append(requirement)
        return kept

    def _extract_tra_translations(
        self, tra_files: list[str], language_code: str, include_reader: IncludeReader
    ) -> dict[str, str]:
        """Extract translations from TRA files.

        Args:
            tra_files: TRA file paths, relative to the game folder
            language_code: Language of the files, which selects their encodings
            include_reader: Finds the files in the game folder

        Returns:
            Dictionary mapping reference ID to translated text
        """
        translations: dict[str, str] = {}
        encodings = LANGUAGE_TEXT_ENCODINGS.get(language_code, TEXT_ENCODINGS)

        for tra_file in tra_files:
            tra_path = include_reader.find(tra_file) or (self.base_dir / tra_file).resolve()

            try:
                content = strip_comments(safe_read(tra_path, encodings))
                for match in RE_TRA_TRANSLATION.finditer(content):
                    text = next(
                        match.group(group)
                        for group in ("text_tilde5", "text_tilde", "text_quote")
                        if match.group(group) is not None
                    )
                    translations[match.group("id")] = text.strip()

            except Exception as e:
                logger.error("Error reading TRA file %s: %s", tra_path, e, exc_info=True)

        return translations

    def _build_translations(self, tp2: WeiDUTp2, include_reader: IncludeReader) -> None:
        """Build translation dictionary for all languages."""
        languages = tp2.preferred_languages()
        translations: dict[str, dict[str, str]] = {
            lang.language_code: self._extract_tra_translations(
                lang.tra_files, lang.language_code, include_reader
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
