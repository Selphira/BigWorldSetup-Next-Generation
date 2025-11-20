"""
ModSelection - Page for selecting mods and components with filtering capabilities.

This module provides the main selection interface with category navigation,
search functionality, and hierarchical component selection.
"""
import logging

from PySide6.QtCore import QEvent, QModelIndex, QTimer
from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QScrollArea,
    QLineEdit,
    QVBoxLayout,
    QWidget,
    QStyledItemDelegate,
    QStyle,
    QToolTip,
    QStyleOptionViewItem
)

from constants import *
from core.StateManager import StateManager
from core.TranslationManager import tr
from core.enums.CategoryEnum import CategoryEnum
from ui.pages.BasePage import BasePage, ButtonConfig
from ui.widgets.CategoryButton import CategoryButton
from ui.widgets.ComponentSelector import ComponentSelector

logger = logging.getLogger(__name__)


# ============================================================================
# Highlight Delegate
# ============================================================================

class HighlightDelegate(QStyledItemDelegate):
    """Delegate that highlights search text in tree view items.

    Features:
    - Highlights matching text with yellow background
    - Shows tooltip for truncated text
    """

    # Rendering constants
    TEXT_HORIZONTAL_OFFSET = 27  # Space for checkbox/icon

    def __init__(self, parent=None):
        super().__init__(parent)
        self._search_text = ""

    def set_search_text(self, text: str) -> None:
        """Set text to highlight."""
        self._search_text = text.lower().strip()

    def get_search_text(self) -> str:
        """Get current search text."""
        return self._search_text

    # ========================================
    # Tooltip Support
    # ========================================

    def helpEvent(
            self,
            event: QEvent,
            view: QWidget,
            option: QStyleOptionViewItem,
            index: QModelIndex
    ) -> bool:
        """Show tooltip if text is truncated."""
        if event.type() != QEvent.Type.ToolTip or not index.isValid():
            return False

        text = index.data()
        if not text:
            return False

        # Check if text is truncated
        if option.fontMetrics.horizontalAdvance(text) > option.rect.width():
            QToolTip.showText(event.globalPos(), text, view)
            return True

        return False

    # ========================================
    # Custom Painting
    # ========================================

    def paint(
            self,
            painter,
            option: QStyleOptionViewItem,
            index: QModelIndex
    ) -> None:
        """Paint item with search text highlighting."""
        # Remove focus outline
        option.state &= ~QStyle.StateFlag.State_HasFocus

        # No search active - use default rendering
        if not self._search_text:
            super().paint(painter, option, index)
            return

        text = index.data(Qt.ItemDataRole.DisplayRole)
        if not text:
            super().paint(painter, option, index)
            return

        # Find search text position
        search_pos = text.lower().find(self._search_text)

        # Search text not found - use default rendering
        if search_pos == -1:
            super().paint(painter, option, index)
            return

        # Render with highlighting
        self._paint_with_highlight(painter, option, index, text, search_pos)

    def _paint_with_highlight(
            self,
            painter,
            option: QStyleOptionViewItem,
            index: QModelIndex,
            text: str,
            search_pos: int
    ) -> None:
        """Paint item with highlighted search text."""
        style = option.widget.style() if option.widget else QStyle()

        # Draw background, checkbox, icon (but not text)
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        opt.features = (
                opt.features & ~QStyleOptionViewItem.ViewItemFeature.HasDisplay
        )
        style.drawControl(
            QStyle.ControlElement.CE_ItemViewItem,
            opt,
            painter,
            option.widget
        )

        # Get text area rectangle
        text_rect = style.subElementRect(
            QStyle.SubElement.SE_ItemViewItemText,
            option,
            option.widget
        )

        # Create HTML with highlighting
        html = self._create_highlighted_html(
            text,
            search_pos,
            len(self._search_text),
            option
        )

        # Render HTML text
        self._render_html_text(painter, text_rect, html, option)

    def _create_highlighted_html(
            self,
            text: str,
            match_start: int,
            match_length: int,
            option: QStyleOptionViewItem
    ) -> str:
        """Create HTML with highlighted match."""
        before = text[:match_start]
        match = text[match_start:match_start + match_length]
        after = text[match_start + match_length:]

        # Get text color based on selection state
        if option.state & QStyle.StateFlag.State_Selected:
            text_color = option.palette.highlightedText().color().name()
        else:
            text_color = option.palette.text().color().name()

        return (
            f'<span style="color: {text_color};">{before}</span>'
            f'<span style="background-color: {COLOR_BACKGROUND_HIGHLIGHT}; '
            f'color: {COLOR_TEXT_HIGHLIGHT}; font-weight: bold;">{match}</span>'
            f'<span style="color: {text_color};">{after}</span>'
        )

    def _render_html_text(
            self,
            painter,
            text_rect,
            html: str,
            option: QStyleOptionViewItem
    ) -> None:
        """Render HTML text in the text rectangle."""
        doc = QTextDocument()
        doc.setDefaultFont(option.font)
        doc.setHtml(html)
        doc.setTextWidth(text_rect.width())
        doc.setDocumentMargin(0)

        painter.save()

        # Position in text area
        painter.translate(text_rect.topLeft())

        # Vertical centering
        font_metrics = option.fontMetrics
        available_height = text_rect.height()
        font_height = font_metrics.height()
        vertical_offset = (
                (available_height - font_height) / 2 + font_metrics.ascent() - font_metrics.ascent()
        )

        painter.translate(self.TEXT_HORIZONTAL_OFFSET, vertical_offset)

        # Clip to prevent overflow
        painter.setClipRect(0, 0, text_rect.width(), text_rect.height())

        doc.drawContents(painter)

        painter.restore()


