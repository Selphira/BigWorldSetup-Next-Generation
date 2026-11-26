from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from constants import WEIDU_EXIT_WARNINGS
from core.Mod import Component, Mod
from core.Rules import DependencyRule

WEIDU_TRANSLATION_KEYS = {
    "installing": "@-1016",
    "error_installing": "@-1017",
    "rolling_back": "@-1018",
    "skipping": "@-1020",
    "successfully_installed": "@-1019",
    "installed_with_warnings": "@-1033",
    "not_installed_due_to_errors": "@-1032",
    "installation_aborded": "@-1063",
    "aborting": "@-1064",
    "warning": "WARNING:",
    "error": "ERROR:",
    "saving_log": "Saving This Log",
    "weidu_timings": "WeiDU Timings",
}

# Default English strings (fallback)
DEFAULT_STRINGS = {
    "installing": "Installing",
    "error_installing": "ERROR Installing [",
    "rolling_back": "], rolling back to previous state",
    "skipping": "SKIPPING:",
    "successfully_installed": "SUCCESSFULLY INSTALLED",
    "installed_with_warnings": "INSTALLED WITH WARNINGS",
    "not_installed_due_to_errors": "NOT INSTALLED DUE TO ERRORS",
    "installation_aborded": "INSTALLATION ABORTED",
    "aborting": "Aborting installation of [",
    "warning": "WARNING:",
    "error": "ERROR:",
    "saving_log": "Saving This Log",
    "weidu_timings": "WeiDU Timings",
}


class ComponentStatus(Enum):
    """Status of one planned operation, from its checkpoint to its proven result."""

    PENDING = "pending"
    RUNNING = "running"
    UNKNOWN = "unknown"
    BLOCKED = "blocked"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
    SKIPPED = "skipped"
    ALREADY_INSTALLED = "already_installed"
    STOPPED = "stopped"

    @property
    def is_installed(self) -> bool:
        """Whether the status proves that the component is in the game."""
        return self in (
            ComponentStatus.SUCCESS,
            ComponentStatus.WARNING,
            ComponentStatus.ALREADY_INSTALLED,
        )

    @property
    def is_undetermined(self) -> bool:
        """Whether the game state cannot be deduced without a user decision."""
        return self in (
            ComponentStatus.RUNNING,
            ComponentStatus.STOPPED,
            ComponentStatus.UNKNOWN,
        )


@dataclass(frozen=True)
class BatchOutcome:
    """Facts shared by every component launched by the same WeiDU process."""

    has_failure: bool
    has_warning: bool
    reliable_mapping: bool


@dataclass(frozen=True)
class AttemptEvidence:
    """Facts observed after one WeiDU attempt for one component.

    Attributes:
        return_code: WeiDU exit code, or None when the process never started
        debug_status: Status read in the attempt DEBUG, or None when the
            component was never reached
        in_log: Whether WeiDU.log records the component after the attempt
    """

    return_code: int | None
    debug_status: ComponentStatus | None
    in_log: bool

    def conclude(self, batch: BatchOutcome, unlogged: bool) -> ComponentStatus:
        """Cross-check DEBUG, exit code and WeiDU.log into a single status.

        Args:
            batch: Facts shared by the components of the same process
            unlogged: Whether the TP2 declares the component NO_LOG_RECORD

        Returns:
            A proven status, or UNKNOWN when the facts contradict each other
        """
        if self.return_code is None:
            return ComponentStatus.ERROR
        if self.debug_status is None:
            return ComponentStatus.UNKNOWN if self.in_log else ComponentStatus.ERROR
        if self.debug_status in (ComponentStatus.ERROR, ComponentStatus.SKIPPED):
            return ComponentStatus.UNKNOWN if self.in_log else self.debug_status
        if self.debug_status not in (ComponentStatus.SUCCESS, ComponentStatus.WARNING):
            return ComponentStatus.UNKNOWN
        if not batch.reliable_mapping or not (self.in_log or unlogged):
            return ComponentStatus.UNKNOWN

        # A failed batch can still contain individually confirmed components.
        compatible_code = (
            self.return_code == 0
            or batch.has_failure
            or (self.return_code == WEIDU_EXIT_WARNINGS and batch.has_warning)
        )
        return self.debug_status if compatible_code else ComponentStatus.UNKNOWN

    def to_dict(self) -> dict[str, Any]:
        """Serialize the evidence for the installation journal."""
        return {
            "return_code": self.return_code,
            "debug_status": self.debug_status.value if self.debug_status else None,
            "in_log": self.in_log,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AttemptEvidence":
        """Rebuild evidence saved in the installation journal."""
        debug_status = data["debug_status"]
        return cls(
            data["return_code"],
            ComponentStatus(debug_status) if debug_status else None,
            bool(data["in_log"]),
        )


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
    evidence: AttemptEvidence | None = None


@dataclass(frozen=True, slots=True)
class ProcessResult:
    """Outcome of one WeiDU process, published by the runner once it has exited.

    Attributes:
        exit_code: Process return code, None if the process never started or
            could not be followed until its end
        stdout: Whole standard output, prompts without newline included
        stderr: Whole error output
        launch_error: Error that prevented starting or following the process,
            empty when the run went to its end
    """

    exit_code: int | None
    stdout: str
    stderr: str
    launch_error: str = ""


@dataclass
class ProcessCallbacks:
    """Hooks called by the engine around one WeiDU process.

    Attributes:
        before_launch: Persists the intent; an exception prevents the launch
        output: Receives the process output as it arrives
        runner_created: Receives the runner before it starts
        runner_finished: Called once the runner has exited
        command_created: Receives the command line
        component_reached: Receives the launch index of each component WeiDU
            starts or skips, as its output arrives
    """

    before_launch: Callable[[], None] | None = None
    output: Callable[[str, str], None] | None = None
    component_reached: Callable[[int], None] | None = None
    runner_created: Callable[[Any], None] | None = None
    runner_finished: Callable[[], None] | None = None
    command_created: Callable[[list[str]], None] | None = None


@dataclass
class ComponentInfo:
    """Information about a component to install."""

    comp_id: str
    mod: Mod | None
    component: Component | None
    tp2_name: str
    sequence_idx: int
    requirements: tuple[DependencyRule, ...] = ()
    subcomponent_answers: list[str] = None
    extra_args: list[str] = None

    def __post_init__(self):
        """Ensure subcomponent_answers is a list."""
        if self.subcomponent_answers is None:
            self.subcomponent_answers = []
