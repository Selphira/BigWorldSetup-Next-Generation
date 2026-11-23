"""Plan, journal and sequence orchestration of an installation run.

The installation page only presents what this object decides: it never
builds a plan, edits the journal or chooses the next operations itself.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import logging
from pathlib import Path

from PySide6.QtCore import QLockFile

from constants import INSTALLATION_JOURNAL_FILE
from core.atomic_io import set_aside_unreadable
from core.ComponentReference import ComponentReference
from core.ExtractionManifest import ExtractionManifest
from core.InstallationJournal import (
    InstallationJournal,
    JournalError,
    Operation,
    OperationDecision,
    RunClosure,
    RunDivergenceError,
    RunPlan,
    RunSequence,
)
from core.InstallationReconciler import InstallationReconciler, ReconciliationReport
from core.Mod import SubComponent
from core.models.PauseEntry import PAUSE_PREFIX, PauseEntry
from core.StateManager import StateManager
from core.TranslationManager import tr
from core.weidu_types import ComponentInfo
from core.WeiDUDebugParser import WeiDUDebugParser
from core.WeiDUInstallerEngine import WeiDUInstallerEngine
from core.WeiDULogParser import WeiDULogParser

logger = logging.getLogger(__name__)


class InstallationError(Exception):
    """A run cannot start or continue; the message is meant for the user."""


@dataclass
class PreparedSequence:
    """Remaining work of one journal sequence, rebuilt from the saved operations.

    Attributes:
        number: Index of the sequence in the journal
        folder: Game folder modified by the sequence
        batches: Remaining batches, each launched by one WeiDU process
    """

    number: int
    folder: Path
    batches: list[list[ComponentInfo]]


@dataclass
class StartReport:
    """What the user must know before the run (re)starts."""

    resumed: bool
    reconciliation: ReconciliationReport = field(default_factory=ReconciliationReport)
    catalogue_changes: list[str] = field(default_factory=list)


class InstallationExecutor:
    """Own the run plan, its journal, its lock and the order of sequences."""

    def __init__(self, state_manager: StateManager, journal_path: Path | None = None):
        self._state = state_manager
        self._log_parser = WeiDULogParser()
        self._reconciler = InstallationReconciler(self._log_parser)

        self.journal_path = journal_path or INSTALLATION_JOURNAL_FILE
        self._lock = QLockFile(str(self.journal_path) + ".lock")
        self.journal: InstallationJournal | None = None
        self.load_error: str | None = None

    # ------------------------------------------------------------------
    # Journal and lock
    # ------------------------------------------------------------------

    @property
    def is_active(self) -> bool:
        """Whether a run is open: running, interrupted, suspended or played."""
        return bool(self.journal and self.journal.active)

    def load(self) -> None:
        """Read the journal; an unreadable journal is reported through `load_error`."""
        try:
            self.journal = InstallationJournal.load(self.journal_path)
            self.load_error = None
        except JournalError as error:
            logger.error("Unreadable installation journal %s: %s", self.journal_path, error)
            self.journal = None
            self.load_error = str(error)

    def set_aside_unreadable_journal(self) -> Path:
        """Rename an unreadable journal so that a new run can start.

        Returns:
            New path of the journal, kept for diagnosis
        """
        target = set_aside_unreadable(self.journal_path)
        logger.warning("Installation journal set aside: %s", target)
        self.load_error = None
        return target

    def acquire(self) -> bool:
        """Prevent another instance from executing the same journal."""
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        return self._lock.isLocked() or self._lock.tryLock(0)

    def release(self) -> None:
        if self._lock.isLocked():
            self._lock.unlock()

    # ------------------------------------------------------------------
    # Run lifecycle
    # ------------------------------------------------------------------

    def start(self, batch: bool) -> StartReport:
        """Resume the active run, or freeze the current selection into a new one.

        Args:
            batch: Whether consecutive components of a mod share one WeiDU process

        Raises:
            InstallationError: The run cannot start
        """
        self.load()
        if self.load_error:
            raise InstallationError(self.load_error)

        try:
            if self.is_active:
                reconciliation = self._reconciler.reconcile(self.journal)
                for sequence in self.journal.sequences:
                    sequence.verify_preparation()
                return StartReport(
                    resumed=True,
                    reconciliation=reconciliation,
                    catalogue_changes=self._catalogue_changes(self.journal),
                )

            plan = self._plan_from_state(batch)
            sequences = self._capture(plan)
            self._ensure_unmodded(sequences)
            self._freeze_preparations(sequences)
            self.journal = InstallationJournal(self.journal_path, plan, sequences)
            self.journal.save()
        except (RunDivergenceError, OSError) as error:
            raise InstallationError(str(error)) from error

        logger.info("New installation run with %d sequences", len(sequences))
        return StartReport(resumed=False)

    def decide(self, sequence: int, position: int, decision: OperationDecision) -> None:
        """Record the user decision for an operation without a proven result."""
        operation = self.journal.sequences[sequence].operations[position]
        self.journal.decide(sequence, position, decision, self.is_logged(sequence, position))
        logger.info("Decision %s recorded for %s", decision.value, operation.reference)

    def is_logged(self, sequence: int, position: int) -> bool:
        """Whether WeiDU.log currently records the component of an operation."""
        operation = self.journal.sequences[sequence].operations[position]
        folder = Path(self.journal.sequences[sequence].folder)
        return operation.weidu_identity in self._reconciler.logged_identities(folder)

    def next_sequence(self, start: int) -> PreparedSequence | None:
        """First sequence from `start` that still has work, or None when the run is done.

        Raises:
            InstallationError: A saved operation no longer exists in the catalogue
        """
        for number in range(start, len(self.journal.sequences)):
            if any(op.needs_work for op in self.journal.sequences[number].operations):
                return self._prepare_sequence(number)
        return None

    def create_engine(self, prepared: PreparedSequence) -> WeiDUInstallerEngine:
        """WeiDU engine of a sequence folder, with weidu.conf and WeiDU.log in place.

        Raises:
            InstallationError: WeiDU is missing or the folder cannot be prepared
        """
        engine = WeiDUInstallerEngine(prepared.folder, self._log_parser, WeiDUDebugParser())
        if not engine.locate_weidu():
            raise InstallationError(
                tr(
                    "page.installation.error_no_weidu_message",
                    sequence=prepared.number + 1,
                    folder=str(prepared.folder),
                )
            )
        try:
            engine.init_weidu_conf(self.journal.plan.game_language)
            engine.init_weidu_log()
        except OSError as error:
            raise InstallationError(str(error)) from error
        return engine

    def verify_played(self) -> ReconciliationReport:
        """Check the games once every sequence ran; the run stays open until finished.

        Raises:
            InstallationError: A game folder was replaced during the run
        """
        try:
            return self._reconciler.reconcile(self.journal)
        except RunDivergenceError as error:
            raise InstallationError(str(error)) from error

    @property
    def is_played(self) -> bool:
        """Whether the open run has nothing left to install or to decide."""
        return bool(self.journal and self.journal.played)

    def finish(self) -> None:
        """Close a played run, complete or not, at the explicit request of the user."""
        if not self.is_played:
            logger.warning("Finish ignored: the run is not played")
            return
        self.journal.close(RunClosure.FINISHED)
        logger.info("Installation run finished")

    def cancel(self) -> None:
        """Abandon the run; its journal stays readable until the next run starts."""
        if self.journal and self.journal.active:
            self.journal.close(RunClosure.CANCELLED)
        logger.info("Installation run cancelled")

    # ------------------------------------------------------------------
    # Plan resolution
    # ------------------------------------------------------------------

    def resolve_components(
        self, order_list: list[str], selection: list[str], folders: dict[str, str]
    ) -> list[ComponentInfo]:
        """Convert an order into components with their SUB answers and requirements.

        Args:
            order_list: Component references ("mod:comp") of one sequence
            selection: Selected references, SUB options included
            folders: Game folders by game code

        Returns:
            Components in order

        Raises:
            ValueError: A reference is invalid or unknown
        """
        mods = self._state.get_mod_manager()
        rules = self._state.get_rule_manager()
        selected = set(ComponentReference.from_string_list(selection))
        options_by_base: dict[ComponentReference, set[ComponentReference]] = {}
        for ref in selected:
            if ref.is_sub_option():
                options_by_base.setdefault(ref.get_base_component_reference(), set()).add(ref)

        components = []
        for idx, comp_id in enumerate(order_list):
            if PauseEntry.is_pause(comp_id):
                components.append(ComponentInfo(comp_id, None, None, PAUSE_PREFIX, idx))
                continue

            mod_id, separator, comp_key = comp_id.partition(":")
            mod = mods.get_mod_by_id(mod_id) if separator else None
            reference = ComponentReference(mod_id, comp_key)
            component = mod.get_component(comp_key) if mod else None
            if not component or not reference.is_component():
                raise ValueError(comp_id)

            options = options_by_base.get(reference, set())
            answers = []
            if isinstance(component, SubComponent):
                answers = component.ordered_answers({option.comp_key for option in options})
            arguments = []
            if mod_id.lower() == "eet":
                arguments = ["--args-list", "p", f'"{folders.get("sod")}"']

            components.append(
                ComponentInfo(
                    tp2_name=mod.tp2,
                    comp_id=str(reference),
                    mod=mod,
                    component=component,
                    sequence_idx=idx,
                    requirements=tuple(rules.active_requirements(reference, selected, options)),
                    subcomponent_answers=answers,
                    extra_args=arguments,
                )
            )
        return components

    @staticmethod
    def group_batches(
        components: list[ComponentInfo], batch: bool
    ) -> list[list[ComponentInfo]]:
        """Group consecutive components of a mod; SUB, pauses and dependents stay alone."""
        if not batch:
            return [[comp] for comp in components]

        batches: list[list[ComponentInfo]] = []
        current: list[ComponentInfo] = []
        for comp in components:
            if comp.subcomponent_answers or comp.component is None or comp.requirements:
                if current:
                    batches.append(current)
                current = []
                batches.append([comp])
                continue

            joins = bool(current) and (
                current[0].mod.id == comp.mod.id
                and current[0].component.is_dwn() == comp.component.is_dwn()
            )
            if current and not joins:
                batches.append(current)
                current = []
            current.append(comp)
        if current:
            batches.append(current)
        return batches

    def _plan_from_state(self, batch: bool) -> RunPlan:
        languages = self._state.get_languages_order()
        selected_languages = self._state.get_page_option(
            "mod_selection", "selected_languages", []
        )
        plan = RunPlan(
            self._state.get_selected_game(),
            {
                key: str(Path(value).resolve())
                for key, value in self._state.get_game_folders().items()
                if value
            },
            self._state.get_install_order(),
            list(self._state.get_selected_components()),
            languages[0],
            [lang for lang in languages if lang in selected_languages],
            batch,
        )
        try:
            plan.verify_coverage()
        except ValueError as error:
            raise InstallationError(
                tr("page.installation.invalid_plan", reference=str(error))
            ) from error
        return plan

    def _capture(self, plan: RunPlan) -> list[RunSequence]:
        """Freeze the plan into journal sequences of operations."""
        game = self._state.get_game_manager().get(plan.game)
        if not game or not plan.orders:
            raise InstallationError(tr("page.installation.summary.no_data"))

        sequences = []
        for seq_idx in range(game.sequence_count):
            sequence = game.get_sequence(seq_idx)
            order = plan.orders.get(seq_idx, [])
            if not sequence or not order:
                continue
            folder = plan.folders.get(sequence.game)
            if not folder:
                raise InstallationError(
                    tr("page.installation.error_no_folder_message", sequence=seq_idx + 1)
                )
            try:
                components = self.resolve_components(order, plan.selection, plan.folders)
            except ValueError as error:
                raise InstallationError(
                    tr("page.installation.invalid_plan", reference=str(error))
                ) from error

            folder_path = Path(folder).resolve(strict=True)
            operations = [
                self._capture_operation(comp, number, plan.languages)
                for number, batch in enumerate(self.group_batches(components, plan.batch))
                for comp in batch
            ]
            sequences.append(
                RunSequence(str(folder_path), RunSequence.identify(folder_path), operations)
            )
        return sequences

    @staticmethod
    def _capture_operation(comp: ComponentInfo, batch: int, languages: list[str]) -> Operation:
        requirements = sorted(
            Operation.requirement_key(
                json.loads(json.dumps(asdict(rule), default=lambda value: value.value))
            )
            for rule in comp.requirements
        )
        return Operation(
            reference=comp.comp_id,
            tp2=comp.tp2_name,
            language=comp.mod.get_language_index(languages) if comp.mod else None,
            batch=batch,
            answers=comp.subcomponent_answers,
            arguments=comp.extra_args or [],
            requirements=requirements,
            kind=str(comp.component.comp_type) if comp.component else "pause",
            version=comp.mod.version if comp.mod else "",
            archive_owner=(comp.mod.provided_by or comp.mod.id) if comp.mod else None,
        )

    @staticmethod
    def _freeze_preparations(sequences: list[RunSequence]) -> None:
        """Require a proven preparation for every operation and record its archive.

        Raises:
            InstallationError: Components whose files were not prepared from a
                verified archive (extraction missing, failed or outdated)
        """
        unprepared = []
        for sequence in sequences:
            for op in sequence.operations:
                if op.archive_owner is None:
                    continue
                manifest = ExtractionManifest.load(
                    ExtractionManifest.path_for(Path(sequence.folder), op.archive_owner)
                )
                if manifest is None:
                    unprepared.append(op.reference)
                else:
                    op.archive_sha256 = manifest.archive_sha256
        if unprepared:
            raise InstallationError(
                tr("page.installation.unprepared", references=", ".join(unprepared))
            )

    def _ensure_unmodded(self, sequences: list[RunSequence]) -> None:
        for sequence in sequences:
            if self._reconciler.logged_identities(Path(sequence.folder)):
                raise InstallationError(
                    tr(
                        "page.installation.error_already_modded_message",
                        game_folder=sequence.folder,
                    )
                )

    def _catalogue_changes(self, journal: InstallationJournal) -> list[str]:
        """References whose recipe differs in the current catalogue (informative)."""
        try:
            current = self._capture(journal.plan)
        except (InstallationError, OSError) as error:
            return [str(error)]

        saved_recipes = [op.recipe for seq in journal.sequences for op in seq.operations]
        current_recipes = [op.recipe for seq in current for op in seq.operations]
        if len(saved_recipes) != len(current_recipes):
            return [tr("page.installation.catalogue_restructured")]
        return [
            saved[0]
            for saved, now in zip(saved_recipes, current_recipes, strict=True)
            if saved != now
        ]

    def _prepare_sequence(self, number: int) -> PreparedSequence:
        sequence = self.journal.sequences[number]
        batches: dict[int, list[ComponentInfo]] = {}
        for position, op in enumerate(sequence.operations):
            if op.needs_work:
                batches.setdefault(op.batch, []).append(self._component_of(op, position))
        return PreparedSequence(
            number, Path(sequence.folder), [batches[key] for key in sorted(batches)]
        )

    def _component_of(self, op: Operation, position: int) -> ComponentInfo:
        """Component to execute, with the recipe saved in the journal."""
        if op.is_pause:
            return ComponentInfo(op.reference, None, None, PAUSE_PREFIX, position)

        mod_id, _, comp_key = op.reference.partition(":")
        mod = self._state.get_mod_manager().get_mod_by_id(mod_id)
        component = mod.get_component(comp_key) if mod else None
        if component is None:
            raise InstallationError(
                tr("page.installation.missing_component", reference=op.reference)
            )
        return ComponentInfo(
            comp_id=op.reference,
            mod=mod,
            component=component,
            tp2_name=op.tp2,
            sequence_idx=position,
            subcomponent_answers=list(op.answers),
            extra_args=list(op.arguments),
        )
