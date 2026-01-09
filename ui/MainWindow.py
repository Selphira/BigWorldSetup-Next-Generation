"""Main application window with page navigation system."""

import logging

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from constants import (
    APP_NAME,
    BUTTON_WIDTH_STANDARD,
    COLOR_BACKGROUND_SECONDARY,
    FOOTER_HEIGHT,
    GAME_BUTTON_ICON_SIZE,
    HEADER_HEIGHT,
    ICON_GAME_DEFAULT,
    ICONS_DIR,
    WINDOW_MIN_HEIGHT,
    WINDOW_MIN_WIDTH,
)
from core.GameModels import GameDefinition
from core.StateManager import StateManager
from core.TranslationManager import get_translator, tr
from core.VersionChecker import VersionChecker, VersionInfo
from ui.pages.BasePage import BasePage
from ui.widgets.LanguageSelector import LanguageSelector

logger = logging.getLogger(__name__)


class VersionCheckerThread(QThread):
    version_checked = Signal(object)  # VersionInfo | None

    def __init__(self, version_checker: VersionChecker):
        super().__init__()
        self.version_checker = version_checker

    def run(self):
        try:
            version_info = self.version_checker.check_for_update()
            self.version_checked.emit(version_info)
        except Exception as e:
            logger.warning(f"Version check failed: {e}")
            self.version_checked.emit(None)


