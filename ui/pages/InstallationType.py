"""Installation type selection page with game-based validation."""

import logging
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from constants import ICONS_DIR
from core.StateManager import StateManager
from core.TranslationManager import tr
from core.enums.GameEnum import GameEnum
from core.validators.FolderValidator import WritableFolderValidator, GameFolderValidator
from ui.pages.BasePage import BasePage, ButtonConfig
from ui.widgets.FolderSelector import FolderSelector, GameFolderSelector
from ui.widgets.GameButton import GameButton
from ui.widgets.SortableLanguages import SortableLanguages

logger = logging.getLogger(__name__)


class InstallationTypePage(BasePage):
    """
    Page for selecting game type and configuring installation folders.

    Features:
    - Game type selection from available options
    - Game folder path configuration with validation
    - Download and backup folder configuration
    - Language preference ordering

    Uses folder sharing: multiple games can reference the same folder widget
    (e.g., EET sequences reference SOD and BG2EE folder widgets).
    """

    # Layout constants
    LEFT_PANEL_WIDTH = 400
    GRID_COLUMNS = 2

    def __init__(self, state_manager: StateManager) -> None:
        """
        Initialize installation type page.

        Args:
            state_manager: Application state manager
        """
        super().__init__(state_manager)

        # UI state
        self.selected_game: Optional[GameEnum] = None
        self.game_buttons: dict[GameEnum, GameButton] = {}

        # Folder widgets indexed by folder key (not game)
        # This supports folder sharing (e.g., EET uses "sod" and "bg2ee" keys)
        self.folder_widgets: dict[str, GameFolderSelector] = {}

        # UI components
        self.right_panel: Optional[QWidget] = None
        self.folders_content: Optional[QWidget] = None
        self.folders_layout: Optional[QVBoxLayout] = None
        self.download_folder: Optional[FolderSelector] = None
        self.backup_folder: Optional[FolderSelector] = None
        self.languages_order: Optional[SortableLanguages] = None

        # Create UI
        self._create_widgets()
        self._initialize_folder_widgets()
        self._load_saved_state()

        logger.info("InstallationTypePage initialized")

    # ========================================
    # UI CREATION
    # ========================================

    def _create_widgets(self) -> None:
        """Create page UI layout."""
        layout = QHBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)

        # Left: Game selection
        left_panel = self._create_left_panel()
        layout.addWidget(left_panel)

        # Right: Folder configuration
        self.right_panel = self._create_right_panel()
        layout.addWidget(self.right_panel, 1)

    def _create_left_panel(self) -> QWidget:
        """
        Create left panel with game selection buttons.

        Returns:
            Panel widget with game buttons in grid
        """
        panel = QFrame()
        panel.setFrameShape(QFrame.Shape.StyledPanel)
        panel.setFixedWidth(self.LEFT_PANEL_WIDTH)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)

        # Title
        self.left_title = self._create_section_title()
        layout.addWidget(self.left_title)

        # Scrollable game grid
        scroll = self._create_game_grid_scroll()
        layout.addWidget(scroll)

        return panel

    def _create_game_grid_scroll(self) -> QScrollArea:
        """
        Create scrollable grid of game buttons.

        Returns:
            Scroll area with game buttons
        """
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        scroll_content = QWidget()
        grid_layout = QGridLayout(scroll_content)
        grid_layout.setSpacing(10)
        grid_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        grid_layout.setContentsMargins(0, 0, 0, 0)

        # Create button for each game in 2-column grid
        row, col = 0, 0
        for game in GameEnum:
            button = self._create_game_button(game)
            grid_layout.addWidget(button, row, col)

            col += 1
            if col >= self.GRID_COLUMNS:
                col = 0
                row += 1

        scroll.setWidget(scroll_content)
        return scroll

    def _create_game_button(self, game: GameEnum) -> GameButton:
        """
        Create button for a game.

        Args:
            game: Game enum

        Returns:
            Configured game button
        """
        icon_path = ICONS_DIR / f"{game.code}.png"
        button = GameButton(
            game,
            icon_path if icon_path.exists() else None,
            parent=self
        )
        button.clicked.connect(self._on_game_selected)
        self.game_buttons[game] = button

        return button

    def _create_right_panel(self) -> QWidget:
        """
        Create right panel for folder configuration.

        Returns:
            Panel widget with folder selectors
        """
        panel = QFrame()
        panel.setFrameShape(QFrame.Shape.StyledPanel)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(15)

        # Title
        self.right_title = self._create_section_title()
        layout.addWidget(self.right_title)

        # Scrollable game folders
        scroll = self._create_game_folders_scroll()
        layout.addWidget(scroll)

        # Download folder
        self.download_folder = FolderSelector(
            "page.type.download_folder",
            "page.type.select_download_folder_title",
            WritableFolderValidator()
        )
        self.download_folder.validation_changed.connect(
            self._on_folder_validation_changed
        )
        layout.addWidget(self.download_folder)

        # Backup folder
        self.backup_folder = FolderSelector(
            "page.type.backup_folder",
            "page.type.select_backup_folder_title",
            WritableFolderValidator()
        )
        self.backup_folder.validation_changed.connect(
            self._on_folder_validation_changed
        )
        layout.addWidget(self.backup_folder)

        # Separator
        separator = self._create_separator()
        layout.addWidget(separator)

        # Languages order section
        self.languages_order_title = self._create_section_title()
        layout.addWidget(self.languages_order_title)

        self.languages_order = SortableLanguages()
        layout.addWidget(self.languages_order)

        return panel

    def _create_game_folders_scroll(self) -> QScrollArea:
        """
        Create scrollable area for game folder selectors.

        Returns:
            Scroll area
        """
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        self.folders_content = QWidget()
        self.folders_layout = QVBoxLayout(self.folders_content)
        self.folders_layout.setSpacing(15)
        self.folders_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        scroll.setWidget(self.folders_content)
        return scroll

    # ========================================
    # GAME FOLDER INITIALIZATION
    # ========================================

    def _initialize_folder_widgets(self) -> None:
        """
        Initialize folder selector widgets efficiently.

        Creates ONE widget per unique folder key, supporting folder sharing.
        For example, EET references "sod" and "bg2ee", so those widgets are
        created/reused rather than creating EET-specific widgets.
        """
        # Collect all unique folder keys across all games
        unique_folder_keys = set()

        for game in GameEnum:
            folder_keys = game.get_unique_folder_keys()
            unique_folder_keys.update(folder_keys)

        # Create one widget per unique folder key
        for folder_key in unique_folder_keys:
            # Get the game this folder key represents
            ref_game = GameEnum.from_code(folder_key)
            if not ref_game:
                logger.error(f"Invalid folder key: {folder_key}")
                continue

            # Get validation rules for this game's first sequence
            # (assuming shared folders use consistent validation)
            validation_sequence = ref_game.get_validation_sequence(0)
            if not validation_sequence:
                logger.error(f"No validation sequence for {folder_key}")
                continue

            # Create selector
            selector = GameFolderSelector(
                "page.type.game_folder",
                "page.type.select_game_folder_title",
                ref_game,
                GameFolderValidator(validation_sequence)
            )
            selector.validation_changed.connect(self._on_folder_validation_changed)

            # Add to layout but hide initially
            self.folders_layout.addWidget(selector)
            selector.hide()

            # Store by folder key
            self.folder_widgets[folder_key] = selector

            logger.debug(f"Created folder widget for key '{folder_key}' ({ref_game.display_name})")

        logger.info(f"Initialized {len(self.folder_widgets)} unique folder widgets")

    # ========================================
    # EVENT HANDLERS
    # ========================================

    def _on_game_selected(self, game: GameEnum) -> None:
        """
        Handle game selection.

        Args:
            game: Selected game
        """
        # Don't do anything if clicking on already selected game
        if self.selected_game == game:
            return

        # Update button states
        for g, button in self.game_buttons.items():
            button.set_selected(g == game)

        self.selected_game = game
        self._update_visible_folder_selectors()
        self.notify_navigation_changed()

        logger.info(f"Game selected: {game.code}")

    def _update_visible_folder_selectors(self) -> None:
        """
        Show/hide folder selectors based on selected game.

        Uses get_unique_folder_keys() to determine which widgets to show.
        """
        # Hide all selectors
        for selector in self.folder_widgets.values():
            selector.hide()

        # Nothing to show if no game selected
        if not self.selected_game:
            return

        # Show selectors for selected game's folder keys
        folder_keys = self.selected_game.get_unique_folder_keys()

        for folder_key in folder_keys:
            selector = self.folder_widgets.get(folder_key)
            if selector:
                selector.show()
                logger.debug(f"Showing folder widget for '{folder_key}'")
            else:
                logger.warning(f"No widget found for folder key '{folder_key}'")

    def _on_folder_validation_changed(self, is_valid: bool) -> None:
        """
        Handle folder validation state change.

        Args:
            is_valid: Whether validation passed
        """
        self.notify_navigation_changed()
        logger.debug(f"Folder validation changed: {is_valid}")

    # ========================================
    # STATE MANAGEMENT
    # ========================================

    def _load_saved_state(self) -> None:
        """Load saved configuration from state manager."""
        # Load selected game
        saved_game_code = self.state_manager.get_selected_game()
        if saved_game_code:
            try:
                game = GameEnum.from_code_strict(saved_game_code)
                self._on_game_selected(game)
            except ValueError:
                logger.warning(f"Unknown game code in saved state: {saved_game_code}")

        # Load game folder paths
        saved_folders = self.state_manager.get_game_folders()
        for folder_key, path in saved_folders.items():
            selector = self.folder_widgets.get(folder_key)
            if selector and path:
                selector.set_path(path)
                logger.debug(f"Restored path for '{folder_key}': {path}")
            else:
                logger.warning(f"No widget for saved folder key: {folder_key}")

        # Load download folder
        download_path = self.state_manager.get_download_folder()
        if download_path:
            self.download_folder.set_path(download_path)

        # Load backup folder
        backup_path = self.state_manager.get_backup_folder()
        if backup_path:
            self.backup_folder.set_path(backup_path)

        # Load languages order
        languages_order = self.state_manager.get_languages_order()
        if languages_order:
            self.languages_order.set_order(languages_order)

        logger.info("Saved state loaded")

    # ========================================
    # BasePage Implementation
    # ========================================

    def get_page_id(self) -> str:
        """Get page identifier."""
        return "installation_type"

    def get_page_title(self) -> str:
        """Get page title."""
        return tr("page.type.title")

    def retranslate_ui(self) -> None:
        """Update all translatable UI elements."""
        self.left_title.setText(tr("page.type.select_game"))
        self.right_title.setText(tr("page.type.configure_folders"))
        self.languages_order_title.setText(tr("page.type.languages_order"))

        self.backup_folder.retranslate_ui()
        self.download_folder.retranslate_ui()

        # Update all folder selectors
        for selector in self.folder_widgets.values():
            selector.retranslate_ui()

    def get_previous_button_config(self) -> ButtonConfig:
        """Configure previous button (hidden on first page)."""
        return ButtonConfig(visible=False)

    def can_proceed(self) -> bool:
        """
        Check if user can proceed to next page.

        Validates:
        - Game is selected
        - All required game folders are valid
        - Download folder is valid
        - Backup folder is valid

        Returns:
            True if all required validations pass
        """
        # Must have selected a game
        if not self.selected_game:
            logger.debug("Cannot proceed: no game selected")
            return False

        # Check all folder widgets for selected game
        folder_keys = self.selected_game.get_unique_folder_keys()

        for folder_key in folder_keys:
            selector = self.folder_widgets.get(folder_key)
            if not selector:
                logger.error(f"Missing widget for folder key: {folder_key}")
                return False

            if not selector.is_valid():
                logger.debug(f"Game folder validation failed for '{folder_key}'")
                return False

        # Check download folder
        if not self.download_folder.is_valid():
            logger.debug("Download folder validation failed")
            return False

        # Check backup folder
        if not self.backup_folder.is_valid():
            logger.debug("Backup folder validation failed")
            return False

        return True

    def validate(self) -> bool:
        """
        Validate page data before proceeding.

        Returns:
            True if validation passes
        """
        return self.can_proceed()

    def save_state(self) -> None:
        """Save page data to state manager."""
        super().save_state()

        # Save selected game
        if self.selected_game:
            self.state_manager.set_selected_game(self.selected_game.code)
            logger.debug(f"Saved selected game: {self.selected_game.code}")

        # Save all valid folder paths (by folder key)
        game_folders = {}
        for folder_key, selector in self.folder_widgets.items():
            if selector.is_valid():
                path = selector.get_path()
                if path:
                    game_folders[folder_key] = path

        if game_folders:
            self.state_manager.set_game_folders(game_folders)
            logger.debug(f"Saved game folders: {game_folders}")

        # Save download folder
        if self.download_folder.is_valid():
            self.state_manager.set_download_folder(self.download_folder.get_path())
            logger.debug(f"Saved download folder: {self.download_folder.get_path()}")

        # Save backup folder
        if self.backup_folder.is_valid():
            self.state_manager.set_backup_folder(self.backup_folder.get_path())
            logger.debug(f"Saved backup folder: {self.backup_folder.get_path()}")

        # Save languages order
        languages_order = self.languages_order.get_order()
        self.state_manager.set_languages_order(languages_order)
        logger.debug(f"Saved languages order: {languages_order}")

        logger.info("Installation configuration saved")
