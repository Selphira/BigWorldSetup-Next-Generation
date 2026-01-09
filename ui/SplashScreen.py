import logging
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)


class SplashScreen(QWidget):
    """Splash screen with integrated progress bar and status text."""

    def __init__(self, pixmap_path: Path, parent=None):
        """
        Initialize splash screen.

        Args:
            pixmap_path: Path to splash image
            parent: Parent widget
        """
        super().__init__(parent)

        self.setWindowFlags(
            Qt.WindowType.SplashScreen
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._setup_ui(pixmap_path)

    def _setup_ui(self, pixmap_path: Path) -> None:
        """
        Setup splash screen UI components.

        Args:
            pixmap_path: Path to splash image
        """
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        container = QWidget()
        container.setObjectName("splashContainer")
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(20, 20, 20, 20)
        container_layout.setSpacing(15)

        if pixmap_path.exists():
            pixmap = QPixmap(str(pixmap_path))
            image_label = QLabel()
            image_label.setPixmap(pixmap)
            image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            container_layout.addWidget(image_label)

        self.status_label = QLabel()
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setWordWrap(True)

        font = QFont()
        font.setPointSize(10)
        self.status_label.setFont(font)

        container_layout.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setMinimumHeight(25)

        container_layout.addWidget(self.progress_bar)

        layout.addWidget(container)

    def _center_on_screen(self) -> None:
        """Center splash screen on primary screen."""
        screen = self.screen()
        if screen:
            screen_geometry = screen.geometry()
            x = (screen_geometry.width() - self.width()) // 2
            y = (screen_geometry.height() - self.height()) // 2
            self.move(x, y)

    def set_progress(self, value: int) -> None:
        """
        Update progress bar value.

        Args:
            value: Progress value (0-100)
        """
        self.progress_bar.setValue(value)

    def set_status(self, message: str) -> None:
        """
        Update status message.

        Args:
            message: Status message to display
        """
        self.status_label.setText(message)
        logger.debug(f"Splash status: {message}")

    def set_stage(self, stage: str, progress: int = 0) -> None:
        """
        Set current stage with optional progress.

        Args:
            stage: Stage name/message
            progress: Progress value (0-100)
        """
        self.set_status(stage)
        self.set_progress(progress)

    def finish_with_delay(self, widget, delay_ms: int = 500) -> None:
        """
        Close splash screen after delay when target widget is shown.

        Args:
            widget: Widget to show after splash
            delay_ms: Delay in milliseconds
        """

        def close_splash():
            self.close()
            widget.raise_()
            widget.activateWindow()

        QTimer.singleShot(delay_ms, close_splash)

    def showEvent(self, event):
        """Handle show event to ensure centering."""
        super().showEvent(event)
        self._center_on_screen()
