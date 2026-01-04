"""
Multi-selection combo box widget with icon-only display.

This widget provides a combo box that allows multiple item selection with visual
feedback through icons.
"""

import logging
from typing import cast, override

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QListView, QSizePolicy, QWidget

from constants import COLOR_BACKGROUND_SECONDARY, ICON_SIZE_MEDIUM

logger = logging.getLogger(__name__)


class MultiSelectComboBox(QComboBox):
    """
    A combo box allowing multi-selection with icon display.

    Features:
    - Popup remains open during selection
    - Checkboxes for item selection
    - Icon preview row in the combo field
    - Enforces minimum selection count (default: 1)

    Signals:
        selection_changed: Emitted when selection changes, provides list of selected keys
    """

    selection_changed = Signal(list)

    def __init__(
        self,
        parent: QWidget | None = None,
        min_selection_count: int = 1,
    ) -> None:
        """
        Initialize the multi-select combo box.

        Args:
            parent: Parent widget
        """
        super().__init__(parent)

        self._icon_size = ICON_SIZE_MEDIUM
        self._icons: dict[str, QIcon] = {}
        self._min_selection_count = min_selection_count
        self._updating_selection = False

        self._setup_combo_box()
        self._setup_model_and_view()
        self._setup_preview_widget()
        self._connect_signals()
        self._update_preview()

    @override
    def model(self) -> QStandardItemModel:  # type: ignore[override]
        return cast(QStandardItemModel, super().model())

    # -------------------------------------------------------------------------
    # Setup methods
    # -------------------------------------------------------------------------

    def _setup_combo_box(self) -> None:
        """Configure the combo box appearance and behavior."""
        self.setEditable(True)
        self.lineEdit().setReadOnly(True)
        self.lineEdit().setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.lineEdit().setFrame(False)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)

        # Hide dropdown arrow
        self.setStyleSheet("""
            QComboBox::drop-down {
                border: none;
                width: 0px;
            }
            QComboBox::down-arrow {
                image: none;
                border: none;
            }
        """)

        # Force zero icon size to prevent display
        self.setIconSize(QSize(0, 0))

    def _setup_model_and_view(self) -> None:
        """Configure the model and view for the combo box."""
        model = QStandardItemModel()
        self.setModel(model)

        view = QListView()
        view.setSelectionRectVisible(False)
        view.setMouseTracking(True)
        self.setView(view)

    def _setup_preview_widget(self) -> None:
        """Create and configure the icon preview widget."""
        self._preview = QWidget(self.lineEdit())
        self._preview_layout = QHBoxLayout(self._preview)
        self._preview_layout.setContentsMargins(2, 0, 2, 0)
        self._preview_layout.setSpacing(4)
        self._preview_layout.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )

        # Make preview clickable to open popup
        self._preview.mousePressEvent = lambda e: self.showPopup()
        self._preview.setStyleSheet(f"background-color: {COLOR_BACKGROUND_SECONDARY};")

    def _connect_signals(self) -> None:
        """Connect internal signals."""
        self.view().pressed.connect(self._on_item_pressed)
        self.model().dataChanged.connect(self._on_data_changed)

        self._update_preview()

    # -------------------------------------------------------------------------
    # Public API - Adding items
    # -------------------------------------------------------------------------

    def add_item(
        self,
        key: str,
        icon_path: str,
        text: str = "",
        selected: bool = False,
    ) -> None:
        """
        Add an item to the combo box.

        Args:
            key: Unique identifier for the item
            icon_path: Path to the icon file
            text: text to display
            selected: Whether the item should be selected by default
        """
        icon = QIcon(icon_path)
        self._icons[key] = icon

        item = QStandardItem()
        item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
        item.setCheckable(True)

        must_be_selected = self._count_selected_items() < self._min_selection_count
        check_state = (
            Qt.CheckState.Checked if (must_be_selected or selected) else Qt.CheckState.Unchecked
        )
        item.setCheckState(check_state)

        item.setData(key, Qt.ItemDataRole.UserRole)
        item.setIcon(icon)
        item.setText(text)

        self.model().appendRow(item)
        self._update_preview()

        logger.debug("Added item: key=%s, selected=%s", key, must_be_selected or selected)

    def set_items(
        self,
        items: dict[str, str],
        selected_keys: list[str] | None = None,
    ) -> None:
        """
        Set all items at once, replacing existing items.

        Args:
            items: Dictionary mapping keys to icon paths
            selected_keys: List of keys to select (if None, selects first item)
        """
        self.model().clear()
        self._icons.clear()

        for key, icon_path in items.items():
            self.add_item(key, icon_path, selected=False)

        # Set selection
        if selected_keys:
            self.set_selected_keys(selected_keys)
        elif self.model().rowCount() > 0:
            # Select first item by default
            self.model().item(0).setCheckState(Qt.CheckState.Checked)
            self._update_preview()

        logger.info("Set %d items with %d selected", len(items), len(selected_keys or []))

    def count_items(self) -> int:
        """Get the count of items."""
        return self.model().rowCount()

    # -------------------------------------------------------------------------
    # Public API - Selection management
    # -------------------------------------------------------------------------

    def selected_keys(self) -> list[str]:
        """Get the list of selected item keys."""
        return [
            item.data(Qt.ItemDataRole.UserRole)
            for item in self._iter_items()
            if item.checkState() == Qt.CheckState.Checked
        ]

    def set_selected_keys(self, keys: list[str]) -> None:
        """
        Set the selected items by their keys.

        Ensures at least one item remains selected. If an empty list is provided,
        the first item will be selected.

        Args:
            keys: List of keys to select (enforces minimum selection)
        """
        if not keys and self.model().rowCount():
            keys = [self.model().item(0).data(Qt.ItemDataRole.UserRole)]

        if not keys:
            return

        self._updating_selection = True
        self.model().blockSignals(True)

        key_set = set(keys)
        for item in self._iter_items():
            item.setCheckState(
                Qt.CheckState.Checked
                if item.data(Qt.ItemDataRole.UserRole) in key_set
                else Qt.CheckState.Unchecked
            )

        self.model().blockSignals(False)
        self._updating_selection = False

        self._update_preview()
        self.selection_changed.emit(self.selected_keys())

    def clear_selection(self) -> None:
        """
        Clear selection, keeping only the first item selected.

        Ensures at least one item remains selected.
        """
        if self.model().rowCount():
            self.set_selected_keys([self.model().item(0).data(Qt.ItemDataRole.UserRole)])

    # -------------------------------------------------------------------------
    # Private - Item interaction
    # -------------------------------------------------------------------------

    def _iter_items(self):
        for row in range(self.model().rowCount()):
            yield self.model().item(row)

    def _on_item_pressed(self, index) -> None:
        """
        Handle item press to toggle checkbox and keep popup open.

        Prevents deselecting the last selected item to enforce minimum selection.

        Args:
            index: Model index of the pressed item
        """
        item = self.model().itemFromIndex(index)
        if not item:
            return

        # Check if trying to deselect the last item
        if (
            item.checkState() == Qt.CheckState.Checked
            and self._count_selected_items() <= self._min_selection_count
        ):
            self.showPopup()
            return

        self.model().blockSignals(True)
        new_state = (
            Qt.CheckState.Unchecked
            if item.checkState() == Qt.CheckState.Checked
            else Qt.CheckState.Checked
        )
        item.setCheckState(new_state)
        self.model().blockSignals(False)

        self._update_preview()
        self.showPopup()
        selected = self.selected_keys()
        self.selection_changed.emit(selected)

        logger.debug("Item toggled: state=%s, selected=%s", new_state, selected)

    def _on_data_changed(self, top_left, bottom_right, roles):
        """
        Appelé quand les données du modèle changent (notamment les checkboxes).
        Empêche de tout désélectionner.
        """
        if self._updating_selection:
            return
        # Vérifier si c'est un changement de CheckState
        if Qt.ItemDataRole.CheckStateRole not in roles:
            return

        selected_count = self._count_selected_items()

        if selected_count < self._min_selection_count:
            for row in range(top_left.row(), bottom_right.row() + 1):
                item = self.model().item(row)
                if item and item.checkState() == Qt.CheckState.Unchecked:
                    # Bloquer temporairement les signaux pour éviter une récursion
                    self.model().blockSignals(True)
                    item.setCheckState(Qt.CheckState.Checked)
                    self.model().blockSignals(False)
                    break

        self._update_preview()
        self.selection_changed.emit(self.selected_keys())

    def _count_selected_items(self) -> int:
        """Count the number of selected items."""
        return sum(item.checkState() == Qt.CheckState.Checked for item in self._iter_items())

    # -------------------------------------------------------------------------
    # Private - Preview display
    # -------------------------------------------------------------------------

    def _update_preview(self) -> None:
        """Update the display of selected item icons."""
        # Clear existing layout
        while self._preview_layout.count():
            child = self._preview_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        selected = self.selected_keys()

        # Hide placeholder and show icons
        self.lineEdit().setPlaceholderText("")

        for key in selected:
            icon = self._icons.get(key)
            if not icon:
                logger.warning("Icon not found for key: %s", key)
                continue

            label = QLabel(self._preview)
            label.setPixmap(icon.pixmap(self._icon_size, self._icon_size))
            label.setFixedSize(self._icon_size, self._icon_size)
            label.setScaledContents(True)
            self._preview_layout.addWidget(label)

        self._preview_layout.addStretch()
        self._position_preview()
        self.updateGeometry()

    def _position_preview(self) -> None:
        """Position the preview widget within the line edit area."""
        line_edit = self.lineEdit()
        if not line_edit:
            return

        rect = line_edit.rect()
        self._preview.setGeometry(rect)
        self._preview.raise_()

    # -------------------------------------------------------------------------
    # Qt overrides
    # -------------------------------------------------------------------------

    def resizeEvent(self, event) -> None:
        """
        Handle resize events to reposition the preview.

        Args:
            event: Resize event
        """
        super().resizeEvent(event)
        self._position_preview()

    def showPopup(self) -> None:
        """Show the popup with adjusted width."""
        super().showPopup()
        self.view().setMinimumWidth(self.width())

    def sizeHint(self) -> QSize:
        """
        Calculate ideal size based on content.

        Returns:
            Suggested size for the widget
        """
        selected_count = len(self.selected_keys())S
        content_width = self._icon_size * selected_count + 4 * (selected_count - 1) + 20

        height = super().sizeHint().height()

        return QSize(content_width, height)
