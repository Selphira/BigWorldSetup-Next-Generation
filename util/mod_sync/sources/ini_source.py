"""INI file parser for mod metadata.

Supports multiple formats:
- .ini: Standard INI format
- .json: JSON format
- .json5: JSON5 with comments and trailing commas
- .jsonc: JSON with comments
- .hjson: Human JSON format
"""

from __future__ import annotations

import configparser
from dataclasses import dataclass, field
import json
import logging
from pathlib import Path

from core.File import safe_read

logger = logging.getLogger(__name__)

# Origin marker of the rules generated from the INI file: mod_sync replaces
# these rules only, the other rules of a definition are manual.
INI_RULE_ORIGIN = "ini"


@dataclass
class IniData:
    """Data extracted from mod .ini file."""

    name: str | None = None
    authors: list[str] = field(default_factory=list)
    description: str | None = None
    homepage: str | None = None
    forum: str | None = None
    download: str | None = None
    label_type: str | None = None
    mod_type: str | None = None
    before: list[str] = field(default_factory=list)
    after: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dictionary for merging.

        Returns:
            Dictionary suitable for merger
        """
        result = {}

        if self.name:
            result["name"] = self.name

        if self.authors:
            result["authors"] = self.authors

        # Links
        if self.homepage or self.forum:
            links = {}
            if (
                self.homepage
                and "baldursextendedworld.com" not in self.homepage
                and "forums.pocketplane.net" not in self.homepage
            ):
                links["homepage"] = self.homepage.strip().replace(
                    "http://www.shsforums.net", "https://www.shsforums.net"
                )
            if (
                self.forum
                and "baldursextendedworld.com" not in self.forum  # lien non fonctionnel
                and "forums.pocketplane.net" not in self.forum  # Lien non fonctionnel
            ):
                links["forums"] = [
                    forum.strip().replace(
                        "http://www.shsforums.net", "https://www.shsforums.net"
                    )
                    for forum in self.forum.replace(", ", " ").split()
                    if forum.strip()
                ]
            result["links"] = links

        # Description stored separately for special handling
        if self.description:
            result["ini_description"] = self.description

        return result

    def order_rules(self, tp2_name: str) -> list[dict]:
        """Return the order rules declared by the Before and After fields.

        Args:
            tp2_name: Mod the rules apply to

        Returns:
            Rules in the format of the "order" list of a definition's "rules"
            section; they carry no text, the application generates their
            description from their origin
        """
        rules = []
        for direction, mods in (("before", self.before), ("after", self.after)):
            if not mods:
                continue

            targets = "|".join(f"{mod}(-)" for mod in mods)
            rules.append(
                {
                    "rule": f"{tp2_name}(-):{targets}",
                    "direction": direction,
                    "severity": "warning",
                    "origin": INI_RULE_ORIGIN,
                }
            )
        return rules


class IniParser:
    """Parser for mod .ini files (supports INI, JSON, JSON5, HJSON, JSONC)."""

    SUPPORTED_FORMATS = [".ini", ".hjson", ".json5", ".jsonc", ".json"]

    def __init__(self, mods_dir: Path | None = None):
        """Initialize parser.

        Args:
            mods_dir: Base directory containing extracted mods
        """
        self.mods_dir = Path(mods_dir) if mods_dir else None

    def find_ini_file(self, tp2_name: str) -> Path | None:
        """Find .ini file matching tp2 name.

        Searches for files in this order:
        1. mods_dir/tp2_name/tp2_name.{ext}
        2. mods_dir/tp2_name.{ext}

        Args:
            tp2_name: Name of the tp2 file (without extension)

        Returns:
            Path to ini file or None if not found
        """
        if not self.mods_dir or not self.mods_dir.exists():
            return None

        # Check in direct subdirectory with mod name
        mod_dir = self.mods_dir / tp2_name
        if mod_dir.exists():
            for ext in self.SUPPORTED_FORMATS:
                ini_path = mod_dir / f"{tp2_name}{ext}"
                if ini_path.exists():
                    logger.debug(f"Found ini file: {ini_path}")
                    return ini_path

        # Check in mods_dir root
        for ext in self.SUPPORTED_FORMATS:
            ini_path = self.mods_dir / f"{tp2_name}{ext}"
            if ini_path.exists():
                logger.debug(f"Found ini file: {ini_path}")
                return ini_path

        logger.debug(f"No ini file found for {tp2_name}")
        return None

    def extract(self, tp2_name: str) -> IniData | None:
        """Extract data from ini file.

        Args:
            tp2_name: Name of the tp2 file

        Returns:
            Extracted IniData or None if not found
        """
        ini_path = self.find_ini_file(tp2_name)
        if not ini_path:
            return None

        try:
            return self.parse(ini_path)
        except Exception as e:
            logger.error(f"Error parsing ini file {ini_path}: {e}", exc_info=True)
            return None

    def parse(self, ini_path: Path) -> IniData:
        """Parse ini file.

        Args:
            ini_path: Path to ini file

        Returns:
            Extracted IniData

        Raises:
            Exception: If parsing fails
        """
        if ini_path.suffix == ".ini":
            return self._parse_ini(ini_path)

        return self._parse_json_like(ini_path)

    def _parse_ini(self, path: Path) -> IniData:
        """Parse standard INI format.

        Args:
            path: Path to INI file

        Returns:
            Extracted IniData
        """
        config = configparser.ConfigParser(
            interpolation=None,  # Désactive l'interpolation
            allow_no_value=True,  # Permet les clés sans valeur
        )

        # Lire et prétraiter le contenu pour gérer les lignes multiples
        content = safe_read(path)

        # Remplacer les retours à la ligne dans les valeurs par des espaces
        # On garde seulement les lignes qui commencent par un commentaire, une section ou une clé
        lines = []
        in_value = False
        current_line = ""

        for line in content.split("\n"):
            stripped = line.strip()

            # Ligne vide
            if not stripped:
                if current_line:
                    lines.append(current_line)
                    current_line = ""
                in_value = False
                lines.append("")
                continue

            # Commentaire
            if stripped.startswith(("#", ";", "//")):
                if current_line:
                    lines.append(current_line)
                    current_line = ""
                in_value = False
                # lines.append(line)
                continue

            # Section
            if stripped.startswith("[") and stripped.endswith("]"):
                if current_line:
                    lines.append(current_line)
                    current_line = ""
                in_value = False
                lines.append(line)
                continue

            # Clé = valeur
            if "=" in line:  # and line[0] not in (" ", "\t"):
                if current_line:
                    lines.append(current_line)
                current_line = line
                in_value = True
                continue

            # Continuation de valeur
            if in_value:
                # Ajouter la ligne comme continuation (avec un espace)
                current_line += " " + stripped
                continue

            # Autre cas (ne devrait pas arriver)
            if current_line:
                lines.append(current_line)
                current_line = ""
            lines.append(line)

        # N'oubliez pas la dernière ligne
        if current_line:
            lines.append(current_line)

        # Créer un fichier temporaire en mémoire

        cleaned_content = "\n".join(lines)

        # Supprimer le BOM
        if cleaned_content.startswith("\ufeff"):
            cleaned_content = cleaned_content[1:]

        config.read_string(cleaned_content)

        if "Metadata" not in config:
            logger.warning(f"No [Metadata] section in {path}")
            return IniData()

        meta = config["Metadata"]

        def clean_value(value: str | None) -> str | None:
            """Remove surrounding quotes from a value."""
            if not value:
                return value
            value = value.strip()
            # Remove surrounding quotes (both single and double)
            if (value.startswith('"') and value.endswith('"')) or (
                value.startswith("'") and value.endswith("'")
            ):
                return value[1:-1]
            return value

        # Parse authors (can be comma-separated)
        authors = []
        if author := clean_value(meta.get("Author")):
            authors = [a.strip() for a in author.split(",") if a.strip()]

        return IniData(
            name=clean_value(meta.get("Name")),
            authors=authors,
            description=clean_value(meta.get("Description")),
            homepage=clean_value(meta.get("Homepage")),
            forum=clean_value(meta.get("Forum")),
            download=clean_value(meta.get("Download")),
            label_type=clean_value(meta.get("LabelType")),
            mod_type=clean_value(meta.get("Type")),
            before=self._parse_list(clean_value(meta.get("Before", ""))),
            after=self._parse_list(clean_value(meta.get("After", ""))),
        )

    def _parse_json_like(self, path: Path) -> IniData:
        """Parse JSON-like formats (JSON, JSON5, HJSON, JSONC).

        Note: For simplicity, we treat these as standard JSON with basic
        comment stripping. A full JSON5/HJSON parser would be better for
        production use.

        Args:
            path: Path to JSON-like file

        Returns:
            Extracted IniData

        Raises:
            Exception: If the file cannot be read or decoded; an unreadable
                file must not pass for one declaring nothing
        """
        content = safe_read(path)

        # Simple comment stripping for JSONC/JSON5
        # This is basic - a full JSON5 parser would be better
        lines = []
        for line in content.split("\n"):
            # Remove // comments (simple version), but not inside strings (basic check)
            if "//" in line and line.count('"') % 2 == 0:
                line = line[: line.index("//")]
            lines.append(line)
        clean_content = "\n".join(lines)

        # Try to parse as JSON
        data = json.loads(clean_content)

        # Extract metadata section
        meta = data.get("Metadata", data)

        # Parse authors
        authors = []
        if author := meta.get("Author"):
            if isinstance(author, list):
                authors = author
            else:
                authors = [a.strip() for a in str(author).split(",") if a.strip()]

        return IniData(
            name=meta.get("Name"),
            authors=authors,
            description=meta.get("Description"),
            homepage=meta.get("Homepage"),
            forum=meta.get("Forum"),
            download=meta.get("Download"),
            label_type=meta.get("LabelType"),
            mod_type=meta.get("Type"),
            before=self._parse_list(meta.get("Before", "")),
            after=self._parse_list(meta.get("After", "")),
        )

    def _parse_list(self, value: str | list) -> list[str]:
        """Parse comma-separated list or list object.

        Args:
            value: String with comma-separated values or list

        Returns:
            List of strings
        """
        if not value:
            return []

        if isinstance(value, list):
            return [str(item).strip() for item in value if item]

        return [item.strip() for item in str(value).split(",") if item.strip()]


class IniSource:
    """Wrapper for INI data extraction."""

    def __init__(self, mods_dir: Path | None):
        """Initialize INI source.

        Args:
            mods_dir: Base directory containing extracted mods
        """
        self.parser = IniParser(mods_dir)

    def extract(self, tp2_name: str) -> dict | None:
        """Extract INI data as dictionary for merging.

        Args:
            tp2_name: Name of the tp2 file

        Returns:
            Dictionary with extracted data or None if not found; a parsed file
            always provides its order rules, possibly none
        """
        ini_data = self.parser.extract(tp2_name)
        if not ini_data:
            return None

        result = ini_data.to_dict()
        result["order_rules"] = ini_data.order_rules(tp2_name)
        logger.info(f"Extracted INI data for {tp2_name}")

        return result
