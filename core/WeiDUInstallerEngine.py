"""Core installation engine with all AutoIt logic ported to Python."""

from collections.abc import Callable
import logging
from pathlib import Path
import re
from typing import Any

from PySide6.QtCore import Qt

from constants import DWN_TP2_LANGUAGE, DWN_TP2_PREFIX, EXTRACT_DIR, WEIDU_SETUP_PREFIX
from core.Mod import Mod
from core.TranslationManager import tr
from core.validators.StructureValidator import StructureValidator
from core.weidu_types import (
    DEFAULT_STRINGS,
    WEIDU_TRANSLATION_KEYS,
    AttemptEvidence,
    BatchOutcome,
    ComponentInfo,
    ComponentStatus,
    InstallResult,
    ProcessCallbacks,
)
from core.WeiDUDebugParser import WeiDUDebugParser
from core.WeiDULogParser import WeiDULogParser
from core.WeiDUTp2Parser import WeiDUTp2, WeiDUTp2Parser

logger = logging.getLogger(__name__)

# Mod identifiers allowed in a generated file name and TP2 script
SAFE_MOD_ID = re.compile(r"[a-zA-Z0-9_#-][a-zA-Z0-9_.#-]*")


class _OutputRelay:
    """Forward the WeiDU output and report each component of the batch it reaches."""

    def __init__(self, callbacks: ProcessCallbacks, starts: Callable[[str], bool]):
        self._callbacks = callbacks
        self._starts = starts
        self._pending = ""
        self._reached = -1

    def __call__(self, text: str, stream_type: str) -> None:
        """Relay one chunk of output; components are detected on complete lines."""
        if self._callbacks.output:
            self._callbacks.output(text, stream_type)
        if stream_type != "stdout" or not self._callbacks.component_reached:
            return
        *lines, self._pending = (self._pending + text).split("\n")
        for line in lines:
            if self._starts(line):
                self._reached += 1
                self._callbacks.component_reached(self._reached)


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
        runner_factory: Callable[..., Any],
        callbacks: ProcessCallbacks | None = None,
    ) -> dict[str, InstallResult]:
        """Install one batch of components and prove the outcome of each one.

        Args:
            tp2: Name of the tp2 file (without extension)
            components: Components installed by the same WeiDU process
            language: WeiDU language index
            extra_args: Optional extra WeiDU arguments
            input_lines: Answers sent to WeiDU prompts
            runner_factory: Builds the process runner from (cmd, cwd, input_lines)
            callbacks: Hooks around the process; `before_launch` runs once every
                preparation succeeded and may raise to prevent the launch

        Returns:
            Results by component reference
        """
        callbacks = callbacks or ProcessCallbacks()

        missing = self._missing_tp2_message(tp2, components)
        if missing:
            return self._failed_results(components, missing)

        if not self._create_or_update_setup(tp2):
            return self._failed_results(components, f"Failed to create setup-{tp2}.exe")

        definition = self.read_definition(tp2)
        try:
            self._backup_debug(tp2)
        except OSError as error:
            return self._failed_results(
                components,
                tr(
                    "page.installation.debug_locked",
                    file=self.debug_file(tp2).name,
                    error=error,
                ),
            )

        cmd = self._build_command(tp2, components, language, extra_args)
        if callbacks.command_created:
            callbacks.command_created(cmd)
        if callbacks.before_launch:
            callbacks.before_launch()

        runner = runner_factory(cmd, self.game_dir, input_lines)
        if callbacks.runner_created:
            callbacks.runner_created(runner)
        if callbacks.output or callbacks.component_reached:
            starts = self.debug_parser.component_start_matcher(
                self.get_weidu_strings(definition, language)
            )
            runner.output_received.connect(
                _OutputRelay(callbacks, starts), Qt.ConnectionType.DirectConnection
            )

        try:
            runner.start()
            runner.wait()
        finally:
            if callbacks.runner_finished:
                callbacks.runner_finished()

        return self._collect_results(tp2, components, language, definition, runner)

    def install_no_weidu_components(
        self,
        mod_id: str,
        components: list[ComponentInfo],
        runner_factory: Callable[..., Any],
        callbacks: ProcessCallbacks | None = None,
    ) -> dict[str, InstallResult]:
        """Register extracted download-only components in WeiDU.log.

        The files are already extracted: a generated TP2 only records the
        components, so that WeiDU.log stays the reference of the installation.
        Uninstalling that entry leaves the extracted files in place.

        Args:
            mod_id: Mod identifier
            components: Download-only components of this mod
            runner_factory: Builds the process runner from (cmd, cwd, input_lines)
            callbacks: Hooks around the WeiDU process

        Returns:
            Results by component reference
        """
        mod = components[0].mod
        tp2 = f"{DWN_TP2_PREFIX}{mod_id}"
        try:
            self._verify_extracted_payload(mod)
            self._write_registration_tp2(tp2, mod)
        except (OSError, ValueError) as error:
            return self._failed_results(components, str(error))

        return self.install_components(
            tp2, components, DWN_TP2_LANGUAGE, [], [], runner_factory, callbacks
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

    def is_component_installed(
        self, tp2: str, comp_key: str, language: int | str | None = None
    ) -> bool:
        """
        Check if a component is installed.

        Args:
            tp2: Name of the tp2 file (without extension)
            comp_key: Component number
            language: WeiDU language index, or None for any language

        Returns:
            True if component is installed
        """
        return any(
            identity[:2] == (tp2.lower(), comp_key)
            and (language is None or identity[2] == str(language))
            for identity in self.log_parser.installed_identities(self.weidu_log)
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _collect_results(
        self,
        tp2: str,
        components: list[ComponentInfo],
        language: str,
        definition: WeiDUTp2 | None,
        runner: Any,
    ) -> dict[str, InstallResult]:
        """Cross-check the DEBUG of this attempt, the exit code and WeiDU.log."""
        stdout = "".join(runner._stdout_lines)
        stderr = "".join(runner._stderr_lines)
        return_code = runner.process.returncode if runner.process else None

        debug_file = self.debug_file(tp2)
        strings = self.get_weidu_strings(definition, language)
        _, errors, debug_content = self.debug_parser.extract_warnings_errors(
            debug_file, strings=strings
        )
        report = self.debug_parser.read_report(debug_file, strings=strings)
        statuses = report.statuses
        self._restore_debug(tp2)

        if return_code is None:
            errors = errors + [tr("page.installation.process_not_started")]
        installed = set(self.log_parser.installed_identities(self.weidu_log))
        unlogged = definition.unlogged_components() if definition else set()
        batch = BatchOutcome(
            has_failure=ComponentStatus.ERROR in statuses.values(),
            has_warning=ComponentStatus.WARNING in statuses.values(),
            reliable_mapping=len(statuses) <= len(components),
        )

        results = {}
        for index, comp in enumerate(components):
            key = comp.component.key
            evidence = AttemptEvidence(
                return_code,
                statuses.get(index),
                (tp2.lower(), key, str(language)) in installed,
            )
            status = evidence.conclude(batch, unlogged=key in unlogged)
            messages = report.messages.get(index, [])
            results[comp.comp_id] = InstallResult(
                status=status,
                return_code=-1 if return_code is None else return_code,
                stdout=stdout,
                stderr=stderr,
                warnings=self._warning_details(status, messages, debug_file),
                errors=(messages or errors)
                if status in (ComponentStatus.ERROR, ComponentStatus.UNKNOWN)
                else [],
                debug_log=debug_content if len(components) == 1 else "",
                evidence=evidence,
            )
        return results

    @staticmethod
    def _warning_details(
        status: ComponentStatus, messages: list[str], debug_file: Path
    ) -> list[str]:
        """Warnings printed for a component; WeiDU does not always detail them."""
        if status != ComponentStatus.WARNING:
            return []
        return messages or [tr("page.installation.warning_no_details", file=debug_file.name)]

    @staticmethod
    def _failed_results(
        components: list[ComponentInfo], message: str
    ) -> dict[str, InstallResult]:
        """Results of a batch that failed before WeiDU could start."""
        return {
            comp.comp_id: InstallResult(
                status=ComponentStatus.ERROR,
                return_code=-1,
                stdout="",
                stderr=message,
                warnings=[],
                errors=[message],
                evidence=AttemptEvidence(None, None, in_log=False),
            )
            for comp in components
        }

    def _missing_tp2_message(self, tp2: str, components: list[ComponentInfo]) -> str | None:
        """Explain why WeiDU cannot start when the TP2 is absent from the game.

        The TP2 of a provided mod may only appear once its provider is
        installed (lolfixer and BWS-FR-Fixpack), so the provider is named.
        """
        if StructureValidator.validate_structure(self.game_dir, tp2)[0]:
            return None
        mod = components[0].mod if components else None
        if mod is not None and mod.provided_by:
            return tr(
                "page.installation.tp2_missing_provided", tp2=tp2, provider=mod.provided_by
            )
        return tr("page.installation.tp2_missing", tp2=tp2)

    def _verify_extracted_payload(self, mod: Mod) -> None:
        """Raise ValueError unless the download-only files can be found."""
        if not SAFE_MOD_ID.fullmatch(mod.id):
            raise ValueError(tr("page.installation.invalid_mod_id", mod=mod.id))

        if mod.tp2:
            prepared, _ = StructureValidator.validate_structure(self.game_dir, mod.tp2)
        else:
            folder = self.game_dir / EXTRACT_DIR / mod.id
            prepared = folder.is_dir() and any(path.is_file() for path in folder.rglob("*"))
        if not prepared:
            raise ValueError(tr("page.installation.unverified_files"))

    def _write_registration_tp2(self, tp2: str, mod: Mod) -> None:
        """Write the TP2 that records every download-only component of a mod."""
        numbers = [component.key for component in mod.get_components() if component.is_dwn()]
        if not all(number.isdigit() for number in numbers):
            raise ValueError(tr("page.installation.invalid_mod_id", mod=mod.id))

        script = f"BACKUP ~weidu_external/backup/{tp2}~\nAUTHOR ~BWS-NG~\n"
        for number in numbers:
            label = f"{mod.id} ({number})"
            script += f"BEGIN ~{label}~ DESIGNATED {number}\nPRINT ~{label}~\n"

        target = self.game_dir / f"{WEIDU_SETUP_PREFIX}{tp2}.tp2"
        if not target.is_file() or target.read_text(encoding="utf-8") != script:
            target.write_text(script, encoding="utf-8")
            logger.info("Wrote registration installer %s", target.name)

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
        except OSError:
            logger.error("Could not isolate %s before launching WeiDU", debug_file)
            raise

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

    def read_definition(self, tp2: str) -> WeiDUTp2 | None:
        """
        Parse the TP2 of a mod installed in the game directory.

        Args:
            tp2: Name of the tp2 file (without extension)

        Returns:
            Parsed definition, or None when the TP2 cannot be found or read
        """
        _, path = StructureValidator.validate_structure(self.game_dir, tp2)
        if path is None:
            logger.warning("Missing TP2 for %s in %s", tp2, self.game_dir)
            return None
        try:
            return self.tp2_parser.parse_file(path)
        except Exception as error:
            logger.warning("Could not read %s: %s", path, error)
            return None

    def get_weidu_strings(self, weidu_tp2: WeiDUTp2 | None, language: str) -> dict[str, str]:
        """
        Extract WeiDU translation strings for a given mod and language.

        Args:
            weidu_tp2: Parsed TP2 definition, or None
            language: Language index (e.g., "0" for first language)

        Returns:
            Dictionary of translated WeiDU strings, or defaults if extraction fails
        """
        if weidu_tp2 is None or not weidu_tp2.languages:
            return DEFAULT_STRINGS
        tp2 = weidu_tp2.name
        try:
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
