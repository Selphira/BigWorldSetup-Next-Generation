"""
Complète les fichiers JSON de mods avec les données de LCC (lcc-docs)

SOURCES:
- https://github.com/RiwsPy/lcc-docs/tree/main/db/mods.json
- https://github.com/RiwsPy/lcc-docs/tree/main/db/mods_en.json
- https://github.com/RiwsPy/lcc-docs/tree/main/db/mods_cn.json

DONNÉES RÉCUPÉRÉES:
- Description (FR, EN, CN)
- Games (jeux supportés)
- Categories (catégories)
- TP2 (nom du fichier .tp2)

USAGE:
     python -m util.lcc_definition_updater <json_dir_or_file>
"""

import json
from pathlib import Path
import sys
from typing import Any, Optional
from urllib.error import HTTPError, URLError
import urllib.request

from constants import ICON_ERROR, ICON_INFO, ICON_SUCCESS, ICON_WARNING, LCC_CACHE_DIR
from util.ini_to_json_converter import CompactJSONEncoder


class LCCDataFetcher:
    """Récupère et cache les données depuis lcc-docs"""

    BASE_URL = "https://raw.githubusercontent.com/RiwsPy/lcc-docs/main/db/"

    FILES = {
        "default": "mods.json",
        "fr": "mods_fr.json",
        "en": "mods_en.json",
        "cn": "mods_cn.json",
    }
    LANGUAGE_CODES = {
        "fr": "fr_FR",
        "en": "en_US",
        "cn": "zh_CN",
    }

    # Mapping des catégories FR → codes JSON
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

    def __init__(self, cache_dir: Path = LCC_CACHE_DIR):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(exist_ok=True)
        self.data = {}
        self.tp2_index = {}  # Index: tp2 -> mod_data
        self.id_index = {}  # Index: id -> mod_data (pour résoudre [[id]])

    def fetch_all(self, force_refresh: bool = False) -> bool:
        """
        Récupère toutes les données LCC

        Args:
            force_refresh: Force le téléchargement même si le cache existe

        Returns:
            True si succès, False sinon
        """
        print("\n" + "=" * 60)
        print("  RÉCUPÉRATION DES DONNÉES LCC")
        print("=" * 60 + "\n")

        success = True

        for lang, filename in self.FILES.items():
            cache_file = self.cache_dir / filename

            # Utiliser le cache si disponible
            if cache_file.exists() and not force_refresh:
                print(f"→ {lang.upper()}: Lecture depuis le cache...")
                try:
                    with open(cache_file, "r", encoding="utf-8") as f:
                        self.data[lang] = json.load(f)
                    print(f"  ✓ {len(self.data[lang])} mod(s) chargé(s)")
                except Exception as e:
                    print(f"  ✗ Erreur lecture cache: {e}")
                    success = False
            else:
                # Télécharger
                url = self.BASE_URL + filename
                print(f"→ {lang.upper()}: Téléchargement depuis {url}...")

                data = self._download_json(url)
                if data:
                    self.data[lang] = data
                    print(f"  ✓ {len(data)} mod(s) téléchargé(s)")

                    # Sauvegarder en cache
                    try:
                        with open(cache_file, "w", encoding="utf-8") as f:
                            json.dump(data, f, indent=2, ensure_ascii=False)
                    except Exception as e:
                        print(f"  ⚠ Impossible de sauvegarder le cache: {e}")
                else:
                    print("  ✗ Échec du téléchargement")
                    success = False

        # Construire l'index tp2 depuis les données par défaut
        if "default" in self.data:
            self._build_tp2_index()
            self._build_id_index()

        return success

    def _download_json(self, url: str) -> Optional[dict]:
        """Télécharge un fichier JSON depuis une URL"""
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                data = response.read()
                return json.loads(data.decode("utf-8"))
        except (URLError, HTTPError) as e:
            print(f"    Erreur réseau: {e}")
            return None
        except json.JSONDecodeError as e:
            print(f"    JSON invalide: {e}")
            return None
        except Exception as e:
            print(f"    Erreur: {e}")
            return None

    def _build_tp2_index(self):
        """Construit un index tp2 -> mod_data pour recherche rapide"""
        for mod_data in self.data["default"]:
            tp2 = mod_data.get("tp2", "").lower()
            if tp2 not in ("", "n/a", "non-weidu"):
                parts = tp2.split(";")

                for part in parts:
                    self.tp2_index[part] = mod_data

    def _build_id_index(self):
        """Construit un index id -> mod_data pour résoudre les [[id]]"""
        for mod_data in self.data["default"]:
            mod_id = mod_data.get("id")
            if mod_id is not None:
                self.id_index[str(mod_id)] = mod_data

    def find_by_tp2(self, tp2_name: str) -> Optional[dict[str, Any]]:
        """
        Trouve un mod par son nom tp2

        Args:
            tp2_name: Nom du fichier (avec ou sans .tp2)

        Returns:
            Dictionnaire avec les données compilées ou None
        """
        # Normaliser le nom
        tp2_key = tp2_name.lower().replace(".tp2", "")

        if tp2_key not in self.tp2_index:
            return None

        mod_data = self.tp2_index[tp2_key]

        # Récupérer les données de toutes les langues
        result = {
            "name": mod_data["name"],
            "tp2": tp2_key,
            "safe": mod_data["safe"],
            "games": self._extract_games(mod_data),
            "categories": self._extract_categories(mod_data),
            "authors": self._extract_authors(mod_data),
            "descriptions": {},
        }

        # Descriptions autres langues
        for lang, lang_code in self.LANGUAGE_CODES.items():
            if lang not in self.data:
                continue

            # Trouver le même mod dans la base de données de cette langue
            translated_mod_data = self._find_mod_by_id(mod_data["id"], lang)

            if translated_mod_data and "description" in translated_mod_data:
                result["descriptions"][lang_code] = self._resolve_mod_references(
                    translated_mod_data["description"], lang
                )

        return result

    def _find_mod_in_lang(self, tp2: str, lang: str) -> Optional[dict]:
        """Trouve un mod dans une langue spécifique"""
        for mod_data in self.data.get(lang, []):
            if mod_data.get("tp2", "").lower() == tp2:
                return mod_data
        return None

    def _extract_games(self, mod_data: dict) -> list[str]:
        """Extrait et normalise la liste des jeux"""
        games = mod_data.get("games", [])
        if not games:
            return []

        # Convertir en minuscules
        return [game.lower() for game in games]

    def _extract_categories(self, mod_data: dict) -> list[str]:
        """Extrait et convertit les catégories"""
        categories = mod_data.get("categories", "")

        # Chercher la correspondance
        return [self.CATEGORY_MAP.get(cat, "") for cat in categories]

    def _extract_authors(self, mod_data: dict) -> list[str]:
        """Extrait et convertit les auteurs"""
        return mod_data.get("authors", [])

    def _resolve_mod_references(self, description: str, lang: str) -> str:
        """
        Résout les références [[id]] dans les descriptions

        Format: [[1234]] → Nom du mod

        Args:
            description: Texte avec potentiellement des [[id]]
            lang: Langue de la description (pour chercher le bon nom)

        Returns:
            Description avec les références résolues
        """
        import re

        # Pattern pour trouver [[nombre]]
        pattern = r"\[\[(\d+)\]\]"

        description = description.replace("|", "\n")

        def replace_reference(match):
            mod_id = match.group(1)

            # Chercher le mod dans l'index
            if mod_id not in self.id_index:
                # Mod non trouvé, garder la référence
                return f"[[{mod_id}]]"

            mod_data = self.id_index[mod_id]

            # Chercher le nom dans la langue appropriée
            lang_mod = self._find_mod_by_id(mod_id, lang)
            if lang_mod and "name" in lang_mod:
                mod_name = lang_mod["name"]
            else:
                # Fallback sur le nom par défaut
                mod_name = mod_data.get("name", f"Mod {mod_id}")

            return mod_name

        # Remplacer toutes les occurrences
        return re.sub(pattern, replace_reference, description)

    def _find_mod_by_id(self, mod_id: str, lang: str) -> Optional[dict]:
        """Trouve un mod par son ID dans une langue spécifique"""
        for mod_data in self.data.get(lang, []):
            if str(mod_data.get("id")) == str(mod_id):
                return mod_data
        return None


