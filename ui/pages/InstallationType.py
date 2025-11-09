"""Installation type selection page with game-based validation."""

import logging
from pathlib import Path
from typing import Dict, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.StateManager import StateManager
from core.TranslationManager import tr
from core.enums.GameEnum import GameEnum
from core.validators.FolderValidator import GameFolderValidator, WritableFolderValidator
from ui.pages.BasePage import BasePage, ButtonConfig
from ui.widgets.FolderSelector import FolderSelector, GameFolderSelector
from ui.widgets.GameButton import GameButton

logger = logging.getLogger(__name__)


class InstallationTypePage(BasePage):
    """Page for selecting game type and configuring installation folders.

    Allows user to:
    - Select game type from available options
    - Configure game folder paths
    - Set download and backup folders
    """

    # Layout constants
    LEFT_PANEL_WIDTH = 400
    GRID_COLUMNS = 2

    # Styling
    PANEL_BG_COLOR = "#1e1e1e"
    SEPARATOR_COLOR = "#404040"

    def __init__(self, state_manager: StateManager) -> None:
        """Initialize installation type page.

        Args:
            state_manager: Application state manager
        """
        super().__init__(state_manager)

        self.icons_dir = Path("resources/icons")

        # UI state
        self.selected_game: Optional[GameEnum] = None
        self.game_buttons: Dict[GameEnum, GameButton] = {}
        self.game_folder_widgets: Dict[GameEnum, FolderSelector] = {}

        # UI components
        self.right_panel: Optional[QWidget] = None
        self.folders_content: Optional[QWidget] = None
        self.folders_layout: Optional[QVBoxLayout] = None
        self.download_folder: Optional[FolderSelector] = None
        self.backup_folder: Optional[FolderSelector] = None

        # Create UI
        self._create_widgets()
        self._initialize_game_selectors()
        self._load_saved_state()
        self.retranslate_ui()

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
        """Create left panel with game selection buttons.

        Returns:
            Panel widget with game buttons in grid
        """
        panel = QFrame()
        panel.setFrameShape(QFrame.Shape.StyledPanel)
        panel.setStyleSheet(
            f"QFrame {{ background-color: {self.PANEL_BG_COLOR}; "
            "border-radius: 8px; }}"
        )
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
        """Create scrollable grid of game buttons.

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
        """Create button for a game.

        Args:
            game: Game enum

        Returns:
            Configured game button
        """
        icon_path = self.icons_dir / f"{game.code}.png"
        button = GameButton(
            game,
            icon_path if icon_path.exists() else None
        )
        button.clicked.connect(self._on_game_selected)
        self.game_buttons[game] = button

        return button

    def _create_right_panel(self) -> QWidget:
        """Create right panel for folder configuration.

        Returns:
            Panel widget with folder selectors
        """
        panel = QFrame()
        panel.setFrameShape(QFrame.Shape.StyledPanel)
        panel.setStyleSheet(
            f"QFrame {{ background-color: {self.PANEL_BG_COLOR}; "
            "border-radius: 8px; }}"
        )

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(15)

        # Title
        self.right_title = self._create_section_title()
        layout.addWidget(self.right_title)

        # Scrollable game folders
        scroll = self._create_game_folders_scroll()
        layout.addWidget(scroll)

        # Separator
        separator = self._create_separator()
        layout.addWidget(separator)

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

        return panel

    def _create_game_folders_scroll(self) -> QScrollArea:
        """Create scrollable area for game folder selectors.

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

    @staticmethod
    def _create_section_title() -> QLabel:
        """Create styled section title label.

        Returns:
            Configured title label
        """
        title = QLabel()
        font = title.font()
        font.setPointSize(14)
        font.setBold(True)
        title.setFont(font)
        return title

    @staticmethod
    def _create_separator() -> QFrame:
        """Create horizontal separator line.

        Returns:
            Separator frame
        """
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setStyleSheet(f"background-color: {InstallationTypePage.SEPARATOR_COLOR};")
        return separator

    # ========================================
    # GAME FOLDER INITIALIZATION
    # ========================================

    def _initialize_game_selectors(self) -> None:
        """Initialize folder selectors for all games.

        Creates selectors upfront and reuses them across games that
        share the same folder requirements.
        """
        for game in GameEnum:
            sequences = game.validation_rules.get("sequences", [])

            for sequence_rules in sequences:
                # Determine which game this sequence references
                ref_code = sequence_rules.get("game_folder", game.code)
                ref_game = GameEnum.from_code(ref_code)

                # Skip if selector already exists for this reference game
                if ref_game in self.game_folder_widgets:
                    logger.debug(
                        f"Reusing selector for {ref_game.code} "
                        f"(requested by {game.code})"
                    )
                    continue

                # Create new selector
                selector = self._create_game_folder_selector(
                    game, ref_game, sequence_rules
                )
                self.game_folder_widgets[ref_game] = selector

        logger.info(f"Initialized {len(self.game_folder_widgets)} game folder selectors")

    def _create_game_folder_selector(
            self,
            game: GameEnum,
            ref_game: GameEnum,
            validation_rules: Dict
    ) -> FolderSelector:
        """Create folder selector for a game.

        Args:
            game: Game requesting the selector
            ref_game: Game whose folder is being validated
            validation_rules: Validation rules for the folder

        Returns:
            Configured folder selector
        """
        # Determine label based on sequence count
        if game.sequence_count == 1:
            game_name = game.display_name
        else:
            game_name = ref_game.display_name

        selector = GameFolderSelector(
            "page.type.game_folder",
            "page.type.select_game_folder_title",
            game,
            GameFolderValidator(validation_rules)
        )
        selector.validation_changed.connect(self._on_folder_validation_changed)

        # Add to layout but hide initially
        self.folders_layout.addWidget(selector)
        selector.hide()

        logger.debug(f"Created selector for {ref_game.code} (label: {game_name})")
        return selector

    # ========================================
    # EVENT HANDLERS
    # ========================================

    def _on_game_selected(self, game: GameEnum) -> None:
        """Handle game selection.

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
        self._update_folder_selectors()
        self.notify_navigation_changed()

        logger.info(f"Game selected: {game.code}")

    def _update_folder_selectors(self) -> None:
        """Show/hide folder selectors based on selected game."""
        # Hide all selectors
        for selector in self.game_folder_widgets.values():
            selector.hide()

        # Nothing to show if no game selected
        if not self.selected_game:
            return

        # Show selectors for selected game
        sequences = self.selected_game.validation_rules.get("sequences", [])
        for sequence_rules in sequences:
            ref_code = sequence_rules.get("game_folder", self.selected_game.code)
            ref_game = GameEnum.from_code(ref_code)

            selector = self.game_folder_widgets.get(ref_game)
            if selector:
                selector.show()
                logger.debug(f"Showing selector for {ref_game.code}")

    def _on_folder_validation_changed(self, is_valid: bool) -> None:
        """Handle folder validation state change.

        Args:
            is_valid: Whether validation passed
        """
        self.notify_navigation_changed()

    # ========================================
    # STATE MANAGEMENT
    # ========================================

    def _load_saved_state(self) -> None:
        """Load saved configuration from state manager."""
        # Load selected game
        saved_game_code = self.state_manager.get_selected_game()
        if saved_game_code:
            try:
                game = GameEnum.from_code(saved_game_code)
                self._on_game_selected(game)
            except ValueError:
                logger.warning(f"Unknown game code in saved state: {saved_game_code}")

        # Load game folder paths
        saved_folders = self.state_manager.get_game_folders()
        for game_code, path in saved_folders.items():
            try:
                game = GameEnum.from_code(game_code)
                selector = self.game_folder_widgets.get(game)
                if selector and path:
                    selector.set_path(path)
                    logger.debug(f"Restored path for {game_code}: {path}")
            except ValueError:
                logger.warning(f"Unknown game code in saved folders: {game_code}")

        # Load download folder
        download_path = self.state_manager.get_download_folder()
        if download_path:
            self.download_folder.set_path(download_path)

        # Load backup folder
        backup_path = self.state_manager.get_backup_folder()
        if backup_path:
            self.backup_folder.set_path(backup_path)

        logger.info("Saved state loaded")

    # ========================================
    # BASEPAGE IMPLEMENTATION
    # ========================================

    def get_page_id(self) -> str:
        """Get page identifier."""
        return "installation_type"

    def get_page_title(self) -> str:
        """Get page title."""
        return tr("page.type.title")

    def retranslate_ui(self) -> None:
        self.left_title.setText(tr("page.type.select_game"))
        self.right_title.setText(tr("page.type.configure_folders"))

        self.backup_folder.retranslate_ui()
        self.download_folder.retranslate_ui()

        for game, selector in self.game_folder_widgets.items():
            selector.retranslate_ui()

    def get_previous_button_config(self) -> ButtonConfig:
        """Configure previous button (hidden on first page)."""
        return ButtonConfig(visible=False)

    def get_next_button_config(self) -> ButtonConfig:
        """Configure next button."""
        return ButtonConfig(
            visible=True,
            enabled=self.can_proceed(),
            text=tr("button.next")
        )

    def can_proceed(self) -> bool:
        """Check if user can proceed to next page.

        Returns:
            True if all required validations pass
        """
        # Must have selected a game
        if not self.selected_game:
            return False

        # Check game folder(s) for selected game
        sequences = self.selected_game.validation_rules.get("sequences", [])
        for sequence_rules in sequences:
            ref_code = sequence_rules.get("game_folder", self.selected_game.code)
            ref_game = GameEnum.from_code(ref_code)

            selector = self.game_folder_widgets.get(ref_game)
            if selector and not selector.is_valid():
                logger.debug(f"Game folder validation failed for {ref_game.code}")
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
        """Validate page data before proceeding.

        Returns:
            True if validation passes
        """
        return self.can_proceed()

    def save_data(self) -> None:
        """Save page data to state manager."""
        # Save selected game
        if self.selected_game:
            self.state_manager.set_selected_game(self.selected_game.code)
            logger.debug(f"Saved selected game: {self.selected_game.code}")

        # Save all valid game folder paths
        game_folders = {}
        for game, selector in self.game_folder_widgets.items():
            if selector.is_valid():
                path = selector.get_path()
                if path:
                    game_folders[game.code] = path

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

        logger.info("Installation configuration saved")
