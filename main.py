from collections.abc import Callable
import contextlib
from dataclasses import dataclass
import logging
from logging.handlers import RotatingFileHandler
import sys

from PySide6.QtCore import QLocale, QObject, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from constants import (
    APP_NAME,
    APP_ORG,
    APP_VERSION,
    CACHE_DIR,
    ICONS_DIR,
    LOG_BACKUP_COUNT,
    LOG_DATE_FORMAT,
    LOG_DIR,
    LOG_FILE_NAME,
    LOG_FORMAT,
    LOG_MAX_BYTES,
    MODS_DIR,
    RESOURCE_ROOT,
    THEMES_DIR,
)
from core.CatalogueUpdateTask import CatalogueUpdateOutcome, CatalogueUpdateTask
from core.DataUpdater import DataUpdater
from core.InstallationExecutor import InstallationExecutor
from core.LocalizedCache import LocalizedCacheManager
from core.StateManager import StateManager
from core.TranslationManager import get_supported_languages, get_translator, tr
from ui.LanguageChoiceDialog import LanguageChoiceDialog
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
application_path = RESOURCE_ROOT

sys.path.insert(0, str(application_path))


@dataclass(frozen=True)
class CacheStage:
    """Cache being prepared at startup and the step following it."""

    manager: LocalizedCacheManager
    progress_range: tuple[int, int]
    on_ready: Callable[[], None]