class JSONCompleter:
    """Complète les fichiers JSON avec les données LCC"""

    def __init__(self, lcc_fetcher: LCCDataFetcher, verbose: bool = True):
        self.fetcher = lcc_fetcher
        self.verbose = verbose
        self.stats = {"processed": 0, "completed": 0, "not_found": 0, "errors": 0}
        self.not_found_list = []

    def log(self, message: str, level: str = "INFO"):
        """Affiche un message si verbose"""
        if self.verbose:
            prefix = {
                "INFO": ICON_INFO,
                "SUCCESS": ICON_SUCCESS,
                "ERROR": ICON_ERROR,
                "WARNING": ICON_WARNING,
            }.get(level, "•")
            print(f"{prefix} {message}")

    def complete_file(self, json_path: Path) -> bool:
        """
        Complète un fichier JSON avec les données LCC

        Args:
            json_path: Chemin vers le fichier JSON

        Returns:
            True si des données ont été ajoutées, False sinon
        """
        self.stats["processed"] += 1

        try:
            # Lire le JSON existant
            with open(json_path, "r", encoding="utf-8") as f:
                mod_data = json.load(f)

            # Déterminer le tp2 depuis le nom du fichier
            tp2_name = json_path.stem

            self.log(f"Traitement: {json_path.name}")

            # Chercher dans LCC
            lcc_data = self.fetcher.find_by_tp2(tp2_name)

            if not lcc_data:
                self.log(f"  ⚠ Mod non trouvé dans LCC: {tp2_name}", "WARNING")
                self.stats["not_found"] += 1
                self.not_found_list.append(tp2_name)
                return False

            # Appliquer les données
            updated = False

            # name
            if not mod_data.get("name"):
                mod_data["name"] = lcc_data["name"]
                updated = True
                self.log(f"  + name: {lcc_data['name']}")

            # TP2
            if not mod_data.get("tp2"):
                mod_data["tp2"] = lcc_data["tp2"]
                updated = True
                self.log(f"  + tp2: {lcc_data['tp2']}")

            # safe
            if not mod_data.get("safe"):
                mod_data["safe"] = lcc_data["safe"]
                updated = True
                self.log(f"  + safe: {lcc_data['safe']}")

            # Games
            if not mod_data.get("games") and lcc_data["games"]:
                mod_data["games"] = lcc_data["games"]
                updated = True
                self.log(f"  + games: {', '.join(lcc_data['games'])}")

            # Categories
            if not mod_data.get("categories") and lcc_data["categories"]:
                mod_data["categories"] = lcc_data["categories"]
                updated = True
                self.log(f"  + categories: {', '.join(lcc_data['categories'])}")

            # Authors
            if not mod_data.get("authors") and lcc_data["authors"]:
                mod_data["authors"] = lcc_data["authors"]
                updated = True
                self.log(f"  + authors: {', '.join(lcc_data['authors'])}")

            # Descriptions
            if "translations" in mod_data:
                for lang_code, description in lcc_data["descriptions"].items():
                    if lang_code in mod_data["translations"]:
                        current_desc = mod_data["translations"][lang_code].get(
                            "description", ""
                        )
                    else:
                        current_desc = ""
                        mod_data["translations"][lang_code] = {}

                    # Ajouter ou remplacer si différent
                    if current_desc != description:
                        mod_data["translations"][lang_code]["description"] = description
                        updated = True
                        self.log(f"  + description ({lang_code})")

            # Sauvegarder si modifié
            if updated:
                with open(json_path, "w", encoding="utf-8") as f:
                    json_str = CompactJSONEncoder(indent=2, ensure_ascii=False).encode(mod_data)
                    f.write(json_str)
                    f.write("\n")

                self.log("  ✓ Fichier mis à jour", "SUCCESS")
                self.stats["completed"] += 1
                return True
            else:
                self.log("  → Aucune donnée manquante")
                return False

        except Exception as e:
            self.log(f"  ✗ Erreur: {e}", "ERROR")
            self.stats["errors"] += 1
            return False

    def complete_directory(self, json_dir: Path) -> dict[str, int]:
        """Complète tous les JSON d'un dossier"""
        json_files = list(json_dir.glob("*.json"))

        if not json_files:
            self.log(f"Aucun fichier .json trouvé dans {json_dir}", "WARNING")
            return self.stats

        print("\n" + "=" * 60)
        print(f"  COMPLÉTION DE {len(json_files)} FICHIER(S)")
        print("=" * 60 + "\n")

        for json_file in json_files:
            self.complete_file(json_file)

        return self.stats

    def print_summary(self):
        """Affiche le résumé"""
        print("\n" + "=" * 60)
        print("  RÉSUMÉ")
        print("=" * 60)
        print(f"  Traités      : {self.stats['processed']}")
        print(f"  ✓ Complétés  : {self.stats['completed']}")
        print(f"  ⚠ Non trouvés: {self.stats['not_found']}")
        print(f"  ✗ Erreurs    : {self.stats['errors']}")

        if self.not_found_list:
            print("\n  MODS NON TROUVÉS DANS LCC:")
            for tp2 in sorted(self.not_found_list):
                print(f"    - {tp2}")

        print("=" * 60 + "\n")


