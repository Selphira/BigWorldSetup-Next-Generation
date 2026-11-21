"""Merge mod data from multiple sources with priority rules."""

from __future__ import annotations

import copy
import logging
from typing import Any

logger = logging.getLogger(__name__)


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
        result = (
            copy.deepcopy(existing)
            if existing
            else {
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
        )

        # Merge simple fields
        for field, priority in self.SOURCE_PRIORITY.items():
            if "." in field:
                self._merge_nested(result, field, priority)
            else:
                self._merge_simple(result, field, priority)

        if self.tp2_name in ["bg1ub", "drizztsaga", "soa"]:
            result["games"].append("eet")

        if self.tp2_name in ["eet", "eet_end", "eet_gui"]:
            result["games"] = {"eet"}

        if "bgee" in result["games"]:
            result["games"].append("sod")
        result["games"] = sorted(list(set(result["games"])))

        # Special handling for complex fields
        self._merge_languages(result)
        self._merge_translations(result)
        self._merge_components(result)
        self._merge_links(result)

        return result

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
            if value:
                result[field] = value
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
            if value:
                parent[final_key] = value
                logger.debug(f"Set '{field_path}' from source '{source_name}'")
                return

    def _merge_languages(self, result: dict) -> None:
        if "languages" not in result:
            result["languages"] = {}

        all_language = None
        if "all" in result["languages"]:
            all_language = result["languages"]["all"]

        if "lcc" in self.sources:
            value = self.sources["lcc"].get("languages")
            if value:
                result["languages"] = value
                logger.debug("Set languages from LCC")
                if all_language is not None:
                    result["languages"]["all"] = all_language
                return

        if "tp2" in self.sources:
            value = self.sources["tp2"].get("languages")
            if value:
                result["languages"] = value
                logger.debug("Set languages from tp2")
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
        if "translations" not in result:
            result["translations"] = {}

        # Description: LCC or INI (priority: LCC > INI)
        if "lcc" in self.sources:
            lcc_descriptions = self.sources["lcc"].get("descriptions", {})
            for lang_code, desc in lcc_descriptions.items():
                if lang_code not in result["translations"]:
                    result["translations"][lang_code] = {}
                if not result["translations"][lang_code].get("description"):
                    result["translations"][lang_code]["description"] = desc
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
            tp2_translations = self.sources["tp2"].get("component_translations", {})
            for lang_code, components in tp2_translations.items():
                if lang_code not in result["translations"]:
                    result["translations"][lang_code] = {}
                result["translations"][lang_code]["components"] = components
                logger.debug(f"Set {len(components)} component translations for {lang_code}")

    def _merge_components(self, result: dict) -> None:
        """Merge component definitions (TP2 only).

        Components are ONLY extracted from TP2, never from LCC.
        """
        if "tp2" not in self.sources or "components" not in self.sources["tp2"]:
            return

        tp2_components = self.sources["tp2"]["components"]

        existing_components = result.get("components", {})

        # Build new components dict preserving TP2 order
        merged_components = {}

        # Step 1: Process all TP2 components in order
        for component_key in tp2_components.keys():
            if component_key in existing_components:
                # Component exists: preserve it with all manual metadata
                merged_component = existing_components[component_key].copy()

                # Special case: MUC - update components list from TP2
                if merged_component.get("type") == "muc":
                    tp2_data = tp2_components[component_key]
                    # Update components list if TP2 provides one (shouldn't, but safety)
                    if "components" in tp2_data:
                        merged_component["components"] = tp2_data["components"]

                if tp2_components[component_key]["games"]:
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
                if merged_components[component_key]["games"] is None:
                    del merged_components[component_key]["games"]
                logger.debug(f"Added new component {component_key} from TP2")

        # Step 2: Preserve SUB components (not in TP2, added manually)
        for component_key, component_data in existing_components.items():
            if component_data.get("type") == "sub" and component_key not in merged_components:
                merged_components[component_key] = component_data.copy()
                logger.debug(f"Preserved SUB component {component_key} (not in TP2)")

        result["components"] = merged_components

        # Log summary
        added = len(merged_components) - len(existing_components)
        removed = len(existing_components) - len(
            [k for k in existing_components if k in merged_components]
        )
        logger.debug(
            f"Component merge: {len(merged_components)} total "
            f"(+{added} new, -{removed} removed, {len(existing_components)} preserved)"
        )

    def _merge_links(self, result: dict) -> None:
        """Ensure links structure exists even if empty."""
        if "links" not in result:
            result["links"] = {}

        if len(result["links"]) > 0:
            return

        # Merge links from sources
        for source_name in ["ini", "lcc"]:
            if source_name not in self.sources:
                continue

            source_links = self.sources[source_name].get("links", {})
            for key, value in source_links.items():
                if key not in result["links"] or not result["links"][key]:
                    result["links"][key] = value

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
