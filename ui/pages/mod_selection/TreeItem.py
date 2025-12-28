from __future__ import annotations

from enum import Enum, auto

from PySide6.QtCore import Qt
from PySide6.QtGui import QStandardItem

from constants import (
    ROLE_COMPONENT,
    ROLE_IS_DEFAULT,
    ROLE_MOD,
    ROLE_OPTION_KEY,
    ROLE_PROMPT_KEY,
)
from core.ComponentReference import ComponentReference


class ItemType(Enum):
    """Types of tree items."""

    MOD = auto()
    COMPONENT = auto()
    MUC_OPTION = auto()
    SUB_PROMPT = auto()
    SUB_PROMPT_OPTION = auto()


class TreeItem(QStandardItem):
    """Unified tree item for all types."""

    def __init__(
        self,
        item_type: ItemType,
        reference: ComponentReference,
        text: str = "",
        mod=None,
        component=None,
        prompt=None,
        option_key: str = None,
        is_default: bool = False,
    ):
        super().__init__(text)

        self._item_type = item_type
        self._reference = reference

        flags = (
            Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsUserCheckable
        )

        if item_type == ItemType.MOD:
            flags |= Qt.ItemFlag.ItemIsAutoTristate

        self.setFlags(flags)
        self.setCheckState(Qt.CheckState.Unchecked)

        self.setData(mod, ROLE_MOD)
        self.setData(component, ROLE_COMPONENT)
        self.setData(prompt, ROLE_PROMPT_KEY)
        self.setData(option_key, ROLE_OPTION_KEY)
        self.setData(is_default, ROLE_IS_DEFAULT)

    @property
    def reference(self) -> ComponentReference:
        """Get item reference."""
        return self._reference

    @property
    def item_type(self) -> ItemType:
        """Get item type."""
        return self._item_type

    # ========================================
    # Factory Methods
    # ========================================

    @classmethod
    def create_mod(cls, mod) -> TreeItem:
        """Create mod item."""
        reference = ComponentReference.for_mod(mod.id)
        return cls(
            item_type=ItemType.MOD,
            reference=reference,
            text=mod.name,
            mod=mod,
        )

    @classmethod
    def create_component(cls, mod, component) -> TreeItem:
        """Create standard/MUC/SUB component item."""
        reference = ComponentReference.for_component(mod.id, component.key)

        return cls(
            item_type=ItemType.COMPONENT,
            reference=reference,
            text="",
            mod=mod,
            component=component,
        )

    @classmethod
    def create_muc_option(cls, mod, component, option_key: str, is_default: bool) -> TreeItem:
        """Create MUC option item."""
        reference = ComponentReference.for_component(mod.id, option_key)

        return cls(
            item_type=ItemType.MUC_OPTION,
            reference=reference,
            text="",
            mod=mod,
            component=component,
            option_key=option_key,
            is_default=is_default,
        )

    @classmethod
    def create_sub_prompt(cls, mod, component, prompt) -> TreeItem:
        """Create SUB prompt item."""
        reference = ComponentReference.from_string(f"{mod.id}:{component.key}.{prompt.key}")

        return cls(
            item_type=ItemType.SUB_PROMPT,
            reference=reference,
            text="",
            mod=mod,
            component=component,
            prompt=prompt,
        )

    @classmethod
    def create_sub_option(
        cls, mod, component, prompt, option_key: str, is_default: bool
    ) -> TreeItem:
        """Create SUB prompt option item."""
        reference = ComponentReference.from_string(
            f"{mod.id}:{component.key}.{prompt.key}.{option_key}"
        )

        return cls(
            item_type=ItemType.SUB_PROMPT_OPTION,
            reference=reference,
            text="",
            mod=mod,
            component=component,
            prompt=prompt,
            option_key=option_key,
            is_default=is_default,
        )
