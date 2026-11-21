"""TP2 file extraction wrapper using WeiDUTp2Parser."""

from __future__ import annotations

import logging
from pathlib import Path

from core.WeiDUTp2Parser import MucComponent, Tp2ParseError, WeiDUTp2Parser

logger = logging.getLogger(__name__)


class TP2Source:
    """Wrapper for TP2 data extraction using WeiDUTp2Parser."""

    def __init__(self, mods_dir: Path | None):
        """Initialize TP2 source.

        Args:
            mods_dir: Base directory containing extracted mods
        """
        self.mods_dir = Path(mods_dir) if mods_dir else None
        self.parser = WeiDUTp2Parser(self.mods_dir) if self.mods_dir else None

    def find_tp2_file(self, tp2_name: str) -> Path | None:
        """Find .tp2 file for a mod.

        Searches in:
        1. mods_dir/tp2_name/tp2_name.tp2
        2. mods_dir/tp2_name.tp2

        Args:
            tp2_name: Name of the tp2 file (without extension)

        Returns:
            Path to tp2 file or None if not found
        """
        if not self.mods_dir or not self.mods_dir.exists():
            return None

        # Check for TP2 file in this directory
        tp2_variants = [
            self.mods_dir / tp2_name / f"{tp2_name}.tp2",
            self.mods_dir / tp2_name / f"setup-{tp2_name}.tp2",
            self.mods_dir / f"{tp2_name}.tp2",
            self.mods_dir / f"setup-{tp2_name}.tp2",
        ]

        for tp2_path in tp2_variants:
            if tp2_path.exists():
                logger.debug(f"Found tp2 file: {tp2_path}")
                return tp2_path

        logger.debug(f"No tp2 file found for {tp2_name}")
        return None

    def extract(self, tp2_name: str, supported_games: set[str] | None = None) -> dict | None:
        """Extract TP2 data as dictionary for merging.

        Args:
            tp2_name: Name of the tp2 file

        Returns:
            Dictionary with extracted data or None if not found/error
        """
        if not self.parser:
            logger.debug("TP2 parser not initialized (no mods_dir)")
            return None

        tp2_path = self.find_tp2_file(tp2_name)
        if not tp2_path:
            return None

        try:
            tp2_data = self.parser.parse_file(tp2_path, supported_games)
            logger.info(f"Parsed TP2: {tp2_name} (v{tp2_data.version})")

            result = {
                "tp2": tp2_name,
            }

            # Version
            if tp2_data.version:
                result["version"] = tp2_data.version

            # Languages - build dict mapping language code to index
            if tp2_data.languages:
                languages = {}
                for lang in tp2_data.languages:
                    languages[lang.language_code] = lang.index
                result["languages"] = languages
                logger.debug(f"Extracted {len(languages)} languages")

            # Components - convert to component definitions
            if tp2_data.components:
                components = {}
                for comp in tp2_data.components:
                    # All components are marked as 'std' by default
                    # MUC and SUB types must be created manually
                    if isinstance(comp, MucComponent):
                        components[comp.designated] = {
                            "type": "muc",
                            "components": [c.designated for c in comp.components],
                        }
                    else:
                        components[comp.designated] = {
                            "type": "std",
                        }
                    components[comp.designated]["games"] = comp.games
                result["components"] = components
                logger.debug(f"Extracted {len(components)} components")

            # Component translations
            if tp2_data.component_translations:
                result["component_translations"] = tp2_data.component_translations
                total_translations = sum(
                    len(comps) for comps in tp2_data.component_translations.values()
                )
                logger.debug(f"Extracted {total_translations} component translations")

            return result

        except Tp2ParseError as e:
            logger.error(f"TP2 parse error for {tp2_path}: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error parsing tp2 file {tp2_path}: {e}", exc_info=True)
            return None
