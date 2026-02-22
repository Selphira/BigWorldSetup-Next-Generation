"""Core installation engine with all AutoIt logic ported to Python."""

import logging
from pathlib import Path

from PySide6.QtCore import Qt

from constants import EXTRACT_DIR
from core.validators.StructureValidator import StructureValidator
from core.weidu_types import (
    DEFAULT_STRINGS,
    WEIDU_TRANSLATION_KEYS,
    ComponentInfo,
    ComponentStatus,
    InstallResult,
)
from core.WeiDUDebugParser import WeiDUDebugParser
from core.WeiDULogParser import WeiDULogParser
from core.WeiDUTp2Parser import WeiDUTp2Parser

logger = logging.getLogger(__name__)


# ============================================================================
# Installer Engine
# ============================================================================


class WeiDUInstallerEngine:
    """WeiDU installation engine."""

    def __init__(
        self, game_dir: str | Path, log_parser: WeiDULogParser, debug_parser: WeiDUDebugParser
    ):
        self.game_dir = Path(game_dir)
        self.weidu_exe: Path | None = None
        self.log_parser = log_parser
        self.debug_parser = debug_parser
        self.tp2_parser = WeiDUTp2Parser(self.game_dir)

    # ------------------------------------------------------------------
    # Paths
    # ------------------------------------------------------------------

    def setup_exe(self, tp2: str) -> Path:
        """Get the path to the WeiDU executable."""
        return self.game_dir / f"setup-{tp2}.exe"

    def debug_file(self, tp2: str) -> Path:
        """Get the path to the debug file."""
        return self.game_dir / f"setup-{tp2}.debug".upper()

    def debug_backup_file(self, tp2: str) -> Path:
        """Get the path to the debug backup file."""
        return self.game_dir / f"setup-{tp2}.debug_backup".upper()

    @property
    def weidu_log(self) -> Path:
        """Get the path to the WeiDU log."""
        return self.game_dir / "WeiDU.log"

    @property
    def weidu_conf(self) -> Path:
        """Get the path to the weidu.conf."""
        return self.game_dir / "weidu.conf"

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def install_components(
        self,
        tp2: str,
        components: list[ComponentInfo],
        language: str,
        extra_args: list[str] | None,
        input_lines: list[str] | None,
        runner_factory,
        output_callback=None,
        runner_created_callback=None,
        runner_finished_callback=None,
        command_created_callback=None,
    ) -> dict[str, InstallResult]:
        """Install one batch of components."""
        results = {}

        if not self._create_or_update_setup(tp2):
            error_result = InstallResult(
                status=ComponentStatus.ERROR,
                return_code=-1,
                stdout="",
                stderr=f"Failed to create setup-{tp2}.exe",
                warnings=[],
                errors=[f"Failed to create setup-{tp2}.exe"],
            )
            results = {
                f"{component.mod.id}:{component.component.key}": error_result
                for component in components
            }
            return results

        self._backup_debug(tp2)

        cmd = self._build_command(tp2, components, language, extra_args)

        if command_created_callback:
            command_created_callback(cmd)

        runner = runner_factory(cmd, self.game_dir, input_lines)

        if runner_created_callback:
            runner_created_callback(runner)

        if output_callback:
            runner.output_received.connect(output_callback, Qt.ConnectionType.DirectConnection)

        runner.start()
        runner.wait()

        if runner_finished_callback:
            runner_finished_callback()

        stdout = "".join(runner._stdout_lines)
        stderr = "".join(runner._stderr_lines)
        return_code = runner.process.returncode if runner.process else -1

        debug_file = self.debug_file(tp2)
        weidu_strings = self._get_weidu_strings(tp2, language)
        warnings, errors, debug_content = self.debug_parser.extract_warnings_errors(
            debug_file, strings=weidu_strings
        )
        statuses = self.debug_parser.parse(debug_file, strings=weidu_strings)

        self._restore_debug(tp2)

        for idx, comp in enumerate(components):
            comp_id = f"{comp.mod.id}:{comp.component.key}"
            status = statuses.get(idx, ComponentStatus.ERROR)

            results[comp_id] = InstallResult(
                status=status,
                return_code=return_code,
                stdout=stdout,
                stderr=stderr,
                warnings=warnings if status == ComponentStatus.WARNING else [],
                errors=errors if status == ComponentStatus.ERROR else [],
                debug_log=debug_content if len(components) == 1 else "",
            )

        return results

    def install_no_weidu_components(
        self,
        mod_id: str,
        components: list[ComponentInfo],
        language: str,
        runner_factory,
        output_callback=None,
        runner_created_callback=None,
        runner_finished_callback=None,
    ) -> dict[str, InstallResult]:
        """
        Install components that don't have a traditional WeiDU tp2 file.

        This method creates a synthetic tp2 file for mods that use the "dwn" (download-only)
        component type. These mods only need their files extracted, not processed by WeiDU scripts.

        The generated tp2 creates a log entry so the installation can be tracked in WeiDU.log,
        but doesn't perform any actual file operations (files are already extracted).

        Args:
            mod_id: Mod identifier
            components: List of components to "install"
            language: Language code
            runner_factory: Factory function to create ProcessRunner
            output_callback: Optional callback for output
            runner_created_callback: Optional callback when runner is created
            runner_finished_callback: Optional callback when runner finishes

        Returns:
            Dictionary mapping component_id to InstallResult
        """
        component = components[0].component
        tp2_name = mod_id
        tp2_path = self.game_dir / f"setup-{tp2_name}.tp2"

        # Generate minimal tp2 content
        # This creates a valid WeiDU tp2 that does nothing except log the installation
        content = (
            f"BACKUP ~weidu_external/backup/{tp2_name}~\n"
            f"AUTHOR ~bws-ng~\n"
            f"\n"
            f"BEGIN ~{component.text}~\n"
            f"// This is a download-only component, files are already extracted\n"
            f"PRINT ~Files for {tp2_name} are already in place~\n"
        )

        try:
            tp2_path.write_text(content, encoding="utf-8")
            logger.info("Created synthetic tp2 file: %s", tp2_path.name)
        except Exception as e:
            logger.error("Failed to create synthetic tp2 file %s: %s", tp2_path.name, e)

            error_result = InstallResult(
                status=ComponentStatus.ERROR,
                return_code=-1,
                stdout="",
                stderr=f"Failed to create synthetic tp2 file: {e}",
                warnings=[],
                errors=[f"Failed to create synthetic tp2 file: {e}"],
            )

            return {f"{comp.mod.id}:{comp.component.key}": error_result for comp in components}
        # TODO: Copier les fichiers extrait dans le override (par défaut)
        return self.install_components(
            tp2=tp2_name,
            components=components,
            language=language,
            extra_args=[],
            input_lines=[],
            runner_factory=runner_factory,
            output_callback=output_callback,
            runner_created_callback=runner_created_callback,
            runner_finished_callback=runner_finished_callback,
        )

    def locate_weidu(self) -> bool:
        """
        Locate WeiDU.exe in the game directory.

        Returns:
            True if found, False otherwise
        """
        root_candidate = self.game_dir / "weidu.exe"
        if root_candidate.exists():
            self.weidu_exe = root_candidate
            return True

        try:
            folders = [
                self.game_dir / EXTRACT_DIR / "weidu64",
                self.game_dir / EXTRACT_DIR / "weidu",
                self.game_dir,
            ]
            for folder in folders:
                if not folder.exists():
                    continue

                candidate = folder / "weidu.exe"
                if candidate.exists():
                    self.weidu_exe = candidate
                    return True

                for child in folder.iterdir():
                    candidate = child / "weidu.exe"
                    if candidate.exists():
                        self.weidu_exe = candidate
                        return True
        except Exception as e:
            logger.warning("WeiDU search failed: %s", e)

        self.weidu_exe = None
        return False

    def init_weidu_log(self) -> None:
        """Initialize WeiDU.log file if it doesn't exist."""
        if not self.weidu_log.exists():
            try:
                self.weidu_log.write_text("", encoding="utf-8")
                logger.info("Created weidu.log")
            except Exception as e:
                logger.error("Failed to create WeiDU.log: %s", e)
                raise
        else:
            logger.debug("WeiDU.log already exists, skipping initialization")

    def init_weidu_conf(self, language: str) -> None:
        """
        Initialize weidu.conf file if it doesn't exist.

        The weidu.conf file tells WeiDU which language directory to use for dialogs.
        This prevents WeiDU from prompting the user to select a language.

        Args:
            language: Language code to set in the configuration
        """
        if not self.weidu_conf.exists():
            try:
                self.weidu_conf.write_text(f"lang_dir = {language}\n", encoding="utf-8")
                logger.info("Created weidu.conf with lang_dir = %s", language)
            except Exception as e:
                logger.error("Failed to create weidu.conf: %s", e)
                raise
        else:
            logger.debug("weidu.conf already exists, skipping initialization")

    def is_component_installed(self, mod_id: str, comp_key: str) -> bool:
        """
        Check if a component is installed.

        Args:
            mod_id: Mod identifier
            comp_key: Component number

        Returns:
            True if component is installed
        """
        return self.log_parser.is_component_installed(self.weidu_log, mod_id, comp_key)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _create_or_update_setup(self, tp2: str) -> bool:
        """
        Create or update setup-{tp2}.exe from WeiDU.exe.

        Args:
            tp2: Name of the tp2 file (without extension)

        Returns:
            True if successful
        """
        setup = self.setup_exe(tp2)

        if setup.exists() and setup.stat().st_size == self.weidu_exe.stat().st_size:
            return True

        try:
            import shutil

            shutil.copy2(self.weidu_exe, setup)
            logger.info("Created/updated %s", setup.name)
            return True
        except Exception as e:
            if setup.exists():
                logger.warning("Failed to update %s: %s", setup.name, e)
                return True
            else:
                logger.error("Failed to create %s: %s", setup.name, e)
                return False

    def _build_command(self, tp2, components, language, extra_args):
        """
        Build the WeiDU installation command.

        Args:
            tp2: Name of the tp2 file
            components: Components to install
            language: Language code
            extra_args: Optional extra arguments

        Returns:
            Command as list of strings
        """
        cmd = [
            str(self.setup_exe(tp2)),
            "--no-exit-pause",
            "--noautoupdate",
            "--language",
            str(language),
            "--skip-at-view",
            "--force-install-list",
            *[component.component.key for component in components],
            "--logapp",
        ]

        if extra_args:
            cmd.extend(extra_args)
        return cmd

    def _backup_debug(self, tp2: str) -> None:
        """
        Backup existing DEBUG file before installation.

        Args:
            tp2: Name of the tp2 file
        """
        debug_file = self.debug_file(tp2)
        backup_file = self.debug_backup_file(tp2)

        if not debug_file.exists():
            return

        try:
            if backup_file.exists():
                current_content = debug_file.read_text(encoding="utf-8", errors="ignore")
                old_content = backup_file.read_text(encoding="utf-8", errors="ignore")
                backup_file.write_text(old_content + "\n" + current_content, encoding="utf-8")
                debug_file.unlink()
            else:
                debug_file.rename(backup_file)
        except Exception as e:
            logger.warning("Could not backup debug file: %s", e)

    def _restore_debug(self, tp2: str) -> None:
        """
        Restore and merge DEBUG file after installation.

        Args:
            tp2: Name of the tp2 file
        """
        debug_file = self.debug_file(tp2)
        backup_file = self.debug_backup_file(tp2)

        if not debug_file.exists() or not backup_file.exists():
            return

        try:
            current_content = debug_file.read_text(encoding="utf-8", errors="ignore")
            old_content = backup_file.read_text(encoding="utf-8", errors="ignore")
            debug_file.write_text(old_content + "\n" + current_content, encoding="utf-8")
            backup_file.unlink()
        except Exception as e:
            logger.warning("Could not restore debug file: %s", e)

    def _get_weidu_strings(self, tp2: str, language: str) -> dict[str, str]:
        """
        Extract WeiDU translation strings for a given mod and language.

        Args:
            tp2: Name of the tp2 file (without extension)
            language: Language code (e.g., "0" for first language)

        Returns:
            Dictionary of translated WeiDU strings, or defaults if extraction fails
        """
        try:
            _, tp2_path = StructureValidator.validate_structure(self.game_dir, tp2)

            if not tp2_path.exists():
                logger.warning(f"TP2 file not found: {tp2_path}, using default strings")
                return DEFAULT_STRINGS

            weidu_tp2 = self.tp2_parser.parse_file(tp2_path)

            # Get the language declaration by index
            try:
                lang_index = int(language)
                if 0 <= lang_index < len(weidu_tp2.languages):
                    lang_decl = weidu_tp2.languages[lang_index]
                    lang_code = lang_decl.language_code
                else:
                    logger.warning(f"Invalid language index {language}, using defaults")
                    return DEFAULT_STRINGS
            except (ValueError, IndexError):
                logger.warning(f"Could not parse language index {language}, using defaults")
                return DEFAULT_STRINGS

            translations = weidu_tp2.translations.get(lang_code, {})

            strings = {}
            for key, ref_id in WEIDU_TRANSLATION_KEYS.items():
                if ref_id.startswith("@"):
                    translated = translations.get(ref_id.lstrip("@"), DEFAULT_STRINGS[key])
                    strings[key] = translated
                else:
                    strings[key] = DEFAULT_STRINGS[key]

            logger.debug(f"Loaded WeiDU strings for {tp2} language {language}")
            return strings

        except Exception as e:
            logger.warning(f"Failed to extract WeiDU strings for {tp2}: {e}, using defaults")
            return DEFAULT_STRINGS
