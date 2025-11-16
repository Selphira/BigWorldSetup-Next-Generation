import logging
import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from core.StateManager import StateManager
from ui.CacheDialog import show_cache_build_dialog
from ui.MainWindow import MainWindow
from ui.pages.InstallationType import InstallationTypePage
from ui.pages.ModSelection import ModSelectionPage

# Configuration simple
logging.basicConfig(
    level=logging.DEBUG,  # Niveau minimum à afficher
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)


def main():
    app = QApplication(sys.argv)
    state = StateManager()
    mod_manager = state.get_mod_manager()

    # Vérifier si le cache doit être construit ou chargé
    if mod_manager.needs_cache_rebuild():
        # Le cache doit être reconstruit - afficher le dialogue
        success = show_cache_build_dialog(mod_manager)

        if not success:
            # Erreur critique, on ne peut pas continuer
            QMessageBox.critical(
                None,
                "Erreur",
                "Impossible de construire le cache des mods.\n"
                "Vérifiez que les fichiers sont présents dans le dossier data/mods."
            )
            return 1
    else:
        # Le cache existe déjà, le charger directement
        if not mod_manager.load_cache():
            QMessageBox.critical(
                None,
                "Erreur",
                "Impossible de charger le cache des mods."
            )
            return 1

    app.setStyle("Fusion")
    app.setStyleSheet("""
        QLineEdit {
            background-image: url("resources/background.jpg");
            padding: 5px 10px;
            border: 1px solid #96846e;
            border-radius: 10px;
        }
        QLineEdit:focus {
            border: 1px solid Goldenrod;
        }

        QToolTip {
            color: #f0f0f0;
            background-color: #2a2a2a;
            border: 1px solid #96846e;
            border-radius: 6px;
            padding: 6px;
            font-size: 10pt;
        }

        QFrame#gameButtonFrame {
            background-color: #2a2a2a;
        }
        QFrame#gameButtonFrame:hover {
            background-color: #333333;
        }
    """)
    window = MainWindow(state)
    window.register_page(InstallationTypePage(state))
    window.register_page(ModSelectionPage(state))
    # TODO: Afficher la page qu'il y avait au moment de fermer l'application
    window.show_page(state.get_ui_current_page() or 'installation_type')
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
