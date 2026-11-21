"""Merge mod data from multiple sources with priority rules.

Field contract: an absent, null or empty value (string, list, dict) from a
source never replaces stored data, while zero and False are genuine values.
Source values are copied, so merging never mutates the sources.

Component labels follow the TP2, except those of MUC groups: their
``choice_N`` key depends on the group rank, so the stored label is kept.
Labels the TP2 does not provide (SUB prompts, manual components) are kept
while their component remains in the definition.

Files come from the archive source, which lists every stored entry: an entry
whose download link changed keeps no hash computed for the previous file.
"""

from __future__ import annotations

import copy
import logging
from typing import Any

from util.mod_sync.models.sync_result import ComponentChanges
from util.mod_sync.sources.archive_source import ALL_PLATFORMS

logger = logging.getLogger(__name__)

# Separates a SUB component key from its prompt and option ("0.1.2").
SUB_LABEL_SEPARATOR = "."


def has_value(value: Any) -> bool:
    """Tell whether a source value carries information worth applying."""
    if value is None:
        return False
    if isinstance(value, (str, list, tuple, set, dict)):
        return len(value) > 0
    return True


class DefinitionMerger:
    """Merge mod data from multiple sources according to priority rules."""

    # Priority rules: first source in list has highest priority
    SOURCE_PRIORITY = {
        "name": ["ini", "lcc"],
        "version": ["tp2", "lcc"],
        "tp2": ["tp2", "lcc"],
        "safe": ["lcc"],
        "games": ["lcc"],
        "categories": ["lcc"],
        "authors": ["lcc", "ini"],
        # "links.homepage": ["ini", "lcc"],
        # "links.forum": ["ini", "lcc"],
    }

    def __init__(self, tp2_name):
        """Initialize merger."""
        self.sources: dict[str, dict[str, Any]] = {}
        self.tp2_name = tp2_name
        self.component_changes = ComponentChanges()

    def add_source(self, name: str, data: dict[str, Any]) -> None:
        """Add a data source.

        Args:
            name: Source name (lcc, tp2, ini, archive)
            data: Source data dictionary
        """
        if data:
            self.sources[name] = data
            logger.debug(f"Added source '{name}' with {len(data)} keys")

    def merge(self, existing: dict[str, Any] | None = None) -> dict[str, Any]:
        """Merge all sources according to priority rules.

        Args:
            existing: Existing definition to merge into (or None for new)

        Returns:
            Merged definition dictionary
        """
        result = self._base_definition(existing)

        # Merge simple fields
        for field, priority in self.SOURCE_PRIORITY.items():
            if "." in field:
                self._merge_nested(result, field, priority)
            else:
                self._merge_simple(result, field, priority)

        self._derive_games(result)

        # Special handling for complex fields; labels depend on merged components
        self._merge_languages(result)
        self._merge_components(result)
        self._merge_translations(result)
        self._merge_links(result)
        self._merge_files(result)

        return result

    @staticmethod
    def _base_definition(existing: dict[str, Any] | None) -> dict[str, Any]:
        """Return a copy of the existing definition whose container fields are usable.

        Missing or null containers of an incomplete definition are replaced by
        empty ones so the merge never fails; stored scalar values are untouched.
        """
        definition: dict[str, Any] = {
            "name": None,
            "version": None,
            "links": {},
            "files": [],
            "games": [],
            "categories": [],
            "components": {},
            "languages": {},
            "translations": {},
            "tp2": "",
            "safe": None,
            "authors": [],
        }
        if not existing:
            return definition

        result = copy.deepcopy(existing)
        for field, default in definition.items():
            if isinstance(default, (list, dict)) and not isinstance(result.get(field), type(default)):
                result[field] = default
        return result

    def _derive_games(self, result: dict) -> None:
        """Add the games implied by the supported ones and by known EET mods."""
        games = set(result["games"])

        if self.tp2_name in ["bg1ub", "drizztsaga", "soa"]:
            games.add("eet")

        if self.tp2_name in ["eet", "eet_end", "eet_gui"]:
            games = {"eet"}

        if "bgee" in games:
            games.add("sod")

        result["games"] = sorted(games)

    def _merge_simple(self, result: dict, field: str, priority: list[str]) -> None:
        """Merge simple field with priority.

        Args:
            result: Result dictionary to update
            field: Field name
            priority: List of source names in priority order
        """
        for source_name in priority:
            if source_name not in self.sources:
                continue

            value = self.sources[source_name].get(field)
            if has_value(value):
                result[field] = copy.deepcopy(value)
                logger.debug(f"Set '{field}' from source '{source_name}'")
                return

    def _merge_nested(self, result: dict, field_path: str, priority: list[str]) -> None:
        """Merge nested field (e.g., 'links.homepage').

        Args:
            result: Result dictionary to update
            field_path: Dot-separated field path
            priority: List of source names in priority order
        """
        parts = field_path.split(".")

        # Navigate to parent
        parent = result
        for part in parts[:-1]:
            if part not in parent:
                parent[part] = {}
            parent = parent[part]

        final_key = parts[-1]

        # Try each source in priority order
        for source_name in priority:
            if source_name not in self.sources:
                continue

            # Navigate in source
            source_parent = self.sources[source_name]
            for part in parts[:-1]:
                source_parent = source_parent.get(part, {})
                if not isinstance(source_parent, dict):
                    break

            value = source_parent.get(final_key) if isinstance(source_parent, dict) else None
            if has_value(value):
                parent[final_key] = copy.deepcopy(value)
                logger.debug(f"Set '{field_path}' from source '{source_name}'")
                return

    def _merge_languages(self, result: dict) -> None:
        """Take languages from the first source providing some, keeping the manual "all"."""
        all_language = result["languages"].get("all")

        for source_name in ["lcc", "tp2"]:
            if source_name not in self.sources:
                continue

            value = self.sources[source_name].get("languages")
            if has_value(value):
                result["languages"] = copy.deepcopy(value)
                logger.debug(f"Set languages from {source_name}")
                if all_language is not None:
                    result["languages"]["all"] = all_language
                return

    def _merge_translations(self, result: dict) -> None:
        """Merge translations from multiple sources.

        Translations structure:
        {
            "en_US": {
                "description": "...",
                "components": {
                    "0": "Component name",
                    ...
                }
            }
        }
        """
        translations = result["translations"]

        # Description: LCC is the reference, its changes replace the stored text
        if "lcc" in self.sources:
            lcc_descriptions = self.sources["lcc"].get("descriptions") or {}
            for lang_code, desc in lcc_descriptions.items():
                if not has_value(desc):
                    continue
                self._translation_entry(translations, lang_code)["description"] = desc
                logger.debug(f"Set description for {lang_code} from LCC")

        # INI description (lower priority)
        """
        if "ini" in self.sources and "ini_description" in self.sources["ini"]:
            ini_desc = self.sources["ini"]["ini_description"]
            # Add to all languages that don't have a description yet
            for lang_code in result["translations"]:
                if not result["translations"][lang_code].get("description"):
                    result["translations"][lang_code]["description"] = ini_desc
                    logger.debug(f"Set description for {lang_code} from INI")
        """

        # Component translations: TP2 only
        if "tp2" in self.sources:
            tp2_translations = self.sources["tp2"].get("component_translations") or {}
            for lang_code, tp2_labels in tp2_translations.items():
                entry = self._translation_entry(translations, lang_code)
                stored_labels = entry.get("components")
                entry["components"] = self._merge_component_labels(
                    stored_labels if isinstance(stored_labels, dict) else {},
                    tp2_labels,
                    result["components"],
                )
                logger.debug(f"Set {len(tp2_labels)} component translations for {lang_code}")

    @staticmethod
    def _translation_entry(translations: dict, lang_code: str) -> dict:
        """Return the translation entry of a language, replacing an unusable one."""
        if not isinstance(translations.get(lang_code), dict):
            translations[lang_code] = {}
        return translations[lang_code]

    @classmethod
    def _merge_component_labels(
        cls,
        stored_labels: dict[str, str],
        tp2_labels: dict[str, str],
        components: dict[str, Any],
    ) -> dict[str, str]:
        """Merge the component labels of one language, in TP2 order.

        Stored labels the TP2 does not provide stay right after their owning
        component; labels of components removed from the definition are dropped.
        """
        owners = {*components, *cls._installable_keys(components)}
        kept_labels = {
            key: label
            for key, label in stored_labels.items()
            if key not in tp2_labels and has_value(label) and cls._label_owner(key) in owners
        }

        labels: dict[str, str] = {}
        for key, tp2_label in tp2_labels.items():
            stored_label = stored_labels.get(key)
            keep_stored = cls._is_muc_group(components.get(key)) or not has_value(tp2_label)
            labels[key] = stored_label if keep_stored and has_value(stored_label) else tp2_label
            labels.update(
                {extra: label for extra, label in kept_labels.items() if cls._label_owner(extra) == key}
            )

        labels.update(kept_labels)
        return labels

    @staticmethod
    def _label_owner(label_key: str) -> str:
        """Return the component key a label belongs to (a SUB prompt belongs to its SUB)."""
        return label_key.split(SUB_LABEL_SEPARATOR, 1)[0]

    @staticmethod
    def _is_muc_group(component: Any) -> bool:
        return isinstance(component, dict) and component.get("type") == "muc"

    @classmethod
    def _installable_keys(cls, components: dict[str, Any]) -> list[str]:
        """Return the installable component keys: MUC groups stand for their choices."""
        keys: dict[str, None] = {}
        for key, component in components.items():
            if cls._is_muc_group(component):
                keys.update(dict.fromkeys(str(choice) for choice in component.get("components") or []))
            else:
                keys[key] = None
        return list(keys)

    def _merge_components(self, result: dict) -> None:
        """Merge component definitions (TP2 only).

        Components are ONLY extracted from TP2, never from LCC.
        """
        self.component_changes = ComponentChanges()
        if "tp2" not in self.sources or "components" not in self.sources["tp2"]:
            return

        tp2_components = copy.deepcopy(self.sources["tp2"]["components"])

        existing_components = result["components"]

        # Build new components dict preserving TP2 order
        merged_components = {}

        # Step 1: Process all TP2 components in order
        for component_key in tp2_components.keys():
            if isinstance(existing_components.get(component_key), dict):
                # Component exists: preserve it with all manual metadata
                merged_component = existing_components[component_key].copy()

                # Special case: MUC - update components list from TP2
                if merged_component.get("type") == "muc":
                    tp2_data = tp2_components[component_key]
                    # Update components list if TP2 provides one (shouldn't, but safety)
                    if "components" in tp2_data:
                        merged_component["components"] = tp2_data["components"]

                if tp2_components[component_key].get("games"):
                    if {"bgee", "sod", "bg2ee"} & set(
                        tp2_components[component_key]["games"]
                    ) and "eet" in result["games"]:
                        tp2_components[component_key]["games"].append("eet")

                    if {"bgee"} & set(tp2_components[component_key]["games"]):
                        tp2_components[component_key]["games"].append("sod")

                    merged_component["games"] = sorted(
                        list(set(tp2_components[component_key]["games"]))
                    )

                merged_components[component_key] = merged_component
                logger.debug(f"Preserved component {component_key} with manual metadata")
            else:
                # New component: add from TP2 (minimal data)
                merged_components[component_key] = tp2_components[component_key].copy()
                if merged_components[component_key].get("games") is None:
                    merged_components[component_key].pop("games", None)
                logger.debug(f"Added new component {component_key} from TP2")

        # Step 2: Preserve SUB components (not in TP2, added manually)
        kept_subs: list[str] = []
        for component_key, component_data in existing_components.items():
            if (
                isinstance(component_data, dict)
                and component_data.get("type") == "sub"
                and component_key not in merged_components
            ):
                merged_components[component_key] = component_data.copy()
                kept_subs.append(component_key)
                logger.debug(f"Preserved SUB component {component_key} (not in TP2)")

        result["components"] = merged_components
        self.component_changes = self._component_changes(
            existing_components, merged_components, kept_subs
        )
        if self.component_changes:
            logger.info(f"{self.tp2_name}: {self.component_changes.describe()}")

    @classmethod
    def _component_changes(
        cls,
        existing_components: dict[str, Any],
        merged_components: dict[str, Any],
        kept_subs: list[str],
    ) -> ComponentChanges:
        """Compare installable components before and after the TP2 merge."""
        existing_keys = cls._installable_keys(existing_components)
        merged_keys = cls._installable_keys(merged_components)
        return ComponentChanges(
            added=tuple(key for key in merged_keys if key not in existing_keys),
            removed=tuple(key for key in existing_keys if key not in merged_keys),
            kept_subs=tuple(kept_subs),
        )

    def _merge_links(self, result: dict) -> None:
        """Fill the links of a definition that has none yet."""
        if len(result["links"]) > 0:
            return

        # Merge links from sources
        for source_name in ["ini", "lcc"]:
            if source_name not in self.sources:
                continue

            source_links = self.sources[source_name].get("links") or {}
            for key, value in source_links.items():
                if has_value(value) and not has_value(result["links"].get(key)):
                    result["links"][key] = copy.deepcopy(value)

    def _merge_files(self, result: dict) -> None:
        """Replace the stored files by those of the archive source."""
        source_files = (self.sources.get("archive") or {}).get("files")
        if not has_value(source_files):
            return

        stored_files = {
            self._platforms_key(entry): entry for entry in result["files"] if isinstance(entry, dict)
        }
        result["files"] = [
            self._without_stale_hash(copy.deepcopy(entry), stored_files.get(self._platforms_key(entry)))
            for entry in source_files
        ]

    @staticmethod
    def _platforms_key(file_entry: dict[str, Any]) -> tuple[str, ...]:
        """Identify a file entry by the platforms it applies to."""
        return tuple(sorted(file_entry.get("platforms") or ALL_PLATFORMS))

    @staticmethod
    def _without_stale_hash(file_entry: dict[str, Any], stored_entry: dict[str, Any] | None) -> dict:
        """Drop the hash a new download link inherited from the previous file."""
        link_changed = stored_entry is not None and file_entry.get("download") != stored_entry.get("download")
        if link_changed and file_entry.get("sha256") == stored_entry.get("sha256"):
            file_entry.pop("sha256", None)
            logger.debug(f"Dropped stale hash of {file_entry.get('filename')}")
        return file_entry

    def has_changes(self, existing: dict, merged: dict) -> bool:
        """Check if merged data differs from existing.

        Args:
            existing: Existing definition
            merged: Merged definition

        Returns:
            True if there are differences
        """
        # Simple comparison - could be made more sophisticated
        return existing != merged
