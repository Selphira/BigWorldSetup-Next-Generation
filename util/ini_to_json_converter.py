#!/usr/bin/env python3
"""
Convertisseur de fichiers .ini de mods vers .json

USAGE:
    python ini_to_json_converter.py <source> <destination>

    source: fichier .ini OU dossier contenant des .ini
    destination: dossier de sortie pour les .json

EXEMPLES:
    # Convertir un fichier
    python ini_to_json_converter.py bp-bgt-worldmap.ini output/

    # Convertir un dossier complet
    python ini_to_json_converter.py mods_ini/ mods_json/
"""

import os
import sys
import json
import hashlib
import re
from pathlib import Path
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, asdict
import configparser


class CompactJSONEncoder(json.JSONEncoder):
    """
    Encodeur JSON personnalisé pour formatter compactement les components

    Format compact pour:
    - {"type": "std"}
    - {"options": ["1", "2"]}
    - {"components": ["1", "2"]}

    Format normal pour le reste
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.current_indent_level = 0

    def encode(self, o):
        """Encode l'objet avec formatting personnalisé"""
        if isinstance(o, dict):
            return self._encode_dict(o, 0)
        return super().encode(o)

    def _encode_dict(self, d: dict, indent_level: int) -> str:
        """Encode un dictionnaire avec indentation"""
        if not d:
            return "{}"

        indent = "  " * indent_level
        next_indent = "  " * (indent_level + 1)

        # Cas spéciaux pour formatting compact
        if self._is_compact_dict(d):
            return self._encode_compact(d)

        # Formatting normal
        items = []
        for key, value in d.items():
            key_str = json.dumps(key)

            if isinstance(value, dict):
                if self._is_compact_dict(value):
                    value_str = self._encode_compact(value)
                else:
                    value_str = self._encode_dict(value, indent_level + 1)
            elif isinstance(value, list):
                value_str = self._encode_list(value)
            else:
                value_str = json.dumps(value, ensure_ascii=False)

            items.append(f'{next_indent}{key_str}: {value_str}')

        return "{\n" + ",\n".join(items) + f"\n{indent}}}"

    def _encode_list(self, lst: list) -> str:
        """Encode une liste (toujours compact)"""
        if not lst:
            return "[]"
        items = [json.dumps(item, ensure_ascii=False) for item in lst]
        return "[" + ", ".join(items) + "]"

    def _is_compact_dict(self, d: dict) -> bool:
        """Vérifie si un dict doit être formatté en compact"""
        # {"type": "std"} ou {"type": "muc"}
        if set(d.keys()) == {"type"}:
            return True

        # {"type": "std", ...} avec peu de clés
        if "type" in d and d["type"] in ["std", "muc", "sub"] and len(d) <= 10:
            return True

        # {"options": [...]}
        if set(d.keys()) == {"options"}:
            return True

        # {"components": [...]}
        if set(d.keys()) == {"components"}:
            return True

        return False

    def _encode_compact(self, d: dict) -> str:
        """Encode un dict en format compact sur une ligne"""
        items = []
        for key, value in d.items():
            key_str = json.dumps(key)
            if isinstance(value, list):
                value_str = self._encode_list(value)
            else:
                value_str = json.dumps(value, ensure_ascii=False)
            items.append(f'"{key}": {value_str}')

        return "{" + ", ".join(items) + "}"

@dataclass
class ModMetadata:
    """Structure des métadonnées d'un mod"""
    name: str
    version: str
    links: Dict[str, str]
    file: Dict[str, Any]
    languages: Dict[str, int]
    games: List[str]
    categories: List[str]
    tp2: str
    safe: int
    components: Dict[str, Any]
    translations: Dict[str, Dict[str, Any]]


