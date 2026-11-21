"""LCC data source - fetch and process data from lcc-docs repository."""

from __future__ import annotations

import json
import logging
from pathlib import Path
import re
from typing import Any
from urllib.error import HTTPError, URLError
import urllib.request

from constants import LCC_CACHE_DIR

logger = logging.getLogger(__name__)


class LCCSource:
    """Fetches and caches data from lcc-docs repository."""

    BASE_URL = "https://raw.githubusercontent.com/RiwsPy/lcc-docs/main/db/"

    FILES = {
        "default": "mods.json",
        "fr": "mods.json",
        "en": "mods_en.json",
        "cn": "mods_cn.json",
    }

    LANGUAGE_CODES = {
        "fr": "fr_FR",
        "en": "en_US",
        "cn": "zh_CN",
    }

    CATEGORY_MAP = {
        "Patch non officiel": "patch",
        "Utilitaire": "util",
        "Conversion": "conv",
        "Interface": "ui",
        "Cosmétique": "cosm",
        "Portrait et son": "portrait",
        "Quête": "quest",
        "PNJ recrutable": "npc",
        "PNJ One Day": "npc1d",
        "PNJ (autre)": "npcx",
        "Forgeron et marchand": "smith",
        "Sort et objet": "spell",
        "Kit": "kit",
        "Gameplay": "gameplay",
        "Script et tactique": "tactic",
        "Personnalisation du groupe": "party",
    }

    def __init__(self, cache_dir: Path = LCC_CACHE_DIR, force_refresh: bool = False):
        """Initialize LCC source.

        Args:
            cache_dir: Directory for caching downloaded data
            force_refresh: Force download even if cache exists
        """
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(exist_ok=True, parents=True)
        self.force_refresh = force_refresh
        self.data: dict[str, list[dict]] = {}
        self.tp2_index: dict[str, dict] = {}
        self.id_index: dict[str, dict] = {}

    def fetch_all(self) -> bool:
        """Fetch all LCC data from all language files.

        Returns:
            True if successful, False otherwise
        """
        logger.info("Fetching LCC data...")
        success = True

        for lang, filename in self.FILES.items():
            cache_file = self.cache_dir / filename

            # Use cache if available and not forcing refresh
            if cache_file.exists() and not self.force_refresh:
                logger.debug(f"Loading {lang} from cache: {cache_file}")
                try:
                    with open(cache_file, "r", encoding="utf-8") as f:
                        self.data[lang] = json.load(f)
                    logger.debug(f"Loaded {len(self.data[lang])} mods for {lang}")
                except Exception as e:
                    logger.error(f"Error reading cache {cache_file}: {e}")
                    success = False
            else:
                # Download from URL
                url = self.BASE_URL + filename
                logger.debug(f"Downloading {lang} from {url}...")

                data = self._download_json(url)
                if data:
                    self.data[lang] = data
                    logger.debug(f"Downloaded {len(data)} mods for {lang}")

                    # Save to cache
                    try:
                        with open(cache_file, "w", encoding="utf-8") as f:
                            json.dump(data, f, indent=2, ensure_ascii=False)
                        logger.debug(f"Saved cache: {cache_file}")
                    except Exception as e:
                        logger.warning(f"Could not save cache {cache_file}: {e}")
                else:
                    logger.error(f"Failed to download {lang}")
                    success = False

        # Build indexes from default language
        if "default" in self.data:
            self._build_tp2_index()
            self._build_id_index()
            logger.info(f"Built indexes: {len(self.tp2_index)} tp2 entries")

        return success

    def _download_json(self, url: str) -> list[dict] | None:
        """Download JSON file from URL.

        Args:
            url: URL to download from

        Returns:
            Parsed JSON data or None if error
        """
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                data = response.read()
                return json.loads(data.decode("utf-8"))
        except (URLError, HTTPError) as e:
            logger.error(f"Network error downloading {url}: {e}")
            return None
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON from {url}: {e}")
            return None
        except Exception as e:
            logger.error(f"Error downloading {url}: {e}")
            return None

    def _build_tp2_index(self) -> None:
        """Build tp2 -> mod_data index for fast lookup."""
        for mod_data in self.data["default"]:
            tp2 = mod_data.get("tp2", "").lower()
            if tp2 and tp2 not in ("", "n/a", "non-weidu"):
                # Handle multiple tp2 names separated by semicolon
                for part in tp2.split(";"):
                    part = part.strip()
                    if part:
                        self.tp2_index[part] = mod_data
            else:
                mod_id = mod_data.get("id")
                self.tp2_index[f"non-weidu-{mod_id}"] = mod_data

    def _build_id_index(self) -> None:
        """Build id -> mod_data index for resolving [[id]] references."""
        for mod_data in self.data["default"]:
            mod_id = mod_data.get("id")
            if mod_id is not None:
                self.id_index[str(mod_id)] = mod_data

    def get_all_mods(self) -> dict[str, dict]:
        """Get all mods indexed by tp2 name.

        Returns:
            Dictionary mapping tp2_name to compiled mod data
        """
        result = {}
        for tp2_name in self.tp2_index.keys():
            mod_data = self.find_by_tp2(tp2_name)
            if mod_data:
                result[tp2_name] = mod_data
        return result

    def find_by_tp2(self, tp2_name: str) -> dict[str, Any] | None:
        """Find mod by tp2 name.

        Args:
            tp2_name: TP2 filename (with or without .tp2 extension)

        Returns:
            Dictionary with compiled mod data or None if not found
        """
        tp2_key = tp2_name.lower().replace(".tp2", "")

        if tp2_key not in self.tp2_index:
            return None

        mod_data = self.tp2_index[tp2_key]

        if mod_data.get("status") == "hidden":
            return None

        games = self._extract_games(mod_data)

        if not {"bgee", "sod", "bg2ee", "eet", "iwdee", "pstee"} & set(games):
            return None

        # Compile result from mod_data
        result = {
            "name": mod_data.get("name", ""),
            "tp2": tp2_key if "non-weidu-" not in tp2_name else "",
            "safe": mod_data.get("safe"),
            "games": games,
            "categories": self._extract_categories(mod_data),
            "authors": mod_data.get("authors", []),
            "descriptions": {},
        }

        # Add links if present
        # FIXME: les urls sont dans urls et n'ont pas de catégorisation
        from urllib.parse import urlparse

        links = result.setdefault("links", {})

        urls = mod_data.get("urls", [])

        homepage = None
        forums: list[str] = []

        for url in urls:
            try:
                domain = urlparse(url).netloc.lower()
            except Exception:
                forums.append(url)
                continue

            if homepage is None and "github.com" in domain:
                homepage = url
            else:
                forums.append(url)

        if homepage:
            links["homepage"] = homepage

        links["forums"] = forums

        # Extract descriptions from all languages
        for lang, lang_code in self.LANGUAGE_CODES.items():
            if lang not in self.data:
                continue

            translated_mod = self._find_mod_by_id(mod_data["id"], lang)
            if translated_mod and "description" in translated_mod:
                description = self._resolve_mod_references(translated_mod["description"], lang)
                result["descriptions"][lang_code] = description

        return result

    def _extract_games(self, mod_data: dict) -> list[str]:
        """Extract and normalize game list.

        Args:
            mod_data: Mod data from LCC

        Returns:
            List of lowercase game identifiers
        """
        games = mod_data.get("games", [])
        return [game.lower() for game in games if game]

    def _extract_categories(self, mod_data: dict) -> list[str]:
        """Extract and convert categories from French to codes.

        Args:
            mod_data: Mod data from LCC

        Returns:
            List of category codes
        """
        categories = mod_data.get("categories", [])
        return [
            self.CATEGORY_MAP.get(cat, "") for cat in categories if self.CATEGORY_MAP.get(cat)
        ]

    def _resolve_mod_references(self, description: str, lang: str) -> str:
        """Resolve [[id]] references in descriptions.

        Format: [[1234]] -> Mod Name

        Args:
            description: Text with potential [[id]] references
            lang: Language of the description

        Returns:
            Description with references resolved to mod names
        """
        pattern = r"\[\[(\d+)\]\]"
        description = description.replace("|", "\n")

        def replace_reference(match: re.Match) -> str:
            mod_id = match.group(1)

            if mod_id not in self.id_index:
                return f"[[{mod_id}]]"

            mod_data = self.id_index[mod_id]

            # Get name in appropriate language
            lang_mod = self._find_mod_by_id(mod_id, lang)
            if lang_mod and "name" in lang_mod:
                return lang_mod["name"]
            else:
                return mod_data.get("name", f"Mod {mod_id}")

        return re.sub(pattern, replace_reference, description)

    def _find_mod_by_id(self, mod_id: int | str, lang: str) -> dict | None:
        """Find mod by ID in specific language.

        Args:
            mod_id: Mod identifier
            lang: Language code (fr, en, cn)

        Returns:
            Mod data in specified language or None
        """
        for mod_data in self.data.get(lang, []):
            if str(mod_data.get("id")) == str(mod_id):
                return mod_data
        return None
