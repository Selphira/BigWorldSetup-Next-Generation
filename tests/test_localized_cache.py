"""Freshness and translation fallback of the localized mod and rule caches."""

import json
import os
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from core.ComponentReference import ComponentReference, IndexManager
from core.LocalizedCache import first_translation
from core.ModManager import ModManager
from core.RuleManager import RuleManager

OLD_DATE = 100


def _write(path: Path, value: object, mtime: float = OLD_DATE) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    os.utime(path, (mtime, mtime))
    return path


def _mod(name: str) -> dict:
    return {"name": name, "tp2": name, "components": {"0": {"type": "std"}}, "links": {}}


def _build(manager: ModManager | RuleManager, qtbot: QtBot) -> None:
    with qtbot.waitSignal(manager.cache_ready, timeout=10_000):
        assert manager.build_cache_async()


@pytest.fixture
def mods(tmp_path: Path) -> ModManager:
    _write(tmp_path / "mods" / "alpha.json", _mod("alpha"))
    _write(tmp_path / "mods" / "beta.json", _mod("beta"))
    return ModManager(tmp_path / "mods", tmp_path / "custom", tmp_path / "cache")


# ========================================
# FRESHNESS
# ========================================


def test_built_cache_is_fresh(mods: ModManager, qtbot: QtBot) -> None:
    _build(mods, qtbot)

    assert not mods.needs_cache_rebuild()


def test_rewriting_identical_sources_keeps_the_cache(
    mods: ModManager, qtbot: QtBot, tmp_path: Path
) -> None:
    _build(mods, qtbot)
    _write(tmp_path / "mods" / "alpha.json", _mod("alpha"), mtime=10**10)

    assert not mods.needs_cache_rebuild()


@pytest.mark.parametrize(
    "change",
    [
        lambda mods_dir: _write(mods_dir / "alpha.json", _mod("renamed")),
        lambda mods_dir: (mods_dir / "beta.json").unlink(),
        lambda mods_dir: _write(mods_dir / "gamma.json", _mod("gamma")),
    ],
    ids=["replaced by an older file", "removed", "added with an old date"],
)
def test_changed_sources_require_a_rebuild_whatever_their_date(
    mods: ModManager, qtbot: QtBot, tmp_path: Path, change
) -> None:
    _build(mods, qtbot)
    for cache_file in [*mods._cache_files(), mods._fingerprint_file()]:
        os.utime(cache_file, (10**10, 10**10))

    change(tmp_path / "mods")

    assert mods.needs_cache_rebuild()


def test_cache_is_stale_when_one_language_is_missing(mods: ModManager, qtbot: QtBot) -> None:
    _build(mods, qtbot)
    mods._cache_files()[-1].unlink()

    assert mods.needs_cache_rebuild()


def test_cache_without_fingerprint_is_rebuilt(mods: ModManager, qtbot: QtBot) -> None:
    _build(mods, qtbot)
    mods._fingerprint_file().unlink()

    assert mods.needs_cache_rebuild()


def test_missing_mod_sources_require_a_rebuild(tmp_path: Path) -> None:
    manager = ModManager(tmp_path / "mods", tmp_path / "custom", tmp_path / "cache")

    assert manager.needs_cache_rebuild()


def test_complete_rule_cache_without_rule_files_is_kept(
    rules: RuleManager, qtbot: QtBot, tmp_path: Path
) -> None:
    _build(rules, qtbot)

    assert not rules.needs_cache_rebuild()

    _write(tmp_path / "rules" / "order.json", {"rules": []})
    assert rules.needs_cache_rebuild()


def test_rules_of_a_mod_definition_are_rule_cache_sources(
    rules: RuleManager, qtbot: QtBot, tmp_path: Path
) -> None:
    _write(tmp_path / "mods" / "alpha.json", _mod("alpha"))
    _build(rules, qtbot)

    _write(
        tmp_path / "mods" / "alpha.json",
        {
            **_mod("alpha"),
            "rules": {"order": [{"rule": "alpha(-):beta(-)", "direction": "after"}]},
        },
    )

    assert rules.needs_cache_rebuild()


# ========================================
# CATALOGUE SUBSTITUTION
# ========================================


def test_reloaded_catalogue_leaves_no_reference_to_a_removed_mod(
    mods: ModManager, qtbot: QtBot, tmp_path: Path
) -> None:
    _build(mods, qtbot)
    indexes = IndexManager.get_indexes()
    assert ComponentReference.for_mod("beta") in indexes.mod_component_index

    (tmp_path / "mods" / "beta.json").unlink()
    _build(mods, qtbot)

    assert set(mods.get_all_mods()) == {"alpha"}
    assert not any(ref.mod_id == "beta" for ref in indexes.mod_component_index)
    assert not any(ref.mod_id == "beta" for ref in indexes.children_index)


def test_failed_cache_load_keeps_the_loaded_catalogue(mods: ModManager, qtbot: QtBot) -> None:
    _build(mods, qtbot)
    mods._cache_file(mods.current_language).write_text(
        json.dumps([_mod("alpha"), {"broken": True}]), encoding="utf-8"
    )

    assert not mods.load_cache()

    assert set(mods.get_all_mods()) == {"alpha", "beta"}
    assert ComponentReference.for_mod("beta") in IndexManager.get_indexes().mod_component_index


# ========================================
# TRANSLATION FALLBACK
# ========================================


def test_translation_falls_back_to_first_language_providing_text() -> None:
    translations = {"en_US": "English", "de_DE": "", "fr_FR": "Français"}

    assert first_translation(translations, "fr_FR", lambda text: text) == "Français"
    assert first_translation(translations, "de_DE", lambda text: text) == "English"
    assert first_translation(translations, "ko_KR", lambda text: text) == "English"
    assert first_translation({"en_US": ""}, "fr_FR", lambda text: text) == ""
