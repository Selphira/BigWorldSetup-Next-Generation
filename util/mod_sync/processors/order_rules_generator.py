"""Generator for order rules from .ini metadata."""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class OrderRulesGenerator:
    """Generates order rules from .ini metadata (before/after fields)."""

    def __init__(self, definitions_dir: Path, mods_dir: Path | None):
        """Initialize generator.

        Args:
            definitions_dir: Directory containing mod JSON definitions
            mods_dir: Directory containing extracted mods with .ini files
        """
        self.definitions_dir = definitions_dir
        self.mods_dir = mods_dir

    def generate_rules(self, output_path: Path) -> dict[str, int]:
        """Generate order rules from all mod definitions.

        Args:
            output_path: Path to save the generated order.json file

        Returns:
            Stats dictionary with counts
        """
        from util.mod_sync.sources.ini_source import IniParser

        if not self.mods_dir:
            logger.warning("No mods directory provided, cannot generate order rules")
            return {"before_rules": 0, "after_rules": 0, "total_rules": 0}

        parser = IniParser(self.mods_dir)
        rules = []

        # Process each mod definition to find .ini files
        for json_file in sorted(self.definitions_dir.glob("*.json")):
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    definition = json.load(f)

                tp2_name = definition.get("tp2")
                if not tp2_name:
                    continue

                # Try to find and parse the .ini file
                ini_data = parser.extract(tp2_name)
                if not ini_data:
                    continue

                # Generate rules from "before" field
                if ini_data.before:
                    targets = "|".join(f"{x}(-)" for x in ini_data.before)
                    rules.append(
                        {
                            "rule": f"{tp2_name}(-):{targets}",
                            "direction": "before",
                            "severity": "warning",
                            "translations": {
                                "en_US": f"Rule defined in the {tp2_name}'s ini file",
                                "fr_FR": f"Règle définie dans l'ini de {tp2_name}",
                            },
                        }
                    )

                # Generate rules from "after" field
                if ini_data.after:
                    targets = "|".join(f"{x}(-)" for x in ini_data.after)
                    rules.append(
                        {
                            "rule": f"{tp2_name}(-):{targets}",
                            "direction": "after",
                            "severity": "warning",
                            "translations": {
                                "en_US": f"Rule defined in the {tp2_name}'s ini file",
                                "fr_FR": f"Règle définie dans l'ini de {tp2_name}",
                            },
                        }
                    )

            except Exception as e:
                logger.error(f"Error processing {json_file.name}: {e}")

        # Sort rules by the "rule" field for consistency
        rules.sort(key=lambda r: r["rule"])

        # Create output structure
        output = {"rules": rules}

        # Save to file
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)

        before_count = sum(1 for r in rules if r["direction"] == "before")
        after_count = sum(1 for r in rules if r["direction"] == "after")

        stats = {
            "before_rules": before_count,
            "after_rules": after_count,
            "total_rules": len(rules),
        }

        logger.info(
            f"Generated {stats['total_rules']} order rules "
            f"({stats['before_rules']} before, {stats['after_rules']} after)"
        )

        return stats
