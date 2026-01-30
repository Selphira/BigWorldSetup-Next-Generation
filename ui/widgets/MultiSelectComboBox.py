from PySide6.QtCore import QEvent, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)


class MultiSelectComboBox(QWidget):
    selection_changed = Signal(list)

    def __init__(
        self,
        parent=None,
        min_selection_count: int = 1,
        separator: str | None = None,
        show_text_selection: bool = True,
        show_icon_selection: bool = True,
    ):
        super().__init__(parent)

        self.items: dict[str, str | None] = {}
        self.selected: set[str] = set()
        self.is_open = False
        self._separator = separator
        self._show_text_selection = show_text_selection
        self._show_icon_selection = show_icon_selection
        self._min_selection_count = min_selection_count
        self._just_closed = False

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        self.label_container = QFrame()
        self.label_container.setFrameStyle(QFrame.Shape.Box)

        self.label_container.setObjectName("lineEdit")

        self.label_layout = QHBoxLayout()
        self.label_container.setLayout(self.label_layout)
        self.label_layout.setContentsMargins(0, 0, 0, 0)
        self.label_layout.setSpacing(4)

        self.selection_widget = QWidget()
        self.selection_widget.setStyleSheet("background:transparent")
        self.selection_widget.hide()
        self.selection_layout = QHBoxLayout(self.selection_widget)
        self.selection_layout.setContentsMargins(0, 0, 0, 0)
        self.selection_layout.setSpacing(4)

        self.placeholder = QLabel("Sélectionner...")
        self.placeholder.setMinimumHeight(24)

        self.arrow = QLabel("▼")

        self.label_layout.addWidget(self.selection_widget)
        self.label_layout.addWidget(self.placeholder)
        self.label_layout.addStretch()
        self.label_layout.addWidget(self.arrow)

        main_layout.addWidget(self.label_container)

        self.dropdown = QFrame(self, Qt.WindowType.Popup)
        self.dropdown.setObjectName("dropdown")
        self.dropdown.setFrameStyle(QFrame.Shape.Box)
        self.dropdown.installEventFilter(self)
        self.label_container.installEventFilter(self)

        self.dropdown_layout = QVBoxLayout(self.dropdown)
        self.dropdown_layout.setContentsMargins(5, 5, 5, 5)
        self.dropdown_layout.setSpacing(2)

        self.checkboxes: dict[str, tuple[str, QCheckBox]] = {}

        self.dropdown.hide()

    # ------------------------------------------------------------------
    # Event handling
    # ------------------------------------------------------------------

    def eventFilter(self, obj, event):
        if obj == self.label_container and event.type() == QEvent.Type.MouseButtonPress:
            if self._just_closed:
                return True

            if self.is_open:
                self._close_dropdown()
            else:
                self._open_dropdown()

        if obj == self.dropdown and event.type() == QEvent.Type.Hide:
            self.is_open = False
            self.arrow.setText("▼")
            self._just_closed = True
            QTimer.singleShot(100, lambda: setattr(self, "_just_closed", False))

        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------
    # Dropdown control
    # ------------------------------------------------------------------

    def _open_dropdown(self):
        pos = self.label_container.mapToGlobal(self.label_container.rect().bottomLeft())
        self.dropdown.move(pos)
        self.dropdown.setFixedWidth(self.label_container.width())
        self.is_open = True
        self.arrow.setText("▲")
        self.dropdown.show()

    def _close_dropdown(self):
        self.is_open = False
        self.arrow.setText("▼")
        self.dropdown.hide()

    def _update_selection(self, item: str, checked: bool):
        if checked:
            self.selected.add(item)
        else:
            self.selected.discard(item)

        while self.selection_layout.count():
            child = self.selection_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        if not self.selected:
            self.selection_widget.hide()
            self.placeholder.show()
            return

        self.selection_widget.show()
        self.placeholder.hide()

        for i, key in enumerate(self.selected):
            cb = self.checkboxes[key]
            entry = QWidget()
            entry.setStyleSheet("background:transparent")
            entry_layout = QHBoxLayout(entry)
            entry_layout.setContentsMargins(0, 0, 0, 0)
            entry_layout.setSpacing(2)
            entry.setMinimumHeight(24)

            if self._show_icon_selection:
                icon_path = self.items[key]
                if icon_path:
                    icon = QLabel()
                    pixmap = QPixmap(icon_path).scaled(
                        24,
                        24,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    icon.setPixmap(pixmap)
                    entry_layout.addWidget(icon)

            if self._show_text_selection:
                entry_layout.addWidget(QLabel(cb[0]))

            self.selection_layout.addWidget(entry)

            if self._separator is not None:
                if i < len(self.selected) - 1:
                    self.selection_layout.addWidget(QLabel(self._separator))

        self.selection_changed.emit(self.selected_keys())

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_items(
        self,
        items: dict[str, str | tuple[str, str]],
    ) -> None:
        for key, item in items.items():
            if isinstance(item, tuple):
                text, icon_path = item
                self.items[key] = icon_path
                cb = QCheckBox(text)
                cb.setIcon(QIcon(icon_path))
                cb.setIconSize(QSize(24, 24))
            else:
                text = item
                self.items[key] = None
                cb = QCheckBox(text)

            must_be_selected = self.count_selected_items() < self._min_selection_count

            cb.setStyleSheet("background:transparent")
            cb.toggled.connect(lambda checked, t=key: self._update_selection(t, checked))
            self.dropdown_layout.addWidget(cb)
            self.checkboxes[key] = (text, cb)

            if must_be_selected:
                cb.setCheckState(Qt.CheckState.Checked)
                self._update_selection(key, True)

    def count_selected_items(self) -> int:
        """Count the number of selected items."""
        return sum(
            item[1].checkState() == Qt.CheckState.Checked for item in self.checkboxes.values()
        )

    def selected_keys(self) -> list[str]:
        """Get the list of selected item keys."""
        return list(self.selected)

    def set_selected_keys(self, keys: list[str]) -> None:
        """Set the selected items by their keys."""
        keys = set(keys)
        for key, item in self.checkboxes.items():
            item[1].setCheckState(
                Qt.CheckState.Checked if key in keys else Qt.CheckState.Unchecked
            )
        self.selection_changed.emit(self.selected_keys())
