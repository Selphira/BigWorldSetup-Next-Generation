"""Diagnostic archive of the current installation run, exported or kept in the history.

The archive lets someone understand an incomplete installation without the
whole WeiDU output: a readable summary, the journal and its frozen plan, the
journals set aside as unreadable, the WeiDU.log of each game folder, the DEBUG
files of the operations that failed or stayed unproven, and the versions of the
application and of the catalogue.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import tempfile
from typing import Any
import zipfile

from constants import (
    APP_NAME,
    APP_VERSION,
    DIAGNOSTIC_DATE_FORMAT,
    DIAGNOSTIC_FILE_PREFIX,
    DIAGNOSTIC_FILE_STAMP_FORMAT,
    DIAGNOSTIC_FILE_SUFFIX,
    DIAGNOSTIC_GAME_DIRECTORY,
    DIAGNOSTIC_PLAN_ENTRY,
    DIAGNOSTIC_SUMMARY_ENTRY,
    DIAGNOSTIC_VERSIONS_ENTRY,
    HISTORY_CANCELLED_OUTCOME,
    HISTORY_FILE_COUNTER,
    HISTORY_FILE_NAME,
    HISTORY_FILE_STAMP_FORMAT,
)
from core.atomic_io import set_aside_files
from core.CatalogueLocation import CatalogueLocation
from core.InstallationJournal import (
    InstallationJournal,
    JournalError,
    Operation,
    OperationDecision,
    RunClosure,
)
from core.TranslationManager import tr
from core.weidu_types import AttemptEvidence, ComponentStatus
from core.WeiDUInstallerEngine import WeiDUInstallerEngine

logger = logging.getLogger(__name__)

# Operations whose DEBUG explains a failure, a warning or a missing proof
DIAGNOSED_STATUSES = frozenset(
    {
        ComponentStatus.ERROR,
        ComponentStatus.WARNING,
        ComponentStatus.UNKNOWN,
        ComponentStatus.RUNNING,
        ComponentStatus.STOPPED,
    }
)

DECISION_LABELS = {
    OperationDecision.CONFIRM_INSTALLED: "page.installation.decision_installed",
    OperationDecision.RETRY: "page.installation.decision_retry",
    OperationDecision.ACCEPT_FAILURE: "page.installation.decision_failed",
}

WEIDU_LOG_NAME = "WeiDU.log"


def describe_evidence(evidence: AttemptEvidence) -> str:
    """Exit code, DEBUG status and WeiDU.log presence of a WeiDU attempt."""
    return tr(
        "page.installation.evidence",
        code="—" if evidence.return_code is None else evidence.return_code,
        debug=tr(f"page.installation.status.{evidence.debug_status.value}")
        if evidence.debug_status
        else tr("page.installation.debug_not_reached"),
        log=describe_log_presence(evidence.in_log),
    )


def describe_log_presence(in_log: bool) -> str:
    return tr("page.installation.log_present" if in_log else "page.installation.log_absent")


@dataclass
class DiagnosticContent:
    """Everything an archive holds, gathered before anything is written.

    Attributes:
        created: Moment of the export
        journal: Journal of the run, or None when absent or unreadable
        journal_error: Why the journal could not be read
        versions: Application and catalogue versions
        game_directories: Archive directory of each game folder of the run
        documents: Archive entry name of each JSON document written from memory
        files: Archive entry name of each file copied from disk
    """

    created: datetime
    journal: InstallationJournal | None
    journal_error: str | None
    versions: dict[str, Any]
    game_directories: dict[str, str] = field(default_factory=dict)
    documents: dict[str, Any] = field(default_factory=dict)
    files: dict[str, Path] = field(default_factory=dict)

    @property
    def missing_files(self) -> list[str]:
        """Expected files that do not exist on disk."""
        return [str(path) for path in self.files.values() if not path.is_file()]


class InstallationDiagnostic:
    """Build the diagnostic zip archive of the installation journal."""

    def __init__(self, journal_path: Path, data_version_file: Path | None = None):
        self._journal_path = journal_path
        self._data_version_file = data_version_file or CatalogueLocation.active().version_file

    @staticmethod
    def default_file_name() -> str:
        """Suggested archive name, dated so that successive exports do not collide."""
        stamp = datetime.now().strftime(DIAGNOSTIC_FILE_STAMP_FORMAT)
        return f"{DIAGNOSTIC_FILE_PREFIX}{stamp}{DIAGNOSTIC_FILE_SUFFIX}"

    @staticmethod
    def history_path(directory: Path, journal: InstallationJournal) -> Path:
        """Free path, in the history `directory`, of the archive of a closed run."""
        lifecycle = journal.lifecycle
        outcome = (
            HISTORY_CANCELLED_OUTCOME
            if lifecycle.closure == RunClosure.CANCELLED
            else journal.outcome().value
        )
        stem = HISTORY_FILE_NAME.format(
            date=lifecycle.closed_at.strftime(HISTORY_FILE_STAMP_FORMAT),
            game=journal.plan.game,
            outcome=outcome,
        )

        target = directory / f"{stem}{DIAGNOSTIC_FILE_SUFFIX}"
        number = 1
        while target.exists():
            number += 1
            counter = HISTORY_FILE_COUNTER.format(number=number)
            target = directory / f"{stem}{counter}{DIAGNOSTIC_FILE_SUFFIX}"
        return target

    def export(self, target: Path, journal: InstallationJournal | None = None) -> None:
        """Write the diagnostic archive, replacing `target` only once it is complete.

        Args:
            target: Destination of the zip archive
            journal: Run to describe instead of the saved journal, written from
                memory: the closed copy of a run whose journal is still open

        Raises:
            OSError: The archive cannot be written
        """
        content = self._gather(journal)
        self._write_archive(target, content)
        logger.info(
            "Installation diagnostic exported to %s (%d files, %d missing)",
            target,
            len(content.files),
            len(content.missing_files),
        )

    # ------------------------------------------------------------------
    # Gathering
    # ------------------------------------------------------------------

    def _gather(self, journal: InstallationJournal | None) -> DiagnosticContent:
        created = datetime.now().astimezone()
        if journal is None:
            journal, journal_error = self._read_journal()
            content = DiagnosticContent(
                created, journal, journal_error, self._versions(created)
            )
            content.files[self._journal_path.name] = self._journal_path
        else:
            content = DiagnosticContent(created, journal, None, self._versions(created))
            content.documents[self._journal_path.name] = journal.to_document()

        content.documents[DIAGNOSTIC_VERSIONS_ENTRY] = content.versions
        for set_aside in set_aside_files(self._journal_path):
            content.files[set_aside.name] = set_aside
        if journal is None:
            return content

        content.documents[DIAGNOSTIC_PLAN_ENTRY] = asdict(journal.plan)
        for sequence in journal.sequences:
            if sequence.folder in content.game_directories:
                continue
            directory = DIAGNOSTIC_GAME_DIRECTORY.format(
                number=len(content.game_directories) + 1
            )
            content.game_directories[sequence.folder] = directory
            content.files[f"{directory}/{WEIDU_LOG_NAME}"] = (
                Path(sequence.folder) / WEIDU_LOG_NAME
            )

        for sequence in journal.sequences:
            directory = content.game_directories[sequence.folder]
            for op in sequence.operations:
                if op.is_pause or op.status not in DIAGNOSED_STATUSES:
                    continue
                debug_file = WeiDUInstallerEngine.debug_file_in(
                    Path(sequence.folder), op.weidu_identity[0]
                )
                content.files[f"{directory}/{debug_file.name}"] = debug_file
        return content

    def _read_journal(self) -> tuple[InstallationJournal | None, str | None]:
        try:
            return InstallationJournal.load(self._journal_path), None
        except JournalError as error:
            return None, str(error)

    def _versions(self, created: datetime) -> dict[str, Any]:
        catalogue = None
        try:
            catalogue = json.loads(self._data_version_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            logger.warning("Catalogue version unavailable for the diagnostic: %s", error)
        return {
            "application": APP_VERSION,
            "catalogue": catalogue,
            "created": created.isoformat(timespec="seconds"),
        }

    # ------------------------------------------------------------------
    # Archive
    # ------------------------------------------------------------------

    def _write_archive(self, target: Path, content: DiagnosticContent) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(
            dir=target.parent, prefix=f".{target.name}.", suffix=".tmp"
        )
        os.close(descriptor)
        temporary = Path(name)
        try:
            with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(DIAGNOSTIC_SUMMARY_ENTRY, self._summary(content))
                for entry, document in content.documents.items():
                    archive.writestr(entry, self._json(document))
                for entry, path in content.files.items():
                    if path.is_file():
                        archive.write(path, entry)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _json(value: object) -> str:
        return json.dumps(value, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # Readable summary
    # ------------------------------------------------------------------

    def _summary(self, content: DiagnosticContent) -> str:
        lines = [tr("diagnostic.title", app=APP_NAME), ""]
        lines += self._version_lines(content)
        lines.append("")
        lines += self._run_lines(content)

        missing = content.missing_files
        if missing:
            lines += ["", tr("diagnostic.missing_files")]
            lines += [f"  {path}" for path in missing]
        return "\n".join(lines) + "\n"

    @staticmethod
    def _version_lines(content: DiagnosticContent) -> list[str]:
        catalogue = content.versions["catalogue"] or {}
        return [
            tr("diagnostic.created", date=content.created.strftime(DIAGNOSTIC_DATE_FORMAT)),
            tr("diagnostic.app_version", version=APP_VERSION),
            tr(
                "diagnostic.catalogue_version",
                version=catalogue.get("version") or tr("diagnostic.unknown"),
                date=catalogue.get("date") or tr("diagnostic.unknown"),
            ),
        ]

    def _run_lines(self, content: DiagnosticContent) -> list[str]:
        journal = content.journal
        if content.journal_error is not None:
            return [tr("diagnostic.journal_unreadable", error=content.journal_error)]
        if journal is None:
            return [tr("diagnostic.journal_missing")]

        stats = journal.stats()
        lines = [
            tr("diagnostic.game", game=journal.plan.game),
            tr("diagnostic.run_active" if journal.active else "diagnostic.run_closed"),
            tr(
                "diagnostic.outcome",
                outcome=tr(f"page.installation.{journal.outcome().value}"),
            ),
            ", ".join(
                tr(f"page.installation.stats.{name}", count=count)
                for name, count in stats.items()
            ),
        ]
        for number, sequence in enumerate(journal.sequences):
            lines += [
                "",
                tr(
                    "diagnostic.sequence",
                    sequence=tr("page.installation.summary.sequence", number=number + 1),
                    folder=sequence.folder,
                    directory=content.game_directories[sequence.folder],
                ),
            ]
            for op in sequence.operations:
                if not op.is_pause:
                    lines += self._operation_lines(op)
        return lines

    @staticmethod
    def _operation_lines(op: Operation) -> list[str]:
        tp2, component, language = op.weidu_identity
        lines = [
            f"  {op.reference} — {tr(f'page.installation.status.{op.status.value}')}",
            "    "
            + tr(
                "diagnostic.identity",
                tp2=tp2,
                component=component,
                language=language,
                batch=op.batch,
            ),
        ]
        if op.decision is not None:
            lines.append(
                "    " + tr("diagnostic.decision", decision=tr(DECISION_LABELS[op.decision]))
            )
        if op.evidence is not None:
            lines.append("    " + describe_evidence(op.evidence))
        lines += [f"    {message}" for message in op.messages]
        return lines
