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
    CUSTOM = ("custom", "⭐")

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
    def list_without_all(cls) -> list["CategoryEnum"]:
        """
        Return list of all categories except ALL.

        Returns:
            List of category enum values (excluding ALL)
        """
        return [category for category in cls if category != cls.ALL]

    @classmethod
    def get_all(cls) -> "CategoryEnum":
        """
        Get the ALL category.

        Returns:
            ALL category enum value
        """
        return cls.ALL

    @classmethod
    def get_display_name(cls, category: "CategoryEnum") -> str:
        """
        Get translated display name for a category.

        Args:
            category: Category enum value

        Returns:
            Translated category name
        """
        return tr(f"category.{category.value}")

    def __repr__(self) -> str:
        """Developer representation."""
        return f"<CategoryEnum.{self.name}: {self.value}>"
