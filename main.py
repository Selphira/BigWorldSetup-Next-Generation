"""
BigWorldSetup NextGen - Main entry point.

Handles application initialization, logging setup, cache management,
and main window display.
"""

import logging
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QApplication, QMessageBox, QSplashScreen

from constants import (
    APP_NAME,
    APP_ORG,
    APP_VERSION,
    ICONS_DIR,
    LOG_BACKUP_COUNT,
    LOG_DATE_FORMAT,
    LOG_DIR,
    LOG_FILE_NAME,
    LOG_FORMAT,
    LOG_MAX_BYTES,
    MODS_DIR,
    THEMES_DIR,
)
from core.StateManager import StateManager
from core.TranslationManager import get_translator, tr
from ui.CacheDialog import show_cache_build_dialog
from ui.MainWindow import MainWindow
from ui.pages.DownloadPage import DownloadPage
from ui.pages.ExtractionPage import ExtractionPage
from ui.pages.InstallationPage import InstallationPage
from ui.pages.InstallationType import InstallationTypePage
from ui.pages.InstallOrder import InstallOrderPage
from ui.pages.ModSelection import ModSelectionPage

logger = logging.getLogger(__name__)


def setup_logging() -> None:
    """
    Configure application logging with file rotation and console output.

    Creates log directory if needed and sets up handlers for both
    file and console output with appropriate formatting.
    """
    # Create log directory
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # Root logger configuration
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    # Remove existing handlers
    root_logger.handlers.clear()

    # File handler with rotation
    from logging.handlers import RotatingFileHandler

    file_handler = RotatingFileHandler(
        LOG_DIR / LOG_FILE_NAME,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT))
    root_logger.addHandler(file_handler)

    # Console handler (only warnings and above)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT))
    root_logger.addHandler(console_handler)

    logger.info(f"{APP_NAME} v{APP_VERSION} - Logging initialized")


def load_stylesheet(theme: str = "lcc") -> str:
    """
    Load application stylesheet from file.

    Returns:
        CSS stylesheet string
    """
    stylesheet_path = THEMES_DIR / theme / "style.qss"

    if stylesheet_path.exists():
        try:
            return stylesheet_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.error(f"Failed to load stylesheet: {e}")

    return ""


def initialize_cache(mod_manager) -> bool:
    """
    Initialize or rebuild mod cache if needed.

    Args:
        mod_manager: ModManager instance

    Returns:
        True if cache is ready, False on error
    """
    if mod_manager.needs_cache_rebuild():
        logger.info("Cache rebuild required")

        # Show progress dialog for cache building
        success = show_cache_build_dialog(mod_manager)

        if not success:
            logger.error("Cache build failed")
            QMessageBox.critical(
                None,
                tr("error.critical_title"),
                tr("error.cache_build_failed", mods_dir=str(MODS_DIR)),
            )
            return False

        logger.info("Cache built successfully")
    else:
        logger.info("Loading existing cache")

        # Load existing cache
        if not mod_manager.load_cache():
            logger.error("Cache load failed")
            QMessageBox.critical(
                None, tr("error.critical_title"), tr("error.cache_load_failed")
            )
            return False

        logger.info(f"Cache loaded: {mod_manager.get_count()} mods")

    return True


def register_pages(window: MainWindow, state: StateManager) -> None:
    """
    Register all application pages with the main window.

    Args:
        window: MainWindow instance
        state: StateManager instance
    """
    pages = [
        InstallationTypePage(state),
        ModSelectionPage(state),
        InstallOrderPage(state),
        DownloadPage(state),
        ExtractionPage(state),
        InstallationPage(state),
        # SummaryPage(state),
    ]

    for page in pages:
        window.register_page(page)

    logger.info(f"Registered {len(pages)} pages")


def get_initial_page(state: StateManager) -> str:
    """
    Determine which page to show initially.

    Args:
        state: StateManager instance

    Returns:
        Page ID to show
    """
    # Try to restore last page
    last_page = state.get_ui_current_page()

    if last_page:
        logger.info(f"Restoring last page: {last_page}")
        return last_page

    # Default to first page
    logger.info("Starting from first page")
    return "installation_type"


def setup_exception_hook() -> None:
    """
    Install global exception handler for uncaught exceptions.

    Logs exceptions and shows error dialog to user.
    """

    def exception_handler(exc_type, exc_value, exc_traceback):
        """Handle uncaught exceptions."""
        if issubclass(exc_type, KeyboardInterrupt):
            # Allow Ctrl+C to work
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return

        # Log the exception
        logger.critical("Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback))

        # Show error dialog
        error_msg = f"{exc_type.__name__}: {exc_value}"
        QMessageBox.critical(
            None, tr("error.critical_title"), tr("error.uncaught_exception", error=error_msg)
        )

    sys.excepthook = exception_handler


def create_window_icon() -> QIcon:
    icon_path = ICONS_DIR / "bws.png"
    icon = QIcon()

    if icon_path.exists():
        pixmap = QPixmap(str(icon_path))
        icon = QIcon(pixmap)
    return icon


def main() -> int:
    """
    Main application entry point.

    Returns:
        Exit code (0 for success, non-zero for error)
    """
    # Setup logging first
    setup_logging()
    logger.info(f"Starting {APP_NAME} v{APP_VERSION}")

    # Install exception handler
    setup_exception_hook()

    try:
        # Create Qt application
        app = QApplication(sys.argv)
        app.setApplicationName(APP_NAME)
        app.setApplicationVersion(APP_VERSION)
        app.setOrganizationName(APP_ORG)
        app.setWindowIcon(create_window_icon())

        # Set visual style
        app.setStyle("Fusion")
        stylesheet = load_stylesheet()
        app.setStyleSheet(stylesheet)

        # Create the splash screen
        pixmap = QPixmap(str(ICONS_DIR / "bws.png"))
        splash = QSplashScreen(
            pixmap, Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.FramelessWindowHint
        )
        splash.show()
        app.processEvents()

        # Initialize state manager
        state = StateManager()

        # Initialize translation
        translator = get_translator(app)
        ui_language = state.get_ui_language()
        translator.set_language(ui_language)
        logger.info(f"UI language: {ui_language}")

        game_manager = state.get_game_manager()
        game_manager.load_games()

        # Initialize mod manager and cache
        mod_manager = state.get_mod_manager()
        if not initialize_cache(mod_manager):
            logger.error("Cache initialization failed, exiting")
            return 1

        # Create and configure main window
        window = MainWindow(state)
        register_pages(window, state)

        # Show initial page
        initial_page = get_initial_page(state)
        window.show_page(initial_page)

        # Show window
        window.show()
        logger.info("Main window displayed")

        # Hide the splash screen
        splash.finish(window)
        # Run event loop
        exit_code = app.exec()

        logger.info(f"Application exiting with code {exit_code}")
        return exit_code

    except Exception as e:
        logger.critical(f"Fatal error during initialization: {e}", exc_info=True)

        # Try to show error dialog
        try:
            QMessageBox.critical(
                None, "Critical Error", f"Fatal error during initialization:\n\n{e}"
            )
        except:
            pass

        return 1


if __name__ == "__main__":
    sys.exit(main())
