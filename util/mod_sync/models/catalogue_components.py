"""Components of the catalogued mods, as the rules generated from a TP2 name them.

The index is read from the definitions once per run and follows each
definition mod_sync saves: a LABEL another mod designates is then found as
soon as that mod has been synchronized, even earlier in the same run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
from typing import Any

from core.ComponentRequirement import normalize_tp2_name

logger = logging.getLogger(__name__)

# Component type of a MUC group, whose choices are the installable components
MUC_COMPONENT_TYPE = "muc"


@dataclass(frozen=True, slots=True)
class CataloguedMod:
    """Installable components of a catalogued mod.

    Attributes:
        components: Numbers of the installable components, the choices of a MUC in its place
        label_numbers: Number of the component each LABEL designates
    """

    components: frozenset[str] = frozenset()
    label_numbers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_definition(cls, definition: dict[str, Any]) -> CataloguedMod:
        """Read the installable components and their LABEL from a mod definition."""
        stored_components = definition.get("components")
        if not isinstance(stored_components, dict):
            return cls()

        components: set[str] = set()
        label_numbers: dict[str, str] = {}
        for key, entry in stored_components.items():
            if not isinstance(entry, dict):
                continue

            if entry.get("type") == MUC_COMPONENT_TYPE:
                components.update(str(choice) for choice in entry.get("components") or [])
                choice_labels = entry.get("labels") or {}
                for choice, label in choice_labels.items():
                    label_numbers.setdefault(label, str(choice))
            else:
                components.add(key)
                if label := entry.get("label"):
                    label_numbers.setdefault(label, key)

        return cls(frozenset(components), label_numbers)


class CatalogueComponents:
    """Index of the catalogued mods by normalized tp2 name."""

    def __init__(self) -> None:
        self._mods: dict[str, CataloguedMod] = {}

    # ========================================
    # Maintenance
    # ========================================

    def load(self, definitions_dir: Path) -> None:
        """Index the definitions of a directory, replacing the current index.

        An unreadable definition is left out: its mod is then unknown.
        """
        self._mods = {}
        for definition_path in sorted(definitions_dir.glob("*.json")):
            try:
                definition = json.loads(definition_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                logger.debug(f"Definition {definition_path.name} left out of the index: {e}")
                continue
            if isinstance(definition, dict):
                self.update(definition_path.stem, definition)

        logger.debug(f"Indexed the components of {len(self._mods)} catalogued mods")

    def update(self, mod_name: str, definition: dict[str, Any]) -> None:
        """Index a definition under its tp2, or the given mod name when it has none."""
        tp2 = definition.get("tp2")
        key = normalize_tp2_name(tp2 if isinstance(tp2, str) and tp2 else mod_name)
        self._mods[key] = CataloguedMod.from_definition(definition)

    # ========================================
    # Lookups
    # ========================================

    def has_mod(self, tp2: str) -> bool:
        """Tell whether a mod, by its normalized tp2 name, is catalogued."""
        return tp2 in self._mods

    def has_component(self, tp2: str, component: str) -> bool:
        """Tell whether a catalogued mod has an installable component of this number."""
        mod = self._mods.get(tp2)
        return mod is not None and component in mod.components

    def component_of_label(self, tp2: str, label: str) -> str | None:
        """Return the number of the component a LABEL of a catalogued mod designates."""
        mod = self._mods.get(tp2)
        return mod.label_numbers.get(label) if mod is not None else None