class ApplicationInitializer(QObject):
    """
    Initialize the application in stages chained by signals.

    The network and the cache builds run in worker threads: the event loop keeps
    running and the splash stays responsive, with a way to skip the data update.

    Signals:
        ready: MainWindow, created once every stage succeeded
        failed: An error stopped the initialization, already reported to the user
    """

    ready = Signal(object)
    failed = Signal()

    def __init__(self, app: QApplication):
        """
        Initialize application initializer.

        Args:
            app: QApplication instance
        """
        super().__init__()
        self.app = app
        self.splash: SplashScreen | None = None
        self.state: StateManager | None = None
        self.window: MainWindow | None = None
        self.data_updater: DataUpdater = DataUpdater()
        self._catalogue_update: CatalogueUpdateTask | None = None
        self._cache_stage: CacheStage | None = None

        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def start(self) -> None:
        """Start the initialization; it ends with `ready` or `failed`."""
        self._run_stage(self._start_stages)

    def shutdown(self) -> None:
        """Wait for a catalogue update thread still running at exit."""
        if self._catalogue_update:
            self._catalogue_update.wait_for_thread()

    def finish_splash(self) -> None:
        """Close splash screen and show main window."""
        if self.splash and self.window:
            self.splash.finish_with_delay(self.window, delay_ms=500)

    # ========================================
    # STAGES
    # ========================================

    def _start_stages(self) -> None:
        # The first launch language dialog must not sit behind the splash screen.
        self.state = self._initialize_state()

        self.splash = SplashScreen(ICONS_DIR / "bws.png")
        self.splash.skip_update_requested.connect(self._skip_catalogue_update)
        self.splash.show()

        self.splash.set_stage(tr("app.initializing"), 5)
        self._recover_catalogue()

        if self._is_installation_in_progress():
            # The run was planned against the current catalogue: never replace it meanwhile.
            logger.info("Installation in progress: catalogue update skipped")
            self._load_catalogue()
            return

        self.splash.set_stage(tr("app.checking_data_update"), 10)
        self._start_catalogue_update()

    def _start_catalogue_update(self) -> None:
        self._catalogue_update = CatalogueUpdateTask(self.data_updater)
        self._catalogue_update.status_changed.connect(self.splash.set_status)
        self._catalogue_update.progress.connect(self._on_update_data_progress)
        self._catalogue_update.finished.connect(self._on_catalogue_update_finished)

        self.splash.set_skip_update_available(True)
        self._catalogue_update.start()

    def _on_catalogue_update_finished(self, outcome: CatalogueUpdateOutcome) -> None:
        self.splash.set_skip_update_available(False)

        if outcome is CatalogueUpdateOutcome.FAILED:
            self._warn_update_failed(self._catalogue_update.errors)

        self._run_stage(self._load_catalogue)

    def _load_catalogue(self) -> None:
        self._recover_catalogue()
        self.state.get_game_manager().load_games()

        self.splash.set_stage(tr("app.loading_mods"), 30)
        self._prepare_cache(self.state.get_mod_manager(), (30, 60), self._load_rules)

    def _load_rules(self) -> None:
        self.splash.set_stage(tr("app.loading_rules"), 60)
        self._prepare_cache(self.state.get_rule_manager(), (60, 90), self._create_interface)

    def _create_interface(self) -> None:
        self.splash.set_stage(tr("app.setting_up_interface"), 90)
        self.window = self._create_main_window()

        self.splash.set_stage(tr("app.ready"), 100)
        self.ready.emit(self.window)

    def _run_stage(self, stage: Callable[[], None]) -> None:
        """Run a stage, turning any error into the terminal failure."""
        try:
            stage()
        except Exception as error:
            logger.critical(f"Fatal error during initialization: {error}", exc_info=True)
            self._fail(f"Fatal error during initialization:\n\n{error}")

    def _fail(self, message: str) -> None:
        with contextlib.suppress(BaseException):
            QMessageBox.critical(self._dialog_parent(), tr("error.critical_title"), message)

        if self.splash:
            self.splash.close()

        self.failed.emit()

    def _dialog_parent(self) -> SplashScreen | None:
        """Return the parent of startup dialogs, which would otherwise open below the splash."""
        if self.splash and self.splash.isVisible():
            return self.splash

        return None

    def _initialize_state(self) -> StateManager:
        """
        Initialize state manager and core components.

        Returns:
            Initialized StateManager instance
        """
        state = StateManager()
        translator = get_translator(self.app)
        languages = [code for code, _ in get_supported_languages()]

        # The UI language comes first: it seeds the mod languages order.
        if not state.has_ui_language():
            state.set_ui_language(self._choose_ui_language(state, languages))

        ui_language = state.get_ui_language()
        translator.set_language(ui_language)
        logger.info(f"UI language: {ui_language}")

        state.initialize_languages_order(languages)

        return state

    @staticmethod
    def _choose_ui_language(state: StateManager, languages: list[str]) -> str:
        """Ask for the UI language, suggesting the system one when supported."""
        system_language = QLocale.system().name()
        initial_language = (
            system_language if system_language in languages else state.get_ui_language()
        )

        dialog = LanguageChoiceDialog(languages, initial_language)
        dialog.exec()

        return dialog.selected_language()

    def _recover_catalogue(self) -> None:
        if not self.data_updater.recover_pending_update():
            raise RuntimeError("Catalogue recovery failed; backup retained")

    def _is_installation_in_progress(self) -> bool:
        """Tell whether a run waits to be resumed; an unreadable journal may hide one."""
        executor = InstallationExecutor(self.state)
        executor.load()
        return executor.is_active or executor.load_error is not None

    # ========================================
    # CATALOGUE UPDATE FEEDBACK
    # ========================================

    def _skip_catalogue_update(self) -> None:
        if self._catalogue_update and not self._catalogue_update.cancel():
            # The new catalogue is being activated: its outcome is awaited.
            self.splash.set_skip_update_available(False)

    def _on_update_data_progress(self, progress: int) -> None:
        # Map update progress to 10-30% range
        self.splash.set_progress(10 + int(progress * 0.20))

    def _warn_update_failed(self, errors: list[str]) -> None:
        QMessageBox.warning(
            self._dialog_parent(),
            tr("warning.data_update_failed_title"),
            tr("warning.data_update_failed_detail", message="\n".join(errors)),
        )
        logger.warning("Continuing with existing data")

    # ========================================
    # CACHES
    # ========================================

    def _prepare_cache(
        self,
        manager: LocalizedCacheManager,
        progress_range: tuple[int, int],
        on_ready: Callable[[], None],
    ) -> None:
        """Load or build a cache, then continue with `on_ready` once it is ready."""
        self._cache_stage = CacheStage(manager, progress_range, on_ready)

        manager.cache_ready.connect(self._on_cache_ready)
        manager.cache_error.connect(self._on_cache_error)
        manager.build_progress.connect(self._on_cache_progress)
        manager.build_status.connect(self.splash.set_status)

        manager.prepare_cache()

    def _on_cache_progress(self, progress: int) -> None:
        start_pct, end_pct = self._cache_stage.progress_range
        self.splash.set_progress(start_pct + int(progress * (end_pct - start_pct) / 100))

    def _on_cache_ready(self, _loaded: bool) -> None:
        stage = self._end_cache_stage()
        logger.info(f"{stage.manager.cache_label.capitalize()} ready")
        self._run_stage(stage.on_ready)

    def _on_cache_error(self, message: str) -> None:
        stage = self._end_cache_stage()
        logger.error(f"{stage.manager.cache_label.capitalize()} preparation failed: {message}")
        self._fail(tr("error.cache_build_failed", mods_dir=str(MODS_DIR)))

    def _end_cache_stage(self) -> CacheStage:
        """Detach the current cache: its first terminal signal ends the stage."""
        stage = self._cache_stage
        self._cache_stage = None

        stage.manager.cache_ready.disconnect(self._on_cache_ready)
        stage.manager.cache_error.disconnect(self._on_cache_error)
        stage.manager.build_progress.disconnect(self._on_cache_progress)
        stage.manager.build_status.disconnect(self.splash.set_status)

        return stage

    # ========================================
    # INTERFACE
    # ========================================

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

        initial_page = (
            "installation"
            if window.pages["installation"].is_resume()
            else self._get_initial_page()
        )
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


def show_main_window(
    app: QApplication, initializer: ApplicationInitializer, window: MainWindow
) -> None:
    """Show the main window once initialized; closing it now quits the application."""
    window.show()
    initializer.finish_splash()
    app.setQuitOnLastWindowClosed(True)


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

        # No window is open during the first stages: closing a dialog must not quit.
        app.setQuitOnLastWindowClosed(False)

        initializer = ApplicationInitializer(app)
        initializer.ready.connect(lambda window: show_main_window(app, initializer, window))
        initializer.failed.connect(lambda: app.exit(1))
        app.aboutToQuit.connect(initializer.shutdown)
        QTimer.singleShot(0, initializer.start)

        exit_code = app.exec()
        logger.info(f"Application exiting with code {exit_code}")

        return exit_code

    except Exception as e:
        logger.critical(f"Fatal error during initialization: {e}", exc_info=True)

        with contextlib.suppress(BaseException):
            QMessageBox.critical(
                None, "Critical Error", f"Fatal error during initialization:\n\n{e}"
            )

        return 1


if __name__ == "__main__":
    sys.exit(main())
