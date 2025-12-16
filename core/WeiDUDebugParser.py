import logging
import re
from pathlib import Path

from core.InstallerEngine import ComponentStatus

logger = logging.getLogger(__name__)


class WeiDUDebugParser:
    """Parser for .DEBUG files."""

    # TODO: La langue et le mod concerné sont très important !!
    # Lire les .tra du mod concerné, dans la langue concernée et tenter de retrouver les chaînes de prompt qui seront utilisées par les regex !!
    # ; 'Installing', 'SKIPPING:', 'WARNING:', 'ERROR:', 'Saving This Log:', 'WeiDU Timings', 'SUCCESSFULLY INSTALLED', 'INSTALLED WITH WARNINGS', 'INSTALLED DUE TO ERRORS'
    # ;@-1016 @-1020 - -          --        --                  --              @-1019        @-1033        @-1032

    @staticmethod
    def parse(file_path: str | Path) -> dict[int, ComponentStatus]:
        """
        Parse a setup-*.DEBUG file to determine component statuses.

        Args:
            file_path: Path to DEBUG file

        Returns:
            Dictionary mapping component index to status
        """
        file_path = Path(file_path)

        if not file_path.exists():
            return {}

        results = {}
        component_idx = -1
        warnings = []
        errors = []

        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            lines = content.split("\n")

            for i, line in enumerate(lines):
                # Track component being installed
                if re.match(r"^Installing \[", line):
                    component_idx += 1
                    warnings = []
                    errors = []
                    results[component_idx] = ComponentStatus.SUCCESS

                # Track skipped components
                elif re.match(r"^SKIPPING: \[", line):
                    component_idx += 1
                    results[component_idx] = ComponentStatus.SKIPPED

                # Collect warnings
                elif re.match(r"^WARNING:", line):
                    warnings.append(line)
                    if component_idx >= 0:
                        results[component_idx] = ComponentStatus.WARNING

                # Collect errors
                elif re.match(r"^ERROR:", line):
                    errors.append(line)
                    if component_idx >= 0:
                        results[component_idx] = ComponentStatus.ERROR

            # Parse final summary (at end of file)
            summary_start = -1
            for i in range(len(lines) - 1, -1, -1):
                if "WeiDU Timings" in lines[i]:
                    summary_start = i
                    break

            if summary_start >= 0:
                comp_idx = len(results) - 1
                print(file_path)
                print(results)
                for i in range(summary_start, -1, -1):
                    if comp_idx < 0:
                        break

                    if "Saving This Log" in lines[i]:
                        print("bye log")
                        break

                    # Skip already-skipped components
                    while (
                            comp_idx >= 0
                            and results.get(comp_idx) == ComponentStatus.SKIPPED
                    ):
                        comp_idx -= 1

                    if (
                            "SUCCESSFULLY INSTALLED" in lines[i]
                            or "INSTALLATION REUSSIE" in lines[i]
                    ):
                        if comp_idx >= 0:
                            results[comp_idx] = ComponentStatus.SUCCESS
                        comp_idx -= 1
                    elif (
                            "INSTALLED WITH WARNINGS" in lines[i]
                            or "INSTALLATION AVEC DES ALERTES" in lines[i]
                    ):
                        if comp_idx >= 0:
                            results[comp_idx] = ComponentStatus.WARNING
                        comp_idx -= 1
                    elif "NOT INSTALLED DUE TO ERRORS" in lines[i]:
                        if comp_idx >= 0:
                            results[comp_idx] = ComponentStatus.ERROR
                        comp_idx -= 1
                print(results)

        except Exception as e:
            logger.error("Error parsing debug log %s: %s", file_path, e)

        return results

    @staticmethod
    def extract_warnings_errors(
            debug_file: Path
    ) -> tuple[list[str], list[str], str]:
        """
        Extract warnings and errors from DEBUG file.

        Args:
            debug_file: Path to DEBUG file

        Returns:
            Tuple of (warnings, errors, full_content)
        """
        warnings = []
        errors = []
        content = ""

        if not debug_file.exists():
            return warnings, errors, content

        try:
            content = debug_file.read_text(encoding="utf-8", errors="ignore")
            for line in content.split("\n"):
                if re.match(r"^WARNING:", line):
                    warnings.append(line)
                elif re.match(r"^ERROR:", line):
                    errors.append(line)
        except Exception as e:
            logger.warning("Could not read debug file: %s", e)

        return warnings, errors, content
