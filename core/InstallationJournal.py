"""Durable checkpoints of the current local installation.

The journal is written before WeiDU is launched and before a batch result is
reported, so that an interrupted run can be resumed from what was proven.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import json
import logging
from pathlib import Path
from typing import Any

from constants import DWN_TP2_LANGUAGE, DWN_TP2_PREFIX, INSTALLATION_JOURNAL_FORMAT
from core.atomic_io import write_json_atomic
from core.ComponentReference import ComponentReference
from core.models.PauseEntry import PauseEntry
from core.RuleManager import RuleManager
from core.TranslationManager import tr
from core.weidu_types import AttemptEvidence, ComponentStatus, InstallResult

logger = logging.getLogger(__name__)

# Rule fields that describe a requirement without changing what it requires
DESCRIPTIVE_RULE_FIELDS = frozenset({"description", "source_url"})

# Layout of the unversioned journal written by the development builds of L4
DRAFT_PLAN_PREFERENCES = ("pause_on_error", "pause_on_warning")
DRAFT_RECIPE_FIELDS = (
    "reference",
    "tp2",
    "language",
    "batch",
    "answers",
    "arguments",
    "kind",
    "version",
)


class JournalError(Exception):
    """The journal file exists but cannot be read."""


class RunDivergenceError(Exception):
    """The game no longer matches the journal; the run cannot continue."""


class OperationDecision(Enum):
    """Decision recorded by the user for an operation without a proven result."""

    CONFIRM_INSTALLED = "confirm_installed"
    RETRY = "retry"
    ACCEPT_FAILURE = "accept_failure"


class RunOutcome(Enum):
    """Final assessment of a run."""

    COMPLETE = "complete"
    WARNINGS = "warnings"
    INCOMPLETE = "incomplete"


# ============================================================================
# Plan and operations
# ============================================================================


@dataclass
class RunPlan:
    """Choices frozen when the run starts; a resume never reads the current selection."""

    game: str
    folders: dict[str, str]
    orders: dict[int, list[str]]
    selection: list[str]
    game_language: str
    languages: list[str]
    batch: bool

    def verify_coverage(self) -> None:
        """Raise ValueError when a selected component is missing from every order."""
        ordered = {
            ComponentReference.from_string(ref) for seq in self.orders.values() for ref in seq
        }
        for value in self.selection:
            ref = ComponentReference.from_string(value)
            if ref.mod_id in ("weidu64", "weidu"):
                continue
            if (
                ref.is_component() or ref.is_sub_option()
            ) and ref.get_base_component_reference() not in ordered:
                raise ValueError(value)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunPlan:
        """Rebuild a plan saved in the journal."""
        return cls(**(data | {"orders": {int(k): v for k, v in data["orders"].items()}}))


@dataclass
class Operation:
    """One planned occurrence of a component, with its recipe and its proven state."""

    reference: str
    tp2: str
    language: int | None
    batch: int
    answers: list[str]
    arguments: list[str]
    requirements: list[str]
    kind: str
    version: str = ""
    status: ComponentStatus = ComponentStatus.PENDING
    decision: OperationDecision | None = None
    evidence: AttemptEvidence | None = None
    messages: list[str] = field(default_factory=list)

    @property
    def recipe(self) -> tuple[Any, ...]:
        """Everything that determines what WeiDU is asked to do."""
        return (
            self.reference,
            self.tp2,
            self.language,
            self.batch,
            self.answers,
            self.arguments,
            self.requirements,
            self.kind,
            self.version,
        )

    @property
    def is_pause(self) -> bool:
        return PauseEntry.is_pause(self.reference)

    @property
    def weidu_identity(self) -> tuple[str, str, str]:
        """(TP2, component, language) as recorded by WeiDU.log."""
        mod_id, number = self.reference.split(":", 1)
        if self.kind == "dwn":
            return f"{DWN_TP2_PREFIX}{mod_id}", number, DWN_TP2_LANGUAGE
        return self.tp2.lower(), number, str(self.language)

    @property
    def needs_work(self) -> bool:
        """Pending, or failed without a decision to continue without it."""
        return self.status == ComponentStatus.PENDING or (
            self.status == ComponentStatus.ERROR
            and self.decision != OperationDecision.ACCEPT_FAILURE
        )

    @property
    def installed(self) -> bool:
        return self.status.is_installed

    @property
    def confirmed_without_log(self) -> bool:
        """Installed although WeiDU.log has no entry (NO_LOG_RECORD or user decision)."""
        return self.installed and self.evidence is not None and not self.evidence.in_log

    @staticmethod
    def requirement_key(rule: dict[str, Any]) -> str:
        """Canonical text of a requirement, ignoring its descriptive fields."""
        relevant = {k: v for k, v in rule.items() if k not in DESCRIPTIVE_RULE_FIELDS}
        return json.dumps(relevant, sort_keys=True, ensure_ascii=False)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the operation for the journal."""
        data = asdict(self)
        data["status"] = self.status.value
        data["decision"] = self.decision.value if self.decision else None
        data["evidence"] = self.evidence.to_dict() if self.evidence else None
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Operation:
        """Rebuild an operation saved in the journal."""
        evidence = data.get("evidence")
        decision = data.get("decision")
        return cls(
            **(
                data
                | {
                    "status": ComponentStatus(data["status"]),
                    "decision": OperationDecision(decision) if decision else None,
                    "evidence": AttemptEvidence.from_dict(evidence) if evidence else None,
                }
            )
        )