# ============================================================================
# Mod Selection Page
# ============================================================================

class ModSelectionPage(BasePage):
    """Main page for mod and component selection.

    Features:
    - Category-based filtering
    - Text search with highlighting
    - Hierarchical component selection
    - Real-time statistics
    """

    # Layout constants
    LEFT_PANEL_WIDTH = 300

    def __init__(self, state_manager: StateManager) -> None:
        super().__init__(state_manager)

        self._mod_manager = self.state_manager.get_mod_manager()
        self._category_buttons: dict[CategoryEnum, CategoryButton] = {}
        self._current_category = CategoryEnum.ALL

        # Search debouncing
        self._search_timer = QTimer()
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._apply_search_filter)

        # Build UI
        self._create_widgets()
        self._update_statistics()
        self.retranslate_ui()

        logger.info("ModSelectionPage initialized")

    # ========================================
    # Widget Creation
    # ========================================

    def _create_widgets(self) -> None:
        """Create page UI layout."""
        layout = QHBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)

        # Left: Category selection
        left_panel = self._create_left_panel()
        layout.addWidget(left_panel)

        # Center: Component selection
        center_panel = self._create_center_panel()
        layout.addWidget(center_panel)

    def _create_left_panel(self) -> QWidget:
        """Create left panel with category buttons."""
        panel = QFrame()
        panel.setFrameShape(QFrame.Shape.StyledPanel)
        panel.setFixedWidth(self.LEFT_PANEL_WIDTH)

        layout = QVBoxLayout(panel)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.setContentsMargins(
            MARGIN_STANDARD,
            MARGIN_STANDARD,
            MARGIN_SMALL,
            MARGIN_STANDARD
        )
        layout.setSpacing(SPACING_MEDIUM)

        # Title
        self._left_title = self._create_section_title()
        layout.addWidget(self._left_title)

        # Scrollable category list
        scroll = self._create_category_scroll()
        layout.addWidget(scroll)

        return panel

    def _create_category_scroll(self) -> QScrollArea:
        """Create scrollable category list."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        scroll_content = QWidget()
        layout = QVBoxLayout(scroll_content)
        layout.setSpacing(0)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.setContentsMargins(0, 0, 0, 0)

        all_button = self._create_category_button(CategoryEnum.ALL, selected=True)
        layout.addWidget(all_button)

        # Other categories
        for category in CategoryEnum.list_without_all():
            button = self._create_category_button(category)
            layout.addWidget(button)

        scroll.setWidget(scroll_content)
        return scroll

    def _create_category_button(
            self,
            category: CategoryEnum,
            selected: bool = False
    ) -> CategoryButton:
        """Create and register a category button."""
        button = CategoryButton(category, 0, selected)
        button.clicked.connect(self._on_category_clicked)
        self._category_buttons[category] = button
        return button

    def _create_center_panel(self) -> QWidget:
        """Create center panel with filters and selector."""
        panel = QFrame()
        panel.setFrameShape(QFrame.Shape.StyledPanel)
        panel.setStyleSheet(
            f"QFrame {{ "
            f"background-color: {COLOR_BACKGROUND_PRIMARY}; "
            f"border-radius: 8px; "
            f"}}"
        )

        layout = QVBoxLayout(panel)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.setContentsMargins(
            MARGIN_SMALL,
            MARGIN_STANDARD,
            MARGIN_STANDARD,
            MARGIN_STANDARD
        )
        layout.setSpacing(SPACING_MEDIUM)

        # Search filter
        filters_widget = self._create_filters_section()
        layout.addWidget(filters_widget)

        # Component selector with highlight delegate
        self._component_selector = ComponentSelector(self._mod_manager, self)
        self._highlight_delegate = HighlightDelegate(self._component_selector)
        self._component_selector.setItemDelegate(self._highlight_delegate)

        # Connect selection changes to update navigation buttons
        self._component_selector._model.itemChanged.connect(
            self._on_selection_changed
        )

        layout.addWidget(self._component_selector)

        return panel

    def _create_filters_section(self) -> QWidget:
        """Create filters section with search bar."""
        filters_widget = QWidget()
        filters_layout = QVBoxLayout(filters_widget)
        filters_layout.setContentsMargins(0, 0, 0, 0)
        filters_layout.setSpacing(SPACING_SMALL)

        # Search bar
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText(
            tr('page.selection.search_placeholder')
        )
        self._search_input.setClearButtonEnabled(True)
        self._search_input.setMaxLength(MAX_SEARCH_LENGTH)
        self._search_input.textChanged.connect(self._on_search_changed)
        filters_layout.addWidget(self._search_input)

        return filters_widget

    # ========================================
    # Event Handlers
    # ========================================

    def _on_category_clicked(self, category: CategoryEnum) -> None:
        """Handle category button click."""
        # Update button states
        for cat, button in self._category_buttons.items():
            button.set_selected(cat == category)

        self._current_category = category
        self._apply_all_filters()

        logger.debug(f"Category selected: {category.value}")

    def _on_search_changed(self, text: str) -> None:
        """Handle search text change with debouncing."""
        self._search_timer.stop()

        # Only apply filter if text is empty or meets minimum length
        if len(text) == 0 or len(text) >= MIN_SEARCH_LENGTH:
            self._search_timer.start(SEARCH_DEBOUNCE_DELAY)

    def _apply_search_filter(self) -> None:
        """Apply search filter after debounce delay."""
        text = self._search_input.text()

        # Update highlight delegate
        self._highlight_delegate.set_search_text(text)

        # Apply all filters
        self._apply_all_filters()

        # Refresh view to show highlights
        self._component_selector.viewport().update()

        logger.debug(f"Search filter applied: '{text}'")

    def _on_selection_changed(self) -> None:
        """Handle component selection change."""
        print(self._component_selector.get_selected_items())
        # self._update_statistics()
        # self.update_navigation_buttons()

    # ========================================
    # Filtering
    # ========================================

    def _apply_all_filters(self) -> None:
        """Apply all active filters to component selector."""
        # Prepare filter parameters
        text = self._search_input.text()
        game = self.state_manager.get_selected_game()
        categories = None

        if self._current_category != CategoryEnum.ALL:
            categories = {self._current_category.value}

        # Apply filters in one operation
        self._component_selector.apply_filters(
            text=text,
            categories=categories,
            game=game
        )

        # Expand/collapse based on search
        if text:
            self._component_selector.expandAll()
        else:
            self._component_selector.collapseAll()

        self._update_statistics()

    def _update_statistics(self) -> None:
        """Update category counters based on current filters."""
        filtered_counts = (
            self._component_selector.get_filtered_mod_count_by_category()
        )

        print(filtered_counts)

        for category in CategoryEnum:
            count = filtered_counts.get(category.value, 0)
            self._category_buttons[category].update_count(count)

    # ========================================
    # BasePage Implementation
    # ========================================

    def get_page_id(self) -> str:
        """Get page identifier."""
        return "mod_selection"

    def get_page_title(self) -> str:
        """Get page title."""
        return tr("page.selection.title")

    def get_previous_button_config(self) -> ButtonConfig:
        """Configure previous button."""
        return ButtonConfig(
            visible=True,
            enabled=True,
            text=tr("button.previous")
        )

    def get_next_button_config(self) -> ButtonConfig:
        """Configure next button."""
        return ButtonConfig(
            visible=True,
            enabled=self.can_proceed(),
            text=tr("button.next")
        )

    def can_proceed(self) -> bool:
        """Check if can proceed to next page."""
        return self._component_selector.has_selection()

    def on_page_shown(self) -> None:
        """Called when page becomes visible."""
        super().on_page_shown()
        self._search_input.setFocus()
        self._apply_all_filters()

    # ========================================
    # Translation Support
    # ========================================

    def retranslate_ui(self) -> None:
        """Update UI text for language change."""
        self._left_title.setText(tr("page.selection.select_category"))
        self._search_input.setPlaceholderText(
            tr('page.selection.search_placeholder')
        )

        # Update category buttons
        for button in self._category_buttons.values():
            button.retranslate_ui()

        # Update component selector
        self._component_selector.retranslate_ui()

        # Reapply filters to update display
        self._apply_all_filters()

    # {'bp-bgt-worldmap': [{'key': '0', 'prompts': {'1': '1', '2': '1'}}, '1', '3', '5'], 'Will_to_Power': ['300', '500']}
