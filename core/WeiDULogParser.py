"""
WeiDU Log Parser - Parse and extract information from WeiDU.log files.

This module provides utilities to read and interpret WeiDU.log files,
which contain the installation history of Infinity Engine mods.
WeiDU.log format follows a specific structure that this parser handles.
"""

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Generator

logger = logging.getLogger(__name__)


# ============================================================================
# Data Models
# ============================================================================

@dataclass(frozen=True, slots=True)
class WeiDULogEntry:
    """Single entry from a WeiDU.log file.

    Represents one installed mod component with all its metadata.

    Attributes:
        mod_name: Mod identifier (lowercase, extracted from TP2 filename)
        component_number: Component number as string
        language: Language code for the installation
        full_line: Original line from WeiDU.log (for debugging)
        line_number: Line number in the file (1-based)
    """

    mod_name: str
    component_number: str
    language: str
    full_line: str
    line_number: int

    @property
    def component_id(self) -> str:
        """Get component ID in format 'mod:component'.

        Returns:
            Component identifier string
        """
        return f"{self.mod_name}:{self.component_number}"

    def __str__(self) -> str:
        """String representation for logging."""
        return f"[{self.line_number}] {self.component_id} (lang:{self.language})"


@dataclass
class WeiDULogParseResult:
    """Result of parsing a WeiDU.log file.

    Contains all successfully parsed entries and any errors encountered.

    Attributes:
        entries: List of successfully parsed log entries in order
        errors: List of error messages for lines that couldn't be parsed
        file_path: Path to the parsed file
    """

    entries: list[WeiDULogEntry]
    errors: list[str]
    file_path: Path

    @property
    def entry_count(self) -> int:
        """Get number of successfully parsed entries.

        Returns:
            Count of entries
        """
        return len(self.entries)

    @property
    def error_count(self) -> int:
        """Get number of parsing errors.

        Returns:
            Count of errors
        """
        return len(self.errors)

    @property
    def is_valid(self) -> bool:
        """Check if parsing was successful.

        Returns:
            True if at least one entry parsed and no critical errors
        """
        return self.entry_count > 0

    def get_component_ids(self) -> list[str]:
        """Get list of component IDs in installation order.

        Returns:
            List of component IDs in format 'mod:component'
        """
        return [entry.component_id for entry in self.entries]

    def get_unique_mods(self) -> list[str]:
        """Get list of unique mod names that were installed.

        Returns:
            List of unique mod identifiers in order of first appearance
        """
        seen = set()
        mods = []
        for entry in self.entries:
            if entry.mod_name not in seen:
                seen.add(entry.mod_name)
                mods.append(entry.mod_name)
        return mods


# ============================================================================
# WeiDU Log Parser
# ============================================================================

