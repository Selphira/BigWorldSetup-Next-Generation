"""Core installation engine with all AutoIt logic ported to Python."""
import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from PySide6.QtCore import Qt

from core.WeiDUDebugParser import WeiDUDebugParser
from core.WeiDULogParser import WeiDULogParser

logger = logging.getLogger(__name__)


# ============================================================================
# Data Classes
# ============================================================================

class ComponentStatus(Enum):
    """Status of a component installation."""
    INSTALLING = "installing"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
    SKIPPED = "skipped"
    ALREADY_INSTALLED = "already_installed"


@dataclass
class InstallResult:
    """Result of a component installation."""
    status: ComponentStatus
    return_code: int
    stdout: str
    stderr: str
    warnings: list[str]
    errors: list[str]
    debug_log: str = ""


@dataclass
class ComponentInfo:
    """Information about a component to install."""
    mod_id: str
    component_key: str
    tp2_name: str
    sequence_idx: int
    requirements: set[tuple[str, str]] = ()
    subcomponent_answers: list[str] = None
    extra_args: list[str] = None

    def __post_init__(self):
        """Ensure subcomponent_answers is a list."""
        if self.subcomponent_answers is None:
            self.subcomponent_answers = []


# ============================================================================
# Installer Engine
# ============================================================================

class WeiDUInstallerEngine:
    """WeiDU installation engine."""

    def __init__(self, game_dir: str | Path, log_parser: WeiDULogParser, debug_parser: WeiDUDebugParser):
        self.game_dir = Path(game_dir)
        self.weidu_exe: Path | None = None
        self.log_parser = log_parser
        self.debug_parser = debug_parser

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
                errors=[f"Failed to create setup-{tp2}.exe"]
            )
            results = {
                f"{component.mod_id}:{component.component_key}": error_result
                for component in components
            }

            return results

        self._backup_debug(tp2)

        cmd = self._build_command(tp2, components, language, extra_args)

        runner = runner_factory(cmd, self.game_dir, input_lines)
        if output_callback:
            runner.output_received.connect(output_callback, Qt.ConnectionType.DirectConnection)

        runner.start()
        runner.wait()

        stdout = ''.join(runner._stdout_lines)
        stderr = ''.join(runner._stderr_lines)
        return_code = runner.process.returncode if runner.process else -1

        warnings, errors, debug_content = self.debug_parser.extract_warnings_errors(
            self.debug_file(tp2)
        )

        statuses = self.debug_parser.parse(self.debug_file(tp2))

        self._restore_debug(tp2)

        print(f"STATUSES: {statuses}")
        print(f"errors: {errors}")
        print(f"warnings: {warnings}")

        for idx, comp in enumerate(components):
            comp_id = f"{comp.mod_id}:{comp.component_key}"
            status = statuses.get(idx, ComponentStatus.ERROR)
            print(f"idx: {idx}, comp_id: {comp_id}, status: {status}")

            verified = self.log_parser.is_component_installed(
                self.weidu_log,
                comp.tp2_name,
                comp.component_key
            )

            if return_code == 0 and verified:
                if errors:
                    status = ComponentStatus.ERROR
                elif warnings:
                    status = ComponentStatus.WARNING
                elif status != ComponentStatus.SKIPPED:
                    status = ComponentStatus.SUCCESS
            elif status != ComponentStatus.SKIPPED:
                status = ComponentStatus.ERROR

            print(f"APRES idx: {idx}, comp_id: {comp_id}, status: {status}")

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
            for child in self.game_dir.iterdir():
                if not child.is_dir():
                    continue
                candidate = child / "weidu.exe"
                if candidate.exists():
                    self.weidu_exe = candidate
                    return True
        except Exception as e:
            logger.warning("WeiDU search failed: %s", e)

        self.weidu_exe = None
        return False

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
            "--language", str(language),
            "--skip-at-view",
            "--force-install-list",
            *[component.component_key for component in components],
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
