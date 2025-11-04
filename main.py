import sys

from PySide6.QtWidgets import QApplication

from core.StateManager import StateManager
from ui.main_window import MainWindow
from ui.pages.InstallationType import InstallationTypePage

import logging

# Configuration simple
logging.basicConfig(
    level=logging.DEBUG,  # Niveau minimum à afficher
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)

def main():
    app = QApplication(sys.argv)
    state = StateManager()

    app.setStyle("Fusion")

    window = MainWindow(state)
    window.register_page(InstallationTypePage(state))
    window.show_page('installation_type')
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
