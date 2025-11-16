#!/usr/bin/env python3
"""
Utilitaire de gestion du cache LCC

USAGE:
    python lcc_cache_manager.py <command>

COMMANDES:
    info     Affiche les informations du cache
    clear    Supprime le cache
    stats    Affiche les statistiques des données LCC
"""

import json
import sys
from datetime import datetime

from constants import LCC_CACHE_DIR


def print_cache_info():
    """Affiche les informations du cache"""
    print("\n" + "=" * 60)
    print("  INFORMATIONS DU CACHE LCC")
    print("=" * 60 + "\n")

    if not LCC_CACHE_DIR.exists():
        print("  Aucun cache trouvé")
        return

    files = {
        'mods.json': 'Français',
        'mods_en.json': 'Anglais',
        'mods_cn.json': 'Chinois'
    }

    total_size = 0

    for filename, lang in files.items():
        filepath = LCC_CACHE_DIR / filename

        if filepath.exists():
            size = filepath.stat().st_size
            total_size += size
            mtime = datetime.fromtimestamp(filepath.stat().st_mtime)

            # Compter les mods
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    count = len(data)
            except:
                count = '?'

            print(f"  {lang:10} : {count:>4} mod(s) | {size:>8} bytes | {mtime.strftime('%Y-%m-%d %H:%M')}")
        else:
            print(f"  {lang:10} : Non présent")

    if total_size > 0:
        print(f"\n  Taille totale: {total_size:,} bytes ({total_size / 1024 / 1024:.2f} MB)")
        print(f"  Emplacement  : {LCC_CACHE_DIR.absolute()}")

    print()


def clear_cache():
    """Supprime le cache"""
    print("\n" + "=" * 60)
    print("  SUPPRESSION DU CACHE")
    print("=" * 60 + "\n")

    if not LCC_CACHE_DIR.exists():
        print("  Aucun cache à supprimer")
        return

    import shutil

    try:
        shutil.rmtree(LCC_CACHE_DIR)
        print("  ✓ Cache supprimé avec succès")
    except Exception as e:
        print(f"  ✗ Erreur: {e}")


def print_stats():
    """Affiche les statistiques des données"""
    print("\n" + "=" * 60)
    print("  STATISTIQUES DES DONNÉES LCC")
    print("=" * 60 + "\n")

    if not LCC_CACHE_DIR.exists():
        print("  Aucun cache trouvé. Lancez d'abord lcc_definition_updater.py")
        return

    # Charger les données françaises (référence)
    fr_file = LCC_CACHE_DIR / 'mods.json'
    if not fr_file.exists():
        print("  Fichier mods.json introuvable dans le cache")
        return

    with open(fr_file, 'r', encoding='utf-8') as f:
        mods = json.load(f)

    print(f"  Total de mods: {len(mods)}\n")

    # Statistiques par catégorie
    categories = {}
    for mod in mods:
        cat = mod.get('categories', 'Non catégorisé')
        for c in cat:
            categories[c] = categories.get(c, 0) + 1

    print("  RÉPARTITION PAR CATÉGORIE:")
    for cat, count in sorted(categories.items(), key=lambda x: -x[1]):
        print(f"    {cat:30} : {count:>4} mod(s)")

    # Statistiques par jeu
    print("\n  SUPPORT PAR JEU:")
    games_count = {}
    for mod in mods:
        for game in mod.get('games', []):
            game_lower = game.lower()
            games_count[game_lower] = games_count.get(game_lower, 0) + 1

    for game, count in sorted(games_count.items(), key=lambda x: -x[1]):
        print(f"    {game:30} : {count:>4} mod(s)")

    # Mods sans tp2
    no_tp2 = [mod for mod in mods if not mod.get('tp2')]
    if no_tp2:
        print(f"\n  ⚠ {len(no_tp2)} mod(s) sans tp2 défini")

    print()


def main():
    """Point d'entrée"""

    if len(sys.argv) < 2:
        print("""
USAGE:
    python lcc_cache_manager.py <command>

COMMANDES:
    info     Affiche les informations du cache
    clear    Supprime le cache
    stats    Affiche les statistiques des données LCC
        """)
        sys.exit(1)

    command = sys.argv[1].lower()

    if command == 'info':
        print_cache_info()
    elif command == 'clear':
        clear_cache()
    elif command == 'stats':
        print_stats()
    else:
        print(f"✗ Commande inconnue: {command}")
        print("  Commandes disponibles: info, clear, stats")
        sys.exit(1)


if __name__ == "__main__":
    main()