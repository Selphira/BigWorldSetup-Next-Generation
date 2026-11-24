"""Dialog collecting the name and notes of a backup, at creation or edition."""

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from constants import BACKUP_DIALOG_MIN_WIDTH, BACKUP_NOTES_MAX_HEIGHT
from core.BackupNames import BackupNames
from core.TranslationManager import tr


class BackupDetailsDialog(QDialog):
    """Let the user name a backup and write notes about it.

    The optional summary is shown above the fields, so a creation can be
    confirmed and described in a single dialog. A name already used by another
    backup of the installation is reported without preventing its use.
    """

    def __init__(
        self,
        parent: QWidget | None,
        title: str,
        accept_text: str,
        used_names: BackupNames,
        *,
        summary: str = "",
        name: str = "",
        name_placeholder: str = "",
        notes: str = "",
    ) -> None:
        """Build the dialog.

        Args:
            parent: Parent widget
            title: Window title
            accept_text: Text of the confirmation button
            used_names: Names of the installation's other backups
            summary: Text shown above the fields, hidden when empty
            name: Initial name
            name_placeholder: Hint shown while the name is empty
            notes: Initial notes
        """
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(BACKUP_DIALOG_MIN_WIDTH)

        layout = QVBoxLayout(self)

        if summary:
            summary_label = QLabel(summary)
            summary_label.setWordWrap(True)
            layout.addWidget(summary_label)

        layout.addWidget(QLabel(tr("page.backup.edit_custom_name")))
        self._name_edit: QLineEdit = QLineEdit(name)
        self._name_edit.setPlaceholderText(name_placeholder)
        layout.addWidget(self._name_edit)

        self._used_names: BackupNames = used_names
        self._duplicate_label: QLabel = QLabel(tr("page.backup.name_already_used"))
        self._duplicate_label.setObjectName("warning")
        self._duplicate_label.setWordWrap(True)
        layout.addWidget(self._duplicate_label)
        self._name_edit.textChanged.connect(self._update_duplicate_warning)
        self._update_duplicate_warning(name)

        layout.addWidget(QLabel(tr("page.backup.edit_notes")))
        self._notes_edit: QTextEdit = QTextEdit()
        self._notes_edit.setPlainText(notes)
        self._notes_edit.setMaximumHeight(BACKUP_NOTES_MAX_HEIGHT)
        layout.addWidget(self._notes_edit)

        buttons = QDialogButtonBox()
        accept_button = buttons.addButton(accept_text, QDialogButtonBox.ButtonRole.AcceptRole)
        accept_button.setDefault(True)
        buttons.addButton(tr("button.cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _update_duplicate_warning(self, name: str) -> None:
        self._duplicate_label.setVisible(self._used_names.contains(name))

    def get_name(self) -> str:
        """Return the name typed by the user, without surrounding spaces."""
        return self._name_edit.text().strip()

    def get_notes(self) -> str:
        """Return the notes typed by the user, without surrounding spaces."""
        return self._notes_edit.toPlainText().strip()
