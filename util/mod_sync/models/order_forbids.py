"""FORBID_COMPONENT declarations that mean an installation order, not an incompatibility.

WeiDU refuses a component whose FORBID_COMPONENT target is already installed:
the TP2 only says that the source must not be installed after the target.
Most mods use it for components that cannot live together, some to make sure
they are installed first (every mod forbidding EET_end, for instance). The
TP2 does not tell them apart, so the maintainer lists the second ones in a
file read by mod_sync.

File format::

    {"forbid_as_order": [
        {"target": "eet_end"},
        {"source": "c#endlessbg1(0)", "target": "transitions(0)"}
    ]}

Each side is a mod, optionally followed by its component numbers; a side
without numbers covers every component, a missing source covers every mod.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from pathlib import Path
import re

from core.ComponentRequirement import ComponentTarget, normalize_tp2_name

logger = logging.getLogger(__name__)

# One side of an entry: "mod" or "mod(1,2)"
SIDE_PATTERN = re.compile(r"^\s*([^()\s]+)\s*(?:\(\s*(\d+(?:\s*,\s*\d+)*)\s*\))?\s*$")
ENTRIES_KEY = "forbid_as_order"


@dataclass(frozen=True, slots=True)
class ForbidSide:
    """Components of a mod one side of an entry covers.

    Attributes:
        mod: Normalized tp2 name of the mod
        components: Component numbers covered, every component when empty
    """

    mod: str
    components: frozenset[str] = frozenset()

    @classmethod
    def parse(cls, notation: str) -> ForbidSide | None:
        """Read "mod" or "mod(1,2)", None when the notation is not one of them."""
        match = SIDE_PATTERN.match(notation)
        if match is None:
            return None

        mod, numbers = match.groups()
        components = frozenset(
            number.strip() for number in (numbers or "").split(",") if number
        )
        return cls(normalize_tp2_name(mod), components)

    def covers(self, mod: str, component: str) -> bool:
        return self.mod == mod and (not self.components or component in self.components)


@dataclass(frozen=True, slots=True)
class OrderForbid:
    """A FORBID_COMPONENT of a source towards a target that means "source before target"."""

    target: ForbidSide
    source: ForbidSide | None = None

    def covers(self, mod: str, component: str, target: ComponentTarget) -> bool:
        if self.source is not None and not self.source.covers(mod, component):
            return False
        return self.target.covers(target.tp2, target.component or "")


class OrderForbids:
    """The FORBID_COMPONENT declarations mod_sync turns into order rules."""

    def __init__(self, entries: tuple[OrderForbid, ...] = ()) -> None:
        self._entries = entries

    @classmethod
    def load(cls, path: Path) -> OrderForbids:
        """Read the maintainer file; a missing or unreadable file lists nothing."""
        if not path.exists():
            logger.info(f"No FORBID_COMPONENT order list at {path}")
            return cls()

        try:
            raw_entries = json.loads(path.read_text(encoding="utf-8")).get(ENTRIES_KEY) or []
        except (OSError, ValueError, AttributeError) as e:
            logger.error(f"Unreadable FORBID_COMPONENT order list {path}: {e}")
            return cls()

        entries = []
        for index, raw_entry in enumerate(raw_entries):
            entry = cls._read_entry(raw_entry)
            if entry is None:
                logger.warning(f"{path.name}: Entry {index} skipped, malformed: {raw_entry!r}")
                continue
            entries.append(entry)

        logger.debug(f"{len(entries)} FORBID_COMPONENT read as an installation order")
        return cls(tuple(entries))

    @staticmethod
    def _read_entry(raw_entry: object) -> OrderForbid | None:
        if not isinstance(raw_entry, dict) or not isinstance(raw_entry.get("target"), str):
            return None

        target = ForbidSide.parse(raw_entry["target"])
        raw_source = raw_entry.get("source")
        if raw_source is None:
            return OrderForbid(target) if target else None

        source = ForbidSide.parse(raw_source) if isinstance(raw_source, str) else None
        return OrderForbid(target, source) if target and source else None

    def means_order(self, mod: str, component: str, target: ComponentTarget) -> bool:
        """Tell whether the component of a mod forbidding a target must just be installed first.

        Args:
            mod: Normalized tp2 name of the mod declaring the FORBID_COMPONENT
            component: Number of the declaring component
            target: Forbidden component
        """
        return any(entry.covers(mod, component, target) for entry in self._entries)