class INIToJSONConverter:
    """
    Convertisseur de fichiers .ini vers .json

    GÈRE:
    ✓ Fichiers individuels ou dossiers
    ✓ Sections [Mod], [WeiDU-XX], [Description]
    ✓ Parsing des langues (Tra=EN:0,FR:5)
    ✓ Parsing des composants WeiDU
    ✓ Nettoyage des caractères spéciaux
    ✓ Validation de la structure
    """

    # Mapping des codes de langues INI → JSON
    LANGUAGE_MAP = {
        'EN': 'en_US',
        'FR': 'fr_FR',
        'DE': 'de_DE',
        'GE': 'de_DE',
        'ES': 'es_ES',
        'SP': 'es_ES',
        'IT': 'it_IT',
        'PL': 'pl_PL',
        'RU': 'ru_RU',
        'CS': 'cs_CZ',
        'PT': 'pt_PT',
        'BR': 'pt_BR',
        'NL': 'nl_NL',
        'NO': 'nb_NO',
        'SV': 'sv_SE',
        'DA': 'da_DK',
        'FI': 'fi_FI',
        'TR': 'tr_TR',
        'HU': 'hu_HU',
        'RO': 'ro_RO',
        'BG': 'bg_BG',
        'EL': 'el_GR',
        'JA': 'ja_JP',
        'KO': 'ko_KR',
        'ZH': 'zh_CN',
        'CH': 'zh_CN',
        '--': 'all',
    }

    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        self.stats = {
            'converted': 0,
            'failed': 0,
            'skipped': 0
        }

    def log(self, message: str, level: str = 'INFO'):
        """Affiche un message si verbose"""
        if self.verbose:
            prefix = {
                'INFO': '→',
                'SUCCESS': '✓',
                'ERROR': '✗',
                'WARNING': '⚠'
            }.get(level, '•')
            print(f"{prefix} {message}")

    def calculate_sha256(self, file_path):
        """
        Calcule le hachage SHA256 d'un fichier spécifié par son chemin.
        """
        # Créer un objet hash SHA256
        sha256_hash = hashlib.sha256()

        # Ouvrir le fichier en mode lecture binaire ('rb')
        try:
            with open(file_path, "rb") as f:
                # Lire le fichier par morceaux (chunks) de 4096 octets
                for byte_block in iter(lambda: f.read(4096), b""):
                    sha256_hash.update(byte_block)

            # Retourner le hachage final sous forme de chaîne hexadécimale
            return sha256_hash.hexdigest()

        except FileNotFoundError:
            self.log(f"Erreur : Le fichier '{file_path}' est introuvable.", 'WARNING')
        except Exception as e:
            self.log(f"Une erreur est survenue lors de la lecture du fichier : {e}", 'WARNING')
        return ""

    def convert_file(self, ini_path: Path, output_dir: Path, files_folder: Path) -> bool:
        """
        Convertit un fichier .ini en .json

        Args:
            ini_path: Chemin vers le fichier .ini
            output_dir: Dossier de sortie

        Returns:
            True si succès, False sinon
        """
        #try:
        self.log(f"Conversion: {ini_path.name}")

        # Lire le fichier INI
        config = self._read_ini_file(ini_path)

        # Extraire les données
        mod_data = self._extract_mod_data(config, ini_path, files_folder)

        # Créer le fichier JSON
        json_filename = ini_path.stem + '.json'
        json_path = output_dir / json_filename

        with open(json_path, 'w', encoding='utf-8') as f:
            json_str = CompactJSONEncoder(
                indent=2,
                ensure_ascii=False
            ).encode(mod_data)
            f.write(json_str)
            f.write('\n')  # Ajouter une ligne vide à la fin

        self.log(f"Créé: {json_filename}", 'SUCCESS')
        self.stats['converted'] += 1
        return True

        '''
        except Exception as e:
            self.log(f"Erreur lors de la conversion de {ini_path.name}: {e}", 'ERROR')
            self.stats['failed'] += 1
            return False
        '''

    def _read_ini_file(self, path: Path) -> configparser.ConfigParser:
        """Lit un fichier INI avec gestion des cas particuliers"""
        config = configparser.ConfigParser(interpolation=None, strict=True)

        # Lire avec plusieurs encodages possibles
        for encoding in ['utf-8', 'cp1252', 'latin-1']:
            try:
                config.read(path, encoding=encoding)
                return config
            except UnicodeDecodeError:
                continue

        raise ValueError(f"Impossible de lire {path} avec les encodages supportés")

    def _extract_mod_data(self, config: configparser.ConfigParser, ini_path: Path, files_folder: Path) -> Dict[str, Any]:
        """Extrait toutes les données du fichier INI"""

        # Section [Mod]
        mod_section = dict(config['Mod']) if 'Mod' in config else {}
        # Nom et version
        name = mod_section.get('name')
        version = mod_section.get('rev')

        # Liens
        links = {}
        if 'link' in mod_section:
            links['homepage'] = mod_section['link']
        if 'down' in mod_section:
            links['download'] = mod_section['down']

        # Fichier
        file_info = {}
        file_sha256 = ""
        if 'save' in mod_section:
            file_info['filename'] = mod_section['save']
            file = files_folder / mod_section['save']
            if file.exists():
                file_sha256 = self.calculate_sha256(file)
                try:
                    file_info['size'] = os.path.getsize(file)
                except ValueError:
                    file_info['size'] = 0

        file_info['sha256'] = file_sha256

        # Langues (parsing de Tra=EN:0,FR:5)
        languages = self._parse_languages(mod_section.get('tra', ''))

        # Traductions (sections [WeiDU-XX] et [Description])
        translations = self._extract_translations(config, languages)

        # Génération de la structure components
        components = {}
        if 'WeiDU-FR' in config:
            weidu_fr = dict(config['WeiDU-FR'])
            components = self._generate_components_structure(weidu_fr)
        elif 'WeiDU-EN' in config:
            weidu_en = dict(config['WeiDU-EN'])
            components = self._generate_components_structure(weidu_en)

        # Structure finale
        return {
            "name": name,
            "version": version,
            "links": links,
            "file": file_info,
            "games": [],  # Rempli via lcc_definition_updater.py
            "categories": [],  # Rempli via lcc_definition_updater.py
            "components": components,
            "languages": languages,
            "translations": translations,
            "tp2": "",  # Rempli via lcc_definition_updater.py
            "safe": 2,  # Rempli via lcc_definition_updater.py
            "authors": [],  # Rempli via lcc_definition_updater.py
        }

    def _parse_languages(self, tra_string: str) -> Dict[str, int]:
        """
        Parse la chaîne Tra du format INI

        Format: EN:0,FR:5,DE:1

        Returns:
            Dict {en_US: 0, fr_FR: 5, ...}
        """
        languages = {}

        if not tra_string:
            return languages

        # Découper par virgule
        parts = tra_string.split(',')

        for part in parts:
            part = part.strip()
            if ':' in part:
                lang_code, tra_index = part.split(':', 1)
                lang_code = lang_code.strip().upper()

                # Convertir le code de langue
                lang = self.LANGUAGE_MAP.get(lang_code)

                try:
                    if lang:
                        languages[lang] = int(tra_index)
                except ValueError:
                    self.log(f"Index TRA invalide pour {lang_code}: {tra_index}", 'WARNING')

        return languages

    def _extract_translations(self, config: configparser.ConfigParser,
                              languages: Dict[str, int]) -> Dict[str, Dict[str, Any]]:
        """
        Extrait les traductions depuis les sections [WeiDU-XX] et [Description]

        Args:
            config: ConfigParser avec le contenu INI
            languages: Dict des langues détectées

        Returns:
            Dict avec les traductions par langue
        """
        translations = {}

        for json_lang, tra_index in languages.items():
            # Trouver le code court (EN, FR, etc.)
            ini_lang = self._json_to_ini_lang(json_lang)

            if not ini_lang:
                continue

            # Section WeiDU
            weidu_section = f"WeiDU-{ini_lang}"
            description_key = f"Mod-{ini_lang}"

            translation = {}

            # Description
            if 'Description' in config and description_key in config['Description']:
                desc = config['Description'][description_key]

                # Nettoyer les | en \n
                translation['description'] = desc.replace('|', '\n')

            # Composants WeiDU
            if weidu_section in config:
                components = self._parse_weidu_components(
                    dict(config[weidu_section])
                )
                translation['components'] = components

            if translation:
                translations[json_lang] = translation

        return translations

    def _json_to_ini_lang(self, json_lang: str) -> Optional[str]:
        """Convertit un code JSON (en_US) en code INI (EN)"""
        for ini_code, json_code in self.LANGUAGE_MAP.items():
            if json_code == json_lang:
                return ini_code
        # Fallback: prendre les 2 premiers caractères en majuscules
        return None

    def _generate_components_structure(self, weidu_dict: Dict[str, str]) -> Dict[str, Any]:
        """
        Génère la structure 'components' depuis les composants WeiDU français

        RÈGLES:
        1. Composant de base = type "std"
        2. Composant avec "->" = partie d'un groupe "muc" (choice_0, choice_1, ...)
        3. Composant avec "?" = type "sub" avec prompts

        IMPORTANT: L'ordre d'apparition est préservé
        """
        components = {}
        muc_groups = {}  # Temporaire pour grouper les MUC
        sub_components = {}  # Temporaire pour grouper les sub-prompts
        component_order = []  # Pour tracer l'ordre d'apparition
        muc_index = 0

        # Extraire les clés dans l'ordre
        ordered_keys = [k for k in weidu_dict.keys() if k.startswith('@')]

        # Première passe: identifier tous les composants et leur ordre
        for key in ordered_keys:
            value = weidu_dict[key].strip()
            component_key = key[1:]  # Enlever @

            # Déterminer le type et l'ordre
            if '?' in component_key:
                # SUB: enregistrer le composant de base
                base_id = component_key.split('?')[0]
                if base_id not in component_order:
                    component_order.append(('sub', base_id))

                # Parser le prompt
                parts = component_key.split('?', 1)
                prompt_part = parts[1]

                if '_' in prompt_part:
                    prompt_id, option_id = prompt_part.split('_', 1)
                else:
                    prompt_id = prompt_part
                    option_id = "1"

                # Stocker
                if base_id not in sub_components:
                    sub_components[base_id] = {}
                if prompt_id not in sub_components[base_id]:
                    sub_components[base_id][prompt_id] = []

                sub_components[base_id][prompt_id].append(option_id)

            elif '->' in value:
                # MUC
                parts = value.split('->', 1)
                muc_label = parts[0].strip()

                # Créer le groupe MUC à la première occurrence
                if muc_label not in muc_groups:
                    muc_key = f"choice_{muc_index}"
                    muc_groups[muc_label] = {
                        'key': muc_key,
                        'components': [],
                        'first_component': component_key
                    }
                    # Enregistrer l'ordre au premier composant du groupe
                    component_order.append(('muc', muc_label))
                    muc_index += 1

                muc_groups[muc_label]['components'].append(component_key)

            else:
                # STD
                component_order.append(('std', component_key))

        # Deuxième passe: construire dans l'ordre
        for comp_type, comp_id in component_order:
            if comp_type == 'std':
                # Composant standard
                components[comp_id] = {"type": "std"}

            elif comp_type == 'sub':
                # Composant avec prompts
                if comp_id in sub_components:
                    prompts = {}
                    for prompt_id in sorted(sub_components[comp_id].keys()):
                        options = sorted(sub_components[comp_id][prompt_id])
                        prompts[prompt_id] = {"options": options}

                    components[comp_id] = {
                        "type": "sub",
                        "prompts": prompts
                    }

            elif comp_type == 'muc':
                # Groupe MUC
                muc_data = muc_groups[comp_id]
                components[muc_data['key']] = {
                    "type": "muc",
                    "components": muc_data['components']
                }

        return components

    def _normalize_muc_key(self, label: str, fallback_key: str) -> str:
        """
        Normalise un label MUC en clé valide

        "Worldmap for Throne of Bhaal" → "worldmap_for_throne_of_bhaal"
        "TOB Style" → "tob_style"
        "UI Style" → "ui_style"

        Args:
            label: Label original du MUC
            fallback_key: Clé de secours (choice_0, choice_1, ...)

        Returns:
            Clé normalisée
        """
        # Convertir en minuscules
        key = label.lower()

        # Remplacer les espaces et caractères spéciaux par _
        key = re.sub(r'[^\w]+', '_', key)

        # Enlever les _ multiples
        key = re.sub(r'_+', '_', key)

        # Enlever les _ au début et à la fin
        key = key.strip('_')

        # Si trop long ou vide, utiliser le fallback
        if len(key) > 30 or not key:
            return fallback_key

        return key

    def _parse_weidu_components(self, weidu_dict: Dict[str, str]) -> Dict[str, str]:
        components = {}
        prev_muc = ""
        muc_idx = 0

        for key, value in weidu_dict.items():
            if key.startswith('@'):
                # Nettoyer la clé
                component_id = key[1:]  # Enlever @

                # Convertir ? en . pour les sous-composants
                # @0?1_1 → 0.1.1
                component_id = component_id.replace('?', '.')
                component_id = component_id.replace('_', '.')

                # Nettoyer la valeur
                value = value.strip()

                # Enlever "-> " si présent (c'est juste du formatage)
                if '->' in value:
                    parts = value.split('->', 1)
                    muc = parts[0].strip()
                    if muc != prev_muc:
                        components[f"choice_{muc_idx}"] = muc
                        muc_idx += 1
                        prev_muc = muc

                    value = parts[1].strip() if len(parts) > 1 else parts[0].strip()

                components[component_id] = value

        # Enlever Tra=X qui est une métadonnée
        components.pop('Tra', None)

        return components

    def convert_directory(self, source_dir: Path, output_dir: Path, files_folder: Path) -> Dict[str, int]:
        """
        Convertit tous les fichiers .ini d'un dossier

        Args:
            source_dir: Dossier source avec les .ini
            output_dir: Dossier de sortie pour les .json

        Returns:
            Statistiques de conversion
        """
        # Créer le dossier de sortie
        output_dir.mkdir(parents=True, exist_ok=True)

        # Trouver tous les .ini
        ini_files = list(source_dir.glob('*.ini'))

        if not ini_files:
            self.log(f"Aucun fichier .ini trouvé dans {source_dir}", 'WARNING')
            return self.stats

        self.log(f"Trouvé {len(ini_files)} fichier(s) .ini")
        print()

        # Convertir chaque fichier
        for ini_file in ini_files:
            self.convert_file(ini_file, output_dir, files_folder)

        return self.stats

    def process(self, source: Path, destination: Path, files_folder: Path) -> Dict[str, int]:
        """
        Point d'entrée principal: détecte automatiquement fichier ou dossier

        Args:
            source: Fichier .ini ou dossier
            destination: Dossier de sortie

        Returns:
            Statistiques de conversion
        """
        destination = Path(destination)

        if source.is_file():
            # Fichier unique
            if source.suffix.lower() != '.ini':
                self.log(f"{source} n'est pas un fichier .ini", 'ERROR')
                return self.stats

            destination.mkdir(parents=True, exist_ok=True)
            self.convert_file(source, destination, files_folder)

        elif source.is_dir():
            # Dossier
            self.convert_directory(source, destination, files_folder)

        else:
            self.log(f"{source} n'existe pas", 'ERROR')
            self.stats['failed'] += 1

        return self.stats


