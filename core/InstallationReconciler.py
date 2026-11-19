"""Compare an installation journal with the WeiDU.log of each game folder."""

from dataclasses import dataclass, field
import logging
from pathlib import Path

from core.InstallationJournal import InstallationJournal
from core.WeiDULogParser import WeiDULogParser

logger = logging.getLogger(__name__)


@dataclass
class ReconciliationReport:
    """Divergences found between the journal and the games.

    Attributes:
        undetermined: (sequence, position) of operations needing a user decision
        reopened: References proven installed whose WeiDU.log entry disappeared
        unexpected: WeiDU.log entries that no operation of the journal explains
    """

    undetermined: list[tuple[int, int]] = field(default_factory=list)
    reopened: list[str] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)


class InstallationReconciler:
    """Check a journal against the games before resuming or closing a run.

    A changed folder is a hard divergence. Per-operation divergences are
    reported so that the user can decide, instead of blocking the run.
    """

    def __init__(self, log_parser: WeiDULogParser):
        self._log_parser = log_parser

    def reconcile(self, journal: InstallationJournal) -> ReconciliationReport:
        """Verify the folders, then compare every operation with WeiDU.log.

        Raises:
            RunDivergenceError: A game folder was replaced, restored or removed
        """
        for sequence in journal.sequences:
            sequence.verify_base()

        report = ReconciliationReport()
        logged = {
            folder: self.logged_identities(Path(folder))
            for folder in {seq.folder for seq in journal.sequences}
        }

        for seq_idx, sequence in enumerate(journal.sequences):
            for position, op in enumerate(sequence.operations):
                if op.is_pause or not op.installed or op.confirmed_without_log:
                    continue
                if op.weidu_identity not in logged[sequence.folder]:
                    journal.reopen(seq_idx, position)
                    report.reopened.append(op.reference)

        for folder, identities in logged.items():
            planned = {
                op.weidu_identity
                for seq in journal.sequences
                if seq.folder == folder
                for op in seq.operations
            }
            report.unexpected.extend(
                f"{folder}: {tp2} #{language} #{number}"
                for tp2, number, language in identities
                if (tp2, number, language) not in planned
            )

        report.undetermined = journal.undetermined_positions()
        if report.reopened:
            logger.warning("WeiDU.log no longer records: %s", ", ".join(report.reopened))
            journal.save()
        return report

    def logged_identities(self, folder: Path) -> set[tuple[str, str, str]]:
        """(TP2, component, language) triples recorded in the WeiDU.log of a folder."""
        return set(self._log_parser.installed_identities(folder / "WeiDU.log"))
