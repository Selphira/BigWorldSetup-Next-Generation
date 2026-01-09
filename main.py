"""
BigWorldSetup NextGen - Main entry point (Optimized).

Handles application initialization with integrated splash screen,
data updates, cache management, and version checking.
"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys
import tracemalloc
import warnings

from PySide6.QtCore import qInstallMessageHandler
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

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
from core.DataUpdater import DataUpdater
from core.StateManager import StateManager
from core.TranslationManager import get_translator, tr
from ui.MainWindow import MainWindow
from ui.pages.BackupPage import BackupPage
from ui.pages.DownloadPage import DownloadPage
from ui.pages.ExtractionPage import ExtractionPage
from ui.pages.InstallationPage import InstallationPage
from ui.pages.InstallationType import InstallationTypePage
from ui.pages.InstallOrder import InstallOrderPage
from ui.pages.ModSelection import ModSelectionPage
from ui.SplashScreen import SplashScreen

logger = logging.getLogger(__name__)

# Add project path to PYTHONPATH
if getattr(sys, "frozen", False):
    application_path = Path(sys._MEIPASS)
else:
    application_path = Path(__file__).parent

sys.path.insert(0, str(application_path))


# Qt message handler for debugging
def qt_message_handler(mode, context, message):
    """Handle Qt messages for debugging."""
    print(f"[Qt:{mode.name}] {message}")
    if context.file:
        print(f"  -> {context.file}:{context.line}")


qInstallMessageHandler(qt_message_handler)
warnings.simplefilter("default")
tracemalloc.start()


class ApplicationInitializer:
    """Handles application initialization in stages."""

    def __init__(self, app: QApplication):
        """
        Initialize application initializer.

        Args:
            app: QApplication instance
        """
        self.app = app
        self.splash = None
        self.state = None
        self.window = None
        self.data_updater = None
        self.version_checker = None

    def run(self) -> tuple[MainWindow, StateManager]:
        """
        Run full initialization process.

        Returns:
            Tuple of (MainWindow, StateManager)

        Raises:
            Exception: If initialization fails
        """
        self.splash = SplashScreen(ICONS_DIR / "bws.png")
        self.splash.show()
        self.app.processEvents()

        self.splash.set_stage("Initializing...", 5)
        self.state = self._initialize_state()

        self.splash.set_stage(tr("app.checking_data_update"), 15)
        self._check_and_update_data()

        self.splash.set_stage(tr("app.loading_mods"), 50)
        self._initialize_cache()

        self.splash.set_stage(tr("app.setting_up_interface"), 90)
        self.window = self._create_main_window()

        self.splash.set_stage(tr("app.ready"), 100)

        return self.window, self.state

    def _initialize_state(self) -> StateManager:
        """
        Initialize state manager and core components.

        Returns:
            Initialized StateManager instance
        """
        state = StateManager()

        # Initialize translation
        translator = get_translator(self.app)
        ui_language = state.get_ui_language()
        translator.set_language(ui_language)
        logger.info(f"UI language: {ui_language}")

        # Load game data
        game_manager = state.get_game_manager()
        game_manager.load_games()

        return state

    def _check_and_update_data(self) -> None:
        """Check for and apply data updates if available."""
        self.data_updater = DataUpdater()
        self.data_updater.status_changed.connect(self.splash.set_status)
        self.data_updater.update_error.connect(self._on_update_data_error)
        self.data_updater.progress.connect(self._on_update_data_progress)

        try:
            if self.data_updater.check_for_updates():
                logger.info("Data updates available, downloading...")

                self.data_updater.update_data()
            else:
                logger.info("Data is up to date")

        except Exception as e:
            logger.error(f"Error during data update check: {e}", exc_info=True)

    def _on_update_data_progress(self, progress: int) -> None:
        self.splash.set_progress(15 + int(progress * 0.35))  # 15-50% range

    @staticmethod
    def _on_update_data_error(message: str) -> None:
        QMessageBox.warning(
            None, tr("warning.title"), tr("warning.data_update_failed_detail", message=message)
        )
        logger.warning("Continuing with existing data")

    def _initialize_cache(self) -> None:
        """Initialize or rebuild mod cache."""
        mod_manager = self.state.get_mod_manager()

        if mod_manager.needs_cache_rebuild():
            logger.info("Cache rebuild required")

            if not self._build_cache_with_feedback(mod_manager):
                raise RuntimeError("Cache build failed")

        else:
            logger.info("Loading existing cache")

            if not mod_manager.load_cache():
                logger.error("Cache load failed")
                raise RuntimeError("Failed to load mod cache")

            logger.info(f"Cache loaded: {mod_manager.get_count()} mods")

    def _build_cache_with_feedback(self, mod_manager) -> bool:
        """
        Build cache with splash screen feedback.

        Args:
            mod_manager: ModManager instance

        Returns:
            True if successful
        """
        success = False
        error_msg = None

        def on_cache_ready():
            nonlocal success
            success = True

        def on_cache_error(msg):
            nonlocal error_msg
            error_msg = msg

        mod_manager.cache_ready.connect(on_cache_ready)
        mod_manager.cache_error.connect(on_cache_error)

        if not mod_manager.build_cache_async():
            QMessageBox.critical(
                None, tr("error.critical_title"), tr("error.unable_to_start_cache_building")
            )
            return False

        if mod_manager.builder_thread:
            mod_manager.builder_thread.progress.connect(
                lambda p: self.splash.set_progress(50 + int(p * 0.4))  # 50-90% range
            )
            mod_manager.builder_thread.status_changed.connect(self.splash.set_status)

        # Wait for completion while processing events
        while not success and error_msg is None:
            self.app.processEvents()

        try:
            mod_manager.cache_ready.disconnect(on_cache_ready)
            mod_manager.cache_error.disconnect(on_cache_error)

            if mod_manager.builder_thread:
                try:
                    mod_manager.builder_thread.progress.disconnect()
                    mod_manager.builder_thread.status_changed.disconnect()
                except (RuntimeError, TypeError):
                    pass
        except RuntimeError:
            pass

        if error_msg:
            QMessageBox.critical(
                None,
                tr("error.critical_title"),
                tr("error.cache_build_failed", mods_dir=str(MODS_DIR)),
            )
            return False

        logger.info("Cache built successfully")
        return True

    def _create_main_window(self) -> MainWindow:
        """Create and configure main window."""
        window = MainWindow(self.state)

        pages = [
            InstallationTypePage(self.state),
            BackupPage(self.state),
            ModSelectionPage(self.state),
            InstallOrderPage(self.state),
            DownloadPage(self.state),
            ExtractionPage(self.state),
            InstallationPage(self.state),
        ]

        for page in pages:
            window.register_page(page)

        initial_page = self._get_initial_page()
        window.show_page(initial_page)
        window.check_for_updates()

        return window

    def _get_initial_page(self) -> str:
        """Determine which page to show initially."""
        if not self.state.is_valid_state():
            return "installation_type"

        last_page = self.state.get_ui_current_page()
        if last_page:
            logger.info(f"Restoring last page: {last_page}")
            return last_page

        return "installation_type"

    def finish_splash(self) -> None:
        """Close splash screen and show main window."""
        if self.splash and self.window:
            self.splash.finish_with_delay(self.window, delay_ms=500)


def setup_logging() -> None:
    """Configure application logging with file rotation and console output."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.handlers.clear()

    file_handler = RotatingFileHandler(
        LOG_DIR / LOG_FILE_NAME,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT))
    root_logger.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT))
    root_logger.addHandler(console_handler)

    logger.info(f"{APP_NAME} v{APP_VERSION} - Logging initialized")


