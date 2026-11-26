"""Execution of WeiDU processes and of the batches of one sequence."""

import codecs
from collections import deque
import contextlib
from dataclasses import replace
from enum import Enum
import logging
import os
from pathlib import Path
import signal
import subprocess
import threading

from PySide6.QtCore import QThread, Signal

from constants import PROCESS_POLL_MS, PROCESS_READ_CHUNK_SIZE, WORKER_POLL_MS
from core.InstallationJournal import InstallationJournal, Operation, OperationDecision
from core.models.PauseEntry import PauseEntry
from core.RuleManager import RuleManager
from core.TranslationManager import tr
from core.weidu_types import (
    ComponentInfo,
    ComponentStatus,
    InstallResult,
    ProcessCallbacks,
    ProcessResult,
)
from core.WeiDUInstallerEngine import WeiDUInstallerEngine

logger = logging.getLogger(__name__)


# ============================================================================
# Constants and Enums
# ============================================================================


class UserDecision(Enum):
    """User decision after installation error/warning."""

    RETRY = "retry"
    SKIP = "skip"
    PAUSE = "pause"
    STOP = "stop"


# ============================================================================
# Process Runner
# ============================================================================


class ProcessRunner(QThread):
    """Process runner"""

    output_received = Signal(str, str)  # text, stream_type ("stdout"/"stderr")
    process_error = Signal(str)

    def __init__(self, cmd: list[str], cwd: Path, input_lines: list[str] | None = None):
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
        self._force_requested = threading.Event()
        self._stdout_lines: list[str] = []
        self._stderr_lines: list[str] = []
        self._lock = threading.Lock()
        self._result: ProcessResult | None = None

        # Queue for thread-safe signal emission
        self._output_queue: deque[tuple[str, str]] = deque()
        self._queue_lock = threading.Lock()

    @property
    def result(self) -> ProcessResult | None:
        """Outcome of the process, None until run() has returned."""
        return self._result

    def run(self) -> None:
        """Execute process with parallel stdout/stderr reading."""
        launch_error = ""
        try:
            logger.debug("Starting process: %s", " ".join(str(c) for c in self.cmd))
            logger.debug("Working directory: %s", self.cwd)

            if self._stop_flag:
                return
            self.process = subprocess.Popen(
                self.cmd,
                cwd=str(self.cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.PIPE,
                start_new_session=os.name != "nt",
                errors="replace",
                bufsize=1,
                text=True,
            )

            logger.debug("Process started with PID: %s", self.process.pid)

            stdout_thread = threading.Thread(
                target=self._read_stream,
                args=(self.process.stdout, "stdout", self._stdout_lines),
                name="WeiDU-stdout",
                daemon=True,
            )

            stderr_thread = threading.Thread(
                target=self._read_stream,
                args=(self.process.stderr, "stderr", self._stderr_lines),
                name="WeiDU-stderr",
                daemon=True,
            )

            stdout_thread.start()
            stderr_thread.start()

            # Send pre-configured input lines immediately
            # TODO: It may not work in all cases. Using a pipe command may be necessary, but compatibility and thread management issues need to be considered...
            if self.input_lines:
                threading.Thread(target=self._send_initial_input, daemon=True).start()

            # Process queued outputs periodically from QThread
            while self.process.poll() is None:
                if self._force_requested.is_set():
                    self._force_requested.clear()
                    self._terminate_tree()
                if self._stop_flag and not self.process.stdin.closed:
                    self.process.stdin.close()
                self._emit_queued_outputs()
                self.msleep(PROCESS_POLL_MS)

            # Wait for reader threads
            logger.debug("Waiting for reader threads to finish")
            stdout_thread.join(timeout=1.0)
            stderr_thread.join(timeout=1.0)

            # Emit remaining outputs
            self._emit_queued_outputs()

            logger.debug("Process completed with return code: %d", self.process.returncode)

            if self.process.stdin:
                self.process.stdin.close()

        except Exception as ex:
            logger.error("Process execution error: %s", ex)
            launch_error = str(ex)
            self.process_error.emit(launch_error)

        finally:
            self._result = self._build_result(launch_error)

    def _build_result(self, launch_error: str) -> ProcessResult:
        """Join the captured output once; reader threads may still be appending."""
        with self._lock:
            stdout = "".join(self._stdout_lines)
            stderr = "".join(self._stderr_lines)
            logger.debug(
                "Captured %d stdout chunks, %d stderr chunks",
                len(self._stdout_lines),
                len(self._stderr_lines),
            )

        exit_code = self.process.returncode if self.process else None
        return ProcessResult(exit_code, stdout, stderr, launch_error)

    def _read_stream(self, stream, stream_name: str, buffer: list[str]):
        """Forward a process stream as it arrives, prompts without newline included."""
        try:
            decoder = codecs.getincrementaldecoder(stream.encoding)(errors="replace")
            while True:
                chunk = os.read(stream.fileno(), PROCESS_READ_CHUNK_SIZE)
                output = decoder.decode(chunk, final=not chunk)
                if output:
                    with self._lock:
                        buffer.append(output)
                    self._queue_output(output, stream_name)
                if not chunk:
                    break

        except Exception as e:
            logger.error("Error reading %s: %s", stream_name, e)

        finally:
            with contextlib.suppress(Exception):
                stream.close()

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

    def _emit_queued_outputs(self) -> None:
        """Emit queued outputs in arrival order, outside the lock.

        Direct connections run their slots in this thread: holding the lock while
        emitting would block the reader threads for as long as those slots last.
        """
        with self._queue_lock:
            outputs, self._output_queue = self._output_queue, deque()

        for text, stream_type in outputs:
            self.output_received.emit(text, stream_type)

    def send_input(self, text: str) -> bool:
        """
        Send input to process stdin (for interactive prompts).

        Args:
            text: Text to send to stdin

        Returns:
            True if successful, False otherwise
        """
        if not self.process or not self.process.stdin or self.process.stdin.closed:
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
        """Close stdin so that WeiDU ends at its next prompt or once its work is done."""
        self._stop_flag = True

    def force_stop(self) -> None:
        """Terminate the process tree after explicit confirmation."""
        self._force_requested.set()

    def _terminate_tree(self) -> None:
        """Kill WeiDU and its children; a partial modification may need a restore."""
        process = self.process
        if process is None or process.poll() is not None:
            return
        try:
            if os.name == "nt":
                command = Path(os.environ["SYSTEMROOT"]) / "System32" / "taskkill.exe"
                subprocess.run(
                    [str(command), "/PID", str(process.pid), "/T", "/F"],
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    check=True,
                    timeout=10,
                    capture_output=True,
                )
            else:
                os.killpg(process.pid, signal.SIGKILL)
        except (OSError, subprocess.SubprocessError) as error:
            if process.poll() is None:
                self._queue_output(str(error), "stderr")
                self.process_error.emit(str(error))


# ============================================================================
# Installation Worker Thread
# ============================================================================


class InstallationWorker(QThread):
    """
    Worker thread executing the remaining batches of one sequence.

    Each batch is checkpointed in the journal before WeiDU starts and its
    result is recorded before Qt is notified or the next batch starts.

    Signals:
        batch_started: (batch number, batch count, mod id)
        component_started: (component id, mod name, component name) when WeiDU reaches it
        component_finished: (component id, InstallResult) once recorded
        output_received: (text, stream type) of the WeiDU process
        error_occurred: (component id, messages); waits for set_user_decision
        warning_occurred: (component id, messages); waits for set_user_decision
        dependents_blocked: references newly blocked by a failure
        installation_paused: (batch number, pause description)
        installation_retryed: number of operations about to be retried
        diagnostic: message explaining why the run was suspended
        command_created: WeiDU command line
    """

    batch_started = Signal(int, int, str)
    component_started = Signal(str, str, str)
    component_finished = Signal(str, object)
    output_received = Signal(str, str)
    error_occurred = Signal(str, list)
    warning_occurred = Signal(str, list)
    dependents_blocked = Signal(list)
    installation_paused = Signal(int, str)
    installation_retryed = Signal(int)
    diagnostic = Signal(str)
    command_created = Signal(str)

    def __init__(
        self,
        engine: WeiDUInstallerEngine,
        batches: list[list[ComponentInfo]],
        journal: InstallationJournal,
        rules: RuleManager,
        sequence: int = 0,
        pause_on_error: bool = True,
        pause_on_warning: bool = True,
    ):
        super().__init__()
        self.engine = engine
        self.batches = batches
        self.journal = journal
        self.rules = rules
        self.sequence = sequence
        self.pause_on_error = pause_on_error
        self.pause_on_warning = pause_on_warning

        self.blocked_positions: set[int] = set()
        self.last_batch = 0
        self.is_stopped = False
        self.is_paused = False
        self.decision_ready = False
        self.user_decision: UserDecision | None = None
        self._active_runner: ProcessRunner | None = None
        self._runner_lock = threading.Lock()

    @property
    def _operations(self) -> list[Operation]:
        return self.journal.sequences[self.sequence].operations

    # ========================================
    # Execution
    # ========================================

    def run(self) -> None:
        """Execute the batches; any unexpected failure suspends the run."""
        try:
            self._update_blocked()
            for number, batch in enumerate(self.batches, start=1):
                self._wait_while_paused(number)
                if self.is_stopped:
                    break

                self.last_batch = number
                if PauseEntry.is_pause(batch[0].comp_id):
                    self._pause_at_entry(number, batch[0])
                else:
                    self.batch_started.emit(number, len(self.batches), batch[0].mod.id)
                    self._process_batch(number, batch)
                if self.is_stopped:
                    break
        except Exception as error:
            self.is_stopped = True
            logger.exception("Installation suspended at batch %d", self.last_batch)
            self.diagnostic.emit(str(error))

    def _process_batch(self, number: int, batch: list[ComponentInfo]) -> None:
        """Install a batch until its failures are retried away or accepted."""
        while not self.is_stopped:
            results = self._collect_results(batch)
            self._record(batch, results)
            if self.is_stopped:
                return

            undetermined = [
                ref for ref, result in results.items() if result.status.is_undetermined
            ]
            if undetermined:
                self.is_stopped = True
                self.diagnostic.emit(
                    tr("page.installation.undetermined", components=", ".join(undetermined))
                )
                return

            decision = self._batch_decision(results)
            if decision == UserDecision.STOP:
                self.is_stopped = True
                return
            if decision == UserDecision.PAUSE:
                # Once resumed, the same failures are presented again.
                self.is_paused = True
                self._wait_while_paused(number)
                continue
            if decision == UserDecision.RETRY:
                self._retry(batch, results)
                continue

            self._accept_failures(batch)
            return

    def _collect_results(self, batch: list[ComponentInfo]) -> dict[str, InstallResult]:
        """Install the pending operations; blocked and failed ones are reported as is."""
        results = {}
        pending = []
        for comp in batch:
            operation = self._operations[comp.sequence_idx]
            if not operation.needs_work:
                continue
            if comp.sequence_idx in self.blocked_positions:
                results[comp.comp_id] = InstallResult(
                    ComponentStatus.BLOCKED, 0, "", "", [], []
                )
            elif operation.status == ComponentStatus.ERROR:
                results[comp.comp_id] = InstallResult(
                    ComponentStatus.ERROR, -1, "", "", [], list(operation.messages)
                )
            else:
                pending.append(comp)
        if pending:
            results.update(self._install_batch(pending))
        return results

    def _record(self, batch: list[ComponentInfo], results: dict[str, InstallResult]) -> None:
        """Checkpoint the results, then notify the interface."""
        self.journal.record(
            self.sequence,
            {
                comp.sequence_idx: results[comp.comp_id]
                for comp in batch
                if comp.comp_id in results
            },
        )
        for reference, result in results.items():
            self.component_finished.emit(reference, result)

    def _pause_at_entry(self, number: int, entry: ComponentInfo) -> None:
        _, description = PauseEntry.parse(entry.comp_id)
        self.is_paused = True
        self._wait_while_paused(number, description)
        if not self.is_stopped:
            result = InstallResult(ComponentStatus.SKIPPED, 0, "", "", [], [])
            self.journal.record(self.sequence, {entry.sequence_idx: result})

    def _wait_while_paused(self, number: int, description: str = "") -> None:
        if not self.is_paused:
            return
        self.installation_paused.emit(number, description)
        while self.is_paused and not self.is_stopped:
            self.msleep(WORKER_POLL_MS)

    # ========================================
    # Decisions
    # ========================================

    def _batch_decision(self, results: dict[str, InstallResult]) -> UserDecision:
        """Ask the user when needed; failures always show the components they block."""
        issue = self._find_issue(results)
        if not issue:
            return UserDecision.SKIP

        reference, result, status = issue
        if status == ComponentStatus.WARNING:
            if not self.pause_on_warning:
                return UserDecision.SKIP
            warned = {
                ref: r.warnings
                for ref, r in results.items()
                if r.status == ComponentStatus.WARNING
            }
            return self._ask(
                self.warning_occurred, ", ".join(warned), self._by_component(warned)
            )

        partial = any(r.status.is_installed for r in results.values())
        affected = self._preview_blocked()
        if not (self.pause_on_error or partial or affected):
            return UserDecision.SKIP
        messages = list(result.errors)
        if affected:
            messages.append(
                tr("page.installation.blocked_preview", components="\n".join(affected))
            )
        return self._ask(self.error_occurred, reference, messages)

    @staticmethod
    def _by_component(messages: dict[str, list[str]]) -> list[str]:
        """Messages of one component as is, of several under the reference of each."""
        if len(messages) == 1:
            return next(iter(messages.values()))
        lines = []
        for reference, component_messages in messages.items():
            lines += [f"[{reference}]", *(f"  {line}" for line in component_messages), ""]
        return lines[:-1]

    def _ask(self, signal: Signal, reference: str, messages: list[str]) -> UserDecision:
        self.decision_ready = False
        self.user_decision = None
        signal.emit(reference, messages)
        while not self.decision_ready and not self.is_stopped:
            self.msleep(WORKER_POLL_MS)
        return self.user_decision or UserDecision.STOP

    def _retry(self, batch: list[ComponentInfo], results: dict[str, InstallResult]) -> None:
        """Retry the failed operations, or the warned ones when only warnings were raised."""

        def positions(status: ComponentStatus) -> list[int]:
            return [
                comp.sequence_idx
                for comp in batch
                if comp.comp_id in results and results[comp.comp_id].status == status
            ]

        retried = positions(ComponentStatus.ERROR) or positions(ComponentStatus.WARNING)
        for position in retried:
            self.journal.decide(self.sequence, position, OperationDecision.RETRY, in_log=False)
        self.installation_retryed.emit(len(retried))

    def _accept_failures(self, batch: list[ComponentInfo]) -> None:
        """Continue without the failed operations and block what depended on them."""
        failed = [
            comp.sequence_idx
            for comp in batch
            if self._operations[comp.sequence_idx].status == ComponentStatus.ERROR
        ]
        for position in failed:
            self.journal.decide(
                self.sequence, position, OperationDecision.ACCEPT_FAILURE, in_log=False
            )
        if failed or any(
            self._operations[comp.sequence_idx].status == ComponentStatus.SKIPPED
            for comp in batch
        ):
            self._update_blocked()

    def _preview_blocked(self) -> list[str]:
        """Remaining operations that a definitive failure would block."""
        return [
            f"{seq + 1} / {self.journal.sequences[seq].operations[pos].reference}"
            for seq, pos in sorted(self.journal.blocked_positions(self.rules, preview=True))
            if self.journal.sequences[seq].operations[pos].needs_work
        ]

    def _update_blocked(self) -> None:
        positions = {
            pos
            for seq, pos in self.journal.blocked_positions(self.rules)
            if seq == self.sequence
        }
        new = positions - self.blocked_positions
        self.blocked_positions = positions
        if new:
            self.dependents_blocked.emit(
                [self._operations[pos].reference for pos in sorted(new)]
            )

    @staticmethod
    def _find_issue(
        results: dict[str, InstallResult],
    ) -> tuple[str, InstallResult, ComponentStatus] | None:
        """Return (component_id, result, issue_type) or None; errors come first."""
        warning = None
        for comp_id, result in results.items():
            if result.status == ComponentStatus.ERROR:
                return comp_id, result, ComponentStatus.ERROR
            if result.status == ComponentStatus.WARNING and warning is None:
                warning = (comp_id, result, ComponentStatus.WARNING)
        return warning

    # ========================================
    # WeiDU process
    # ========================================

    def _install_batch(self, batch: list[ComponentInfo]) -> dict[str, InstallResult]:
        """Install a batch of components with real-time output."""
        # Read on every call: each process, retry or reconciliation may change the log.
        installed = self.engine.installed_identities()
        already_installed = {}
        to_install = []
        for comp in batch:
            operation = self._operations[comp.sequence_idx]
            reinstall = operation.decision == OperationDecision.RETRY
            if not reinstall and operation.weidu_identity in installed:
                logger.info("Component %s already installed", comp.comp_id)
                confirmed = self.journal.previously_confirmed(self.sequence, comp.sequence_idx)
                already_installed[comp.comp_id] = InstallResult(
                    ComponentStatus.ALREADY_INSTALLED if confirmed else ComponentStatus.UNKNOWN,
                    0,
                    "",
                    "",
                    [],
                    [],
                )
            else:
                to_install.append(comp)
        if not to_install:
            return already_installed

        first = to_install[0]
        self._announce(first)

        def reached(index: int) -> None:
            # The first component is announced before launch.
            if 0 < index < len(to_install):
                self._announce(to_install[index])

        positions = [comp.sequence_idx for comp in to_install]
        callbacks = ProcessCallbacks(
            # Persist the intent only once nothing can fail before WeiDU starts.
            before_launch=lambda: self.journal.begin(self.sequence, positions),
            output=self.output_received.emit,
            component_reached=reached,
            runner_created=self._on_runner_created,
            runner_finished=self._on_runner_finished,
            command_created=self._on_command_created,
        )
        if first.component.is_dwn():
            results = self.engine.install_no_weidu_components(
                first.mod.id, to_install, ProcessRunner, callbacks
            )
        else:
            results = self.engine.install_components(
                tp2=first.tp2_name,
                components=to_install,
                language=str(self._operations[first.sequence_idx].language),
                extra_args=first.extra_args,
                input_lines=first.subcomponent_answers if len(to_install) == 1 else [],
                runner_factory=ProcessRunner,
                callbacks=callbacks,
            )

        if self.is_stopped:
            self._mark_interrupted(results)
        return results | already_installed

    def _announce(self, comp: ComponentInfo) -> None:
        """Tell the interface which component WeiDU is installing."""
        mod_name = comp.mod.name or comp.mod.id
        self.component_started.emit(comp.comp_id, mod_name, comp.component.get_name())

    @staticmethod
    def _mark_interrupted(results: dict[str, InstallResult]) -> None:
        """An unproven result after a stop request is an interruption."""
        for comp_id, result in results.items():
            if result.status == ComponentStatus.UNKNOWN:
                results[comp_id] = replace(result, status=ComponentStatus.STOPPED)

    # ========================================
    # Control from the interface
    # ========================================

    def set_user_decision(self, decision: UserDecision) -> None:
        """Set user decision and resume."""
        self.user_decision = decision
        self.decision_ready = True

    def pause(self) -> None:
        """Request pause after current component finishes."""
        self.is_paused = True
        logger.info("Pause requested")

    def resume(self) -> None:
        """Resume from paused state."""
        self.is_paused = False
        logger.info("Resuming installation")

    def stop(self) -> None:
        """Stop after the current process; its stdin is closed to end any prompt."""
        self.is_stopped = True
        with self._runner_lock:
            if self._active_runner:
                logger.info("Stopping active runner")
                self._active_runner.stop()

    def force_stop(self) -> None:
        """Terminate the WeiDU process tree after an explicit confirmation."""
        with self._runner_lock:
            if self._active_runner:
                self._active_runner.force_stop()

    def has_live_process(self) -> bool:
        """Whether a WeiDU process is still running."""
        with self._runner_lock:
            runner = self._active_runner
            return bool(runner and runner.process and runner.process.poll() is None)

    def send_input(self, text: str) -> bool:
        """
        Send input to the currently active runner.

        Args:
            text: Text to send to stdin

        Returns:
            True if input was sent successfully, False otherwise
        """
        with self._runner_lock:
            if self._active_runner:
                return self._active_runner.send_input(text)
            logger.warning("No active runner to send input to")
            return False

    def update_pause_settings(self, pause_on_error: bool, pause_on_warning: bool) -> None:
        """Update pause settings during execution."""
        self.pause_on_error = pause_on_error
        self.pause_on_warning = pause_on_warning
        logger.debug(
            f"Updated pause settings: error={pause_on_error}, warning={pause_on_warning}"
        )

    def _on_command_created(self, command: list[str]) -> None:
        self.command_created.emit(" ".join(command))

    def _on_runner_created(self, runner: ProcessRunner) -> None:
        """Keep the runner reachable for input and stop requests."""
        runner.process_error.connect(self.diagnostic)
        with self._runner_lock:
            self._active_runner = runner
            if self.is_stopped:
                runner.stop()

    def _on_runner_finished(self) -> None:
        with self._runner_lock:
            self._active_runner = None
