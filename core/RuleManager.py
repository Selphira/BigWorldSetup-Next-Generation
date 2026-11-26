from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum
from graphlib import CycleError, TopologicalSorter
import json
import logging
from pathlib import Path
from typing import Any, Protocol

from constants import (
    CACHE_DIR,
    CUSTOM_MODS_DIR,
    OPTIONAL_DEPENDENCY_DESCRIPTION,
    RULE_CACHE_LOCALIZATION_PROGRESS,
    RULE_CACHE_PREFIX,
    RULE_ORIGIN_DESCRIPTION_KEYS,
)
from core.ComponentReference import ComponentReference, IndexManager
from core.LocalizedCache import (
    CacheGroups,
    LocalizedCacheBuilder,
    LocalizedCacheManager,
    first_translation,
)
from core.ModManager import ModManager, select_mod_files
from core.Rules import (
    ComponentGroup,
    DependencyMode,
    DependencyRule,
    IncompatibilityRule,
    OrderDirection,
    OrderRule,
    Rule,
    RuleViolation,
)
from core.TranslationManager import SUPPORTED_LANGUAGES, get_translator, tr

logger = logging.getLogger(__name__)

# Rule types, each read from "<rule type>.json" in the rules directory and
# from the matching list of the "rules" section of mod definitions
RULE_TYPES = ("dependencies", "incompatibilities", "order")


def rule_source_file(rules_dir: Path, rule_type: str) -> Path:
    """Return the source file of a rule type."""
    return rules_dir / f"{rule_type}.json"


@dataclass(frozen=True, slots=True)
class RuleSources:
    """Locations of the rules: rule files and mod definitions."""

    rules_dir: Path
    mods_dir: Path
    custom_mods_dir: Path

    def rule_files(self) -> list[Path]:
        """Return the existing rule files."""
        return [
            source_file
            for rule_type in RULE_TYPES
            if (source_file := rule_source_file(self.rules_dir, rule_type)).exists()
        ]

    def mod_files(self) -> list[Path]:
        """Return the mod definition files read by the application."""
        return [path for path, _ in select_mod_files(self.mods_dir, self.custom_mods_dir)]


class GroupOperator(Enum):
    """Operator within a side expression."""

    AND = "and"
    OR = "or"


@dataclass(frozen=True, slots=True)
class ComponentSet:
    """A set of components from a single mod."""

    mod_id: str
    component_keys: tuple[str, ...] | None

    def get_references(self) -> tuple[ComponentReference, ...]:
        """Convert to ComponentReferences."""
        if self.component_keys is None:
            return (ComponentReference(self.mod_id, "*"),)
        return tuple(ComponentReference(self.mod_id, key) for key in self.component_keys)

    @classmethod
    def parse(cls, text: str) -> ComponentSet:
        """Parse 'ModID(1,2,...)' or 'ModID(-)'."""
        text = text.strip()

        mod_id, components_str = text.split("(", 1)
        mod_id = mod_id.strip()
        components_str = components_str.rstrip(")").strip()

        if components_str == "-":
            return cls(mod_id=mod_id, component_keys=None)

        comp_keys = tuple(c.strip() for c in components_str.split(",") if c.strip())

        return cls(mod_id=mod_id, component_keys=comp_keys)


@dataclass(frozen=True, slots=True)
class SideExpression:
    """A side expression like 'ModA(1,2)&ModB(3)' or 'ModC(-)|ModD(5,6)'."""

    component_sets: tuple[ComponentSet, ...]
    operator: GroupOperator

    def to_groups_format(
        self, set_operator: DependencyMode = DependencyMode.ANY
    ) -> list[dict[str, Any]]:
        """Convert to source_groups/target_groups format.

        Args:
            set_operator: How the components of each mod of an "&" side are
                required; an "|" side always needs any of its components
        """
        if self.operator == GroupOperator.OR:
            all_refs = [
                ref for comp_set in self.component_sets for ref in comp_set.get_references()
            ]
            return [{"components": [str(ref) for ref in all_refs], "operator": "any"}]

        return [
            {
                "components": [str(ref) for ref in comp_set.get_references()],
                "operator": set_operator.value,
            }
            for comp_set in self.component_sets
        ]

    def inject_into_rule(
        self,
        rule: dict[str, Any],
        key: str,
        set_operator: DependencyMode = DependencyMode.ANY,
    ) -> None:
        use_groups = self.operator == GroupOperator.AND or len(self.component_sets) > 1

        if use_groups:
            rule[f"{key}_groups"] = self.to_groups_format(set_operator)
        else:
            rule[key] = [str(ref) for ref in self.get_all_references()]

    def get_all_references(self) -> tuple[ComponentReference, ...]:
        """Get all component references in this expression."""
        all_refs = []
        for comp_set in self.component_sets:
            all_refs.extend(comp_set.get_references())
        return tuple(all_refs)

    @classmethod
    def parse(cls, text: str) -> SideExpression:
        """Parse a side expression like 'ModA(1,2)&ModB(3)' or 'ModC(-)|ModD(5,6)'."""
        text = text.strip()

        has_and = "&" in text
        has_or = "|" in text

        if has_and and has_or:
            raise ValueError(f"Cannot mix & and | in same side: '{text}'")

        if has_and:
            operator, delimiter = GroupOperator.AND, "&"
        elif has_or:
            operator, delimiter = GroupOperator.OR, "|"
        else:
            operator, delimiter = GroupOperator.OR, None

        parts = text.split(delimiter) if delimiter else [text]

        component_sets = []
        for part in parts:
            part = part.strip()
            if part:
                component_sets.append(ComponentSet.parse(part))

        return cls(component_sets=tuple(component_sets), operator=operator)


