"""Category enumeration for mod classification."""

from enum import Enum

from core.TranslationManager import tr


class CategoryEnum(Enum):
    """
    Enumeration of mod categories with display icons.

    Each category has a value (internal identifier) and an icon (display).
    """
    ALL = ("all", "🐉")
    PATCH = ("patch", "🩹")
    UTIL = ("util", "⚙️")
    CONV = ("conv", "🧪")
    UI = ("ui", "🪞")
    COSM = ("cosm", "💀")
    PORTRAIT = ("portrait", "🖼️")
    QUEST = ("quest", "📜")
    NPC = ("npc", "🧙")
    NPC1D = ("npc1d", "👤")
    NPCX = ("npcx", "🧝")
    SMITH = ("smith", "⚒️")
    SPELL = ("spell", "✨")
    ITEM = ("item", "🗡️")
    KIT = ("kit", "🧬")
    GAMEPLAY = ("gameplay", "🎮")
    TACTIC = ("tactic", "♜")
    PARTY = ("party", "⚔️")

    def __init__(self, value: str, icon: str) -> None:
        """
        Initialize category with value and icon.

        Args:
            value: Internal category identifier
            icon: Display icon
        """
        self._value_ = value
        self.icon = icon

    def __str__(self) -> str:
        """Return string representation (_value_)."""
        return self._value_

    @classmethod
    def list_without_all(cls) -> list['CategoryEnum']:
        """
        Return list of all categories except ALL.

        Returns:
            List of category enum values (excluding ALL)
        """
        return [category for category in cls if category != cls.ALL]

    @classmethod
    def get_all(cls) -> 'CategoryEnum':
        """
        Get the ALL category.

        Returns:
            ALL category enum value
        """
        return cls.ALL

    @classmethod
    def from_value(cls, value: str) -> 'CategoryEnum':
        """
        Get category from its value string.

        Args:
            value: Category value (e.g., 'spell')

        Returns:
            CategoryEnum instance

        Raises:
            ValueError: If value not found
        """
        for category in cls:
            if category.value == value:
                return category

        raise ValueError(tr("error.unknown_category", category=value))

    @classmethod
    def get_display_name(cls, category: 'CategoryEnum') -> str:
        """
        Get translated display name for a category.

        Args:
            category: Category enum value

        Returns:
            Translated category name
        """
        return tr(f"category.{category.value}")

    def get_icon_text(self) -> str:
        """
        Get category as icon + text.

        Returns:
            Formatted string with icon and translated name
        """
        return f"{self.icon} {self.get_display_name(self)}"

    def __repr__(self) -> str:
        """Developer representation."""
        return f"<CategoryEnum.{self.name}: {self.value}>"