class MainWindow(QMainWindow):
    """Main application window with page-based navigation system.

    Manages page registration, navigation flow, and UI updates for language changes.
    """

    def __init__(self, state_manager: StateManager) -> None:
        """Initialize main window.

        Args:
            state_manager: Application state manager
        """
        super().__init__()

        self.state_manager = state_manager
        self.state_manager.set_reset_workflow_callback(self.reset_workflow)

        # Page management
        self.pages: dict[str, BasePage] = {}
        self.page_order: list[str] = []
        self.current_page_id: str | None = None

        # UI components (initialized in create_widgets)
        self.stack: QStackedWidget | None = None
        self.page_title: QLabel | None = None
        self.page_step: QLabel | None = None
        self.btn_previous: QPushButton | None = None
        self.btn_next: QPushButton | None = None
        self.lang_button: LanguageSelector | None = None
        self.update_button: QPushButton | None = None
        self.version_check_thread: VersionCheckerThread | None = None
        self._current_version_info: VersionInfo | None = None

        self._page_buttons: dict[str, list[QPushButton]] = {}

        self._setup_window()
        self._create_widgets()
        self._connect_signals()

        self.lang_button.select_language(self.state_manager.get_ui_language())

        logger.info("Main window initialized")

    def _setup_window(self) -> None:
        """Configure window properties."""
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT)

    def _create_widgets(self) -> None:
        """Create and layout all UI components."""
        # Create main stack
        self.stack = QStackedWidget()

        # Create central widget with layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        layout = QVBoxLayout(central_widget)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)

        # Add components
        layout.addWidget(self._create_header())
        layout.addWidget(self.stack, 1)  # Stack takes remaining space
        layout.addWidget(self._create_footer())

    def _create_header(self) -> QFrame:
        """Create header with page title and language selector.

        Returns:
            Header frame widget
        """
        frame = QFrame()
        frame.setFixedHeight(HEADER_HEIGHT)
        frame.setObjectName("header")

        layout = QHBoxLayout(frame)
        layout.setContentsMargins(30, 10, 10, 10)

        self.game_label = QLabel()
        self.game_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.game_label)

        # Left side: Title and step
        left_layout = self._create_title_section()

        # Right side: Update button (hidden by default) + Language selector
        self.update_button = self._create_update_button()
        self.update_button.hide()
        self.lang_button = LanguageSelector(
            available_languages=get_translator().get_available_languages()
        )

        layout.addLayout(left_layout)
        layout.addStretch()
        layout.addWidget(self.update_button)
        layout.addWidget(self.lang_button)

        return frame

    @staticmethod
    def _create_update_button() -> QPushButton:
        """Create update notification button."""
        button = QPushButton()
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    def _create_title_section(self) -> QVBoxLayout:
        """Create page title and step indicator section.

        Returns:
            Layout with title and step labels
        """
        layout = QVBoxLayout()
        layout.setSpacing(2)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # Page title
        self.page_title = QLabel("")
        self.page_title.setObjectName("page-title")
        layout.addWidget(self.page_title)

        # Step indicator
        self.page_step = QLabel("")
        self.page_step.setObjectName("page-step")
        layout.addWidget(self.page_step)

        return layout

    def _create_footer(self) -> QFrame:
        """Create footer with navigation buttons.

        Returns:
            Footer frame widget
        """
        frame = QFrame()
        frame.setFixedHeight(FOOTER_HEIGHT)
        frame.setAutoFillBackground(True)
        frame.setObjectName("footer")

        # Set background color
        palette = frame.palette()
        palette.setColor(frame.backgroundRole(), QColor(COLOR_BACKGROUND_SECONDARY))
        frame.setPalette(palette)

        # Create buttons
        self.btn_previous = self._create_navigation_button(
            text=f"← {tr('button.previous')}", callback=self._on_previous_clicked
        )

        self.btn_next = self._create_navigation_button(
            text=f"{tr('button.next')} →", callback=self._on_next_clicked
        )

        # Layout
        self._additional_buttons_layout = QHBoxLayout()
        self._additional_buttons_layout.setContentsMargins(0, 0, 0, 0)

        layout = QHBoxLayout(frame)
        layout.setContentsMargins(20, 15, 20, 15)
        layout.addLayout(self._additional_buttons_layout)
        layout.addStretch()
        layout.addWidget(self.btn_previous)
        layout.addWidget(self.btn_next)

        return frame

    @staticmethod
    def _create_navigation_button(text: str, callback) -> QPushButton:
        """Create a navigation button.

        Args:
            text: Button text
            callback: Click callback function

        Returns:
            Configured button
        """
        button = QPushButton(text)
        button.setFixedWidth(BUTTON_WIDTH_STANDARD)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(callback)
        return button

    def _connect_signals(self) -> None:
        """Connect all signal handlers."""
        self.lang_button.language_changed.connect(self._on_language_button_changed)

    # ========================================
    # PAGE MANAGEMENT
    # ========================================

    def register_page(self, page: BasePage) -> None:
        """Register a page in the navigation system.

        Args:
            page: Page to register
        """
        page_id = page.get_page_id()

        if page_id in self.pages:
            logger.warning(f"Page already registered: {page_id}")
            return

        page.navigation_changed.connect(self._on_page_navigation_changed)
        page.game_changed.connect(self._on_game_changed)

        page.load_state()
        # Initial ui translation
        page.retranslate_ui()

        self.pages[page_id] = page
        self.page_order.append(page_id)
        self.stack.addWidget(page)

        logger.debug(f"Page registered: {page_id}")

    def show_page(self, page_id: str) -> bool:
        """Show a specific page.

        Args:
            page_id: ID of page to show

        Returns:
            True if page was shown, False if page not found
        """
        if page_id not in self.pages:
            logger.warning(f"Page not found: {page_id}")
            return False

        page = self.pages[page_id]

        # Hide current page
        if self.current_page_id and self.current_page_id in self.pages:
            self.pages[self.current_page_id].on_page_hidden()

        # Show new page
        self.current_page_id = page_id
        self.stack.setCurrentWidget(page)

        # Notify page
        page.on_page_shown()
        self._update_page_title()
        self._update_navigation_buttons()
        self._update_additional_buttons(page_id, page.get_additional_buttons())

        self.state_manager.set_ui_current_page(page.get_page_id())

        logger.info(f"Page shown: {page_id}")
        return True

    def reset_workflow(self) -> None:
        """Reset the entire workflow: reload all pages state and navigate to start.

        This is typically called when user cancels installation or wants to
        start over. It ensures all pages reload their state from the (now cleared)
        state manager.
        """
        logger.info("Resetting workflow: reloading all pages")

        for page_id, page in self.pages.items():
            try:
                page.load_state()
                logger.debug(f"Page {page_id} state reloaded")
            except Exception as e:
                logger.error(f"Failed to reload state for page {page_id}: {e}")

        first_page_id = self.page_order[0] if self.page_order else None
        if first_page_id:
            self.show_page(first_page_id)
            logger.info(f"Workflow reset complete, navigated to {first_page_id}")
        else:
            logger.warning("No pages registered, cannot navigate after reset")

    # ========================================
    # NAVIGATION LOGIC
    # ========================================

    def get_next_page_id(self, current_id: str) -> str | None:
        """Get next non-skipped page ID.

        Args:
            current_id: Current page ID

        Returns:
            Next page ID or None if at end
        """
        try:
            idx = self.page_order.index(current_id)

            # Find next non-skipped page
            while idx < len(self.page_order) - 1:
                idx += 1
                next_id = self.page_order[idx]
                page = self.pages[next_id]

                if not page.should_skip_page():
                    return next_id

            return None

        except ValueError:
            logger.error(f"Page not in order: {current_id}")
            return None

    def get_previous_page_id(self, current_id: str) -> str | None:
        """Get previous non-skipped page ID.

        Args:
            current_id: Current page ID

        Returns:
            Previous page ID or None if at beginning
        """
        try:
            idx = self.page_order.index(current_id)

            # Find previous non-skipped page
            while idx > 0:
                idx -= 1
                prev_id = self.page_order[idx]
                page = self.pages[prev_id]

                if not page.should_skip_page():
                    return prev_id

            return None

        except ValueError:
            logger.error(f"Page not in order: {current_id}")
            return None

    # ========================================
    # UI UPDATES
    # ========================================

    def _update_page_title(self) -> None:
        """Update page title and step indicator."""
        if not self.current_page_id or self.current_page_id not in self.pages:
            return

        page = self.pages[self.current_page_id]
        current_index = self.page_order.index(self.current_page_id)
        total = len(self.page_order)

        self.page_title.setText(page.get_page_title())
        self.page_step.setText(tr("app.step", current=current_index + 1, total=total))

    def _update_navigation_buttons(self) -> None:
        """Update navigation button states based on current page."""
        if not self.current_page_id or self.current_page_id not in self.pages:
            return

        page = self.pages[self.current_page_id]

        # Previous button
        prev_config = page.get_previous_button_config()
        self.btn_previous.setVisible(prev_config.visible)
        self.btn_previous.setEnabled(prev_config.enabled)
        self.btn_previous.setText(f"← {prev_config.text}")

        # Next button
        next_config = page.get_next_button_config()
        self.btn_next.setVisible(next_config.visible)
        self.btn_next.setEnabled(next_config.enabled)
        self.btn_next.setText(f"{next_config.text} →")

    def _update_additional_buttons(self, page_id: str, buttons: list[QPushButton]) -> None:
        """Show buttons for a specific page."""
        self._hide_additional_buttons()

        if page_id not in self._page_buttons:
            self._page_buttons[page_id] = buttons
            for button in buttons:
                self._additional_buttons_layout.addWidget(button)

        for button in self._page_buttons[page_id]:
            button.show()

    def _hide_additional_buttons(self) -> None:
        """Hide buttons for a specific page."""
        for page_buttons in self._page_buttons.values():
            for button in page_buttons:
                button.hide()

    def _update_update_button_text(self) -> None:
        if not self.update_button or not self._current_version_info:
            return

        version = self._current_version_info.version
        self.update_button.setText(tr("app.update_available", version=version))
        self.update_button.setToolTip(tr("app.update_available_tooltip", version=version))

    def _update_ui_language(self, code: str) -> None:
        """Update all UI text for new language.

        Args:
            code: New language code
        """
        # Update navigation buttons
        self._update_page_title()
        self.btn_previous.setText(f"← {tr('button.previous')}")
        self.btn_next.setText(f"{tr('button.next')} →")

        self.lang_button.retranslate_ui()

        if self.update_button and self.update_button.isVisible():
            self._update_update_button_text()

        # Notify all pages
        for page in self.pages.values():
            page.retranslate_ui()

        logger.info(f"UI language updated: {code}")

    # ========================================
    # EVENT HANDLERS
    # ========================================

    def _on_game_changed(self, game: GameDefinition) -> None:
        """Handle game change"""
        icon_path = ICONS_DIR / f"{game.id}.png"

        if icon_path and icon_path.exists():
            pixmap = QPixmap(str(icon_path))
            scaled_pixmap = pixmap.scaled(
                GAME_BUTTON_ICON_SIZE,
                GAME_BUTTON_ICON_SIZE,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.game_label.setPixmap(scaled_pixmap)
        else:
            self.game_label.setText(ICON_GAME_DEFAULT)
            font = self.game_label.font()
            font.setPointSize(GAME_BUTTON_ICON_SIZE)
            self.game_label.setFont(font)

    def _on_page_navigation_changed(self) -> None:
        """Handle navigation state change from current page.

        Called when page state changes that affect navigation buttons
        (e.g., validation state, game selection).
        """
        self._update_navigation_buttons()
        logger.debug("Navigation buttons updated from page signal")

    def _on_previous_clicked(self) -> None:
        """Handle previous button click."""
        if not self.current_page_id:
            return

        page = self.pages[self.current_page_id]

        # Save page data
        page.save_state()

        # Check for custom previous page
        prev_id = page.get_previous_page_id()
        if prev_id is None:
            prev_id = self.get_previous_page_id(self.current_page_id)

        if prev_id:
            self.show_page(prev_id)

    def _on_next_clicked(self) -> None:
        """Handle next button click."""
        if not self.current_page_id:
            return

        page = self.pages[self.current_page_id]

        # Validate before proceeding
        if not page.validate():
            logger.debug("Page validation failed")
            return

        # Save page data
        page.save_state()

        # Check for custom next page
        next_id = page.get_next_page_id()
        if next_id is None:
            next_id = self.get_next_page_id(self.current_page_id)

        if next_id:
            self.show_page(next_id)

    def _on_language_button_changed(self, code: str) -> None:
        """Handle language change from language button.

        Args:
            code: New language code
        """
        self.state_manager.get_mod_manager().reload_for_language(code)
        get_translator().set_language(code)
        self.state_manager.set_ui_language(code)
        self._update_ui_language(code)

    def _on_version_checked(self, version_info: VersionInfo | None) -> None:
        """Handle version check result."""
        if version_info and version_info.is_newer:
            logger.info(f"Update available: {version_info.version}")

            self._current_version_info = version_info
            self.update_button.clicked.connect(
                lambda: self._open_release_page(version_info.release_url)
            )
            self._update_update_button_text()
            self.update_button.show()
        else:
            logger.debug("No update available or check failed")

    def _on_version_check_finished(self) -> None:
        """Clean up version check thread once completed."""
        if self.version_check_thread:
            self.version_check_thread.deleteLater()
            self.version_check_thread = None

    @staticmethod
    def _open_release_page(url: str) -> None:
        """Open release page in default browser."""
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        if QDesktopServices.openUrl(QUrl(url)):
            logger.info(f"Opened release page: {url}")
        else:
            logger.error(f"Failed to open release page: {url}")

    # ========================================
    # LIFECYCLE
    # ========================================

    def check_for_updates(self) -> None:
        """Check asynchronously if an update is available."""
        if self.version_check_thread is not None:
            logger.debug("Version check already in progress")
            return

        version_checker = VersionChecker()
        self.version_check_thread = VersionCheckerThread(version_checker)
        self.version_check_thread.version_checked.connect(self._on_version_checked)
        self.version_check_thread.finished.connect(self._on_version_check_finished)
        self.version_check_thread.start()

        logger.debug("Version check started in background")

    def closeEvent(self, event) -> None:
        """Handle window close event.

        Args:
            event: Close event
        """
        logger.info("Application closing, saving state")

        if self.current_page_id:
            page = self.pages[self.current_page_id]
            page.save_state()

        self.state_manager.save_state()
        super().closeEvent(event)
