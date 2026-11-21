"""Freshness and translation fallback of the localized mod and rule caches."""

import json
import os
from pathlib import Path

from core.LocalizedCache import first_translation, is_cache_stale
from core.ModManager import ModManager
from core.RuleManager import RuleManager


def _write(path: Path, value: object, mtime: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    os.utime(path, (mtime, mtime))
    return path


def test_cache_is_stale_when_one_language_is_missing(tmp_path: Path) -> None:
    cache = _write(tmp_path / "mods_en_US.json", [], 200)
    source = _write(tmp_path / "src" / "a.json", {}, 100)

    assert is_cache_stale([cache, tmp_path / "mods_fr_FR.json"], [source])


def test_cache_is_stale_only_when_a_source_is_newer_than_the_oldest_cache(tmp_path: Path) -> None:
    older_cache = _write(tmp_path / "mods_en_US.json", [], 150)
    newer_cache = _write(tmp_path / "mods_fr_FR.json", [], 300)
    source = _write(tmp_path / "src" / "a.json", {}, 200)

    assert is_cache_stale([older_cache, newer_cache], [source])

    os.utime(source, (100, 100))
    assert not is_cache_stale([older_cache, newer_cache], [source])


def test_complete_cache_without_sources_is_kept(tmp_path: Path) -> None:
    cache = _write(tmp_path / "rules_en_US.json", {}, 100)

    assert not is_cache_stale([cache], [])


def test_translation_falls_back_to_first_language_providing_text() -> None:
    translations = {"en_US": "English", "de_DE": "", "fr_FR": "Français"}

    assert first_translation(translations, "fr_FR", lambda text: text) == "Français"
    assert first_translation(translations, "de_DE", lambda text: text) == "English"
    assert first_translation(translations, "ko_KR", lambda text: text) == "English"
    assert first_translation({"en_US": ""}, "fr_FR", lambda text: text) == ""


def test_missing_mod_sources_require_a_rebuild(tmp_path: Path) -> None:
    manager = ModManager(tmp_path / "mods", tmp_path / "custom", tmp_path / "cache")

    assert manager.needs_cache_rebuild()


def test_complete_rule_cache_without_rule_files_is_kept(
    rules: RuleManager, tmp_path: Path
) -> None:
    for cache_file in rules._cache_files():
        _write(cache_file, {}, 100)

    assert not rules.needs_cache_rebuild()

    _write(tmp_path / "rules" / "order.json", {"rules": []}, 200)
    assert rules.needs_cache_rebuild()
