"""Dialogs shared by the pages that let the user choose or accept an archive."""

from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget

from core.ArchiveRegistry import ArchiveRecord
from core.File import format_size
from core.Mod import Mod
from core.TranslationManager import tr

ARCHIVE_FILE_FILTER = "Archives (*.zip *.rar *.7z *.tar.gz *.tgz *.exe);;All files (*)"


def select_archive_file(parent: QWidget, start_dir: Path | None) -> Path | None:
    """Ask the user for an archive to associate with a mod."""
    selected, _ = QFileDialog.getOpenFileName(
        parent,
        tr("dialog.link_archive.select_file"),
        str(start_dir or Path.home()),
        ARCHIVE_FILE_FILTER,
    )
    return Path(selected) if selected else None


def confirm_archive_mismatch(
    parent: QWidget, mod: Mod, record: ArchiveRecord, expected_size: int
) -> bool:
    """Show the catalogue and actual identities, and ask for an explicit acceptance."""
    details = tr(
        "dialog.archive_mismatch.details",
        version=mod.version or "?",
        expected_hash=record.expected_sha256 or "?",
        actual_hash=record.sha256 or "?",
        expected_size=format_size(expected_size) if expected_size > 0 else "?",
        actual_size=format_size(record.size),
        path=record.path,
    )
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(tr("dialog.archive_mismatch.title"))
    box.setText(tr("dialog.archive_mismatch.text", mod_name=mod.name))
    box.setInformativeText(details)
    box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    box.setDefaultButton(QMessageBox.StandardButton.No)
    return box.exec() == QMessageBox.StandardButton.Yes