@dataclass
class RunSequence:
    """Operations of one sequence and the identity of the folder they modify."""

    folder: str
    base: str
    operations: list[Operation]

    @staticmethod
    def identify(folder: Path) -> str:
        """Identity of the folder itself: a restored or replaced game is another base.

        The content of the game changes legitimately during the installation
        (chitin.key, BIFF files…), so it is not part of the identity.
        """
        stat = folder.stat()
        return f"{stat.st_dev}:{stat.st_ino}"

    def verify_base(self) -> None:
        """Raise RunDivergenceError when the folder is no longer the journal's base."""
        folder = Path(self.folder)
        try:
            current = self.identify(folder)
        except OSError as error:
            raise RunDivergenceError(
                tr("page.installation.divergence.folder_missing", folder=self.folder)
            ) from error
        if current != self.base:
            raise RunDivergenceError(
                tr("page.installation.divergence.folder", folder=self.folder)
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "folder": self.folder,
            "base": self.base,
            "operations": [op.to_dict() for op in self.operations],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunSequence:
        return cls(
            data["folder"], data["base"], [Operation.from_dict(op) for op in data["operations"]]
        )


# ============================================================================
# Journal
# ============================================================================


class InstallationJournal:
    """Plan, operations and their transitions, saved atomically after each change."""

    def __init__(
        self, path: Path, plan: RunPlan, sequences: list[RunSequence], active: bool = True
    ):
        self.path = path
        self.plan = plan
        self.sequences = sequences
        self.active = active

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, path: Path) -> InstallationJournal | None:
        """Read the journal, or return None when there is none.

        Raises:
            JournalError: The file exists but cannot be interpreted
        """
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if "format" not in data:
                data = cls._upgrade_draft(data)
            if (
                data["format"] != INSTALLATION_JOURNAL_FORMAT
                or type(data["active"]) is not bool
            ):
                raise ValueError(f"format {data['format']}")
            return cls(
                path,
                RunPlan.from_dict(data["plan"]),
                [RunSequence.from_dict(seq) for seq in data["sequences"]],
                data["active"],
            )
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
            raise JournalError(f"{type(error).__name__}: {error}") from error

    def save(self) -> None:
        """Replace the journal atomically."""
        write_json_atomic(
            self.path,
            {
                "format": INSTALLATION_JOURNAL_FORMAT,
                "plan": asdict(self.plan),
                "sequences": [seq.to_dict() for seq in self.sequences],
                "active": self.active,
            },
        )

    @staticmethod
    def _upgrade_draft(data: dict[str, Any]) -> dict[str, Any]:
        """Read the unversioned layout written by the development builds of L4."""
        logger.info("Upgrading unversioned installation journal")
        plan = {
            key: value
            for key, value in data["plan"].items()
            if key not in DRAFT_PLAN_PREFERENCES
        }
        sequences = [
            {
                "folder": seq["folder"],
                # The draft identity also hashed chitin.key, which mods legitimately change.
                "base": ":".join(seq["base"].split(":")[:2]),
                "operations": [
                    InstallationJournal._upgrade_draft_operation(op) for op in seq["operations"]
                ],
            }
            for seq in data["sequences"]
        ]
        return {
            "format": INSTALLATION_JOURNAL_FORMAT,
            "plan": plan,
            "sequences": sequences,
            "active": data["active"],
        }

    @staticmethod
    def _upgrade_draft_operation(op: dict[str, Any]) -> dict[str, Any]:
        evidence = None
        if op.get("confirmed_without_log"):
            evidence = {
                "return_code": op.get("return_code"),
                "debug_status": op["status"],
                "in_log": False,
            }
        requirements = [
            Operation.requirement_key(rule) if isinstance(rule, dict) else rule
            for rule in op["requirements"]
        ]
        return {key: op[key] for key in DRAFT_RECIPE_FIELDS} | {
            "requirements": requirements,
            "status": op["status"],
            "decision": OperationDecision.ACCEPT_FAILURE.value
            if op.get("acknowledged")
            else None,
            "evidence": evidence,
            "messages": op.get("messages", []),
        }

    # ------------------------------------------------------------------
    # Transitions
    # ------------------------------------------------------------------

    def begin(self, sequence: int, positions: list[int]) -> None:
        """Persist that WeiDU is about to modify the game for these operations."""
        target = self.sequences[sequence]
        target.verify_base()
        for position in positions:
            target.operations[position].status = ComponentStatus.RUNNING
        self.save()

    def record(self, sequence: int, results: dict[int, InstallResult]) -> None:
        """Persist the proven result of operations before anyone is notified."""
        operations = self.sequences[sequence].operations
        for position, result in results.items():
            operation = operations[position]
            operation.status = result.status
            operation.decision = None
            if result.evidence is not None:
                operation.evidence = result.evidence
            operation.messages = result.errors + result.warnings
        self.save()

    def decide(
        self, sequence: int, position: int, decision: OperationDecision, in_log: bool
    ) -> None:
        """Apply a user decision to an operation, or a group of failed operations.

        Args:
            sequence: Sequence index
            position: Operation index within the sequence
            decision: Decision of the user
            in_log: Whether WeiDU.log currently records the component
        """
        operation = self.sequences[sequence].operations[position]
        operation.decision = decision
        if decision == OperationDecision.CONFIRM_INSTALLED:
            previous = operation.evidence
            operation.status = ComponentStatus.SUCCESS
            operation.evidence = AttemptEvidence(
                previous.return_code if previous else None,
                previous.debug_status if previous else None,
                in_log,
            )
        elif decision == OperationDecision.RETRY:
            operation.status = ComponentStatus.PENDING
        else:
            operation.status = ComponentStatus.ERROR
        self.save()

    def reopen(self, sequence: int, position: int) -> None:
        """Mark an installed operation whose WeiDU.log entry disappeared as undetermined."""
        operation = self.sequences[sequence].operations[position]
        previous = operation.evidence
        operation.status = ComponentStatus.UNKNOWN
        operation.decision = None
        operation.evidence = AttemptEvidence(
            previous.return_code if previous else None,
            previous.debug_status if previous else None,
            in_log=False,
        )

    def deactivate(self) -> None:
        """Close the run; its journal stays readable until the next run starts."""
        self.active = False
        self.save()

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def undetermined_positions(self) -> list[tuple[int, int]]:
        """Operations whose result must be decided before the run can continue."""
        return [
            (seq_idx, pos)
            for seq_idx, sequence in enumerate(self.sequences)
            for pos, op in enumerate(sequence.operations)
            if op.status.is_undetermined and not op.is_pause
        ]

    def previously_confirmed(self, sequence: int, position: int) -> bool:
        """Whether another operation of the same folder already installed this identity."""
        target = self.sequences[sequence]
        current = target.operations[position]
        return any(
            op.installed and op.weidu_identity == current.weidu_identity
            for seq in self.sequences
            if seq.folder == target.folder
            for op in seq.operations
        )

    def blocked_positions(
        self, rules: RuleManager, *, preview: bool = False
    ) -> set[tuple[int, int]]:
        """Operations whose prerequisites can no longer be satisfied.

        Args:
            rules: Dependency rules
            preview: Also treat failures awaiting a decision as definitive

        Returns:
            (sequence, position) of the blocked operations
        """
        orders = [
            [ComponentReference.from_string(op.reference) for op in seq.operations]
            for seq in self.sequences
        ]
        unavailable = {
            (seq, pos)
            for seq, data in enumerate(self.sequences)
            for pos, op in enumerate(data.operations)
            if not op.is_pause
            and (
                op.status in (ComponentStatus.SKIPPED, ComponentStatus.BLOCKED)
                or (op.status == ComponentStatus.ERROR and (not op.needs_work or preview))
            )
        }
        return rules.blocked_positions(
            orders,
            unavailable,
            (-1, -1),
            set(ComponentReference.from_string_list(self.plan.selection)),
        )

    def stats(self) -> dict[str, int]:
        """Counters displayed by the installation page."""
        counts = {"success": 0, "warnings": 0, "errors": 0, "skipped": 0}
        for op in self._component_operations():
            if op.status == ComponentStatus.WARNING:
                counts["warnings"] += 1
            elif op.installed:
                counts["success"] += 1
            elif op.status in (ComponentStatus.SKIPPED, ComponentStatus.BLOCKED):
                counts["skipped"] += 1
            elif op.status not in (ComponentStatus.PENDING, ComponentStatus.RUNNING):
                counts["errors"] += 1
        return counts

    def outcome(self) -> RunOutcome:
        """Complete only when every requested component is proven installed."""
        operations = self._component_operations()
        if not operations or any(not op.installed for op in operations):
            return RunOutcome.INCOMPLETE
        if any(op.status == ComponentStatus.WARNING for op in operations):
            return RunOutcome.WARNINGS
        return RunOutcome.COMPLETE

    def _component_operations(self) -> list[Operation]:
        return [op for seq in self.sequences for op in seq.operations if not op.is_pause]
