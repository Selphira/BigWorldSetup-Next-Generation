"""Dependency and incompatibility rules generated from the requirements of a TP2.

Every rule names mods by their normalized tp2 name. The components of a mod
that have the same requirement, with the same message, share a rule.
"""

from __future__ import annotations

from dataclasses import dataclass
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
from core.Rules import RuleSeverity
from core.WeiDUTp2Parser import WeiDUTp2
from util.mod_sync.models.catalogue_components import CatalogueComponents
from util.mod_sync.sources.ini_source import MOD_ID_PATTERN

logger = logging.getLogger(__name__)

# Component numbers a rule notation accepts
COMPONENT_NUMBER_PATTERN = re.compile(r"^\d+$")

# Severity of a rule by the kind of the requirement it comes from; an optional
# dependency only warns about the order of installation
REQUIREMENT_SEVERITIES = {
    RequirementKind.REQUIRED: RuleSeverity.ERROR,
    RequirementKind.FORBIDDEN: RuleSeverity.ERROR,
    RequirementKind.OPTIONAL: RuleSeverity.WARNING,
}

DEPENDENCY_RULES = "dependencies"
INCOMPATIBILITY_RULES = "incompatibilities"


@dataclass(frozen=True, slots=True)
class GeneratedRule:
    """A rule generated from the requirements of a TP2.

    Attributes:
        rule: Rule notation, "source(components):target(components)|..."
        severity: Severity of a violation
        translations: Message of the mod in each language, as (language code, text) pairs
        optional: Whether the dependency only adapts the source to its targets
    """

    rule: str
    severity: RuleSeverity
    translations: tuple[tuple[str, str], ...] = ()
    optional: bool = False

    def to_definition(self) -> dict[str, Any]:
        """Return the rule as written in the rules section of a definition."""
        entry: dict[str, Any] = {"rule": self.rule, "severity": self.severity.value}
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

    def sections(self) -> dict[str, list[dict[str, Any]]]:
        """Return the rules as written in each section of a definition, empty ones included."""
        return {
            DEPENDENCY_RULES: [rule.to_definition() for rule in self.dependencies],
            INCOMPATIBILITY_RULES: [rule.to_definition() for rule in self.incompatibilities],
        }


@dataclass(frozen=True, slots=True)
class _SharedRequirement:
    """What the components sharing a rule require, in the same terms."""

    kind: RequirementKind
    alternatives: tuple[ComponentTarget, ...]
    translations: tuple[tuple[str, str], ...]


class Tp2RuleBuilder:
    """Turns the requirements of the components of a TP2 into rules."""

    def __init__(
        self,
        catalogue: CatalogueComponents,
        labels_language: str,
        excluded_languages: set[str],
    ) -> None:
        """Initialize the builder.

        Args:
            catalogue: Catalogued mods, which resolve the labels of other mods
            labels_language: Language of the literal messages of a TP2 declaring no LANGUAGE
            excluded_languages: Languages of the TP2 a definition cannot hold
        """
        self._catalogue = catalogue
        self._labels_language = labels_language
        self._excluded_languages = excluded_languages

    def build(self, tp2: WeiDUTp2, tp2_name: str) -> Tp2Rules:
        """Generate the rules of a parsed TP2.

        Args:
            tp2: Parsed TP2 whose components carry their requirements
            tp2_name: Mod the TP2 belongs to

        Returns:
            Rules in order of first appearance of their requirement
        """
        mod = normalize_tp2_name(tp2_name)
        if not MOD_ID_PATTERN.match(mod):
            logger.warning(f"{tp2_name}: TP2 rules skipped, {mod!r} is not a mod identifier")
            return Tp2Rules()

        shared = self._share_requirements(tp2, mod)

        dependencies: list[GeneratedRule] = []
        incompatibilities: list[GeneratedRule] = []
        for requirement, components in shared.items():
            rule = self._rule(mod, components, requirement)
            if requirement.kind == RequirementKind.FORBIDDEN:
                incompatibilities.append(rule)
            else:
                dependencies.append(rule)

        logger.debug(
            f"{tp2_name}: {len(dependencies)} dependency and "
            f"{len(incompatibilities)} incompatibility rules from the TP2"
        )
        return Tp2Rules(tuple(dependencies), tuple(incompatibilities))

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
                    requirement.kind, alternatives, self._translations(requirement)
                )
                components = shared.setdefault(key, [])
                if component.designated not in components:
                    components.append(component.designated)

        return shared

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
    def _rule(
        mod: str, components: list[str], requirement: _SharedRequirement
    ) -> GeneratedRule:
        """Write the rule of the components sharing a requirement."""
        targets_by_mod: dict[str, list[str]] = {}
        for target in requirement.alternatives:
            targets_by_mod.setdefault(target.tp2, []).append(target.component or "")

        targets = "|".join(
            f"{target_mod}({','.join(numbers)})"
            for target_mod, numbers in targets_by_mod.items()
        )
        return GeneratedRule(
            rule=f"{mod}({','.join(components)}):{targets}",
            severity=REQUIREMENT_SEVERITIES[requirement.kind],
            translations=requirement.translations,
            optional=requirement.kind == RequirementKind.OPTIONAL,
        )
