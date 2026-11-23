"""Start the application against the BWS_NG_VAR_DIR root, then use its services.

Run by `test_l12_var_root` in a separate interpreter, because the root is read
once, when `constants` is imported. The network is disabled; the published
catalogue is served from a local zip.

Usage: python -m tests.var_root_probe <published catalogue zip> <user archive>
"""

import hashlib
from pathlib import Path
import shutil
import socket
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from constants import DOWNLOADED_CATALOGUE_DIR
from core.DataUpdater import DataUpdater
from core.InstallationExecutor import InstallationExecutor
from core.StateManager import StateManager
import main
from ui.MainWindow import MainWindow

PROBE_TIMEOUT_MS = 90_000
EXIT_SUCCESS = 0
EXIT_STARTUP_FAILED = 2
EXIT_TIMEOUT = 3
EXIT_WRONG_CATALOGUE = 4
EXIT_LOCK_REFUSED = 5


def disable_network() -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise OSError("Network disabled by the probe")

    socket.socket.connect = refuse  # type: ignore[method-assign]
    socket.create_connection = refuse  # type: ignore[assignment]


def publish_catalogue(package: Path) -> None:
    """Serve `package` as the published catalogue, newer than any local one."""
    version = {"sha256": hashlib.sha256(package.read_bytes()).hexdigest(), "timestamp": 1}

    def download(self: DataUpdater, url: str, destination: Path) -> bool:
        shutil.copyfile(package, destination)
        return True

    DataUpdater._fetch_remote_version = staticmethod(lambda: version)  # type: ignore[method-assign]
    DataUpdater._download_file = download  # type: ignore[method-assign]


def answer_dialogs() -> None:
    """Startup dialogs would wait for a user: answer them without showing them."""
    main.ApplicationInitializer._choose_ui_language = staticmethod(  # type: ignore[method-assign]
        lambda state, languages: "en_US"
    )
    for name in ("critical", "warning", "information"):
        setattr(
            QMessageBox,
            name,
            staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Ok),
        )


def use_services(state: StateManager, user_archive: Path) -> int:
    """Write every kind of user data the application keeps between sessions."""
    if state.get_mod_manager().mods_dir.parent != DOWNLOADED_CATALOGUE_DIR:
        return EXIT_WRONG_CATALOGUE

    state.save_state()
    state.add_custom_archive("demo", user_archive)

    executor = InstallationExecutor(state)
    if not executor.acquire():
        return EXIT_LOCK_REFUSED
    executor.release()

    return EXIT_SUCCESS


def run(package: Path, user_archive: Path) -> int:
    disable_network()
    publish_catalogue(package)
    answer_dialogs()
    main.setup_logging()

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    initializer = main.ApplicationInitializer(app)

    def on_ready(window: MainWindow) -> None:
        exit_code = use_services(initializer.state, user_archive)
        # Qt aborts the process if a running thread is destroyed at exit
        if window.version_check_thread is not None:
            window.version_check_thread.wait()
        app.exit(exit_code)

    initializer.ready.connect(on_ready)
    initializer.failed.connect(lambda: app.exit(EXIT_STARTUP_FAILED))
    QTimer.singleShot(0, initializer.start)
    QTimer.singleShot(PROBE_TIMEOUT_MS, lambda: app.exit(EXIT_TIMEOUT))

    exit_code = app.exec()
    initializer.shutdown()
    return exit_code


if __name__ == "__main__":
    sys.exit(run(Path(sys.argv[1]), Path(sys.argv[2])))