def print_usage():
    """Affiche l'aide"""
    print("""
╔══════════════════════════════════════════════════════════════╗
║      COMPLÉTEUR JSON DEPUIS LCC (lcc-docs)                  ║
╚══════════════════════════════════════════════════════════════╝

USAGE:
     python -m util.lcc_definition_updater <fichier_ou_dossier> [--refresh]

ARGUMENTS:
    fichier_ou_dossier   Fichier .json OU dossier contenant des .json
    --refresh            Force le téléchargement (ignore le cache)

EXEMPLES:
    # Compléter un fichier
     python -m util.lcc_definition_updater bp-bgt-worldmap.json

    # Compléter un dossier
     python -m util.lcc_definition_updater mods_json/

    # Forcer le refresh du cache
     python -m util.lcc_definition_updater mods_json/ --refresh

DONNÉES RÉCUPÉRÉES:
    • tp2          : Nom du fichier .tp2
    • games        : Liste des jeux supportés (minuscules)
    • categories   : Catégories du mod
    • descriptions : Descriptions FR/EN/CN

SOURCES:
    https://github.com/RiwsPy/lcc-docs/tree/main/db

NOTES:
    • Le cache est stocké dans .cache/
    • La correspondance se fait par nom de fichier = tp2
    • Les champs vides sont complétés, les existants préservés
""")


