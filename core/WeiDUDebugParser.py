import logging
from pathlib import Path
import re

from core.weidu_types import DEFAULT_STRINGS
from core.WeiDUInstallerEngine import ComponentStatus

logger = logging.getLogger(__name__)


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
            Dictionary mapping component index to status
        """
        file_path = Path(file_path)

        if not file_path.exists():
            return {}

        strings = strings or DEFAULT_STRINGS
        patterns = {
            "installing": re.compile(
                rf"^{re.escape(strings['installing'])}\s+\[", re.IGNORECASE
            ),
            "skipping": re.compile(rf"^{re.escape(strings['skipping'])}\s+\[", re.IGNORECASE),
            "warning": re.compile(rf"^{re.escape(strings['warning'])}", re.IGNORECASE),
            "error": re.compile(rf"^{re.escape(strings['error'])}", re.IGNORECASE),
        }

        results = {}
        component_idx = -1
        warnings = []
        errors = []

        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            lines = content.split("\n")

            for line in lines:
                # Track component being installed
                if patterns["installing"].match(line):
                    component_idx += 1
                    warnings = []
                    errors = []
                    results[component_idx] = ComponentStatus.SUCCESS

                # Track skipped components
                elif patterns["skipping"].match(line):
                    component_idx += 1
                    results[component_idx] = ComponentStatus.SKIPPED

                # Collect warnings
                elif patterns["warning"].match(line):
                    warnings.append(line)
                    if component_idx >= 0:
                        results[component_idx] = ComponentStatus.WARNING

                # Collect errors
                elif patterns["error"].match(line):
                    errors.append(line)
                    if component_idx >= 0:
                        results[component_idx] = ComponentStatus.ERROR

            # Parse final summary (at end of file)
            summary_start = -1
            for i in range(len(lines) - 1, -1, -1):
                if strings["weidu_timings"] in lines[i]:
                    summary_start = i
                    break

            if summary_start >= 0:
                comp_idx = len(results) - 1

                for i in range(summary_start, -1, -1):
                    if comp_idx < 0:
                        break

                    if strings["saving_log"] in lines[i]:
                        break

                    # Skip already-skipped components
                    while comp_idx >= 0 and results.get(comp_idx) == ComponentStatus.SKIPPED:
                        comp_idx -= 1

                    if strings["successfully_installed"] in lines[i]:
                        if comp_idx >= 0:
                            results[comp_idx] = ComponentStatus.SUCCESS
                        comp_idx -= 1
                    elif strings["installed_with_warnings"] in lines[i]:
                        if comp_idx >= 0:
                            results[comp_idx] = ComponentStatus.WARNING
                        comp_idx -= 1
                    elif strings["not_installed_due_to_errors"] in lines[i]:
                        if comp_idx >= 0:
                            results[comp_idx] = ComponentStatus.ERROR
                        comp_idx -= 1
                    elif strings["installation_aborded"] in lines[i]:
                        if comp_idx >= 0:
                            results[comp_idx] = ComponentStatus.ERROR
                        comp_idx -= 1

        except Exception as e:
            logger.error("Error parsing debug log %s: %s", file_path, e)

        return results

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
        warnings = []
        errors = []
        content = ""

        if not debug_file.exists():
            return warnings, errors, content

        strings = strings or DEFAULT_STRINGS
        warning_pattern = re.compile(rf"^{re.escape(strings['warning'])}", re.IGNORECASE)
        error_pattern = re.compile(rf"^{re.escape(strings['error'])}", re.IGNORECASE)

        try:
            content = debug_file.read_text(encoding="utf-8", errors="ignore")
            for line in content.split("\n"):
                if warning_pattern.match(line):
                    warnings.append(line)
                elif error_pattern.match(line):
                    errors.append(line)
        except Exception as e:
            logger.warning("Could not read debug file: %s", e)

        return warnings, errors, content
