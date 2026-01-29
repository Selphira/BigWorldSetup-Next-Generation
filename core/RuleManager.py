from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
import json
import logging
from pathlib import Path
from typing import Any, Callable, Protocol

from PySide6.QtCore import QObject, QThread, Signal

from constants import CACHE_DIR, RULES_DIR
from core.ComponentReference import ComponentReference, IndexManager
from core.Rules import (
    ComponentGroup,
    DependencyMode,
    DependencyRule,
    IncompatibilityRule,
    OrderDirection,
    OrderRule,
    Rule,
    RuleType,
    RuleViolation,
)
from core.TranslationManager import SUPPORTED_LANGUAGES, get_translator, tr

logger = logging.getLogger(__name__)


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

    def to_groups_format(self) -> list[dict[str, Any]]:
        """Convert to source_groups/target_groups format."""
        if self.operator == GroupOperator.OR:
            all_refs = [
                ref for comp_set in self.component_sets for ref in comp_set.get_references()
            ]
            return [{"components": [str(ref) for ref in all_refs], "operator": "any"}]

        return [
            {"components": [str(ref) for ref in comp_set.get_references()], "operator": "any"}
            for comp_set in self.component_sets
        ]

    def inject_into_rule(self, rule: dict[str, Any], key: str) -> None:
        use_groups = self.operator == GroupOperator.AND or len(self.component_sets) > 1

        if use_groups:
            rule[f"{key}_groups"] = self.to_groups_format()
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
        """Expand to dependency rule."""
        if len(self.sides) != 2:
            raise ValueError("Dependency rules must have exactly 2 sides (source:target)")

        source_side = self.sides[0]
        target_side = self.sides[1]

        source_side.inject_into_rule(rule, "source")
        target_side.inject_into_rule(rule, "target")

        return rule

    def to_order_rule(
        self,
        rule: dict[str, Any],
    ) -> dict[str, Any]:
        """Expand to dependency rule."""
        if len(self.sides) != 2:
            raise ValueError("Order rules must have exactly 2 sides (source:target)")

        source_side = self.sides[0]
        target_side = self.sides[1]

        source_side.inject_into_rule(rule, "source")
        target_side.inject_into_rule(rule, "target")

        return rule


