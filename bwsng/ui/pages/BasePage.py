"""Base page class for navigation system with configurable buttons."""

import logging
from abc import ABCMeta, abstractmethod
from dataclasses import dataclass
from typing import List, Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget

from bwsng.core.StateManager import StateManager
from bwsng.core.TranslationManager import tr

logger = logging.getLogger(__name__)


@dataclass
class ButtonConfig:
    """Configuration for a navigation button.

    Attributes:
        visible: Whether the button should be displayed
        enabled: Whether the button is clickable
        text: Button label text (translation key or literal)
        icon: Optional icon path or name
    """
    visible: bool = True
    enabled: bool = True
    text: str = ""
    icon: Optional[str] = None


class QWidgetABCMeta(type(QWidget), ABCMeta):
    """Combined metaclass for QWidget and ABC."""
    pass


class BasePage(QWidget, metaclass=QWidgetABCMeta):
    """Base class for all application pages with navigation support.

    This abstract class defines the interface that all pages must implement,
    including metadata, navigation configuration, and lifecycle methods.

    Signals:
        navigation_changed: Emitted when navigation state changes
        data_changed: Emitted when page data is modified
    """

    # Signals
    navigation_changed = Signal()
    data_changed = Signal()

    def __init__(self, state_manager: StateManager) -> None:
        """Initialize the base page.

        Args:
            state_manager: Application state manager instance
        """
        super().__init__()
        self.state_manager = state_manager
        self.widgets_created = False

        logger.debug(f"Page initialized: {self.__class__.__name__}")

    # ========================================
    # PAGE METADATA (REQUIRED)
    # ========================================

    @abstractmethod
    def get_page_title(self) -> str:
        """Get the page title for display in navigation.

        Returns:
            Translated page title
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement get_page_title()"
        )

    @abstractmethod
    def get_page_id(self) -> str:
        """Get unique page identifier for navigation routing.

        Returns:
            Unique page ID (e.g., 'installation_type', 'mod_selection')
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement get_page_id()"
        )

    def get_page_description(self) -> str:
        """Get optional page description for tooltips or help text.

        Returns:
            Translated description or empty string
        """
        return ""

    def get_page_icon(self) -> Optional[str]:
        """Get optional icon for the page (for tabs/sidebar navigation).

        Returns:
            Icon path/name or None
        """
        return None

    # ========================================
    # NAVIGATION BUTTON CONFIGURATION
    # ========================================

    def get_previous_button_config(self) -> ButtonConfig:
        """Configure the 'Previous' navigation button.

        Allows each page to customize:
        - Button visibility
        - Button enabled state
        - Button text

        Returns:
            ButtonConfig with previous button settings
        """
        return ButtonConfig(
            visible=False,
            enabled=True,
            text=tr('button.previous')
        )

    def get_next_button_config(self) -> ButtonConfig:
        """Configure the 'Next' navigation button.

        Allows customization of the next button, including changing
        text (e.g., 'Install' on final page).

        Returns:
            ButtonConfig with next button settings
        """
        return ButtonConfig(
            visible=True,
            enabled=self.can_proceed(),
            text=tr('button.next')
        )

    def get_additional_buttons(self) -> List[ButtonConfig]:
        """Get additional page-specific action buttons.

        Examples:
        - Mod selection page: "Import .tp2", "Clear all"
        - Order page: "Restore default", "Import weidu.log"

        Returns:
            List of additional button configurations
        """
        return []

    # ========================================
    # CONDITIONAL NAVIGATION
    # ========================================

    def get_next_page_id(self) -> Optional[str]:
        """Get the ID of the next page for conditional navigation.

        Enables dynamic navigation flow based on user choices:
        - Skip BG2 config if BG1-only installation
        - Skip download page if all archives present
        - Jump to summary if no mods selected

        Returns:
            Next page ID for custom routing, or None for default sequence
        """
        return None

    def get_previous_page_id(self) -> Optional[str]:
        """Get the ID of the previous page for conditional navigation.

        Less commonly needed than get_next_page_id(), but useful for
        non-linear navigation flows.

        Returns:
            Previous page ID for custom routing, or None for default sequence
        """
        return None

    def should_skip_page(self) -> bool:
        """Determine if this page should be skipped in navigation.

        Useful for context-dependent page visibility:
        - Skip download page if all archives present
        - Skip BG2 config for BG1-only installation
        - Skip component selection if mod has no components

        Returns:
            True if page should be skipped, False otherwise
        """
        return False

    # ========================================
    # PAGE LIFECYCLE
    # ========================================

    @abstractmethod
    def can_proceed(self) -> bool:
        """Check if user can navigate away from this page.

        Typically used to:
        - Validate required fields are filled
        - Ensure valid selections are made
        - Check prerequisites are met

        Returns:
            True if navigation to next page is allowed
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement can_proceed()"
        )

    def validate(self) -> bool:
        """Validate page data before proceeding.

        Called before navigation to ensure data integrity.
        Can display error messages to user if validation fails.

        Returns:
            True if validation passed, False otherwise
        """
        return True

    def save_data(self) -> None:
        """Save page data to state manager.

        Called when navigating away from the page to persist user choices.
        Should update state_manager with current page data.
        """
        pass

    def on_page_shown(self) -> None:
        """Called when page becomes visible.

        Use for:
        - Loading data from state_manager
        - Starting animations or timers
        - Refreshing dynamic content
        - Setting focus to appropriate widget
        """
        logger.debug(f"Page shown: {self.get_page_id()}")

    def on_page_hidden(self) -> None:
        """Called when page is about to be hidden.

        Use for:
        - Saving data
        - Stopping animations or timers
        - Cleanup operations
        """
        logger.debug(f"Page hidden: {self.get_page_id()}")

    def update_ui_language(self, code: str) -> None:
        """Called when application language changes.

        Override to update translatable UI elements.

        Args:
            code: New language code (e.g., 'en_US', 'fr_FR')
        """
        pass

    # ========================================
    # UTILITY METHODS
    # ========================================

    def notify_navigation_changed(self) -> None:
        """Emit signal to notify navigation state has changed.

        Call this when page state changes affect navigation
        (e.g., required field filled, selection made).
        """
        self.navigation_changed.emit()

    def notify_data_changed(self) -> None:
        """Emit signal to notify page data has changed.

        Call this when user modifies data that should trigger
        auto-save or update other UI elements.
        """
        self.data_changed.emit()

    def __repr__(self) -> str:
        """String representation for debugging."""
        return f"<{self.__class__.__name__} id={self.get_page_id()!r}>"