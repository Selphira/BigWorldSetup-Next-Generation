"""TP2 file extraction wrapper using WeiDUTp2Parser."""

from __future__ import annotations

import logging
from pathlib import Path
import re

from constants import TP2_EXTENSION, WEIDU_SETUP_PREFIX
from core.WeiDUTp2Parser import MucComponent, Tp2ParseError, WeiDUTp2Parser
from util.mod_sync.models.catalogue_components import CatalogueComponents
from util.mod_sync.sources.tp2_rules import Tp2RuleBuilder

logger = logging.getLogger(__name__)

# Language of literal labels when the mod declares none, or several without it
DEFAULT_LABELS_LANGUAGE = "en_US"

# Language codes accepted by the mod definition schema; a TP2 language missing
# from LANG_MAP keeps its raw name and must not reach the definition
LANGUAGE_CODE_PATTERN = re.compile(r"^[a-z]{2}_[A-Z]{2}$")


def labels_language(definition_languages: dict | None) -> str:
    """Return the language literal TP2 labels belong to.

    English when the mod declares it, otherwise its first language: a TP2
    without LANGUAGE is shown in a single language.
    """
    declared = [code for code in (definition_languages or {}) if code != "all"]
    if not declared or DEFAULT_LABELS_LANGUAGE in declared:
        return DEFAULT_LABELS_LANGUAGE
    return declared[0]


class TP2Source:
    """Wrapper for TP2 data extraction using WeiDUTp2Parser."""

    def __init__(self, mods_dir: Path | None, catalogue: CatalogueComponents | None = None):
        """Initialize TP2 source.

        Args:
            mods_dir: Base directory containing extracted mods
            catalogue: Catalogued mods the generated rules refer to; without
                it, every other mod is unknown
        """
        self.mods_dir = Path(mods_dir) if mods_dir else None
        self.parser = WeiDUTp2Parser(self.mods_dir) if self.mods_dir else None
        self.catalogue = catalogue if catalogue is not None else CatalogueComponents()

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
        file_names = [
            f"{tp2_name}{TP2_EXTENSION}",
            f"{WEIDU_SETUP_PREFIX}{tp2_name}{TP2_EXTENSION}",
        ]
        tp2_variants = [
            directory / file_name
            for directory in (self.mods_dir / tp2_name, self.mods_dir)
            for file_name in file_names
        ]

        for tp2_path in tp2_variants:
            if tp2_path.exists():
                logger.debug(f"Found tp2 file: {tp2_path}")
                return tp2_path

        logger.debug(f"No tp2 file found for {tp2_name}")
        return None

    def extract(
        self,
        tp2_name: str,
        supported_games: set[str] | None = None,
        labels_language: str = DEFAULT_LABELS_LANGUAGE,
    ) -> dict | None:
        """Extract TP2 data as dictionary for merging.

        Args:
            tp2_name: Name of the tp2 file
            supported_games: Games the mod supports
            labels_language: Language of the literal labels of a TP2 declaring no LANGUAGE

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

            unknown_languages = {
                lang.language_code
                for lang in tp2_data.languages
                if not LANGUAGE_CODE_PATTERN.match(lang.language_code)
            }
            if unknown_languages:
                logger.warning(
                    f"{tp2_name}: TP2 languages missing from LANG_MAP ignored: "
                    f"{sorted(unknown_languages)}"
                )

            # Languages - build dict mapping language code to index, one translation per language
            if tp2_data.languages:
                languages = {}
                for lang in tp2_data.preferred_languages():
                    if lang.language_code not in unknown_languages:
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
                        # The choices of a MUC have no entry of their own
                        choice_labels = {
                            c.designated: c.label for c in comp.components if c.label
                        }
                        if choice_labels:
                            components[comp.designated]["labels"] = choice_labels
                    else:
                        components[comp.designated] = {
                            "type": "std",
                        }
                        if comp.label:
                            components[comp.designated]["label"] = comp.label
                    components[comp.designated]["games"] = comp.games
                result["components"] = components
                logger.debug(f"Extracted {len(components)} components")

            # Component translations; literal labels have the language of the mod
            if tp2_data.untranslated_component_labels:
                tp2_data.component_translations[labels_language] = (
                    tp2_data.untranslated_component_labels
                )
                logger.debug(
                    f"Literal component labels of {tp2_name} taken as {labels_language}"
                )

            component_translations = {
                code: labels
                for code, labels in tp2_data.component_translations.items()
                if code not in unknown_languages
            }
            if component_translations:
                result["component_translations"] = component_translations
                total_translations = sum(
                    len(comps) for comps in component_translations.values()
                )
                logger.debug(f"Extracted {total_translations} component translations")

            # Rules from the requirements; the merger replaces the stored TP2 rules with them
            rule_builder = Tp2RuleBuilder(self.catalogue, labels_language, unknown_languages)
            result["requirement_rules"] = rule_builder.build(tp2_data, tp2_name)

            return result

        except Tp2ParseError as e:
            logger.error(f"TP2 parse error for {tp2_path}: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error parsing tp2 file {tp2_path}: {e}", exc_info=True)
            return None
