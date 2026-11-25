"""Requirements a TP2 component declares on other components.

A requirement is read from the TP2 of a mod and names its targets by
their tp2 and component number, or LABEL, before any link with the catalogue.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from pathlib import PureWindowsPath

from constants import TP2_EXTENSION, WEIDU_SETUP_PREFIX


def normalize_tp2_name(name: str) -> str:
    """Reduce a tp2 reference to the name WeiDU logs it under.

    "MyMod/Setup-MyMod.TP2" and "mymod" both give "mymod".
    """
    return (
        PureWindowsPath(name.strip())
        .name.lower()
        .removesuffix(TP2_EXTENSION)
        .removeprefix(WEIDU_SETUP_PREFIX)
    )


class RequirementKind(Enum):
    """What a requirement asks of its targets."""

    REQUIRED = auto()  # One of the targets must be installed before
    FORBIDDEN = auto()  # None of the targets may be installed
    OPTIONAL = auto()  # The component adapts to the targets when installed before


class RequirementSource(Enum):
    """Where the TP2 declares a requirement."""

    HEADER = auto()  # REQUIRE_COMPONENT or FORBID_COMPONENT flag
    PREDICATE = auto()  # MOD_IS_INSTALLED in a REQUIRE_PREDICATE
    BODY = auto()  # MOD_IS_INSTALLED in the component code


@dataclass(frozen=True, slots=True)
class ComponentTarget:
    """A component named by a requirement, by its number or by its LABEL.

    Attributes:
        tp2: Normalized tp2 name of the mod
        component: Component number, None when the target is named by its label
        label: LABEL of the component, None when the target is named by its number
    """

    tp2: str
    component: str | None = None
    label: str | None = None

    def __post_init__(self) -> None:
        if (self.component is None) == (self.label is None):
            raise ValueError("A component target needs either a number or a label")
        object.__setattr__(self, "tp2", normalize_tp2_name(self.tp2))

    def with_component(self, component: str) -> ComponentTarget:
        """Return the target named by the number of the component its label designates."""
        return ComponentTarget(tp2=self.tp2, component=component)


@dataclass(frozen=True, slots=True)
class ComponentRequirement:
    """A requirement of a component on alternative targets.

    Attributes:
        kind: What the requirement asks of its targets
        alternatives: Targets of which one is enough to fulfil the requirement
        source: Where the TP2 declares the requirement
        text_ref: Reference to the message translation string ID
        text: Direct message text
    """

    kind: RequirementKind
    alternatives: tuple[ComponentTarget, ...]
    source: RequirementSource
    text_ref: str | None = None
    text: str | None = None
