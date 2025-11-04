"""Main application window with page navigation system."""

import logging
from typing import Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
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

from core.StateManager import StateManager
from core.TranslationManager import get_translator, tr
from ui.pages.BasePage import BasePage
from ui.widgets.LanguageSelector import LanguageSelector

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """Main application window with page-based navigation system.

    Manages page registration, navigation flow, and UI updates for language changes.
    """

    # UI Constants
    WINDOW_MIN_WIDTH = 1200
    WINDOW_MIN_HEIGHT = 800
    HEADER_HEIGHT = 80
    FOOTER_HEIGHT = 70
    BUTTON_WIDTH = 150

    # Style Constants
    HEADER_BG_COLOR = "#252525"
    FOOTER_BG_COLOR = "#1a1a1a"
    STEP_TEXT_COLOR = "#888888"

    def __init__(self, state_manager: StateManager) -> None:
        """Initialize main window.

        Args:
            state_manager: Application state manager
        """
        super().__init__()

        self.state_manager = state_manager

        # Page management
        self.pages: Dict[str, BasePage] = {}
        self.page_order: List[str] = []
        self.current_page_id: Optional[str] = None

        # UI components (initialized in create_widgets)
        self.stack: Optional[QStackedWidget] = None
        self.page_title: Optional[QLabel] = None
        self.page_step: Optional[QLabel] = None
        self.btn_previous: Optional[QPushButton] = None
        self.btn_next: Optional[QPushButton] = None
        self.lang_button: Optional[LanguageSelector] = None

        self._setup_window()
        self._create_widgets()
        self._connect_signals()

        self.lang_button.select_language(self.state_manager.get_ui_language())

        logger.info("Main window initialized")

    def _setup_window(self) -> None:
        """Configure window properties."""
        self.setWindowTitle("BigWorldSetup NextGen")
        self.setMinimumSize(self.WINDOW_MIN_WIDTH, self.WINDOW_MIN_HEIGHT)

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
        frame.setFixedHeight(self.HEADER_HEIGHT)
        frame.setStyleSheet(
            f"background-color: {self.HEADER_BG_COLOR}; border-radius: 0px;"
        )

        layout = QHBoxLayout(frame)
        layout.setContentsMargins(30, 10, 10, 10)

        # Left side: Title and step
        left_layout = self._create_title_section()

        # Right side: Language selector
        self.lang_button = LanguageSelector(
            available_languages=get_translator().get_available_languages()
        )

        # Assemble layout
        layout.addLayout(left_layout)
        layout.addStretch()
        layout.addWidget(self.lang_button)

        return frame

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
        title_font = self.page_title.font()
        title_font.setPointSize(20)
        title_font.setBold(True)
        self.page_title.setFont(title_font)
        layout.addWidget(self.page_title)

        # Step indicator
        self.page_step = QLabel("")
        step_font = self.page_step.font()
        step_font.setPointSize(12)
        self.page_step.setFont(step_font)
        self.page_step.setStyleSheet(f"color: {self.STEP_TEXT_COLOR};")
        layout.addWidget(self.page_step)

        return layout

    def _create_footer(self) -> QFrame:
        """Create footer with navigation buttons.

        Returns:
            Footer frame widget
        """
        frame = QFrame()
        frame.setFixedHeight(self.FOOTER_HEIGHT)
        frame.setAutoFillBackground(True)

        # Set background color
        palette = frame.palette()
        palette.setColor(frame.backgroundRole(), QColor(self.FOOTER_BG_COLOR))
        frame.setPalette(palette)

        # Create buttons
        self.btn_previous = self._create_navigation_button(
            text=f"← {tr('button.previous')}",
            callback=self._on_previous_clicked
        )

        self.btn_next = self._create_navigation_button(
            text=f"{tr('button.next')} →",
            callback=self._on_next_clicked
        )

        # Layout
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(20, 15, 20, 15)
        layout.addStretch()
        layout.addWidget(self.btn_previous)
        layout.addWidget(self.btn_next)

        return frame

    def _create_navigation_button(
            self,
            text: str,
            callback
    ) -> QPushButton:
        """Create a navigation button.

        Args:
            text: Button text
            callback: Click callback function

        Returns:
            Configured button
        """
        button = QPushButton(text)
        button.setFixedWidth(self.BUTTON_WIDTH)
        button.clicked.connect(callback)
        return button

    def _connect_signals(self) -> None:
        """Connect all signal handlers."""
        get_translator().language_changed.connect(self._on_translator_language_changed)
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

        # Update UI
        self._update_page_title()
        self._update_navigation_buttons()

        # Notify page
        page.on_page_shown()

        logger.info(f"Page shown: {page_id}")
        return True

    # ========================================
    # NAVIGATION LOGIC
    # ========================================

    def get_next_page_id(self, current_id: str) -> Optional[str]:
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

    def get_previous_page_id(self, current_id: str) -> Optional[str]:
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
        self.page_step.setText(
            tr('app.step', current=current_index + 1, total=total)
        )

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

    def _update_ui_language(self, code: str) -> None:
        """Update all UI text for new language.

        Args:
            code: New language code
        """
        # Update navigation buttons
        self._update_page_title()
        self.btn_previous.setText(f"← {tr('button.previous')}")
        self.btn_next.setText(f"{tr('button.next')} →")

        # Notify all pages
        for page in self.pages.values():
            page.update_ui_language(code)

        logger.info(f"UI language updated: {code}")

    # ========================================
    # EVENT HANDLERS
    # ========================================

    def _on_previous_clicked(self) -> None:
        """Handle previous button click."""
        if not self.current_page_id:
            return

        page = self.pages[self.current_page_id]

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
        page.save_data()

        # Check for custom next page
        next_id = page.get_next_page_id()
        if next_id is None:
            next_id = self.get_next_page_id(self.current_page_id)

        if next_id:
            self.show_page(next_id)

    def _on_translator_language_changed(self, code: str) -> None:
        """Handle language change from translator.

        Args:
            code: New language code
        """
        self._update_ui_language(code)

    def _on_language_button_changed(self, code: str) -> None:
        """Handle language change from language button.

        Args:
            code: New language code
        """
        get_translator().set_language(code)
        self.state_manager.set_ui_language(code)

    # ========================================
    # LIFECYCLE
    # ========================================

    def closeEvent(self, event) -> None:
        """Handle window close event.

        Args:
            event: Close event
        """
        logger.info("Application closing, saving state")
        self.state_manager.save_state()
        super().closeEvent(event)