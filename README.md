[![Github Releases](https://img.shields.io/github/v/release/Selphira/BigWorldSetup-Enhanced-Edition)](https://github.com/Selphira/BigWorldSetup-Enhanced-Edition/releases/latest)
![Langues](https://img.shields.io/static/v1?label=Langues&message=Français%20%7C%20English&color=limegreen)
![Jeux supportés](https://img.shields.io/static/v1?label=Jeux%20supportés&message=EET%20%7C%20BG2EE%20%7C%20BGEE%20%7C%20SOD%20%7C%20IWDEE%20%7C%20PSTEE&color=dodgerblue)

# Big World Setup - Next Generation (BWS-NG)

## Présentation

**Big World Setup - Next Generation** est une application graphique dédiée à l'installation automatisée
de mods pour les jeux Infinity Engine.

Elle a pour objectif de fournir un outil moderne, fiable et maintenable permettant de gérer des installations
complexes de mods, tout en restant fidèle aux principes et à l'esprit du projet historique **Big World Setup FR**.

---

## Héritage de Big World Setup FR

**Big World Setup - Next Generation** est l'évolution directe
de [Big World Setup FR](<https://github.com/Selphira/BigWorldSetupFR>).

Le projet original a rendu de grands services à la communauté pendant plusieurs années, mais il présentait
progressivement plusieurs limitations :

* Le language AutoIt est principalement orienté vers Windows
* La base de code est difficile à maintenir et à faire évoluer
* L'interface est peu réactive
* Seuls les mods traduits en Français et compatibles EET étaient supportés
* Impossibilité d'ajouter des mods non supportés
* Impossibilité de modifier l'ordre d'installation

**Big World Setup - Next Generation** a été conçu tout d'abord par désir d'apprendre le Python, et pour répondre à ces
problèmes de fond, sans renier les acquis fonctionnels du projet d'origine.

---

## Fonctionnalités supplémentaires par rapport à Big World Setup FR

* Support de BG2EE, BGEE, SOD, IWDEE, PSTEE
* Compatibilité Linux
* Interface fluide
* Synchronisation des données avec
  la [Liste des mods de l'Infinity Engine](<https://riwspy.github.io/lcc-docs/>)
* Support de multiples langues pour l'interface et les mods
* Divers filtres sur les mods et les composants
* Possibilité d'ajouter des mods non supportés
* Possibilité de modifier l'ordre d'installation
* Gestion des règles d'ordre d'installation
* Possibilité d'importer un ordre d'installation depuis un fichier Weidu.log

## Mise à jour automatique

Au démarrage de l'application, une vérification automatique des données est effectuée.

Les informations relatives aux mods et aux règles d'installation sont comparées à celles disponibles sur le dépôt
officiel.
Si vos données locales sont plus anciennes, elles sont mises à jour automatiquement, sans action de votre part.

Une vérification distincte concerne la version de l'application elle-même.
Si une nouvelle version est disponible, vous en serez informé au lancement, mais la mise à jour de l'application reste
manuelle.

## Déroulement d'une installation

L'installation d'un ensemble de mods est conçue pour être simple et largement automatisée.
Une fois vos choix effectués, l'application prend en charge la majorité des opérations techniques.

Voici le déroulement typique d'une installation :

- Lancez l'application
- Sélectionnez le jeu que vous souhaitez modder
- Configurez les dossiers du jeu, de sauvegarde et de téléchargement
- Créez ou restaurez une sauvegarde des fichiers du jeu
- Sélectionnez les mods et les composants à installer
- Vérifiez et, si nécessaire, ajustez l'ordre d'installation proposé
- Lancez le téléchargement des archives
- Lancez l'extraction des archives
- Lancez l'installation des mods

## État du projet

**Big World Setup - Next Generation** est en cours de développement actif.

Toutes les fonctionnalités du **Big World Setup FR** ne sont pas encore adaptées, et les nouvelles fonctionnalités ne
sont pas encore toutes développées.

---

## Licence et crédits

Ce projet est distribué sous licence GNU General Public License v3.0 (GPL-3.0).

Cela signifie notamment que :

* le code source est librement accessible
* les modifications et redistributions doivent conserver la même licence
* toute redistribution doit fournir le code source correspondant

Pour plus de détails, voir le fichier LICENSE ou le texte complet de la licence GNU GPL v3.

*Inspiré et issu du projet [Big World Setup FR](<https://github.com/Selphira/BigWorldSetupFR>) qui est lui-même issu du
projet [Big World Setup](<https://github.com/BigWorldSetup/BigWorldSetup>).*
