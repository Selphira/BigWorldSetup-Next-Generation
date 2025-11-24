"""
ModDetailsPanel - Widget displaying detailed information about a selected mod.

This module provides a collapsible panel showing mod metadata including
name, description, links, supported languages, authors, and quality indicators.
"""
import logging

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QScrollArea, QFrame, QHBoxLayout, QSizePolicy
)

from constants import *
from core.Mod import Mod
from core.ModManager import ModManager
from core.TranslationManager import tr, get_translator
from ui.layouts.FlowLayout import FlowLayout

logger = logging.getLogger(__name__)


class ModDetailsPanel(QWidget):
    """Panel displaying detailed mod information."""

    # Layout constants
    PANEL_MIN_WIDTH = 340
    PANEL_MAX_WIDTH = 600
    PANEL_PREFERRED_WIDTH = 450

    def __init__(self, mod_manager: ModManager, parent=None):
        super().__init__(parent)
        self._mod_manager: ModManager = mod_manager
        self._current_mod: Mod | None = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Initialize UI components."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(MARGIN_STANDARD, MARGIN_STANDARD, MARGIN_STANDARD, MARGIN_STANDARD)
        layout.setSpacing(SPACING_MEDIUM)

        # Scroll area for content
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setObjectName('mod-detail-panel')

        # Content widget
        self._content_widget = QWidget()
        self._content_layout = QVBoxLayout(self._content_widget)
        self._content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._content_layout.setSpacing(SPACING_MEDIUM)
        self._content_layout.setContentsMargins(0, 0, MARGIN_SMALL, 0)

        # Create sections (initially hidden)
        self._create_header_section()
        self._create_description_section()
        self._create_authors_section()
        self._create_categories_section()
        self._create_games_section()
        self._create_links_section()

        scroll.setWidget(self._content_widget)
        layout.addWidget(scroll)

        # Set size constraints
        self.setMinimumWidth(self.PANEL_MIN_WIDTH)
        self.setMaximumWidth(self.PANEL_MAX_WIDTH)

        # Show placeholder initially
        self._show_placeholder()
        self.retranslate_ui()

    def _create_header_section(self) -> None:
        """Create header with mod name and quality indicator."""
        self._header_frame = QFrame()
        # self._header_frame.setFrameShape(QFrame.Shape.StyledPanel)

        # header_layout = QVBoxLayout(self._header_frame)

        header_layout = QHBoxLayout(self._header_frame)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(SPACING_SMALL)

        # Mod name
        self._name_label = QLabel()
        self._name_label.setWordWrap(True)
        self._name_label.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred
        )
        self._name_label.setStyleSheet(f"""
            QLabel {{
                font-size: 16pt;
                font-weight: bold;
                color: {COLOR_TEXT};
            }}
        """)
        header_layout.addWidget(self._name_label, 1)

        self._version_label = QLabel()
        self._version_label.setStyleSheet(f"""
            QLabel {{
                font-size: 9pt;
                color: {COLOR_TEXT};
            }}
        """)
        header_layout.addStretch()
        header_layout.addWidget(self._version_label)

        header_layout2 = QHBoxLayout(self._header_frame)
        header_layout2.setContentsMargins(0, 0, 0, 0)
        header_layout2.setSpacing(SPACING_SMALL)

        self._languages_widget = QWidget()
        flags_layout = QHBoxLayout(self._languages_widget)
        flags_layout.setContentsMargins(0, 0, 0, 0)
        flags_layout.setSpacing(4)
        flags_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        header_layout2.addWidget(self._languages_widget)

        self._quality_label = QLabel()
        header_layout2.addWidget(self._quality_label)

        self._content_layout.addWidget(self._header_frame)
        self._content_layout.addLayout(header_layout2)

    def _create_description_section(self) -> None:
        """Create description section."""
        # Section title
        self._description_title = QLabel()
        self._description_title.setObjectName('mod-detail-section')
        self._content_layout.addWidget(self._description_title)

        # Description text
        self._description_label = QLabel()
        self._description_label.setWordWrap(True)
        self._description_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._content_layout.addWidget(self._description_label)

    def _create_authors_section(self) -> None:
        """Create description section."""
        self._authors_widget = QWidget()
        layout = QVBoxLayout(self._authors_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING_SMALL)

        # Section title
        self._authors_title = QLabel()
        self._authors_title.setObjectName('mod-detail-section')
        layout.addWidget(self._authors_title)

        # Authors text
        self._authors_label = QLabel()
        self._authors_label.setWordWrap(True)
        layout.addWidget(self._authors_label)
        self._content_layout.addWidget(self._authors_widget)

    def _create_categories_section(self) -> None:
        """Create description section."""
        self._categories_widget = QWidget()
        layout = QVBoxLayout(self._categories_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING_SMALL)

        # Section title
        self._categories_title = QLabel()
        self._categories_title.setObjectName('mod-detail-section')
        layout.addWidget(self._categories_title)

        # Container for badges
        badges_container = QWidget()
        self._categories_badges_layout = FlowLayout(badges_container)
        self._categories_badges_layout.setContentsMargins(8, 0, 0, 0)
        self._categories_badges_layout.setSpacing(6)
        self._categories_badges_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(badges_container)

        self._content_layout.addWidget(self._categories_widget)

    def _create_games_section(self) -> None:
        self._games_widget = QWidget()
        layout = QVBoxLayout(self._games_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING_SMALL)

        # Title
        self._games_widget_title = QLabel()
        self._games_widget_title.setObjectName('mod-detail-section')
        layout.addWidget(self._games_widget_title)

        # Container for badges
        badges_container = QWidget()
        self._games_badges_layout = FlowLayout(badges_container)
        self._games_badges_layout.setContentsMargins(8, 0, 0, 0)
        self._games_badges_layout.setSpacing(6)
        self._games_badges_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(badges_container)

        self._content_layout.addWidget(self._games_widget)

    def _create_links_section(self) -> None:
        """Create links section."""
        self._links_widget = QWidget()
        layout = QVBoxLayout(self._links_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING_SMALL)

        # Title
        self._links_widget_title = QLabel()
        self._links_widget_title.setObjectName('mod-detail-section')
        layout.addWidget(self._links_widget_title)

        # Links container
        self._links_container = QWidget()
        self._links_layout = QVBoxLayout(self._links_container)
        self._links_layout.setContentsMargins(8, 0, 0, 0)
        self._links_layout.setSpacing(SPACING_SMALL)

        layout.addWidget(self._links_container)

        self._content_layout.addWidget(self._links_widget)

    def _create_link_label(self, icon: str, text: str, url: str) -> QLabel:
        """Create a clickable link label."""
        label = QLabel(f'{icon} <a href="{url}" style="color: {COLOR_TEXT};">{text}</a>')
        label.setOpenExternalLinks(False)
        label.linkActivated.connect(lambda u: QDesktopServices.openUrl(QUrl(u)))
        label.setToolTip(url)
        return label

    def _update_quality_indicator(self, safe_value: int | None) -> None:
        """Update quality indicator badge."""

        quality_map = {
            2: (ICON_SUCCESS, tr("widget.mod_details.quality.safe"), COLOR_SUCCESS),
            1: (ICON_WARNING, tr("widget.mod_details.quality.caution"), COLOR_WARNING),
            0: (ICON_ERROR, tr("widget.mod_details.quality.avoid"), COLOR_ERROR),
        }

        data = quality_map.get(safe_value)

        if not data:
            self._quality_label.setVisible(False)
            return

        icon, tooltip, color = data

        self._quality_label.setText(icon)
        self._quality_label.setToolTip(tooltip)
        self._quality_label.setStyleSheet(f"""
            QLabel {{
                color: {color};
                font-weight: bold;
                font-size: 9pt;
            }}
        """)
        self._quality_label.setVisible(True)

    def _update_languages(self, mod) -> None:
        """Update languages section with flag icons."""
        if not mod.languages:
            self._languages_widget.setVisible(False)
            return

        flags_layout = self._languages_widget.layout()
        self._clear_layout(flags_layout)

        # Add flag icons
        for lang_code in sorted(mod.languages.keys()):
            icon_path = FLAGS_DIR / f"{lang_code}.png"

            if icon_path.exists():
                flag_label = QLabel()
                pixmap = QPixmap(str(icon_path))
                # Scale flag to reasonable size (24x16 or similar)
                scaled_pixmap = pixmap.scaled(
                    16, 16,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
                flag_label.setPixmap(scaled_pixmap)
                flag_label.setToolTip(get_translator().get_language_name(lang_code))
                flags_layout.addWidget(flag_label)
            else:
                # Fallback: show language code as text
                lang_label = QLabel(get_translator().get_language_name(lang_code))
                lang_label.setStyleSheet(f"""
                    QLabel {{
                        color: {COLOR_TEXT};
                        font-size: 8pt;
                        padding: 2px 4px;
                        background-color: {COLOR_BACKGROUND_PRIMARY};
                        border-radius: 3px;
                    }}
                """)
                flags_layout.addWidget(lang_label)

        flags_layout.addStretch()

        self._languages_widget.setVisible(True)

    def _update_authors(self, mod) -> None:
        """Update authors section."""
        authors = mod.authors
        if not authors:
            self._authors_widget.setVisible(False)
            return

        text = ", ".join(authors)

        self._authors_label.setText(text)
        self._authors_widget.setVisible(True)

    def _update_categories(self, mod) -> None:
        """Update categories section."""
        if not mod.categories:
            self._categories_widget.setVisible(False)
            return

        self._categories_badges_layout.clear()

        # Create a badge for each category
        for category in mod.categories:
            badge = QLabel(category)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setMinimumWidth(BADGE_MIN_WIDTH)
            badge.setFixedHeight(BADGE_HEIGHT)
            badge.setObjectName("badge")
            self._categories_badges_layout.addWidget(badge)

        self._categories_widget.setVisible(True)

    def _update_games(self, mod) -> None:
        """Update games section with badges."""
        if not mod.games:
            self._games_widget.setVisible(False)
            return

        self._games_badges_layout.clear()

        # Create a badge for each game
        for game in mod.games:
            badge = QLabel(game)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setMinimumWidth(BADGE_MIN_WIDTH)
            badge.setFixedHeight(BADGE_HEIGHT)
            badge.setObjectName("badge")
            self._games_badges_layout.addWidget(badge)

        self._games_widget.setVisible(True)

    def _update_links(self, mod) -> None:
        """Populate the links section dynamically."""
        self._clear_layout(self._links_layout)

        links = {
            mod.homepage: ("🏠", tr("widget.mod_details.link.homepage")),
            mod.readme: ("📄", tr("widget.mod_details.link.readme")),
            mod.download: ("📦", tr("widget.mod_details.link.download")),
        }

        # Filter only existing links
        has_links = False
        for url, (icon, label_text) in links.items():
            if url:
                self._links_layout.addWidget(
                    self._create_link_label(icon, label_text, url)
                )
                has_links = True

        self._links_widget.setVisible(has_links)

    def _show_placeholder(self) -> None:
        """Show placeholder when no mod is selected."""
        self._current_mod = None
        self._content_widget.setVisible(False)

    @staticmethod
    def _clear_layout(layout):
        while layout.count():
            item = layout.takeAt(0)
            if widget := item.widget():
                widget.deleteLater()

    # ========================================
    # PUBLIC API
    # ========================================

    def update_mod(self, mod, force: bool = False) -> None:
        """Update panel with mod information."""
        if mod is None:
            self._show_placeholder()
            return

        # Check if we're showing the same mod
        if self._current_mod == mod and not force:
            return

        self._content_widget.setVisible(True)
        self._current_mod = mod

        # Update header
        self._name_label.setText(mod.name)
        self._update_quality_indicator(mod.safe)

        # Version (if available)
        version = mod.version
        if version:
            self._version_label.setText(version)
            self._version_label.setVisible(True)
        else:
            self._version_label.setVisible(False)

        # Update description
        description = mod.description if mod.description else tr("widget.mod_details.no_description")
        self._description_label.setText(description)

        # Update metadata
        self._update_languages(mod)
        self._update_authors(mod)
        self._update_categories(mod)
        self._update_games(mod)

        # Update links
        self._update_links(mod)

        logger.debug(f"Updated mod details for: {mod.name}")

    def clear(self) -> None:
        """Clear the panel."""
        self._show_placeholder()

    # ========================================
    # Translation Support
    # ========================================

    def retranslate_ui(self) -> None:
        """Update UI text after language change."""
        self._description_title.setText(tr("widget.mod_details.description"))
        self._authors_title.setText(tr("widget.mod_details.authors"))
        self._categories_title.setText(tr("widget.mod_details.categories"))
        self._games_widget_title.setText(tr("widget.mod_details.games"))
        self._links_widget_title.setText(tr("widget.mod_details.links"))

        if self._current_mod:
            mod = self._mod_manager.get_mod_by_id(self._current_mod.id)
            self.update_mod(mod, True)
