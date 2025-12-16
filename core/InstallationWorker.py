import logging
import subprocess
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from PySide6.QtCore import Signal, QThread

from core.WeiDUInstallerEngine import (
    WeiDUInstallerEngine, ComponentInfo, ComponentStatus, InstallResult,
)

logger = logging.getLogger(__name__)


# ============================================================================
# Constants and Enums
# ============================================================================

class UserDecision(Enum):
    """User decision after installation error/warning."""
    RETRY = 'retry'
    SKIP = "skip"
    PAUSE = "pause"
    STOP = "stop"


# ============================================================================
# Installation State
# ============================================================================

@dataclass
class InstallationState:
    """Persistent state of installation progress."""
    last_installed_component_index: int
    last_installed_batch_index: int
    current_sequence: int


# ============================================================================
# Process Runner
# ============================================================================

class ProcessRunner(QThread):
    """Process runner"""

    output_received = Signal(str, str)  # text, stream_type ("stdout"/"stderr")
    process_finished = Signal(int, str, str)  # return_code, stdout, stderr
    process_error = Signal(str)

    def __init__(
            self,
            cmd: list[str],
            cwd: Path,
            input_lines: list[str] | None = None
    ):
        """
        Initialize process runner.

        Args:
            cmd: Command and arguments to execute
            cwd: Working directory
            input_lines: Lines to send to stdin at startup (for automated answers)
        """
        super().__init__()
        self.cmd = cmd
        self.cwd = cwd
        self.input_lines = input_lines or []
        self.process: subprocess.Popen | None = None
        self._stop_flag = False
        self._stdout_lines = []
        self._stderr_lines = []
        self._lock = threading.Lock()

        # Queue for thread-safe signal emission
        self._output_queue = []
        self._queue_lock = threading.Lock()

    def run(self):
        """Execute process with parallel stdout/stderr reading."""
        try:
            logger.debug("Starting process: %s", ' '.join(str(c) for c in self.cmd))
            logger.debug("Working directory: %s", self.cwd)

            self.process = subprocess.Popen(
                self.cmd,
                cwd=str(self.cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.PIPE,
                bufsize=1,
                text=True
            )

            logger.debug("Process started with PID: %s", self.process.pid)

            stdout_thread = threading.Thread(
                target=self._read_stream,
                args=(self.process.stdout, "stdout", self._stdout_lines),
                name="WeiDU-stdout",
                daemon=True
            )

            stderr_thread = threading.Thread(
                target=self._read_stream,
                args=(self.process.stderr, "stderr", self._stderr_lines),
                name="WeiDU-stderr",
                daemon=True
            )

            stdout_thread.start()
            stderr_thread.start()

            # Send pre-configured input lines immediately
            # TODO: It may not work in all cases. Using a pipe command may be necessary, but compatibility and thread management issues need to be considered...
            if self.input_lines:
                threading.Thread(
                    target=self._send_initial_input,
                    daemon=True
                ).start()

            # Process queued outputs periodically from QThread
            while self.process.poll() is None:
                self._emit_queued_outputs()
                self.msleep(50)

            # Wait for reader threads
            logger.debug("Waiting for reader threads to finish")
            stdout_thread.join(timeout=1.0)
            stderr_thread.join(timeout=1.0)

            # Emit remaining outputs
            self._emit_queued_outputs()

            # Get return code
            return_code = self.process.returncode
            logger.debug("Process completed with return code: %d", return_code)

            # Compile final output
            with self._lock:
                stdout = ''.join(self._stdout_lines)
                stderr = ''.join(self._stderr_lines)

            logger.debug("Captured %d stdout lines, %d stderr lines",
                         len(self._stdout_lines), len(self._stderr_lines))

            self.process_finished.emit(return_code, stdout, stderr)

        except Exception as ex:
            logger.error("Process execution error: %s", ex)
            self.process_error.emit(str(ex))

    def _read_stream(self, stream, stream_name: str, buffer: list[str]):
        """Read a process stream (stdout/stderr) line by line."""
        try:
            for line in iter(stream.readline, ''):
                if self._stop_flag:
                    break

                line = line.rstrip("\n")

                with self._lock:
                    buffer.append(line)

                self._queue_output(line, stream_name)

        except Exception as e:
            logger.error("Error reading %s: %s", stream_name, e)

        finally:
            try:
                stream.close()
            except Exception:
                pass

    def _send_initial_input(self) -> None:
        """Send pre-configured input lines at startup."""
        if not self.process or not self.process.stdin:
            logger.warning("Cannot send input: stdin not available")
            return

        try:
            # Small delay to let process start
            threading.Event().wait(0.2)

            for line in self.input_lines:
                self.send_input(line)
                threading.Event().wait(0.05)

        except Exception as e:
            logger.error(f"Error sending initial input: {e}")

    def _queue_output(self, text: str, stream_type: str):
        """Queue output for emission from QThread context."""
        with self._queue_lock:
            self._output_queue.append((text, stream_type))

    def _emit_queued_outputs(self):
        """Emit all queued outputs from QThread context."""
        with self._queue_lock:
            while self._output_queue:
                text, stream_type = self._output_queue.pop(0)
                self.output_received.emit(text, stream_type)

    def send_input(self, text: str) -> bool:
        """
        Send input to process stdin (for interactive prompts).

        Args:
            text: Text to send to stdin

        Returns:
            True if successful, False otherwise
        """
        if not self.process or not self.process.stdin:
            return False

        try:
            self.process.stdin.write(text + "\n")
            self.process.stdin.flush()
            logger.debug("Sending input: %s", text)
            return True
        except Exception as e:
            logger.error("Failed to send input: %s", e)
            return False

    def stop(self):
        """Stop the running process cleanly."""
        self._stop_flag = True

        if self.process:
            try:
                # Try graceful termination first
                self.process.terminate()
                self.process.wait(timeout=2)
                logger.info("Process terminated gracefully")
            except subprocess.TimeoutExpired:
                # Force kill if needed
                self.process.kill()
                logger.warning("Process killed forcefully")
            except Exception as e:
                logger.error("Error stopping process: %s", e)


# ============================================================================
# Installation Worker Thread
# ============================================================================

class InstallationWorker(QThread):
    """
    Worker thread for non-blocking installation.

    Manages the full installation workflow:
    - Iterates through batches
    - Uses ProcessRunner for each installation
    - Handles errors and user decisions
    - Tracks progress and state
    - Supports pause/resume
    """

    # Signals
    batch_started = Signal(int, int, str)  # current_index, total_batches, mod_id
    component_started = Signal(str, str)  # component_id, mod_name
    component_finished = Signal(str, object)  # component_id, InstallResult
    output_received = Signal(str, str)  # text, stream_type
    error_occurred = Signal(str, list)  # component_id, errors
    warning_occurred = Signal(str, list)  # component_id, warnings
    installation_complete = Signal(dict)  # All results
    installation_stopped = Signal(int)  # last_index
    installation_paused = Signal(int)  # last_index
    installation_retryed = Signal(int)  # count_components

    def __init__(
            self,
            engine: WeiDUInstallerEngine,
            batches: list[list[ComponentInfo]],
            start_index: int,
            languages_order: list[str],
            mod_manager,
            pause_on_error: bool = True,
            pause_on_warning: bool = True,
    ):
        super().__init__()
        self.engine = engine
        self.batches = batches
        self.start_index = start_index
        self.languages_order = languages_order
        self.mod_manager = mod_manager
        self.pause_on_error = pause_on_error
        self.pause_on_warning = pause_on_warning

        self.all_results: dict[str, InstallResult] = {}
        self.is_stopped = False
        self.is_paused = False
        self.decision_ready = False
        self.user_decision: UserDecision | None = None
        self.current_runner: ProcessRunner | None = None

    def run(self):
        """Execute installation process."""
        try:
            total_batches = len(self.batches)
            batch_idx = self.start_index

            while batch_idx < total_batches:
                if self.is_stopped:
                    self.installation_stopped.emit(batch_idx + 1)
                    return

                # Check for pause before starting new batch
                if self.is_paused:
                    logger.info("Installation paused at batch %d", batch_idx + 1)
                    self.installation_paused.emit(batch_idx + 1)
                    # Wait for resume instead of returning
                    while self.is_paused and not self.is_stopped:
                        self.msleep(100)

                    # If stopped during pause, exit
                    if self.is_stopped:
                        self.installation_stopped.emit(batch_idx + 1)
                        return

                    # Resumed, continue with current batch
                    logger.info("Resuming from batch %d", batch_idx)

                batch = self.batches[batch_idx]
                self.batch_started.emit(batch_idx + 1, total_batches, batch[0].mod_id)

                # Install batch
                results = self._install_batch(batch)
                self.all_results.update(results)

                # Check for errors and warnings
                issue = self._find_issue(results)

                if issue:
                    comp_id, result, status = issue

                    if (
                            (status == ComponentStatus.ERROR and self.pause_on_error)
                            or (status == ComponentStatus.WARNING and self.pause_on_warning)
                    ):
                        signal, messages = (
                            (self.error_occurred, result.errors)
                            if status == ComponentStatus.ERROR
                            else (self.warning_occurred, result.warnings)
                        )

                        signal.emit(comp_id, messages)
                        self._wait_for_decision()

                        if self.user_decision == UserDecision.RETRY:
                            self.installation_retryed.emit(len(batch))
                            continue

                        elif self.user_decision == UserDecision.PAUSE:
                            self.is_paused = True
                            logger.info("Installation paused by user after %s", status.name.lower())
                            continue
                        elif self.user_decision == UserDecision.STOP:
                            self.is_stopped = True
                            self.installation_stopped.emit(batch_idx + 1)
                            return

                        # SKIP: continue to next

                batch_idx += 1

            self.installation_complete.emit(self.all_results)

        except Exception as e:
            logger.error('Critical error in installation worker: %s', e)
            self.installation_stopped.emit(self.start_index)

    @staticmethod
    def _find_issue(results):
        """Return (component_id, result, issue_type) or None."""
        warning = None

        for comp_id, result in results.items():
            if result.status == ComponentStatus.ERROR:
                return comp_id, result, ComponentStatus.ERROR

            if result.status == ComponentStatus.WARNING and warning is None:
                warning = (comp_id, result, ComponentStatus.WARNING)

        return warning

    def _install_batch(self, batch: list[ComponentInfo]) -> dict[str, InstallResult]:
        """Install a batch of components with real-time output."""
        mod_id = batch[0].mod_id

        # Get mod name for display
        mod = self.mod_manager.get_mod_by_id(mod_id)
        mod_name = mod.name if mod else mod_id

        language = mod.get_language_index(self.languages_order)
        extra_args = []

        components_to_install = []
        skipped_results = {}

        for comp in batch:
            comp_id = f"{comp.mod_id}:{comp.component_key}"

            if self.engine.is_component_installed(comp.tp2_name, comp.component_key):
                logger.info("Component %s already installed", comp_id)
                skipped_results[comp_id] = InstallResult(
                    status=ComponentStatus.ALREADY_INSTALLED,
                    return_code=0,
                    stdout="",
                    stderr="",
                    warnings=[],
                    errors=[]
                )
                self.component_finished.emit(comp_id, skipped_results[comp_id])
            else:
                components_to_install.append(comp)

        if not components_to_install:
            return skipped_results

        # Emit start signals
        for comp in components_to_install:
            comp_id = f"{comp.mod_id}:{comp.component_key}"
            extra_args = comp.extra_args
            self.component_started.emit(comp_id, mod_name)

        is_single = len(components_to_install) == 1

        results = self.engine.install_components(
            tp2=batch[0].tp2_name,
            components=components_to_install,
            language=language,
            extra_args=extra_args,
            input_lines=components_to_install[0].subcomponent_answers if is_single else [],
            runner_factory=ProcessRunner,
            output_callback=self.output_received.emit,
        )

        for comp_id, result in results.items():
            self.component_finished.emit(comp_id, result)

        # Merge with skipped
        results.update(skipped_results)
        return results

    def _wait_for_decision(self):
        """Wait for user decision on error/warning."""
        self.decision_ready = False
        self.user_decision = None

        while not self.decision_ready and not self.is_stopped and not self.is_paused:
            self.msleep(100)

    def set_user_decision(self, decision: UserDecision):
        """Set user decision and resume."""
        self.user_decision = decision
        self.decision_ready = True

    def pause(self):
        """Request pause after current component finishes."""
        self.is_paused = True
        logger.info("Pause requested")

    def resume(self):
        """Resume from paused state."""
        self.is_paused = False
        logger.info("Resuming installation")

    def stop(self):
        """Stop installation."""
        self.is_stopped = True

        # Stop current runner if active
        if self.current_runner:
            self.current_runner.stop()

    def update_pause_settings(self, pause_on_error: bool, pause_on_warning: bool):
        """Update pause settings during execution."""
        self.pause_on_error = pause_on_error
        self.pause_on_warning = pause_on_warning
        logger.debug(f"Updated pause settings: error={pause_on_error}, warning={pause_on_warning}")