class RuleCacheBuilderThread(QThread):
    """Thread for building rule cache without blocking UI."""

    progress = Signal(int)  # Progress 0-100
    status_changed = Signal(str)  # Status message
    finished = Signal(bool)  # True if success, False if error
    error = Signal(str)  # Error message

    def __init__(self, rules_dir: Path, cache_dir: Path, languages: list[str]) -> None:
        """
        Initialize rule cache builder thread.

        Args:
            rules_dir: Directory containing rule JSON files (dependencies.json, etc.)
            cache_dir: Directory for cache output
            languages: Languages to build cache for
        """
        super().__init__()
        self.rules_dir = rules_dir
        self.cache_dir = cache_dir
        self.languages = languages
        self._should_stop = False

    def run(self) -> None:
        """Build cache for all languages."""
        try:
            self.status_changed.emit(tr("app.loading_rules"))

            source_files = {
                "dependencies": self.rules_dir / "dependencies.json",
                "incompatibilities": self.rules_dir / "incompatibilities.json",
                "order": self.rules_dir / "order.json",
            }

            self.status_changed.emit(tr("app.parsing_rules"))
            source_data = {}

            for rule_type, file_path in source_files.items():
                if file_path.exists():
                    try:
                        with open(file_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                            raw_rules = data.get("rules", [])

                            expanded_rules = []
                            for rule in raw_rules:
                                try:
                                    expression = RuleExpression.parse(rule["rule"])

                                    if rule_type == "incompatibilities":
                                        expanded = expression.to_incompatibility_rules(rule)
                                        expanded_rules.extend(expanded)

                                    elif rule_type == "dependencies":
                                        expanded_rule = expression.to_dependency_rule(rule)
                                        expanded_rules.append(expanded_rule)

                                    elif rule_type == "order":
                                        expanded_rule = expression.to_order_rule(rule)
                                        expanded_rules.append(expanded_rule)

                                except Exception as e:
                                    logger.error(
                                        f"Error expanding rule '{rule.get('rule')}': {e}"
                                    )

                            source_data[rule_type] = expanded_rules

                    except Exception as e:
                        logger.error(f"Error loading {file_path.name}: {e}")
                        source_data[rule_type] = []
                else:
                    logger.warning(f"Rule file not found: {file_path}")
                    source_data[rule_type] = []

            # Count total rules for progress tracking
            total_rules = sum(len(rules) for rules in source_data.values())

            if total_rules == 0:
                logger.warning("No rules found in source files")

            self.progress.emit(10)  # Initial progress

            total_languages = len(self.languages)
            current_step = 0
            total_steps = total_languages * total_rules if total_rules > 0 else total_languages

            for lang in self.languages:
                if self._should_stop:
                    self.finished.emit(False)
                    return

                self.status_changed.emit(tr("app.generating_cache_for_lang", lang=lang))

                # Localize all rule types
                localized_data = {}

                for rule_type, rules_list in source_data.items():
                    localized_rules = []

                    for rule in rules_list:
                        if self._should_stop:
                            self.finished.emit(False)
                            return

                        localized_rules.append(self._localize_rule(rule, lang))

                        current_step += 1
                        # Progress 10-100%
                        progress_value = 10 + int((current_step / total_steps) * 90)
                        self.progress.emit(progress_value)

                    localized_data[rule_type] = localized_rules

                # Save cache
                cache_path = self.cache_dir / f"rules_{lang}.json"
                with open(cache_path, "w", encoding="utf-8") as f:
                    json.dump(localized_data, f, indent=2, ensure_ascii=False)

                logger.info(f"Rule cache generated for {lang}: {cache_path}")

            self.status_changed.emit(tr("app.cache_generated_successfully"))
            self.progress.emit(100)
            self.finished.emit(True)

        except Exception as e:
            logger.exception(f"Error during rule cache generation: {e}")
            self.error.emit(f"Error during rule cache generation: {e}")
            self.finished.emit(False)

    def stop(self) -> None:
        """Request thread to stop gracefully."""
        self._should_stop = True

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

        # Fallback order: target language first, then others
        fallback_order = [target_lang] + [
            lang for lang in translations.keys() if lang != target_lang
        ]

        # Resolve description with fallback
        description = ""
        for lang in fallback_order:
            desc = translations.get(lang, "")
            if desc:
                description = desc
                break

        result["description"] = description
        return result


class ConditionEvaluator(Protocol):
    """Protocol for evaluating source/target conditions."""

    def is_satisfied(self, selected_set: set[ComponentReference]) -> bool:
        """Check if condition is satisfied."""
        ...

    def get_missing(self, selected_set: set[ComponentReference]) -> list[ComponentReference]:
        """Get missing components."""
        ...


class StandardCondition:
    """Evaluates standard source/target."""

    __slots__ = ("components", "mode", "_matcher")

    def __init__(
        self,
        components: tuple[ComponentReference, ...],
        mode: DependencyMode,
        matcher: Callable[[ComponentReference, set[ComponentReference]], bool],
    ):
        self.components = components
        self.mode = mode
        self._matcher = matcher

    def is_satisfied(self, selected_set: set[ComponentReference]) -> bool:
        """Check if condition is met."""
        matches = [self._matcher(comp, selected_set) for comp in self.components]

        if self.mode == DependencyMode.ALL:
            return all(matches)
        return any(matches)

    def get_missing(self, selected_set: set[ComponentReference]) -> list[ComponentReference]:
        """Get components that don't match."""
        return [comp for comp in self.components if not self._matcher(comp, selected_set)]


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


# ===================================================================
# Rule Manager with optimized checking
# ===================================================================


class RuleManager(QObject):
    """Manages rules."""

    cache_ready = Signal(bool)  # Emitted when cache is ready
    cache_building = Signal()  # Emitted when cache building starts
    cache_error = Signal(str)  # Emitted on error

    def __init__(self, rules_dir: Path = RULES_DIR, cache_dir: Path = CACHE_DIR) -> None:
        """Initialize rule manager."""
        super().__init__()

        self.rules_dir = rules_dir
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.translator = get_translator()
        self.current_language = self.translator.current_language

        # Rule storage
        self._dependency_rules: list[DependencyRule] = []
        self._incompatibility_rules: list[IncompatibilityRule] = []
        self._order_rules: list[OrderRule] = []

        # Indexes
        self._indexes = IndexManager.get_indexes()
        self._all_rules: list[Rule] = []
        self._rules_by_source: dict[ComponentReference, list[Rule]] = defaultdict(list)
        self._rules_by_type: dict[RuleType, list[Rule]] = defaultdict(list)
        self._components_by_mod: dict[str, set[str]] = defaultdict(set)

        self._last_selection_hash: int | None = None

        # Cache builder thread
        self.builder_thread: RuleCacheBuilderThread | None = None

    # ========================================
    # CACHE MANAGEMENT
    # ========================================

    def needs_cache_rebuild(self) -> bool:
        """Check if cache needs to be rebuilt."""
        return self._should_rebuild_cache()

    def build_cache_async(self) -> bool:
        """Start asynchronous cache building."""
        if self.builder_thread is not None and self.builder_thread.isRunning():
            logger.warning("Rule cache build already in progress")
            return False

        self.cache_building.emit()

        self.builder_thread = RuleCacheBuilderThread(
            self.rules_dir, self.cache_dir, [lang for lang, _ in SUPPORTED_LANGUAGES]
        )

        self.builder_thread.finished.connect(self._on_cache_build_finished)
        self.builder_thread.error.connect(self._on_cache_build_error)
        self.builder_thread.start()

        logger.info("Rule cache build thread started")
        return True

    def _on_cache_build_finished(self, success: bool) -> None:
        """Called when cache building finishes."""
        if success:
            if self.load_cache():
                logger.info("Rule cache loaded successfully after build")
                self.cache_ready.emit(True)
            else:
                error_msg = "Error loading rule cache after build"
                logger.error(error_msg)
                self.cache_error.emit(error_msg)
        else:
            error_msg = "Rule cache build failed"
            logger.error(error_msg)
            self.cache_error.emit(error_msg)

        self.builder_thread = None

    def _on_cache_build_error(self, error_message: str) -> None:
        """Called on cache build error."""
        logger.error(f"Rule cache build error: {error_message}")
        self.cache_error.emit(error_message)
        self.builder_thread = None

    def _should_rebuild_cache(self) -> bool:
        """Check if cache should be rebuilt."""
        for lang, _ in SUPPORTED_LANGUAGES:
            cache_file = self.cache_dir / f"rules_{lang}.json"
            if not cache_file.exists():
                logger.info(f"Rule cache missing for {lang}")
                return True

        source_files = [
            self.rules_dir / "dependencies.json",
            self.rules_dir / "incompatibilities.json",
            self.rules_dir / "order.json",
        ]

        existing_sources = [f for f in source_files if f.exists()]

        if not existing_sources:
            logger.warning("No source rule files found")
            return False

        try:
            last_source_mod = max(f.stat().st_mtime for f in existing_sources)

            # Compare with oldest cache
            oldest_cache = min(
                (self.cache_dir / f"rules_{lang}.json").stat().st_mtime
                for lang, _ in SUPPORTED_LANGUAGES
            )

            if last_source_mod > oldest_cache:
                logger.info("Source rule files newer than cache")
                return True

        except OSError as e:
            logger.error(f"Error checking rule file timestamps: {e}")
            return True

        return False

    def load_cache(self) -> bool:
        """Load cached rules for current language."""
        cache_file = self.cache_dir / f"rules_{self.current_language}.json"

        if not cache_file.exists():
            logger.error(f"Rule cache file not found: {cache_file}")
            return False

        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Reset all rules
            self._dependency_rules.clear()
            self._incompatibility_rules.clear()
            self._order_rules.clear()
            self._all_rules.clear()
            self._rules_by_source.clear()
            self._rules_by_type.clear()
            self._components_by_mod.clear()

            self._load_rules_from_cache(data.get("dependencies", []), DependencyRule)
            self._load_rules_from_cache(data.get("incompatibilities", []), IncompatibilityRule)
            self._load_rules_from_cache(data.get("order", []), OrderRule)

            group_rules = sum(1 for rule in self._dependency_rules if rule.uses_groups())

            logger.info(
                "Loaded rules for %s: %d dependencies (%d with groups), %d incompatibilities, %d order rules",
                self.current_language,
                len(self._dependency_rules),
                group_rules,
                len(self._incompatibility_rules),
                len(self._order_rules),
            )

            return True

        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in rule cache: {e}")
            return False
        except Exception as e:
            logger.exception(f"Error loading rule cache: {e}")
            return False

    def reload_for_language(self, language: str) -> bool:
        """Reload cache for a new language."""
        if not any(lang == language for lang, _ in SUPPORTED_LANGUAGES):
            logger.warning(f"Unsupported language: {language}")
            return False

        if self.current_language == language:
            return True

        self.current_language = language
        success = self.load_cache()

        if success:
            self._last_selection_hash = None
            self._indexes.clear_selection_violations()
            self._indexes.clear_order_violations()

        return success

    def _load_rules_from_cache(self, rules_data: list, cls) -> None:
        """Load rules from cache data."""
        loaded_count = 0
        error_count = 0

        # Determine target list
        if cls == DependencyRule:
            target_list = self._dependency_rules
        elif cls == IncompatibilityRule:
            target_list = self._incompatibility_rules
        elif cls == OrderRule:
            target_list = self._order_rules
        else:
            logger.error(f"Unknown rule class: {cls}")
            return

        for rule_data in rules_data:
            try:
                rule = cls.from_dict(rule_data)
                target_list.append(rule)
                self._add_rule_to_indexes(rule)
                loaded_count += 1
            except (ValueError, KeyError, TypeError) as e:
                error_count += 1
                logger.error(f"Failed to load rule '{rule_data}': {e}. Rule skipped.")
            except Exception as e:
                error_count += 1
                logger.error(
                    f"Unexpected error loading rule '{rule_data}': {e}. Rule skipped.",
                    exc_info=True,
                )

        if error_count > 0:
            logger.warning(f"Skipped {error_count} invalid {cls.__name__}(s)")

    def _add_rule_to_indexes(self, rule: Rule) -> None:
        """Add rule to all indexes."""
        self._all_rules.append(rule)
        self._rules_by_type[rule.rule_type].append(rule)

        for source_ref in rule.sources:
            self._rules_by_source[source_ref].append(rule)
            if not source_ref.is_mod():
                self._components_by_mod[source_ref.mod_id].add(source_ref.comp_key)

        for target_ref in rule.targets:
            if not target_ref.is_mod():
                self._components_by_mod[target_ref.mod_id].add(target_ref.comp_key)

    def _get_known_components(self, mod_id: str) -> set[str]:
        """Get all known component keys for a mod from indexed rules."""
        return self._components_by_mod.get(mod_id.lower(), set())

    # ========================================
    # SELECTION VALIDATION
    # ========================================

    def validate_selection(
        self, selected_components: list[ComponentReference]
    ) -> list[RuleViolation]:
        """Validate component selection against dependency and incompatibility rules."""
        references = [reference for reference in selected_components if not reference.is_mod()]
        selection_hash = hash(frozenset(references))

        if self._last_selection_hash == selection_hash:
            return self._get_all_cached_selection_violations()

        self._indexes.clear_selection_violations()
        self._last_selection_hash = selection_hash

        violations: list[RuleViolation] = []
        selected_set = set(references)
        checked_rules = set()

        for reference in references:
            rules = list(self._rules_by_source.get(reference, []))

            wildcard_ref = ComponentReference(reference.mod_id, "*")
            wildcard_rules = self._rules_by_source.get(wildcard_ref, [])
            rules.extend(wildcard_rules)

            for rule in rules:
                if rule.rule_type == RuleType.ORDER:
                    continue  # Order rules validated separately

                rule_id = id(rule)
                if rule_id in checked_rules:
                    continue
                checked_rules.add(rule_id)

                violation = self._check_rule(rule, reference, selected_set)
                if violation:
                    violations.append(violation)
                    self._indexes.add_selection_violation(violation)

        for rule in self._all_rules:
            if rule.rule_type == RuleType.ORDER:
                continue

            rule_id = id(rule)
            if rule_id in checked_rules:
                continue

            for source in rule.sources:
                if source.is_mod() and self._matches_reference(source, selected_set):
                    # Find a matching component to use as source_ref
                    for reference in selected_set:
                        if reference.mod_id == source.mod_id:
                            checked_rules.add(rule_id)
                            violation = self._check_rule(rule, reference, selected_set)
                            if violation:
                                violations.append(violation)
                                self._indexes.add_selection_violation(violation)
                            break
                    break

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

    @staticmethod
    def _matches_reference(
        reference: ComponentReference, selected_set: set[ComponentReference]
    ) -> bool:
        """Check if a reference matches any selected component."""
        if reference.is_mod():
            return any(selected.mod_id == reference.mod_id for selected in selected_set)
        return reference in selected_set

    def _check_dependency(
        self,
        rule: DependencyRule,
        source_ref: ComponentReference,
        selected_set: set[ComponentReference],
    ) -> RuleViolation | None:
        """Check DependencyRule: supports ALL and ANY modes."""
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
        self, rule: DependencyRule, source_ref: ComponentReference
    ) -> ConditionEvaluator:
        """Factory method: create appropriate source evaluator."""
        if rule.source_groups:
            return GroupCondition(rule.source_groups)

        for rule_source in rule.sources:
            if self._references_match_for_source(source_ref, rule_source):
                return TrivialCondition(True)
        return TrivialCondition(False)

    @staticmethod
    def _references_match_for_source(
        selected_ref: ComponentReference, rule_source: ComponentReference
    ) -> bool:
        """Check if a selected component matches a rule source (handles wildcards)."""
        if rule_source.mod_id != selected_ref.mod_id:
            return False

        if rule_source.is_mod():
            return True

        return rule_source.comp_key == selected_ref.comp_key

    def _create_target_evaluator(self, rule: DependencyRule) -> ConditionEvaluator:
        """Factory method: create appropriate target evaluator."""
        if rule.target_groups:
            return GroupCondition(rule.target_groups)
        else:
            return StandardCondition(
                components=rule.targets,
                mode=rule.dependency_mode,
                matcher=self._matches_reference,
            )

    def _check_incompatibility(
        self,
        rule: IncompatibilityRule,
        source_ref: ComponentReference,
        selected_set: set[ComponentReference],
    ) -> RuleViolation | None:
        """Check incompatibility: collect conflicting selected components."""
        if rule.source_groups:
            if not all(group.matches(selected_set) for group in rule.source_groups):
                return None

        if rule.target_groups:
            conflicts = [
                comp
                for group in rule.target_groups
                for comp in group.get_matched_components(selected_set)
            ]

            if not all(group.matches(selected_set) for group in rule.target_groups):
                return None
        else:
            conflicts = [
                target
                for target in rule.targets
                if self._matches_reference(target, selected_set)
            ]

            if not conflicts:
                return None

        affected = (source_ref,) + tuple(conflicts)
        return RuleViolation(rule=rule, affected_components=affected)

    # ========================================
    # ORDER VALIDATION
    # ========================================

    def validate_order(
        self, sequences: dict[int, list[ComponentReference]]
    ) -> dict[int, list[RuleViolation]]:
        """Validate order for multiple sequences at once."""
        self._indexes.clear_order_violations()
        all_violations = {}

        for seq_idx, install_order in sequences.items():
            violations = []

            if not install_order:
                all_violations[seq_idx] = violations
                continue

            # Build position map
            positions = {ref: idx for idx, ref in enumerate(install_order)}

            # Check DEPENDENCY rules (implicit ordering)
            for rule in self._dependency_rules:
                if rule.implicit_order:
                    violations.extend(
                        self._validate_dependency_order(rule, install_order, positions)
                    )

            # Check explicit ORDER rules
            for rule in self._order_rules:
                violations.extend(self._validate_explicit_order(rule, install_order, positions))

            all_violations[seq_idx] = violations

        return all_violations

    def _validate_dependency_order(
        self,
        rule: DependencyRule,
        install_order: list[ComponentReference],
        positions: dict[ComponentReference, int],
    ) -> list[RuleViolation]:
        """Validate that dependencies come before dependents."""
        violations: list[RuleViolation] = []

        # find sources (dependents) positions - check all sources
        source_positions = [
            (positions[ref], ref) for ref in install_order if ref in rule.sources
        ]

        if not source_positions:
            return violations

        # For each source, ensure all targets come before it
        for source_idx, source_ref in source_positions:
            for target_ref in rule.targets:
                if target_ref not in positions:
                    continue

                if positions[target_ref] > source_idx:
                    violation = RuleViolation(
                        rule=rule,
                        affected_components=(source_ref, target_ref),
                    )
                    violations.append(violation)
                    self._indexes.add_order_violation(violation)

        return violations

    def _validate_explicit_order(
        self,
        rule: OrderRule,
        install_order: list[ComponentReference],
        positions: dict[ComponentReference, int],
    ) -> list[RuleViolation]:
        """Validate explicit order rules."""
        violations: list[RuleViolation] = []

        # Find source positions
        source_positions = [
            (positions[ref], ref) for ref in install_order if ref in rule.sources
        ]

        if not source_positions:
            return violations

        for source_idx, source_ref in source_positions:
            for target_ref in rule.targets:
                if target_ref not in positions or target_ref == source_ref:
                    continue

                target_idx = positions[target_ref]
                violation_detected = False

                if rule.order_direction == OrderDirection.BEFORE:
                    if source_idx > target_idx:
                        violation_detected = True
                else:  # AFTER
                    if source_idx < target_idx:
                        violation_detected = True

                if violation_detected:
                    violation = RuleViolation(
                        rule=rule,
                        affected_components=(source_ref, target_ref),
                    )
                    violations.append(violation)
                    self._indexes.add_order_violation(violation)

        return violations

    # -------------------------
    # Public API
    # -------------------------

    def get_rules_for_component(self, reference: ComponentReference) -> list[Rule]:
        """Get all rules where the component is a source."""
        return self._rules_by_source.get(reference, [])

    def get_dependency_rules(self) -> tuple[DependencyRule, ...]:
        """Get all dependency rules (read-only)."""
        return tuple(self._dependency_rules)

    def get_order_rules(self) -> tuple[OrderRule, ...]:
        """Get all explicit order rules (read-only)."""
        return tuple(self._order_rules)

    def get_incompatibility_rules(self) -> tuple[IncompatibilityRule, ...]:
        """Get all incompatibility rules (read-only)."""
        return tuple(self._incompatibility_rules)

    def get_selection_violations(self, reference: ComponentReference) -> list[RuleViolation]:
        """Get cached selection violations for a specific component."""
        return self._indexes.get_selection_violations(reference)

    def get_order_violations(self, reference: ComponentReference) -> list[RuleViolation]:
        """Get cached order violations for a specific component."""
        return self._indexes.get_order_violations(reference)

    def get_count(self) -> int:
        """Return total number of rules."""
        return len(self._all_rules)

    def get_requirements(
        self, mod_id: str, comp_key: str, recursive: bool = False
    ) -> set[tuple[str, str]]:
        """Get all components required by a specific component."""
        requirements: set[tuple[str, str]] = set()
        visiting: set[tuple[str, str]] = set()

        def _collect_requirements(mod_id: str, comp_key: str):
            current = (mod_id, comp_key)

            if current in visiting:
                return

            visiting.add(current)
            reference = ComponentReference.from_string(f"{mod_id}:{comp_key}")
            rules = self.get_rules_for_component(reference)

            for rule in rules:
                if not isinstance(rule, DependencyRule):
                    continue

                for target in rule.targets:
                    requirements.add((target.mod_id, target.comp_key))

                    if recursive:
                        if target.is_mod():
                            known_comps = self._get_known_components(target.mod_id)
                            for comp in known_comps:
                                _collect_requirements(target.mod_id, comp)
                        else:
                            _collect_requirements(target.mod_id, target.comp_key)

        _collect_requirements(mod_id, comp_key)
        return requirements
