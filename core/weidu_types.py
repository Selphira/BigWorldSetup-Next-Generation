from dataclasses import dataclass
from enum import Enum

from core.Mod import Component, Mod

WEIDU_TRANSLATION_KEYS = {
    "installing": "@-1016",
    "skipping": "@-1020",
    "successfully_installed": "@-1019",
    "installed_with_warnings": "@-1033",
    "not_installed_due_to_errors": "@-1032",
    "installation_aborded": "@-1063",
    "warning": "WARNING:",
    "error": "ERROR:",
    "saving_log": "Saving This Log",
    "weidu_timings": "WeiDU Timings",
}

# Default English strings (fallback)
DEFAULT_STRINGS = {
    "installing": "Installing",
    "skipping": "SKIPPING:",
    "successfully_installed": "SUCCESSFULLY INSTALLED",
    "installed_with_warnings": "INSTALLED WITH WARNINGS",
    "not_installed_due_to_errors": "NOT INSTALLED DUE TO ERRORS",
    "installation_aborded": "INSTALLATION ABORTED",
    "warning": "WARNING:",
    "error": "ERROR:",
    "saving_log": "Saving This Log",
    "weidu_timings": "WeiDU Timings",
}


class ComponentStatus(Enum):
    """Status of a component installation."""

    INSTALLING = "installing"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
    SKIPPED = "skipped"
    ALREADY_INSTALLED = "already_installed"
    STOPPED = "stopped"


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

    comp_id: str
    mod: Mod | None
    component: Component | None
    tp2_name: str
    sequence_idx: int
    requirements: set[tuple[str, str]] = ()
    subcomponent_answers: list[str] = None
    extra_args: list[str] = None

    def __post_init__(self):
        """Ensure subcomponent_answers is a list."""
        if self.subcomponent_answers is None:
            self.subcomponent_answers = []