def load_stylesheet(theme: str = "lcc") -> str:
    """
    Load application stylesheet from file.

    Args:
        theme: Theme name

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


def setup_exception_hook() -> None:
    """Install global exception handler for uncaught exceptions."""

    def exception_handler(exc_type, exc_value, exc_traceback):
        """Handle uncaught exceptions."""
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return

        logger.critical("Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback))

        error_msg = f"{exc_type.__name__}: {exc_value}"
        QMessageBox.critical(
            None, tr("error.critical_title"), tr("error.uncaught_exception", error=error_msg)
        )

    sys.excepthook = exception_handler


def create_window_icon() -> QIcon:
    """Create application icon."""
    icon_path = ICONS_DIR / "bws.png"

    if icon_path.exists():
        return QIcon(str(icon_path))

    return QIcon()


def main() -> int:
    """
    Main application entry point.

    Returns:
        Exit code (0 for success, non-zero for error)
    """
    setup_logging()
    setup_exception_hook()
    logger.info(f"Starting {APP_NAME} v{APP_VERSION}")

    try:
        app = QApplication(sys.argv)
        app.setApplicationName(APP_NAME)
        app.setApplicationVersion(APP_VERSION)
        app.setOrganizationName(APP_ORG)
        app.setWindowIcon(create_window_icon())

        # Set visual style
        app.setStyle("Fusion")
        stylesheet = load_stylesheet()
        app.setStyleSheet(stylesheet)

        initializer = ApplicationInitializer(app)
        window, state = initializer.run()

        window.show()
        initializer.finish_splash()

        exit_code = app.exec()
        logger.info(f"Application exiting with code {exit_code}")

        return exit_code

    except Exception as e:
        logger.critical(f"Fatal error during initialization: {e}", exc_info=True)

        try:
            QMessageBox.critical(
                None, "Critical Error", f"Fatal error during initialization:\n\n{e}"
            )
        except:
            pass

        return 1


if __name__ == "__main__":
    sys.exit(main())
