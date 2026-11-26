"""Read the outcome of each component from a WeiDU DEBUG file."""

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
import logging
from pathlib import Path
import re

from constants import DEBUG_MESSAGES_LIMIT
from core.weidu_types import DEFAULT_STRINGS, ComponentStatus

logger = logging.getLogger(__name__)

# English words of the diagnostics printed by WeiDU and by mods
_DIAGNOSTIC_WORDS = ("warning", "error", "near text")


@dataclass
class DebugReport:
    """What one DEBUG file says about each component reached, by launch index.

    Attributes:
        statuses: Final status of each component reached
        messages: Diagnostic lines printed while each component was installed
    """

    statuses: dict[int, ComponentStatus] = field(default_factory=dict)
    messages: dict[int, list[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class DebugAttempt:
    """Everything a single read of a DEBUG file tells about one WeiDU attempt.

    Attributes:
        report: Status and diagnostics of each component reached
        warnings: Lines starting with the WeiDU warning prefix
        errors: Lines starting with the WeiDU error prefix
        content: Whole text of the file, undecodable bytes dropped
    """

    report: DebugReport = field(default_factory=DebugReport)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    content: str = ""


class _DebugTranscript:
    """Outcome of the components reached by one WeiDU process, in launch order.

    WeiDU prints the message of the mod language when the TP2 translates it and
    its English text otherwise, so both variants are accepted.
    """

    SUMMARY_STATUSES = (
        ("successfully_installed", ComponentStatus.SUCCESS),
        ("installed_with_warnings", ComponentStatus.WARNING),
        ("not_installed_due_to_errors", ComponentStatus.ERROR),
        ("installation_aborded", ComponentStatus.ERROR),
    )

    def __init__(self, strings: dict[str, str]):
        self._strings = strings
        self._names: list[str] = []
        self._skipped: set[int] = set()
        self._warned: set[int] = set()
        self._rolled_back: set[int] = set()
        self._terminal: dict[int, ComponentStatus] = {}
        self._conflicts: set[int] = set()
        self._diagnostics: dict[int, list[str]] = {}
        self._diagnostic = self._diagnostic_pattern()
        self._in_diagnostic = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def read(self, lines: list[str]) -> DebugReport:
        """Interpret DEBUG lines and report the outcome of each reached component."""
        for raw_line in lines:
            if not self._read_line(raw_line.strip()):
                self._collect_diagnostic(raw_line)
        indexes = range(len(self._names))
        return DebugReport(
            statuses={index: self._final_status(index) for index in indexes},
            messages={index: self._summarize(index) for index in indexes},
        )

    def is_component_start(self, line: str) -> bool:
        """Whether WeiDU starts or skips a component of the batch on this line."""
        line = line.strip()
        return any(
            self._bracketed_after(key, line) is not None for key in ("installing", "skipping")
        )

    # ------------------------------------------------------------------
    # Line interpretation
    # ------------------------------------------------------------------

    def _read_line(self, line: str) -> bool:
        """Interpret a WeiDU message; False when the line is mod output."""
        if (name := self._bracketed_after("installing", line)) is not None:
            self._names.append(name)
        elif (name := self._bracketed_after("skipping", line)) is not None:
            self._names.append(name)
            self._skipped.add(len(self._names) - 1)
        elif (name := self._failed_component(line)) is not None:
            self._rolled_back.add(self._index_of(name))
        elif (summary := self._summary(line)) is not None:
            self._record_terminal(*summary)
        else:
            if self._names and self._remainder("warning", line) is not None:
                self._warned.add(len(self._names) - 1)
            return False
        self._in_diagnostic = False
        return True

    def _summary(self, line: str) -> tuple[str, ComponentStatus] | None:
        """Component name and status of a summary line, or None."""
        for key, status in self.SUMMARY_STATUSES:
            remainder = self._remainder(key, line)
            if remainder is not None:
                return remainder.strip().strip("[]"), status
        return None

    def _collect_diagnostic(self, raw_line: str) -> None:
        """Keep the warnings and errors of the current component, with their details.

        Mods print warnings in many forms (`WARNING:`, `UA WARNING:`, `WARN`
        text, script compilation notices), so any line naming a diagnostic is
        kept, together with the indented lines that detail it.
        """
        if not self._names:
            return
        line = raw_line.rstrip()
        detail = self._in_diagnostic and line[:1] in (" ", "\t") and bool(line.strip())
        self._in_diagnostic = detail or bool(self._diagnostic.search(line))
        if self._in_diagnostic:
            self._diagnostics.setdefault(len(self._names) - 1, []).append(line.strip())

    def _summarize(self, index: int) -> list[str]:
        """Distinct diagnostic lines in order, repeated ones counted, within the limit."""
        counts = Counter(self._diagnostics.get(index, []))
        lines = [line if n == 1 else f"{line} (×{n})" for line, n in counts.items()]
        if len(lines) > DEBUG_MESSAGES_LIMIT:
            hidden = len(lines) - DEBUG_MESSAGES_LIMIT
            lines = lines[:DEBUG_MESSAGES_LIMIT] + [f"… (+{hidden})"]
        return lines

    def _failed_component(self, line: str) -> str | None:
        """Name of the component rolled back by `ERROR Installing [...]` or an abort."""
        for key in ("error_installing", "aborting"):
            remainder = self._remainder(key, line)
            if remainder is None:
                continue
            suffix = self._variants("rolling_back")
            folded = remainder.casefold()
            ends = [folded.find(variant) for variant in suffix if variant in folded]
            end = min(ends) if ends else remainder.rfind("]")
            return remainder[:end] if end >= 0 else remainder
        return None

    def _record_terminal(self, name: str, status: ComponentStatus) -> None:
        candidates = [
            i for i, known in enumerate(self._names) if known.casefold() == name.casefold()
        ]
        if not candidates:
            # Untranslated or missing names: only a single open component is unambiguous.
            open_components = [
                i
                for i in range(len(self._names))
                if i not in self._terminal and i not in self._skipped
            ]
            candidates = open_components if len(open_components) == 1 else []

        target = next((i for i in candidates if i not in self._terminal), None)
        if target is not None:
            self._terminal[target] = status
        elif candidates and self._terminal[candidates[-1]] != status:
            self._conflicts.add(candidates[-1])

    def _final_status(self, index: int) -> ComponentStatus:
        if index in self._skipped:
            return ComponentStatus.SKIPPED
        if index in self._conflicts:
            return ComponentStatus.UNKNOWN

        terminal = self._terminal.get(index)
        if terminal is None:
            return (
                ComponentStatus.ERROR if index in self._rolled_back else ComponentStatus.UNKNOWN
            )
        if index in self._rolled_back and terminal != ComponentStatus.ERROR:
            return ComponentStatus.UNKNOWN
        if terminal == ComponentStatus.SUCCESS and index in self._warned:
            return ComponentStatus.WARNING
        return terminal

    # ------------------------------------------------------------------
    # Matching helpers
    # ------------------------------------------------------------------

    def _diagnostic_pattern(self) -> re.Pattern[str]:
        """Diagnostic words, not inside a file name such as `sfo_warnings.txt`."""
        words = set(_DIAGNOSTIC_WORDS)
        for key in ("warning", "error"):
            words.update(variant.rstrip(": ") for variant in self._variants(key))
        alternatives = "|".join(
            re.escape(word) for word in sorted(words, key=len, reverse=True)
        )
        return re.compile(rf"(?<![\w./\\])(?:{alternatives})s?\b", re.IGNORECASE)

    def _variants(self, key: str) -> tuple[str, ...]:
        values = (self._strings.get(key, ""), DEFAULT_STRINGS[key])
        folded = [value.strip().casefold() for value in values]
        # "SKIPPING:" and "Skipping" both introduce a skipped component.
        folded += [value.removesuffix(":") for value in folded if key == "skipping"]
        return tuple(dict.fromkeys(value for value in folded if value))

    def _remainder(self, key: str, line: str) -> str | None:
        folded = line.casefold()
        for variant in sorted(self._variants(key), key=len, reverse=True):
            if folded.startswith(variant):
                return line[len(variant) :]
        return None

    def _bracketed_after(self, key: str, line: str) -> str | None:
        """Component name of `<prefix> [name] [version]`, or None."""
        remainder = self._remainder(key, line)
        if remainder is None:
            return None
        text = remainder.strip()
        if not text.startswith("["):
            return None
        text = text[1:]
        if "] [" in text:
            return text.rsplit("] [", 1)[0]
        return text.removesuffix("]")

    def _index_of(self, name: str) -> int:
        """Latest component with this name; a truncated DEBUG may lack its start line."""
        for index in range(len(self._names) - 1, -1, -1):
            if self._names[index].casefold() == name.casefold():
                return index
        self._names.append(name)
        return len(self._names) - 1


class WeiDUDebugParser:
    """Parser for .DEBUG files with language-aware string matching."""

    @staticmethod
    def parse(
        file_path: str | Path, strings: dict[str, str] | None = None
    ) -> dict[int, ComponentStatus]:
        """
        Parse a setup-*.DEBUG file to determine component statuses.

        Args:
            file_path: Path to DEBUG file
            strings: Dictionary of translated WeiDU strings

        Returns:
            Status of each component reached, by launch index. A component
            reached without terminal evidence is UNKNOWN; a component that
            WeiDU never reached is absent.
        """
        return WeiDUDebugParser.read_report(file_path, strings).statuses

    @staticmethod
    def read_report(
        file_path: str | Path, strings: dict[str, str] | None = None
    ) -> DebugReport:
        """
        Read the status and the diagnostic lines of each component reached.

        Args:
            file_path: Path to DEBUG file
            strings: Dictionary of translated WeiDU strings

        Returns:
            Report by launch index; empty when the file cannot be read
        """
        data = WeiDUDebugParser._read_bytes(Path(file_path))
        if data is None:
            return DebugReport()
        return WeiDUDebugParser._interpret(data, strings or DEFAULT_STRINGS)

    @staticmethod
    def read_attempt(
        file_path: str | Path, strings: dict[str, str] | None = None
    ) -> DebugAttempt:
        """
        Read a DEBUG file once and give both of its interpretations.

        The bytes are decoded twice, as `read_report` and
        `extract_warnings_errors` would, so the results stay identical to
        theirs while the file cannot change between the two readings.

        Args:
            file_path: Path to DEBUG file
            strings: Dictionary of translated WeiDU strings

        Returns:
            Report, prefixed lines and content; empty when the file cannot be read
        """
        data = WeiDUDebugParser._read_bytes(Path(file_path))
        if data is None:
            return DebugAttempt()

        strings = strings or DEFAULT_STRINGS
        content = WeiDUDebugParser._decode(data, errors="ignore")
        warnings, errors = WeiDUDebugParser._prefixed_lines(content, strings)
        return DebugAttempt(
            report=WeiDUDebugParser._interpret(data, strings),
            warnings=warnings,
            errors=errors,
            content=content,
        )

    @staticmethod
    def component_start_matcher(strings: dict[str, str] | None = None) -> Callable[[str], bool]:
        """Predicate telling whether a line of WeiDU output starts or skips a component."""
        return _DebugTranscript(strings or DEFAULT_STRINGS).is_component_start

    @staticmethod
    def extract_warnings_errors(
        debug_file: Path, strings: dict[str, str] | None = None
    ) -> tuple[list[str], list[str], str]:
        """
        Extract warnings and errors from DEBUG file.

        Args:
            debug_file: Path to DEBUG file
            strings: Dictionary of translated WeiDU strings

        Returns:
            Tuple of (warnings, errors, full_content)
        """
        data = WeiDUDebugParser._read_bytes(debug_file)
        if data is None:
            return [], [], ""

        strings = strings or DEFAULT_STRINGS
        content = WeiDUDebugParser._decode(data, errors="ignore")
        warnings, errors = WeiDUDebugParser._prefixed_lines(content, strings)
        return warnings, errors, content

    # ------------------------------------------------------------------
    # Reading helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _read_bytes(file_path: Path) -> bytes | None:
        """Raw content of the DEBUG file, or None when it is absent or unreadable."""
        if not file_path.exists():
            return None
        try:
            return file_path.read_bytes()
        except OSError as e:
            logger.error("Error reading debug log %s: %s", file_path, e)
            return None

    @staticmethod
    def _decode(data: bytes, errors: str) -> str:
        """UTF-8 text with the universal newlines applied by `Path.read_text()`."""
        return data.decode("utf-8", errors=errors).replace("\r\n", "\n").replace("\r", "\n")

    @staticmethod
    def _interpret(data: bytes, strings: dict[str, str]) -> DebugReport:
        """Outcome of each component, invalid bytes shown as replacement characters."""
        lines = WeiDUDebugParser._decode(data, errors="replace").splitlines()
        return _DebugTranscript(strings).read(lines)

    @staticmethod
    def _prefixed_lines(content: str, strings: dict[str, str]) -> tuple[list[str], list[str]]:
        """Lines starting with the WeiDU warning prefix, then with the error prefix."""
        warning_pattern = re.compile(rf"^{re.escape(strings['warning'])}", re.IGNORECASE)
        error_pattern = re.compile(rf"^{re.escape(strings['error'])}", re.IGNORECASE)

        warnings: list[str] = []
        errors: list[str] = []
        for line in content.split("\n"):
            if warning_pattern.match(line):
                warnings.append(line)
            elif error_pattern.match(line):
                errors.append(line)
        return warnings, errors