def main():
    """Point d'entrée"""

    # Vérifier les arguments
    if len(sys.argv) < 2 or sys.argv[1] in ["-h", "--help", "help"]:
        print_usage()
        sys.exit(0 if len(sys.argv) < 2 else 1)

    target = Path(sys.argv[1])
    force_refresh = "--refresh" in sys.argv

    if not target.exists():
        print(f"✗ {target} n'existe pas")
        sys.exit(1)

    # Banner
    print("\n" + "═" * 60)
    print("  COMPLÉTION JSON DEPUIS LCC")
    print("═" * 60)

    # Récupérer les données LCC
    fetcher = LCCDataFetcher()
    if not fetcher.fetch_all(force_refresh=force_refresh):
        print("\n✗ Impossible de récupérer les données LCC")
        print("  Vérifiez votre connexion internet ou réessayez plus tard")
        sys.exit(1)

    # Compléter les fichiers
    completer = JSONCompleter(fetcher, verbose=True)

    if target.is_file():
        if target.suffix.lower() != ".json":
            print(f"✗ {target} n'est pas un fichier .json")
            sys.exit(1)

        completer.complete_file(target)
    elif target.is_dir():
        completer.complete_directory(target)

    # Résumé
    completer.print_summary()

    # Code de sortie
    sys.exit(0 if completer.stats["errors"] == 0 else 1)


if __name__ == "__main__":
    main()