class WeiDULogParser:
    """Parser for WeiDU.log files.

    WeiDU.log format:
    - Comment lines start with '//'
    - Installation entries: ~MOD/FILE.TP2~ #language #component // Description
    - Entries are in chronological installation order

    Example log entry:
        ~ASCENSION/ASCENSION.TP2~ #0 #0 // Ascension v1.41 (Gebhardt Barthel)
    """

    # Regex pattern to match WeiDU.log entries
    # Format: ~MOD/FILE.TP2~ #lang #comp // Description
    ENTRY_PATTERN = re.compile(
        r'~(?P<tp2_path>[^~]+)~\s+'  # TP2 path between tildes
        r'#(?P<language>\d+)\s+'  # Language number
        r'#(?P<component>\d+)'  # Component number
        r'(?:\s+//\s*(?P<description>.*))?$'  # Optional description
    )

    # Default encoding for WeiDU.log files
    DEFAULT_ENCODING = 'utf-8'

    # Fallback encodings to try if UTF-8 fails
    FALLBACK_ENCODINGS = ['latin-1', 'cp1252', 'iso-8859-1']

    def __init__(self):
        """Initialize WeiDU log parser."""
        self._current_file: Path | None = None

    def parse_file(self, file_path: str | Path) -> WeiDULogParseResult:
        """Parse a WeiDU.log file.

        Args:
            file_path: Path to WeiDU.log file

        Returns:
            Parse result with entries and errors

        Raises:
            FileNotFoundError: If file doesn't exist
            PermissionError: If file can't be read
        """
        path = Path(file_path)

        logger.info(f"Parsing WeiDU.log: {path}")

        entries = []
        errors = []

        try:
            for entry in self.iter_entries(path):
                entries.append(entry)
        except Exception as e:
            errors.append(f"Fatal error: {type(e).__name__}: {e}")
            logger.error(f"Error during parsing: {e}")

        result = WeiDULogParseResult(entries, errors, path)

        logger.info(
            f"Parsed {result.entry_count} entries with {result.error_count} errors"
        )

        return result

    def _read_file_with_encoding(self, path: Path) -> str:
        """Read file trying multiple encodings.

        Args:
            path: File path

        Returns:
            File content as string

        Raises:
            UnicodeDecodeError: If all encodings fail
        """
        # Try UTF-8 first
        try:
            return path.read_text(encoding=self.DEFAULT_ENCODING)
        except UnicodeDecodeError:
            logger.debug(f"UTF-8 failed, trying fallback encodings")

        # Try fallback encodings
        for encoding in self.FALLBACK_ENCODINGS:
            try:
                content = path.read_text(encoding=encoding)
                logger.info(f"Successfully read file with {encoding} encoding")
                return content
            except UnicodeDecodeError:
                continue

        # If all fail, read with errors='ignore'
        logger.warning("All encodings failed, reading with error replacement")
        return path.read_text(encoding=self.DEFAULT_ENCODING, errors='ignore')

    def _parse_line(self, line: str, line_num: int) -> WeiDULogEntry | None:
        """Parse a single line from WeiDU.log.

        Args:
            line: Line content
            line_num: Line number (1-based)

        Returns:
            Parsed entry or None if line doesn't match pattern
        """
        match = self.ENTRY_PATTERN.match(line)
        if not match:
            return None

        # Extract TP2 path and get mod name
        tp2_path = match.group('tp2_path')
        mod_name = self._extract_mod_name(tp2_path)

        if not mod_name:
            logger.debug(f"Could not extract mod name from: {tp2_path}")
            return None

        return WeiDULogEntry(
            mod_name=mod_name,
            component_number=match.group('component'),
            language=match.group('language'),
            full_line=line,
            line_number=line_num
        )

    def _extract_mod_name(self, tp2_path: str) -> str:
        """Extract mod name from TP2 path.

        Args:
            tp2_path: Path from WeiDU.log (e.g., "ASCENSION/ASCENSION.TP2")

        Returns:
            Mod name in lowercase or empty string if invalid
        """
        # Remove any leading/trailing whitespace
        tp2_path = tp2_path.strip()

        # Split by forward or backward slash
        parts = re.split(r'[/\\]', tp2_path)

        if len(parts) < 2:
            # Invalid format, try to extract filename
            if parts:
                filename = parts[0]
                mod_name = filename.replace('.TP2', '').replace('.tp2', '')
                return mod_name.lower()
            return ""

        # First part is typically the mod folder
        mod_folder = parts[0]

        # Normalize to lowercase
        return mod_folder.lower()

    def parse_file_simple(self, file_path: str | Path) -> list[str]:
        """Parse WeiDU.log and return simple list of component IDs.

        Convenience method that returns just the component IDs without
        full parse result details.

        Args:
            file_path: Path to WeiDU.log file

        Returns:
            List of component IDs in format 'mod:component'

        Raises:
            FileNotFoundError: If file doesn't exist
            ValueError: If parsing fails or file is empty
        """
        result = self.parse_file(file_path)

        if not result.is_valid:
            raise ValueError(
                f"Failed to parse WeiDU.log: {result.error_count} errors"
            )

        return result.get_component_ids()

    def iter_entries(self, file_path: str | Path) -> Generator[WeiDULogEntry, None, None]:
        """Iterate over entries in WeiDU.log without loading all in memory.

        Useful for very large log files.

        Args:
            file_path: Path to WeiDU.log file

        Yields:
            WeiDULogEntry objects in order

        Raises:
            FileNotFoundError: If file doesn't exist
        """
        path = Path(file_path)
        self._current_file = path

        if not path.exists():
            raise FileNotFoundError(f"WeiDU.log not found: {path}")

        if not path.is_file():
            raise ValueError(f"Path is not a file: {path}")

        content = self._read_file_with_encoding(path)

        for line_num, line in enumerate(content.splitlines(), start=1):
            line = line.strip()

            # Skip empty lines and comments
            if not line or line.startswith('//'):
                continue

            try:
                entry = self._parse_line(line, line_num)
                if entry:
                    yield entry
            except Exception as e:
                logger.debug(f"Skipping line {line_num}: {e}")
                continue
