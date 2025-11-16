"""
Cache building dialog for UI layer.
"""

import logging
from typing import Optional

from PySide6.QtCore import QEventLoop, Qt
from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog

from core.ModManager import ModManager
from core.TranslationManager import tr

logger = logging.getLogger(__name__)


class CacheDialog:
    """
    Dialog for displaying cache building progress.

    Handles UI presentation while ModManager handles the actual work.
    """

    def __init__(self, parent=None) -> None:
        """
        Initialize cache dialog.

        Args:
            parent: Parent widget (optional)
        """
        self.parent = parent
        self._dialog: Optional[QProgressDialog] = None
        self._event_loop: Optional[QEventLoop] = None
        self._result = {"success": False, "finished": False}

    def show_and_wait(self, mod_manager: ModManager) -> bool:
        """
        Display cache building dialog and block until complete.

        Args:
            mod_manager: ModManager instance to build cache

        Returns:
            True if successful, False otherwise
        """
        # Create progress dialog
        self._dialog = QProgressDialog(
            tr("app.initializing"),
            "",  # No cancel button text
            0,
            100,
            self.parent
        )
        self._dialog.setWindowTitle(tr("app.loading_mods"))
        self._dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        self._dialog.setMinimumDuration(0)  # Show immediately
        self._dialog.setCancelButton(None)  # No cancel button
        self._dialog.setAutoClose(False)
        self._dialog.setAutoReset(False)
        self._dialog.setMinimumWidth(400)

        # Prevent manual closing
        self._dialog.setWindowFlags(
            self._dialog.windowFlags() & ~Qt.WindowCloseButtonHint
        )

        # Reset result
        self._result = {"success": False, "finished": False}

        # Event loop for blocking
        self._event_loop = QEventLoop()

        # Connect signals BEFORE starting thread
        mod_manager.cache_ready.connect(self._on_cache_ready)
        mod_manager.cache_error.connect(self._on_cache_error)

        # Start cache building
        if not mod_manager.build_cache_async():
            # Thread couldn't start
            self._dialog.close()
            QMessageBox.critical(
                self.parent,
                tr("app.error"),
                tr("error.unable_to_start_cache_building")
            )
            return False

        # Connect thread signals (now that thread exists)
        if mod_manager.builder_thread:
            mod_manager.builder_thread.progress.connect(self._dialog.setValue)
            mod_manager.builder_thread.status_changed.connect(
                self._dialog.setLabelText
            )

        # Show dialog
        self._dialog.show()
        self._dialog.raise_()
        self._dialog.activateWindow()

        # Force event processing to display dialog
        QApplication.processEvents()

        # Block until finished (dialog remains responsive)
        self._event_loop.exec()

        # Cleanup connections
        try:
            mod_manager.cache_ready.disconnect(self._on_cache_ready)
            mod_manager.cache_error.disconnect(self._on_cache_error)
        except RuntimeError:
            # Signals already disconnected
            pass

        return self._result["success"]

    def _on_cache_ready(self) -> None:
        """Called when cache is ready (success)."""
        self._result["success"] = True
        self._result["finished"] = True

        if self._dialog:
            self._dialog.close()

        if self._event_loop:
            self._event_loop.quit()

        logger.info(tr("cache_ready_signal_received"))

    def _on_cache_error(self, message: str) -> None:
        """Called on error."""
        self._result["success"] = False
        self._result["finished"] = True

        if self._dialog:
            self._dialog.close()

        QMessageBox.critical(
            self.parent,
            tr("error.loading_error"),
            tr("error.unable_to_load_mods", message=message)
        )

        if self._event_loop:
            self._event_loop.quit()

        logger.error(tr("error.cache_error", message=message))


def show_cache_build_dialog(
        mod_manager: ModManager,
        parent=None
) -> bool:
    """
    Convenience function to show cache build dialog.

    Args:
        mod_manager: ModManager instance
        parent: Parent widget (optional)

    Returns:
        True if successful, False otherwise
    """
    dialog = CacheDialog(parent)
    return dialog.show_and_wait(mod_manager)