@dataclass(frozen=True, slots=True)
class RuleExpression:
    """Parsed compact rule notation."""

    sides: tuple[SideExpression, ...]

    @classmethod
    def parse(cls, compact_str: str) -> RuleExpression:
        """Parse rule expression."""
        sides = tuple(
            SideExpression.parse(side.strip())
            for side in compact_str.split(":")
            if side.strip()
        )

        if len(sides) < 2:
            raise ValueError("Need at least 2 sides (source:target)")

        return cls(sides=sides)

    def to_incompatibility_rules(
        self,
        base_rule: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Expand to incompatibility rules."""
        rules = []

        for i in range(len(self.sides)):
            for j in range(i + 1, len(self.sides)):
                source_side = self.sides[i]
                target_side = self.sides[j]

                for source, target in ((source_side, target_side), (target_side, source_side)):
                    rule = base_rule.copy()

                    source.inject_into_rule(rule, "source")
                    target.inject_into_rule(rule, "target")

                    rules.append(rule)

        return rules

    def to_dependency_rule(
        self,
        rule: dict[str, Any],
    ) -> dict[str, Any]:
        """Expand to dependency rule.

        The mode of the rule also applies to the components of each mod of an
        "&" target side: "a(1,2)&b(3)" in mode "all" needs a(1), a(2) and b(3).
        """
        if len(self.sides) != 2:
            raise ValueError("Dependency rules must have exactly 2 sides (source:target)")

        source_side = self.sides[0]
        target_side = self.sides[1]

        source_side.inject_into_rule(rule, "source")
        target_side.inject_into_rule(
            rule, "target", DependencyMode(rule.get("mode", DependencyMode.ANY.value))
        )

        return rule

    def to_order_rule(
        self,
        rule: dict[str, Any],
    ) -> dict[str, Any]:
        """Expand to order rule."""
        if len(self.sides) != 2:
            raise ValueError("Order rules must have exactly 2 sides (source:target)")

        source_side = self.sides[0]
        target_side = self.sides[1]

        source_side.inject_into_rule(rule, "source")
        target_side.inject_into_rule(rule, "target")

        return rule


@dataclass
class SuggestedOrder:
    components: list[ComponentReference]
    cycle: tuple[ComponentReference, ...] = ()


@dataclass(frozen=True, slots=True)
class RuleIndexes:
    """Lookups derived from the rules and the catalogue, built aside then published together."""

    rules_by_component: dict[ComponentReference, set[Rule]]
    resolved_wildcards: dict[
        int, tuple[frozenset[ComponentReference], frozenset[ComponentReference]]
    ]
    dependents_by_target: dict[ComponentReference, set[DependencyRule]]
    rule_positions: dict[Rule, int]
    dependencies_by_source: dict[ComponentReference, list[DependencyRule]]


@dataclass(frozen=True, slots=True)
class OrderContext:
    """What an order validation depends on besides the sequence itself."""

    revision: int
    earlier: frozenset[ComponentReference]
    later: frozenset[ComponentReference]
    # The whole selection, SUB answers included: ordering requirements are conditioned on it
    selection: frozenset[ComponentReference]


@dataclass
class ValidationState:
    """Order findings of one sequence, valid for the context they were computed in."""

    context: OrderContext | None = None

    positions: dict[ComponentReference, int] = field(default_factory=dict)

    violations_by_rule: dict[int, list[RuleViolation]] = field(
        default_factory=lambda: defaultdict(list)
    )

    active_components: set[ComponentReference] = field(default_factory=set)

    def update_positions(self, order: list[ComponentReference]) -> None:
        """Update positions AND active components set."""
        self.positions = {ref: idx for idx, ref in enumerate(order)}
        self.active_components = set(order)


class RuleCacheBuilderThread(LocalizedCacheBuilder):
    """Thread for building rule cache without blocking UI."""

    cache_prefix = RULE_CACHE_PREFIX
    cache_label = "rule cache"
    localization_progress_start = RULE_CACHE_LOCALIZATION_PROGRESS

    def __init__(self, sources: RuleSources, cache_dir: Path, languages: list[str]) -> None:
        """
        Initialize rule cache builder thread.

        Args:
            sources: Rule files and mod definitions to read rules from
            cache_dir: Directory for cache output
            languages: Languages to build cache for
        """
        super().__init__(cache_dir, languages)
        self.sources = sources

    def _load_sources(self) -> CacheGroups:
        """Load and expand the rules of each type; a missing or invalid file gives no rules."""
        self.status_changed.emit(tr("app.loading_rules"))
        raw_rules = {
            rule_type: self._read_rule_file(rule_source_file(self.sources.rules_dir, rule_type))
            for rule_type in RULE_TYPES
        }
        for mod_file in self.sources.mod_files():
            for rule_type, mod_rules in self._read_mod_rules(mod_file).items():
                raw_rules[rule_type].extend(mod_rules)

        self.status_changed.emit(tr("app.parsing_rules"))
        source_data = {
            rule_type: self._expand_rules(rule_type, rules)
            for rule_type, rules in raw_rules.items()
        }

        if not any(source_data.values()):
            logger.warning("No rules found in source files")

        return source_data

    def _localize_entry(self, entry: dict[str, Any], language: str) -> dict[str, Any]:
        return self._localize_rule(entry, language)

    def _read_rule_file(self, file_path: Path) -> list[dict[str, Any]]:
        """Read the rules of a rule file."""
        if not file_path.exists():
            logger.warning(f"Rule file not found: {file_path}")
            return []

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return list(json.load(f).get("rules", []))
        except Exception as e:
            self._report_skipped_source(f"Error loading {file_path.name}: {e}")
            return []

    def _read_mod_rules(self, mod_file: Path) -> dict[str, list[dict[str, Any]]]:
        """Read the "rules" section of a mod definition, by rule type."""
        try:
            with open(mod_file, "r", encoding="utf-8") as f:
                definition = json.load(f)

            section = definition.get("rules") or {}
            mod_name = definition.get("name") or mod_file.stem
            return {
                rule_type: [
                    self._describe_generated_rule(rule, rule_type, mod_name)
                    for rule in section.get(rule_type) or []
                ]
                for rule_type in RULE_TYPES
            }
        except Exception as e:
            self._report_skipped_source(f"Error loading rules of {mod_file.name}: {e}")
            return {}

    @staticmethod
    def _describe_generated_rule(rule: Any, rule_type: str, mod_name: str) -> Any:
        """
        Give a rule generated by a tool the description of its origin.

        The texts the rule carries are kept: the description of its origin only
        fills the languages it has no text in.
        """
        if not isinstance(rule, dict):
            return rule

        description_keys = RULE_ORIGIN_DESCRIPTION_KEYS.get(rule.get("origin"), {})
        is_optional = rule_type == "dependencies" and rule.get("optional")
        description_key = description_keys.get(
            OPTIONAL_DEPENDENCY_DESCRIPTION if is_optional else rule_type
        )
        if description_key is None:
            return rule

        stored_texts = {
            code: text for code, text in (rule.get("translations") or {}).items() if text
        }
        origin_texts = get_translator().get_all_languages(description_key, mod=mod_name)
        return {**rule, "translations": {**origin_texts, **stored_texts}}

    @staticmethod
    def _expand_rules(rule_type: str, raw_rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Expand each rule expression into cacheable rules."""
        expanded_rules = []
        for rule in raw_rules:
            try:
                expression = RuleExpression.parse(rule["rule"])

                if rule_type == "incompatibilities":
                    expanded_rules.extend(expression.to_incompatibility_rules(rule))

                elif rule_type == "dependencies":
                    expanded_rules.append(expression.to_dependency_rule(rule))

                elif rule_type == "order":
                    expanded_rules.append(expression.to_order_rule(rule))

            except Exception as e:
                logger.error(f"Error expanding rule '{rule.get('rule')}': {e}")

        return expanded_rules

    @staticmethod
    def _localize_rule(rule: dict[str, Any], target_lang: str) -> dict[str, Any]:
        """
        Create localized version of rule for target language.

        Applies fallback system:
        target_language → other languages → empty string

        Args:
            rule: Rule data dictionary
            target_lang: Target language code

        Returns:
            Localized rule dictionary
        """
        result = rule.copy()
        translations = rule.get("translations", {})

        # Remove translations from final output
        result.pop("translations", None)

        if not translations:
            # No translations available, keep existing description or empty
            if "description" not in result:
                result["description"] = ""
            return result

        result["description"] = first_translation(translations, target_lang, lambda text: text)
        return result


class ConditionEvaluator(Protocol):
    """Protocol for evaluating source/target conditions."""

    def is_satisfied(self, selected_set: set[ComponentReference]) -> bool:
        """Check if condition is satisfied."""
        ...

    def get_missing(self, selected_set: set[ComponentReference]) -> list[ComponentReference]:
        """Get missing components."""
        ...

    def get_matching(self, selected_set: set[ComponentReference]) -> list[ComponentReference]:
        """Get matching components."""
        ...


class StandardCondition:
    """Evaluates standard source/target."""

    __slots__ = ("components", "mode")

    def __init__(self, components: tuple[ComponentReference, ...], mode: DependencyMode):
        self.components = components
        self.mode = mode

    def is_satisfied(self, selected_set: set[ComponentReference]) -> bool:
        """Check if condition is met."""
        matches = (comp.is_selected_in(selected_set) for comp in self.components)

        if self.mode == DependencyMode.ALL:
            return all(matches)
        return any(matches)

    def get_missing(self, selected_set: set[ComponentReference]) -> list[ComponentReference]:
        """Get components that don't match."""
        return [comp for comp in self.components if not comp.is_selected_in(selected_set)]

    def get_matching(self, selected_set: set[ComponentReference]) -> list[ComponentReference]:
        """Get components that match."""
        return [comp for comp in self.components if comp.is_selected_in(selected_set)]


class GroupCondition:
    """Evaluates component groups (all groups must be satisfied)."""

    __slots__ = ("groups",)

    def __init__(self, groups: tuple[ComponentGroup, ...]):
        self.groups = groups

    def is_satisfied(self, selected_set: set[ComponentReference]) -> bool:
        """All groups must be satisfied."""
        return all(group.matches(selected_set) for group in self.groups)

    def get_missing(self, selected_set: set[ComponentReference]) -> list[ComponentReference]:
        """Get missing components from unsatisfied groups."""
        missing = []
        for group in self.groups:
            if not group.matches(selected_set):
                missing.extend(group.get_missing_components(selected_set))
        return missing

    def get_matching(self, selected_set: set[ComponentReference]) -> list[ComponentReference]:
        """Get components that match."""
        matching = []
        for group in self.groups:
            if group.matches(selected_set):
                matching.extend(group.get_matched_components(selected_set))
        return matching


class TrivialCondition:
    """Trivial condition evaluator (for optimization)."""

    __slots__ = ("_result",)

    def __init__(self, result: bool):
        self._result = result

    def is_satisfied(self, selected_set: set[ComponentReference]) -> bool:
        return self._result

    @staticmethod
    def get_missing(selected_set: set[ComponentReference]) -> list[ComponentReference]:
        return []

    @staticmethod
    def get_matching(selected_set: set[ComponentReference]) -> list[ComponentReference]:
        return []


# ===================================================================
# Rule Manager
# ===================================================================


class RuleManager(LocalizedCacheManager):
    """Manages rules."""

    cache_prefix = RULE_CACHE_PREFIX
    cache_label = "rule cache"

    def __init__(
        self,
        mod_manager: ModManager,
        rules_dir: Path,
        *,
        mods_dir: Path,
        cache_dir: Path = CACHE_DIR,
        custom_mods_dir: Path = CUSTOM_MODS_DIR,
    ) -> None:
        """Initialize rule manager."""
        super().__init__(cache_dir)

        self.sources = RuleSources(rules_dir, mods_dir, custom_mods_dir)

        self._mod_manager = mod_manager
        self._current_game_id: str | None = None
        self._validation_states: dict[tuple[str, int], ValidationState] = {}
        self.translator = get_translator()
        self.current_language = self.translator.current_language
        # In-memory counter, incremented each time rules or their indexes are published
        self.revision: int = 0

        # Rule storage
        self._all_rules: list[Rule] = []
        self._dependency_rules: list[DependencyRule] = []
        self._incompatibility_rules: list[IncompatibilityRule] = []
        self._order_rules: list[OrderRule] = []

        # Indexes
        self._indexes = IndexManager.get_indexes()
        self._rules_by_component: dict[ComponentReference, set[Rule]] = defaultdict(set)
        self._resolved_wildcards_cache: dict[
            int, tuple[frozenset[ComponentReference], frozenset[ComponentReference]]
        ] = {}

        self._dependents_by_target: dict[ComponentReference, set[DependencyRule]] = defaultdict(
            set
        )
        self._rule_positions: dict[Rule, int] = {}
        self._dependencies_by_source: dict[ComponentReference, list[DependencyRule]] = (
            defaultdict(list)
        )
        self._last_selection_key: tuple[int, frozenset[ComponentReference]] | None = None

    # ========================================
    # CACHE MANAGEMENT
    # ========================================

    def needs_cache_rebuild(self) -> bool:
        """Check if cache needs to be rebuilt; without any source file the cache is kept.

        Mod definitions are sources too: their "rules" section feeds the cache.
        """
        if self._is_cache_stale():
            return True

        if not self.sources.rule_files():
            logger.warning("No source rule files found")

        return False

    def _create_cache_builder(self, languages: list[str]) -> RuleCacheBuilderThread:
        return RuleCacheBuilderThread(self.sources, self.cache_dir, languages)

    def _source_files(self) -> list[Path]:
        return self.sources.rule_files() + self.sources.mod_files()

    def load_cache(self) -> bool:
        """Load cached rules for current language."""
        return self._load_language(self.current_language)

    def reload_for_language(self, language: str) -> bool:
        """Reload cache for a new language, which becomes current only once loaded."""
        if not any(lang == language for lang, _ in SUPPORTED_LANGUAGES):
            logger.warning(f"Unsupported language: {language}")
            return False

        if self.current_language == language:
            return True

        if not self._load_language(language):
            return False

        self.current_language = language
        return True

    def rebuild_indexes(self) -> None:
        """Index the loaded rules again against the current catalogue.

        Needed once the catalogue changed without a rule reload, so that the
        wildcards cover an added mod. Published validations are withdrawn.
        """
        self._publish_indexes(self._index_rules(self._all_rules))

    def _load_language(self, language: str) -> bool:
        """Load and publish the rules of a language; a failure keeps the loaded ones.

        An invalid rule is skipped with a diagnostic and does not fail the load.
        """
        cache_file = self._cache_file(language)

        if not cache_file.exists():
            logger.error(f"Rule cache file not found: {cache_file}")
            return False

        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            dependency_rules = self._load_rules_from_cache(
                data.get("dependencies", []), DependencyRule
            )
            incompatibility_rules = self._load_rules_from_cache(
                data.get("incompatibilities", []), IncompatibilityRule
            )
            order_rules = self._load_rules_from_cache(data.get("order", []), OrderRule)
            all_rules: list[Rule] = [*dependency_rules, *incompatibility_rules, *order_rules]

            indexes = self._index_rules(all_rules)

        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in rule cache: {e}")
            return False
        except Exception as e:
            logger.exception(f"Error loading rule cache: {e}")
            return False

        self._dependency_rules = dependency_rules
        self._incompatibility_rules = incompatibility_rules
        self._order_rules = order_rules
        self._all_rules = all_rules
        self._publish_indexes(indexes)

        logger.info(
            "Loaded rules for %s: %d dependencies (%d with groups), %d incompatibilities, %d order rules",
            language,
            len(dependency_rules),
            sum(1 for rule in dependency_rules if rule.uses_groups()),
            len(incompatibility_rules),
            len(order_rules),
        )
        return True

    @staticmethod
    def _load_rules_from_cache[RuleT: Rule](
        rules_data: list[dict[str, Any]], cls: type[RuleT]
    ) -> list[RuleT]:
        """Read the rules of one type; an invalid rule is skipped."""
        rules: list[RuleT] = []
        error_count = 0

        for rule_data in rules_data:
            try:
                rules.append(cls.from_dict(rule_data))
            except (ValueError, KeyError, TypeError) as e:
                error_count += 1
                logger.error(f"Failed to load rule '{rule_data}': {e}. Rule skipped.")
            except Exception as e:
                error_count += 1
                logger.error(
                    f"Unexpected error loading rule '{rule_data}': {e}. Rule skipped.",
                    exc_info=True,
                )

        logger.debug(f"{len(rules)} rules loaded from cache")
        if error_count > 0:
            logger.warning(f"Skipped {error_count} invalid {cls.__name__}(s)")

        return rules

    def _get_all_known_components(
        self,
    ) -> tuple[set[ComponentReference], dict[str, set[ComponentReference]]]:
        """Retrieve all components from all known mods."""
        all_components = set()
        components_by_mod: dict[str, set[ComponentReference]] = defaultdict(set)

        for mod in self._mod_manager.get_all_mods().values():
            for comp_ref in ComponentReference.from_string_list(list(mod.get_component_refs())):
                all_components.add(comp_ref)
                components_by_mod[comp_ref.mod_id].add(comp_ref)

        return all_components, components_by_mod

    @staticmethod
    def _resolve_refs_globally(
        refs: tuple[ComponentReference, ...],
        by_mod: dict[str, set[ComponentReference]],
    ) -> set[ComponentReference]:
        """Resolve references with all known components."""
        result = set()

        for ref in refs:
            if ref.is_mod():
                result.update(by_mod.get(ref.mod_id, set()))
            else:
                result.add(ref)

        return result

    def _index_rules(self, rules: list[Rule]) -> RuleIndexes:
        """Build the lookups of rules against the current catalogue, without publishing them."""
        _, components_by_mod = self._get_all_known_components()

        rules_by_component: dict[ComponentReference, set[Rule]] = defaultdict(set)
        resolved_wildcards: dict[
            int, tuple[frozenset[ComponentReference], frozenset[ComponentReference]]
        ] = {}
        dependents_by_target: dict[ComponentReference, set[DependencyRule]] = defaultdict(set)
        dependencies_by_source: dict[ComponentReference, list[DependencyRule]] = defaultdict(
            list
        )

        for rule in rules:
            if isinstance(rule, DependencyRule):
                for target in rule.targets:
                    dependents_by_target[target].add(rule)
                for source in dict.fromkeys(rule.sources):
                    dependencies_by_source[source].append(rule)

            sources = self._resolve_refs_globally(rule.sources, components_by_mod)
            targets = self._resolve_refs_globally(rule.targets, components_by_mod)
            resolved_wildcards[id(rule)] = (frozenset(sources), frozenset(targets))

            for ref in (*rule.sources, *rule.targets, *sources, *targets):
                rules_by_component[ref].add(rule)

        return RuleIndexes(
            rules_by_component=rules_by_component,
            resolved_wildcards=resolved_wildcards,
            dependents_by_target=dependents_by_target,
            rule_positions={rule: idx for idx, rule in enumerate(rules)},
            dependencies_by_source=dependencies_by_source,
        )

    def _publish_indexes(self, indexes: RuleIndexes) -> None:
        """Publish lookups and withdraw validations computed with the previous ones."""
        self._rules_by_component = indexes.rules_by_component
        self._resolved_wildcards_cache = indexes.resolved_wildcards
        self._dependents_by_target = indexes.dependents_by_target
        self._rule_positions = indexes.rule_positions
        self._dependencies_by_source = indexes.dependencies_by_source
        self.revision += 1

        self._validation_states.clear()
        self._last_selection_key = None
        self._indexes.clear_selection_violations()
        self._indexes.clear_order_violations()

    # ========================================
    # SELECTION VALIDATION
    # ========================================

    def validate_selection(
        self, selected_components: list[ComponentReference]
    ) -> list[RuleViolation]:
        """Validate component selection against dependency and incompatibility rules."""
        references = [reference for reference in selected_components if not reference.is_mod()]
        selection_key = (self.revision, frozenset(references))
        if self._last_selection_key == selection_key:
            return self._get_all_cached_selection_violations()

        self._indexes.clear_selection_violations()
        self._last_selection_key = selection_key

        violations = self.selection_violations(set(references))
        for violation in violations:
            self._indexes.add_selection_violation(violation)
        return violations

    def selection_violations(self, selection: set[ComponentReference]) -> list[RuleViolation]:
        """Evaluate a candidate selection without publishing it to the UI."""
        selected = {ref for ref in selection if ref.is_component() or ref.is_sub_option()}
        violations = []
        for ref in sorted(selected, key=str):
            for rule in self.get_rules_for_component(ref):
                violation = self._check_rule(rule, ref, selected)
                if violation:
                    violations.append(violation)
        return violations

    def _get_all_cached_selection_violations(self) -> list[RuleViolation]:
        """Extract all violations from cache as flat list."""
        all_violations = []
        seen_violations = set()

        for violations in self._indexes.selection_violation_index.values():
            for violation in violations:
                # Use id() to avoid duplicates (same violation object)
                if id(violation) not in seen_violations:
                    all_violations.append(violation)
                    seen_violations.add(id(violation))

        return all_violations

    def _check_rule(
        self, rule: Rule, source_ref: ComponentReference, selected_set: set[ComponentReference]
    ) -> RuleViolation | None:
        if isinstance(rule, DependencyRule):
            return self._check_dependency(rule, source_ref, selected_set)
        if isinstance(rule, IncompatibilityRule):
            return self._check_incompatibility(rule, source_ref, selected_set)
        return None

    def _check_dependency(
        self,
        rule: DependencyRule,
        source_ref: ComponentReference,
        selected_set: set[ComponentReference],
    ) -> RuleViolation | None:
        """Check DependencyRule: supports ALL and ANY modes."""
        if rule.optional:
            # An absent optional target is normal: only the order of selected ones matters.
            return None

        source_evaluator = self._create_source_evaluator(rule, source_ref)

        if not source_evaluator.is_satisfied(selected_set):
            return None

        target_evaluator = self._create_target_evaluator(rule)

        if target_evaluator.is_satisfied(selected_set):
            return None

        missing = target_evaluator.get_missing(selected_set)
        affected = (source_ref,) + tuple(missing)

        return RuleViolation(rule=rule, affected_components=affected)

    def _create_source_evaluator(
        self, rule: DependencyRule | IncompatibilityRule, source_ref: ComponentReference
    ) -> ConditionEvaluator:
        """Factory method: create appropriate source evaluator."""
        if not any(source.covers(source_ref) for source in rule.sources):
            return TrivialCondition(False)
        if rule.source_groups:
            return GroupCondition(rule.source_groups)

        return TrivialCondition(True)

    def _create_target_evaluator(
        self, rule: DependencyRule | IncompatibilityRule
    ) -> ConditionEvaluator:
        """Factory method: create appropriate target evaluator."""
        if rule.target_groups:
            return GroupCondition(rule.target_groups)

        return StandardCondition(
            components=rule.targets,
            mode=rule.dependency_mode
            if isinstance(rule, DependencyRule)
            else DependencyMode.ANY,
        )

    def _create_order_evaluator(
        self, rule: DependencyRule, selection: set[ComponentReference]
    ) -> ConditionEvaluator:
        """Evaluate the targets that must precede the sources of a dependency.

        An optional dependency is reduced to its selected targets: with none selected,
        nothing has to come first.
        """
        if not rule.optional:
            return self._create_target_evaluator(rule)

        groups = rule.target_groups or (ComponentGroup(rule.targets, rule.dependency_mode),)
        selected_groups = tuple(
            ComponentGroup(selected, group.operator)
            for group in groups
            if (selected := tuple(group.get_matched_components(selection)))
        )
        if not selected_groups:
            return TrivialCondition(True)
        return GroupCondition(selected_groups)

    def _check_incompatibility(
        self,
        rule: IncompatibilityRule,
        source_ref: ComponentReference,
        selected_set: set[ComponentReference],
    ) -> RuleViolation | None:
        """Check incompatibility: collect conflicting selected components."""
        source_evaluator = self._create_source_evaluator(rule, source_ref)
        if not source_evaluator.is_satisfied(selected_set):
            return None

        target_evaluator = self._create_target_evaluator(rule)
        if not target_evaluator.is_satisfied(selected_set):
            return None

        conflicts = target_evaluator.get_matching(selected_set)
        affected = (source_ref,) + tuple(conflicts)
        return RuleViolation(rule=rule, affected_components=affected)

    # ========================================
    # ORDER VALIDATION
    # ========================================

    def validate_order(
        self,
        game_id: str,
        seq_idx: int,
        install_order: list[ComponentReference],
        moved_components: set[ComponentReference] | None = None,
        earlier: set[ComponentReference] | None = None,
        later: set[ComponentReference] | None = None,
        selection: set[ComponentReference] | None = None,
    ) -> list[RuleViolation]:
        """Validate order with unified ultra-fast algorithm."""
        if self._current_game_id != game_id:
            self._clear_game_states(self._current_game_id)
            self._current_game_id = game_id

        earlier = earlier or set()
        later = later or set()
        selection = selection if selection is not None else earlier | set(install_order)
        choices = self._selected_options(selection)
        earlier = earlier | {option for base in earlier for option in choices.get(base, ())}
        context = OrderContext(
            self.revision, frozenset(earlier), frozenset(later), frozenset(selection)
        )

        # Only moves are incremental: any other change of context needs a full validation.
        state_key = (game_id, seq_idx)
        state = self._validation_states.get(state_key)
        full_validation = state is None or not moved_components or state.context != context
        if state is None:
            state = ValidationState()
            self._validation_states[state_key] = state
        state.context = context
        state.update_positions(install_order)

        changed = install_order if full_validation else moved_components
        affected_rules = set()
        for ref in changed:
            affected_rules.update(self.get_rules_for_component(ref))
            for option in choices.get(ref, ()):
                affected_rules.update(self.get_rules_for_component(option))

        # Withdraw this sequence's previous findings before publishing replacements.
        obsolete = (
            list(state.violations_by_rule) if full_validation else map(id, affected_rules)
        )
        self._withdraw_order_violations(state, obsolete)

        for rule in affected_rules:
            rule_id = id(rule)

            resolved = self._resolved_wildcards_cache.get(rule_id)
            if not resolved:
                continue

            all_sources, all_targets = resolved

            active = state.active_components
            sources = active & all_sources
            targets = all_targets & active

            if isinstance(rule, DependencyRule) and rule.implicit_order:
                sources.update(
                    option.get_base_component_reference()
                    for option in all_sources & selection
                    if option.is_sub_option()
                    and option.get_base_component_reference() in active
                )
                for src in sources:
                    if rule not in self.ordering_requirements(
                        src, selection, choices.get(src, set())
                    ):
                        continue
                    preceding = earlier | set(install_order[: state.positions[src]])
                    preceding.update(
                        option for base in tuple(preceding) for option in choices.get(base, ())
                    )
                    evaluator = self._create_order_evaluator(rule, selection)
                    if evaluator.is_satisfied(preceding):
                        continue
                    missing = tuple(evaluator.get_missing(preceding))
                    violation = RuleViolation(rule=rule, affected_components=(src, *missing))
                    state.violations_by_rule[rule_id].append(violation)
                    self._indexes.add_order_violation(violation)
                continue
            if not isinstance(rule, OrderRule):
                continue
            direction = rule.order_direction
            outside = earlier if direction == OrderDirection.BEFORE else later
            for source in sources:
                for target in all_targets & outside:
                    if source != target:
                        violation = RuleViolation(rule, (source, target))
                        state.violations_by_rule[rule_id].append(violation)
                        self._indexes.add_order_violation(violation)

            positions = state.positions
            sources_with_pos = [(src, positions[src]) for src in sources if src in positions]
            targets_with_pos = [(tgt, positions[tgt]) for tgt in targets if tgt in positions]

            for src, src_pos in sources_with_pos:
                for tgt, tgt_pos in targets_with_pos:
                    if src == tgt:
                        continue

                    if self._check_order_violation(src_pos, tgt_pos, direction):
                        violation = RuleViolation(
                            rule=rule,
                            affected_components=(src, tgt),
                        )

                        state.violations_by_rule[rule_id].append(violation)
                        self._indexes.add_order_violation(violation)

        return [v for violations in state.violations_by_rule.values() for v in violations]

    @staticmethod
    def _check_order_violation(
        source_pos: int,
        target_pos: int,
        direction: OrderDirection,
    ) -> bool:
        """Check if there is an order violation."""
        if direction == OrderDirection.BEFORE:
            return source_pos > target_pos
        return source_pos < target_pos

    def _withdraw_order_violations(
        self, state: ValidationState, rule_ids: Iterable[int]
    ) -> None:
        """Forget a sequence's findings for these rules, in the state and in the shared index."""
        for rule_id in rule_ids:
            for violation in state.violations_by_rule.pop(rule_id, []):
                for ref in violation.affected_components:
                    entries = self._indexes.order_violation_index.get(ref, [])
                    entries[:] = [entry for entry in entries if entry is not violation]

    def _clear_game_states(self, game_id: str | None) -> None:
        """Clean up validation states for a specific game, withdrawing their published findings."""
        if game_id is None:
            return

        keys_to_remove = [key for key in self._validation_states if key[0] == game_id]
        for key in keys_to_remove:
            state = self._validation_states.pop(key)
            self._withdraw_order_violations(state, list(state.violations_by_rule))

        logger.debug(f"Cleared {len(keys_to_remove)} validation states for game {game_id}")

    # ========================================
    # PUBLIC API
    # ========================================

    def get_rules_for_component(self, reference: ComponentReference) -> list[Rule]:
        """Get presentation rules in catalogue order for either side of a reference."""
        return sorted(
            self._rules_by_component.get(reference, ()),
            key=lambda rule: self._rule_positions[rule],
        )

    def get_selection_violations(self, reference: ComponentReference) -> list[RuleViolation]:
        """Get cached selection violations for a specific component."""
        return self._indexes.get_selection_violations(reference)

    def get_order_violations(self, reference: ComponentReference) -> list[RuleViolation]:
        """Get cached order violations for a specific component."""
        return self._indexes.get_order_violations(reference)

    def get_count(self) -> int:
        """Return total number of rules."""
        return len(self._all_rules)

    def get_requirements(self, reference: ComponentReference) -> tuple[DependencyRule, ...]:
        """Return outgoing expressions, including rules applying to the entire mod."""
        wildcard = ComponentReference.for_mod(reference.mod_id)
        return tuple(
            dict.fromkeys(
                self._dependencies_by_source.get(reference, [])
                + self._dependencies_by_source.get(wildcard, [])
            )
        )

    def active_requirements(
        self,
        reference: ComponentReference,
        selection: set[ComponentReference],
        options: set[ComponentReference],
    ) -> set[DependencyRule]:
        """Return the mandatory prerequisites that the execution plan must keep.

        Optional dependencies are left out: they never block a component and must not
        change the identity of a plan or the grouping of its batches.
        """
        return {
            rule
            for rule in self.ordering_requirements(reference, selection, options)
            if not rule.optional
        }

    def ordering_requirements(
        self,
        reference: ComponentReference,
        selection: set[ComponentReference],
        options: set[ComponentReference],
    ) -> set[DependencyRule]:
        """Lift conditions on a selected SUB answer to its installable component.

        Optional dependencies are included: their selected targets must come first.
        """
        return {
            rule
            for source in {reference} | options
            for rule in self.get_requirements(source)
            if self._create_source_evaluator(rule, source).is_satisfied(selection)
        }

    def lost_requirement(
        self,
        reference: ComponentReference,
        selection: set[ComponentReference],
        expected: set[ComponentReference],
        available: set[ComponentReference],
        options: set[ComponentReference],
    ) -> bool:
        """Whether an active prerequisite lost its last preceding provider."""
        relevant = set()
        for lost in expected - available:
            relevant.update(self._dependents_by_target.get(lost, ()))
            relevant.update(
                self._dependents_by_target.get(ComponentReference.for_mod(lost.mod_id), ())
            )
        for rule in self.active_requirements(reference, selection, options) & relevant:
            target = self._create_target_evaluator(rule)
            if target.is_satisfied(expected) and not target.is_satisfied(available):
                return True
        return False

    def preferred_order(
        self,
        editorial: list[ComponentReference],
        earlier: set[ComponentReference],
        selection: set[ComponentReference],
    ) -> SuggestedOrder:
        """Schedule the earliest ready editorial entry, retaining unresolved cycles for review."""
        pending = list(dict.fromkeys(editorial))
        active = set(pending)
        before: dict[ComponentReference, set[ComponentReference]] = defaultdict(set)
        for rule in {
            rule
            for ref in active
            for rule in self.get_rules_for_component(ref)
            if isinstance(rule, OrderRule)
        }:
            sources, targets = self._resolved_wildcards_cache[id(rule)]
            for source in sources & active:
                for target in targets & active:
                    if source != target:
                        first, second = (
                            (source, target)
                            if rule.order_direction == OrderDirection.BEFORE
                            else (target, source)
                        )
                        before[second].add(first)
        choices = self._selected_options(selection)
        available = earlier | {option for base in earlier for option in choices.get(base, ())}
        ordered = []
        placed = set()
        while pending:
            ready = next(
                (
                    ref
                    for ref in pending
                    if before[ref] <= placed
                    and all(
                        self._create_order_evaluator(rule, selection).is_satisfied(available)
                        for rule in self.ordering_requirements(
                            ref, selection, choices.get(ref, set())
                        )
                        if rule.implicit_order
                    )
                ),
                None,
            )
            if ready is None:
                break
            ordered.append(ready)
            pending.remove(ready)
            placed.add(ready)
            available.add(ready)
            available.update(choices.get(ready, ()))
        cycle = ()
        if pending:
            remaining = set(pending)
            for ref in pending:
                for rule in self.ordering_requirements(ref, selection, choices.get(ref, set())):
                    if not rule.implicit_order:
                        continue
                    evaluator = self._create_order_evaluator(rule, selection)
                    if evaluator.is_satisfied(available):
                        continue
                    for missing in evaluator.get_missing(available):
                        before[ref].update(
                            candidate
                            for candidate in remaining
                            if missing.is_selected_in(
                                {candidate} | choices.get(candidate, set())
                            )
                        )
            try:
                TopologicalSorter(before).prepare()
            except CycleError as error:
                cycle = tuple(error.args[1])
        return SuggestedOrder(ordered + pending, cycle)

    @staticmethod
    def _selected_options(
        selection: set[ComponentReference],
    ) -> dict[ComponentReference, set[ComponentReference]]:
        provided: dict[ComponentReference, set[ComponentReference]] = defaultdict(set)
        for choice in selection:
            if choice.is_sub_option():
                provided[choice.get_base_component_reference()].add(choice)
        return provided

    def blocked_positions(
        self,
        sequences: list[list[ComponentReference]],
        unavailable: set[tuple[int, int]],
        completed_through: tuple[int, int],
        selection: set[ComponentReference],
    ) -> set[tuple[int, int]]:
        """Project losses in plan order, keeping repeated occurrences independent."""
        expected: set[ComponentReference] = set()
        available: set[ComponentReference] = set()
        provided = self._selected_options(selection)
        blocked = set()
        for seq_idx, sequence in enumerate(sequences):
            for idx, ref in enumerate(sequence):
                position = (seq_idx, idx)
                identities = {ref} | provided.get(ref, set())
                if position in unavailable:
                    expected.update(identities)
                    continue
                if position > completed_through and self.lost_requirement(
                    ref, selection, expected, available, provided.get(ref, set())
                ):
                    blocked.add(position)
                else:
                    available.update(identities)
                expected.update(identities)
        return blocked
