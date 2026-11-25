"""Dependency, incompatibility and order rules generated from the requirements of a TP2.

Every rule names mods by their normalized tp2 name. The components of a mod
that have the same requirement, with the same message, share a rule; the
requirements alike of the same components are then merged into one rule. A
FORBID_COMPONENT is an incompatibility, unless the maintainer listed it as an
installation order (see OrderForbids).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import re
from typing import Any

from constants import TP2_RULE_ORIGIN
from core.ComponentRequirement import (
    ComponentRequirement,
    ComponentTarget,
    RequirementKind,
    normalize_tp2_name,
)
from core.Rules import DependencyMode, OrderDirection, RuleSeverity
from core.WeiDUTp2Parser import WeiDUTp2
from util.mod_sync.models.catalogue_components import CatalogueComponents
from util.mod_sync.models.order_forbids import OrderForbids
from util.mod_sync.sources.ini_source import MOD_ID_PATTERN

logger = logging.getLogger(__name__)

# Component numbers a rule notation accepts
COMPONENT_NUMBER_PATTERN = re.compile(r"^\d+$")

# Separator of the targets of a rule: a dependency on all its targets joins
# them with "&", the other rules with "|"
ALL_TARGETS_SEPARATOR = "&"
ANY_TARGET_SEPARATOR = "|"

# Severity of a rule by the kind of the requirement it comes from; an optional
# dependency only warns about the order of installation
REQUIREMENT_SEVERITIES = {
    RequirementKind.REQUIRED: RuleSeverity.ERROR,
    RequirementKind.FORBIDDEN: RuleSeverity.ERROR,
    RequirementKind.OPTIONAL: RuleSeverity.WARNING,
}

DEPENDENCY_RULES = "dependencies"
INCOMPATIBILITY_RULES = "incompatibilities"
ORDER_RULES = "order"


@dataclass(frozen=True, slots=True)
class GeneratedRule:
    """A rule generated from the requirements of a TP2.

    Attributes:
        rule: Rule notation, "source(components):target(components)|..."
        severity: Severity of a violation
        translations: Message of the mod in each language, as (language code, text) pairs
        optional: Whether the dependency only adapts the source to its targets
        direction: Place of the source relative to its targets, for an order rule
        mode: How a dependency needs its targets, written only when all of them
    """

    rule: str
    severity: RuleSeverity
    translations: tuple[tuple[str, str], ...] = ()
    optional: bool = False
    direction: OrderDirection | None = None
    mode: DependencyMode = DependencyMode.ANY

    def to_definition(self) -> dict[str, Any]:
        """Return the rule as written in the rules section of a definition."""
        entry: dict[str, Any] = {"rule": self.rule, "severity": self.severity.value}
        if self.direction is not None:
            entry["direction"] = self.direction.value
        if self.mode == DependencyMode.ALL:
            entry["mode"] = self.mode.value
        if self.optional:
            entry["optional"] = True
        if self.translations:
            entry["translations"] = dict(self.translations)
        entry["origin"] = TP2_RULE_ORIGIN
        return entry


@dataclass(frozen=True, slots=True)
class Tp2Rules:
    """Rules generated from the requirements of a TP2, by section of a definition."""

    dependencies: tuple[GeneratedRule, ...] = ()
    incompatibilities: tuple[GeneratedRule, ...] = ()
    order: tuple[GeneratedRule, ...] = ()

    def sections(self) -> dict[str, list[dict[str, Any]]]:
        """Return the rules as written in each section of a definition, empty ones included."""
        return {
            DEPENDENCY_RULES: [rule.to_definition() for rule in self.dependencies],
            INCOMPATIBILITY_RULES: [rule.to_definition() for rule in self.incompatibilities],
            ORDER_RULES: [rule.to_definition() for rule in self.order],
        }


@dataclass(frozen=True, slots=True)
class _SharedRequirement:
    """What the components sharing a rule require, in the same terms.

    Attributes:
        section: Section of the definition the rule goes to
    """

    kind: RequirementKind
    alternatives: tuple[ComponentTarget, ...]
    translations: tuple[tuple[str, str], ...]
    section: str

    @property
    def mergeable(self) -> bool:
        """Whether the targets can join those of other requirements of the same components.

        A rule side cannot mix "&" and "|": a dependency on one of several
        alternatives keeps its own rule.
        """
        return self.kind != RequirementKind.REQUIRED or len(self.alternatives) == 1


@dataclass(slots=True)
class _MergedRequirement:
    """Requirements alike of the same components, written as one rule.

    Attributes:
        requirement: First of the requirements, which gives the terms of the rule
        components: Components of the mod the rule applies to
        targets: Targets of every merged requirement, in order of first appearance
        merged: Whether several requirements were merged
    """

    requirement: _SharedRequirement
    components: tuple[str, ...]
    targets: list[ComponentTarget] = field(default_factory=list)
    merged: bool = False

    @property
    def needs_all_targets(self) -> bool:
        """Whether the rule is a dependency on each of its targets.

        A merged dependency needs each target its requirements named; merged
        forbidden targets are each incompatible, or each installed after.
        """
        return (
            self.merged
            and self.requirement.kind != RequirementKind.FORBIDDEN
            and len(self.targets) > 1
        )


class Tp2RuleBuilder:
    """Turns the requirements of the components of a TP2 into rules."""

    def __init__(
        self,
        catalogue: CatalogueComponents,
        labels_language: str,
        excluded_languages: set[str],
        order_forbids: OrderForbids | None = None,
    ) -> None:
        """Initialize the builder.

        Args:
            catalogue: Catalogued mods, which resolve the labels of other mods
            labels_language: Language of the literal messages of a TP2 declaring no LANGUAGE
            excluded_languages: Languages of the TP2 a definition cannot hold
            order_forbids: FORBID_COMPONENT declarations that mean an installation order
        """
        self._catalogue = catalogue
        self._labels_language = labels_language
        self._excluded_languages = excluded_languages
        self._order_forbids = order_forbids if order_forbids is not None else OrderForbids()

    def build(self, tp2: WeiDUTp2, tp2_name: str) -> Tp2Rules:
        """Generate the rules of a parsed TP2.

        Args:
            tp2: Parsed TP2 whose components carry their requirements
            tp2_name: Mod the TP2 belongs to

        Returns:
            Rules in order of first appearance of their first requirement
        """
        mod = normalize_tp2_name(tp2_name)
        if not MOD_ID_PATTERN.match(mod):
            logger.warning(f"{tp2_name}: TP2 rules skipped, {mod!r} is not a mod identifier")
            return Tp2Rules()

        shared = self._share_requirements(tp2, mod)

        sections: dict[str, list[GeneratedRule]] = {
            DEPENDENCY_RULES: [],
            INCOMPATIBILITY_RULES: [],
            ORDER_RULES: [],
        }
        for merged in self._merge_requirements(shared):
            sections[merged.requirement.section].append(self._rule(mod, merged))

        logger.debug(
            f"{tp2_name}: {len(sections[DEPENDENCY_RULES])} dependency, "
            f"{len(sections[INCOMPATIBILITY_RULES])} incompatibility and "
            f"{len(sections[ORDER_RULES])} order rules from the TP2"
        )
        return Tp2Rules(
            dependencies=tuple(sections[DEPENDENCY_RULES]),
            incompatibilities=tuple(sections[INCOMPATIBILITY_RULES]),
            order=tuple(sections[ORDER_RULES]),
        )

    # ========================================
    # Requirements
    # ========================================

    def _share_requirements(
        self, tp2: WeiDUTp2, mod: str
    ) -> dict[_SharedRequirement, list[str]]:
        """Group the components of the mod by requirement, in order of first appearance."""
        installable = tp2.installable_components()
        own_components = {component.designated for component in installable}

        shared: dict[_SharedRequirement, list[str]] = {}
        for component in installable:
            location = f"{mod}:{component.designated}"
            if not COMPONENT_NUMBER_PATTERN.match(component.designated):
                if component.requirements:
                    logger.warning(
                        f"{location}: Requirements of a component without number skipped"
                    )
                continue

            for requirement in component.requirements:
                alternatives = self._resolve_alternatives(requirement, location)
                if alternatives is None:
                    continue

                self._warn_unknown_targets(alternatives, mod, own_components, location)
                key = _SharedRequirement(
                    requirement.kind,
                    alternatives,
                    self._translations(requirement),
                    self._section(requirement.kind, mod, component.designated, alternatives),
                )
                components = shared.setdefault(key, [])
                if component.designated not in components:
                    components.append(component.designated)

        return shared

    @staticmethod
    def _merge_requirements(
        shared: dict[_SharedRequirement, list[str]],
    ) -> list[_MergedRequirement]:
        """Merge the requirements of the same components that have the same kind and message."""
        merged_requirements: dict[object, _MergedRequirement] = {}
        for requirement, components in shared.items():
            sources = tuple(components)
            key: object = (
                (sources, requirement.kind, requirement.section, requirement.translations)
                if requirement.mergeable
                else (sources, requirement)
            )

            merged = merged_requirements.get(key)
            if merged is None:
                merged_requirements[key] = _MergedRequirement(
                    requirement, sources, list(requirement.alternatives)
                )
                continue

            merged.merged = True
            merged.targets.extend(
                target for target in requirement.alternatives if target not in merged.targets
            )

        return list(merged_requirements.values())

    def _section(
        self,
        kind: RequirementKind,
        mod: str,
        component: str,
        alternatives: tuple[ComponentTarget, ...],
    ) -> str:
        """Return the section of the rule of a requirement.

        A forbidden target listed as an installation order only asks for the
        component to be installed before it.
        """
        if kind != RequirementKind.FORBIDDEN:
            return DEPENDENCY_RULES

        if all(
            self._order_forbids.means_order(mod, component, target) for target in alternatives
        ):
            return ORDER_RULES
        return INCOMPATIBILITY_RULES

    def _resolve_alternatives(
        self, requirement: ComponentRequirement, location: str
    ) -> tuple[ComponentTarget, ...] | None:
        """Return the alternatives named by their number, None if one cannot be.

        A label of another mod is looked up in its catalogued definition; the
        requirement is found again once that mod is synchronized with its labels.
        Dropping a single alternative would make the requirement stricter, so the
        whole requirement is skipped.
        """
        alternatives = []
        for target in requirement.alternatives:
            component = target.component
            if component is None:
                component = self._catalogue.component_of_label(target.tp2, target.label or "")
                if component is None:
                    logger.info(
                        f"{location}: Skipping {requirement.kind.name.lower()} requirement, "
                        f"label {target.label!r} of {target.tp2} not catalogued yet"
                    )
                    return None

            if not MOD_ID_PATTERN.match(target.tp2) or not COMPONENT_NUMBER_PATTERN.match(
                component
            ):
                logger.warning(
                    f"{location}: Skipping {requirement.kind.name.lower()} requirement, "
                    f"{target.tp2}({component}) cannot be written in a rule"
                )
                return None

            alternatives.append(ComponentTarget(tp2=target.tp2, component=component))

        return tuple(alternatives)

    def _warn_unknown_targets(
        self,
        alternatives: tuple[ComponentTarget, ...],
        mod: str,
        own_components: set[str],
        location: str,
    ) -> None:
        """Log the targets missing from the catalogue; their rule is generated all the same."""
        for target in alternatives:
            component = target.component or ""
            if target.tp2 == mod:
                if component not in own_components:
                    logger.warning(
                        f"{location}: Target component {mod}({component}) not in the TP2"
                    )
            elif not self._catalogue.has_mod(target.tp2):
                logger.warning(f"{location}: Target mod {target.tp2} not catalogued")
            elif not self._catalogue.has_component(target.tp2, component):
                logger.warning(
                    f"{location}: Target component {target.tp2}({component}) not catalogued"
                )

    def _translations(self, requirement: ComponentRequirement) -> tuple[tuple[str, str], ...]:
        """Return the message of a requirement in each language a definition can hold.

        A TP2 declaring no LANGUAGE gives no translated message: its literal
        message is taken in the language of its literal labels.
        """
        if not requirement.messages and requirement.text:
            return ((self._labels_language, requirement.text),)

        return tuple(
            (code, text)
            for code, text in requirement.messages
            if code not in self._excluded_languages
        )

    # ========================================
    # Rule notation
    # ========================================

    @staticmethod
    def _rule(mod: str, merged: _MergedRequirement) -> GeneratedRule:
        """Write the rule of merged requirements.

        A dependency on all its targets is written "a(1,2)&b(3)" in mode
        "all", which needs each component of each mod.
        """
        targets_by_mod: dict[str, list[str]] = {}
        for target in merged.targets:
            targets_by_mod.setdefault(target.tp2, []).append(target.component or "")

        needs_all = merged.needs_all_targets
        separator = ALL_TARGETS_SEPARATOR if needs_all else ANY_TARGET_SEPARATOR
        targets = separator.join(
            f"{target_mod}({','.join(numbers)})"
            for target_mod, numbers in targets_by_mod.items()
        )

        requirement = merged.requirement
        return GeneratedRule(
            rule=f"{mod}({','.join(merged.components)}):{targets}",
            severity=REQUIREMENT_SEVERITIES[requirement.kind],
            translations=requirement.translations,
            optional=requirement.kind == RequirementKind.OPTIONAL,
            direction=OrderDirection.BEFORE if requirement.section == ORDER_RULES else None,
            mode=DependencyMode.ALL if needs_all else DependencyMode.ANY,
        )