def print_usage():
    """Affiche l'aide d'utilisation"""
    print("""
╔══════════════════════════════════════════════════════════════╗
║         CONVERTISSEUR INI → JSON POUR MODS BALDUR'S GATE    ║
╚══════════════════════════════════════════════════════════════╝

USAGE:
    python ini_to_json_converter.py <source> <destination>

ARGUMENTS:
    source       Fichier .ini OU dossier contenant des .ini
    destination  Dossier de sortie pour les .json
    download     Dossier contenant les archives des mods pour le calcul du sha256

EXEMPLES:
    # Convertir un fichier
    python ini_to_json_converter.py bp-bgt-worldmap.ini output/ download/

    # Convertir un dossier complet
    python ini_to_json_converter.py mods_ini/ mods_json/ download/

    # Avec chemins absolus
    python ini_to_json_converter.py /path/to/mods/ /path/to/output/ /path/to/download/

NOTES:
    • Les champs tp2, games, categories et sha256 seront vides
    • Les components seront à traiter ultérieurement
    • Les encodages UTF-8, CP1252 et Latin-1 sont supportés
    • Les fichiers existants seront écrasés
""")


def main():
    """Point d'entrée du script"""

    # Vérifier les arguments
    if len(sys.argv) != 4:
        print_usage()
        sys.exit(1)

    source_arg = sys.argv[1]
    dest_arg = sys.argv[2]
    files_arg = sys.argv[3]

    # Affichage spécial pour l'aide
    if source_arg in ['-h', '--help', 'help']:
        print_usage()
        sys.exit(0)

    # Convertir en Path
    source = Path(source_arg)
    destination = Path(dest_arg)
    files_folder = Path(files_arg)

    # Banner
    print("\n" + "═" * 60)
    print("  CONVERSION INI → JSON")
    print("═" * 60 + "\n")

    # Créer le convertisseur
    converter = INIToJSONConverter(verbose=True)

    # Traiter
    stats = converter.process(source, destination, files_folder)

    # Résumé
    print("\n" + "═" * 60)
    print("  RÉSUMÉ")
    print("═" * 60)
    print(f"  ✓ Convertis : {stats['converted']}")
    print(f"  ✗ Échecs    : {stats['failed']}")
    print(f"  ⊘ Ignorés   : {stats['skipped']}")
    print("═" * 60 + "\n")

    # Code de sortie
    sys.exit(0 if stats['failed'] == 0 else 1)


if __name__ == "__main__":
    main()