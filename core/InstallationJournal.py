"""Durable checkpoints of the current local installation.

The journal is written before WeiDU is launched and before a batch result is
reported, so that an interrupted run can be resumed from what was proven.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
import json
import logging
from pathlib import Path
from typing import Any
from uuid import uuid4

from constants import (
    DWN_TP2_LANGUAGE,
    DWN_TP2_PREFIX,
    INSTALLATION_JOURNAL_FORMAT,
)
from core.atomic_io import write_json_atomic
from core.ComponentReference import ComponentReference
from core.ExtractionManifest import ExtractionManifest
from core.models.PauseEntry import PauseEntry
from core.RuleManager import RuleManager
from core.TranslationManager import tr
from core.weidu_types import AttemptEvidence, ComponentStatus, InstallResult

logger = logging.getLogger(__name__)

# Rule fields that describe a requirement without changing what it requires
DESCRIPTIVE_RULE_FIELDS = frozenset({"description", "source_url"})


class JournalError(Exception):
    """The journal file exists but cannot be read."""


class RunDivergenceError(Exception):
    """The game no longer matches the journal; the run cannot continue."""


class OperationDecision(Enum):
    """Decision recorded by the user for an operation without a proven result."""

    CONFIRM_INSTALLED = "confirm_installed"
    RETRY = "retry"
    ACCEPT_FAILURE = "accept_failure"


@dataclass(frozen=True)
class JournalDecision:
    """User decision for one operation of the journal.

    Attributes:
        sequence: Sequence index
        position: Operation index within the sequence
        decision: Decision of the user
        in_log: Whether WeiDU.log currently records the component
    """

    sequence: int
    position: int
    decision: OperationDecision
    in_log: bool = False


class RunOutcome(Enum):
    """Final assessment of a run."""

    COMPLETE = "complete"
    WARNINGS = "warnings"
    INCOMPLETE = "incomplete"


class RunClosure(Enum):
    """How a run was closed; its outcome is computed from the operations."""

    FINISHED = "finished"
    CANCELLED = "cancelled"


# ============================================================================
# Lifecycle
# ============================================================================


@dataclass
class RunLifecycle:
    """Identity of a run, when it started and how it was closed."""

    run_id: str
    started_at: datetime
    closed_at: datetime | None = None
    closure: RunClosure | None = None

    @classmethod
    def start(cls) -> RunLifecycle:
        """Lifecycle of a run starting now."""
        return cls(uuid4().hex, cls._now())

    @property
    def is_open(self) -> bool:
        return self.closure is None

    def close(self, closure: RunClosure) -> None:
        self.closed_at = self._now()
        self.closure = closure

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "started_at": self.started_at.isoformat(),
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
            "closure": self.closure.value if self.closure else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunLifecycle:
        """Rebuild the lifecycle saved in the journal.

        Raises:
            ValueError: A field is invalid, or the closure lacks its date or its mode
        """
        closed_at, closure = data["closed_at"], data["closure"]
        if (closed_at is None) != (closure is None):
            raise ValueError("closure without its date or its mode")
        if not isinstance(data["run_id"], str) or not data["run_id"]:
            raise ValueError("missing run identifier")
        return cls(
            data["run_id"],
            datetime.fromisoformat(data["started_at"]),
            datetime.fromisoformat(closed_at) if closed_at else None,
            RunClosure(closure) if closure else None,
        )

    @staticmethod
    def _now() -> datetime:
        # Local time with its offset: readable in the diagnostic, unambiguous across moves
        return datetime.now().astimezone().replace(microsecond=0)


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

    def to_dict(self) -> dict[str, Any]:
        """Serialize the plan for the journal, in the order of its fields."""
        return {
            "game": self.game,
            "folders": dict(self.folders),
            "orders": {number: list(order) for number, order in self.orders.items()},
            "selection": list(self.selection),
            "game_language": self.game_language,
            "languages": list(self.languages),
            "batch": self.batch,
        }

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
    # Mod whose archive supplied the files (the provider of a provided mod)
    archive_owner: str | None = None
    # Archive identity of the extraction manifest when the run was planned
    archive_sha256: str | None = None

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

    def decided(self, decision: OperationDecision, in_log: bool) -> Operation:
        """Copy of the operation once the user decision is applied.

        Args:
            decision: Decision of the user
            in_log: Whether WeiDU.log currently records the component
        """
        if decision == OperationDecision.CONFIRM_INSTALLED:
            previous = self.evidence
            return replace(
                self,
                decision=decision,
                status=ComponentStatus.SUCCESS,
                evidence=AttemptEvidence(
                    previous.return_code if previous else None,
                    previous.debug_status if previous else None,
                    in_log,
                ),
            )
        if decision == OperationDecision.RETRY:
            return replace(self, decision=decision, status=ComponentStatus.PENDING)
        return replace(self, decision=decision, status=ComponentStatus.ERROR)

    def adopt_decision(self, decided: Operation) -> None:
        """Take the state of a decided copy, keeping this object for those who hold it."""
        self.status = decided.status
        self.decision = decided.decision
        self.evidence = decided.evidence

    def to_dict(self) -> dict[str, Any]:
        """Serialize the operation for the journal, in the order of its fields."""
        return {
            "reference": self.reference,
            "tp2": self.tp2,
            "language": self.language,
            "batch": self.batch,
            "answers": list(self.answers),
            "arguments": list(self.arguments),
            "requirements": list(self.requirements),
            "kind": self.kind,
            "version": self.version,
            "status": self.status.value,
            "decision": self.decision.value if self.decision else None,
            "evidence": self.evidence.to_dict() if self.evidence else None,
            "messages": list(self.messages),
            "archive_owner": self.archive_owner,
            "archive_sha256": self.archive_sha256,
        }

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

    def verify_preparation(self, positions: list[int] | None = None) -> None:
        """Raise RunDivergenceError when files were prepared from another archive.

        Args:
            positions: Operations about to run; by default every one still to do
        """
        selected = (
            [self.operations[position] for position in positions]
            if positions is not None
            else [op for op in self.operations if op.needs_work]
        )
        frozen = [
            op
            for op in selected
            if op.archive_sha256 is not None and op.archive_owner is not None
        ]
        manifests = ExtractionManifest.load_for_owners(
            Path(self.folder), (op.archive_owner for op in frozen)
        )

        for op in frozen:
            manifest = manifests[op.archive_owner]
            if manifest is None or manifest.archive_sha256 != op.archive_sha256:
                raise RunDivergenceError(
                    tr(
                        "page.installation.divergence.archive",
                        reference=op.reference,
                        mod=op.archive_owner,
                    )
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
        self,
        path: Path,
        plan: RunPlan,
        sequences: list[RunSequence],
        lifecycle: RunLifecycle | None = None,
    ):
        """
        Args:
            path: Journal file
            plan: Choices frozen when the run started
            sequences: Operations by sequence
            lifecycle: Saved lifecycle; by default a run starting now
        """
        self.path = path
        self.plan = plan
        self.sequences = sequences
        self.lifecycle = lifecycle or RunLifecycle.start()

    @property
    def active(self) -> bool:
        """Whether the run is still open, possibly waiting to be resumed."""
        return self.lifecycle.is_open

    @property
    def played(self) -> bool:
        """Whether the open run has nothing left to install or to decide.

        Such a run waits for the user to finish it: it stays open until then.
        """
        return (
            self.active
            and not any(op.needs_work for seq in self.sequences for op in seq.operations)
            and not self.undetermined_positions()
        )

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
            if data["format"] != INSTALLATION_JOURNAL_FORMAT:
                raise ValueError(f"format {data['format']}")
            return cls(
                path,
                RunPlan.from_dict(data["plan"]),
                [RunSequence.from_dict(seq) for seq in data["sequences"]],
                RunLifecycle.from_dict(data["run"]),
            )
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
            raise JournalError(f"{type(error).__name__}: {error}") from error

    def save(self) -> None:
        """Replace the journal atomically."""
        write_json_atomic(self.path, self.to_document())

    def to_document(self) -> dict[str, Any]:
        """Content of the journal file."""
        return self._document_of(self.sequences)

    def _document_of(self, sequences: list[RunSequence]) -> dict[str, Any]:
        return {
            "format": INSTALLATION_JOURNAL_FORMAT,
            "run": self.lifecycle.to_dict(),
            "plan": self.plan.to_dict(),
            "sequences": [seq.to_dict() for seq in sequences],
        }

    # ------------------------------------------------------------------
    # Transitions
    # ------------------------------------------------------------------

    def begin(self, sequence: int, positions: list[int]) -> None:
        """Persist that WeiDU is about to modify the game for these operations."""
        target = self.sequences[sequence]
        target.verify_base()
        target.verify_preparation(positions)
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
        """Apply and persist a user decision for one operation (see decide_many)."""
        self.decide_many([JournalDecision(sequence, position, decision, in_log)])

    def decide_many(self, decisions: Iterable[JournalDecision]) -> None:
        """Apply user decisions, persisted in a single write before they are published.

        The decisions are applied in order on copies of the operations. The journal
        in memory only changes once the file is written: when the write fails, the
        error propagates and nothing was decided.
        """
        decided: dict[tuple[int, int], Operation] = {}
        for item in decisions:
            key = (item.sequence, item.position)
            current = decided[key] if key in decided else self._operation(*key)
            decided[key] = current.decided(item.decision, item.in_log)
        if not decided:
            return

        write_json_atomic(self.path, self._document_of(self._sequences_with(decided)))

        for (sequence, position), operation in decided.items():
            self._operation(sequence, position).adopt_decision(operation)

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

    def _operation(self, sequence: int, position: int) -> Operation:
        return self.sequences[sequence].operations[position]

    def _sequences_with(self, decided: dict[tuple[int, int], Operation]) -> list[RunSequence]:
        """Sequences where the decided copies replace their operations; self is unchanged."""
        sequences = [replace(seq, operations=list(seq.operations)) for seq in self.sequences]
        for (sequence, position), operation in decided.items():
            sequences[sequence].operations[position] = operation
        return sequences

    def closed_copy(self, closure: RunClosure) -> InstallationJournal:
        """Copy of the run closed now, left unsaved.

        The saved journal stays open until the closed copy is archived: a failure
        in between leaves the run resumable.
        """
        lifecycle = replace(self.lifecycle)
        lifecycle.close(closure)
        return InstallationJournal(self.path, self.plan, self.sequences, lifecycle)

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
