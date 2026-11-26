"""Catalogue and rules are published whole, and only after a successful load."""

import json
import os
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from core.ComponentReference import ComponentReference, IndexManager
from core.ModManager import ModManager
from core.RuleManager import RuleManager, rule_source_file
from core.StateManager import StateManager
from core.TranslationManager import SUPPORTED_LANGUAGES

OLD_DATE = 100

REPLACED_MOD = {
    "name": "Demo",
    "tp2": "demo",
    "links": {},
    "components": {
        "0": {"type": "std"},
        "choice_0": {"type": "muc", "components": ["10", "11"]},
        "1": {"type": "sub", "prompts": {"1": {"options": ["A", "B"], "default": "A"}}},
    },
}


def _write(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    os.utime(path, (OLD_DATE, OLD_DATE))
    return path


def _mod(name: str) -> dict:
    return {"name": name, "tp2": name, "components": {"0": {"type": "std"}}, "links": {}}


def _build(manager: ModManager | RuleManager, qtbot: QtBot) -> None:
    with qtbot.waitSignal(manager.cache_ready, timeout=10_000):
        assert manager.build_cache_async()


def _other_language(manager: ModManager | RuleManager) -> str:
    return next(code for code, _ in SUPPORTED_LANGUAGES if code != manager.current_language)


def _references_of(mod_id: str) -> set[ComponentReference]:
    """References of a mod in the catalogue indexes; every child is a key of `parent_index`."""
    indexes = IndexManager.get_indexes()
    return {
        reference
        for index in (indexes.mod_component_index, indexes.parent_index, indexes.children_index)
        for reference in index
        if reference.mod_id == mod_id
    }


@pytest.fixture
def mods(tmp_path: Path) -> ModManager:
    _write(tmp_path / "mods" / "alpha.json", _mod("alpha"))
    _write(tmp_path / "mods" / "demo.json", REPLACED_MOD)
    return ModManager(tmp_path / "mods", tmp_path / "custom", tmp_path / "cache")


@pytest.fixture
def mod_rules(mods: ModManager, tmp_path: Path, qtbot: QtBot) -> RuleManager:
    """Rules over a loaded catalogue; "custom" is unknown until added as a custom mod."""
    _build(mods, qtbot)
    _write(
        rule_source_file(tmp_path / "rules", "dependencies"),
        {"rules": [{"rule": "custom(-):alpha(0)"}, {"rule": "demo(0):alpha(0)"}]},
    )
    manager = RuleManager(
        mods,
        tmp_path / "rules",
        mods_dir=tmp_path / "mods",
        cache_dir=tmp_path / "cache",
        custom_mods_dir=tmp_path / "custom",
    )
    _build(manager, qtbot)
    return manager


# ========================================
# LANGUAGE RELOAD
# ========================================


@pytest.mark.parametrize("manager_name", ["mods", "mod_rules"])
def test_language_failing_to_load_is_not_considered_loaded(
    request: pytest.FixtureRequest, qtbot: QtBot, manager_name: str
) -> None:
    manager: ModManager | RuleManager = request.getfixturevalue(manager_name)
    _build(manager, qtbot)
    loaded_language = manager.current_language
    target_language = _other_language(manager)
    target_cache = manager._cache_file(target_language)
    built_cache = target_cache.read_bytes()
    target_cache.write_text("{broken", encoding="utf-8")

    assert not manager.reload_for_language(target_language)
    assert manager.current_language == loaded_language

    target_cache.write_bytes(built_cache)
    assert manager.reload_for_language(target_language)
    assert manager.current_language == target_language


# ========================================
# REVISION
# ========================================


def test_each_publication_gives_a_new_revision(
    mods: ModManager, mod_rules: RuleManager, tmp_path: Path
) -> None:
    mod_revision, rule_revision = mods.revision, mod_rules.revision

    assert mods.load_cache()
    assert mod_rules.load_cache()
    mod_rules.rebuild_indexes()

    assert mods.revision == mod_revision + 1
    assert mod_rules.revision == rule_revision + 2

    _write(tmp_path / "custom" / "custom.json", _mod("custom"))
    assert mods.add_mod("custom")
    assert mods.revision == mod_revision + 2


def test_failed_load_keeps_the_revision(mods: ModManager, mod_rules: RuleManager) -> None:
    mod_revision, rule_revision = mods.revision, mod_rules.revision
    for manager in (mods, mod_rules):
        manager._cache_file(manager.current_language).write_text("{broken", encoding="utf-8")

    assert not mods.load_cache()
    assert not mod_rules.load_cache()
    assert not mods.add_mod("missing")

    assert (mods.revision, mod_rules.revision) == (mod_revision, rule_revision)


# ========================================
# RULE PUBLICATION
# ========================================


def test_late_failure_while_indexing_keeps_the_published_rules(
    mod_rules: RuleManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    demo = ComponentReference.for_component("demo", "0")
    published_rules = mod_rules.get_rules_for_component(demo)
    revision = mod_rules.revision
    assert published_rules

    def failing_resolution(*args: object) -> set[ComponentReference]:
        raise RuntimeError("injected indexing failure")

    monkeypatch.setattr(RuleManager, "_resolve_refs_globally", staticmethod(failing_resolution))

    assert not mod_rules.load_cache()

    assert mod_rules.get_count() == 2
    assert mod_rules.get_rules_for_component(demo) == published_rules
    assert mod_rules.revision == revision


def test_rebuilt_indexes_resolve_the_wildcard_of_an_added_mod(
    mods: ModManager, mod_rules: RuleManager, tmp_path: Path
) -> None:
    custom = ComponentReference.for_component("custom", "0")
    _write(tmp_path / "custom" / "custom.json", _mod("custom"))
    assert mods.add_mod("custom")
    assert not mod_rules.selection_violations({custom})

    mod_rules.rebuild_indexes()

    (violation,) = mod_rules.selection_violations({custom})
    assert violation.affected_components[0] == custom


def test_rebuilt_indexes_withdraw_published_violations(mod_rules: RuleManager) -> None:
    demo = ComponentReference.for_component("demo", "0")
    assert mod_rules.validate_selection([demo])
    assert mod_rules.validate_order("eet", 0, [demo])
    assert mod_rules.get_selection_violations(demo)

    mod_rules.rebuild_indexes()

    assert not mod_rules.get_selection_violations(demo)
    assert not mod_rules.get_order_violations(demo)
    assert mod_rules.validate_selection([demo])


# ========================================
# CUSTOM MOD REPLACEMENT
# ========================================


def test_replaced_mod_leaves_no_reference_to_its_former_components(
    mods: ModManager, qtbot: QtBot, tmp_path: Path
) -> None:
    _build(mods, qtbot)
    former_references = _references_of("demo")
    _write(tmp_path / "custom" / "demo.json", _mod("demo"))

    assert mods.add_mod("demo")

    remaining = _references_of("demo")
    assert remaining == {
        ComponentReference.for_mod("demo"),
        ComponentReference.for_component("demo", "0"),
    }
    assert remaining < former_references
    assert mods.get_mod_by_id("demo").get_component("choice_0") is None
    assert _references_of("alpha")


def test_unreadable_custom_mod_keeps_the_replaced_mod(
    mods: ModManager, qtbot: QtBot, tmp_path: Path
) -> None:
    _build(mods, qtbot)
    former_references = _references_of("demo")
    (tmp_path / "custom").mkdir(exist_ok=True)
    (tmp_path / "custom" / "demo.json").write_text("{broken", encoding="utf-8")

    assert not mods.add_mod("demo")

    assert _references_of("demo") == former_references
    assert mods.get_mod_by_id("demo").get_component("choice_0") is not None


def test_added_custom_mod_is_validated_against_wildcard_rules(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mods: ModManager,
    mod_rules: RuleManager,
) -> None:
    from ui.pages.ModSelection import ModSelectionPage

    monkeypatch.setattr(StateManager, "STATE_FILE", tmp_path / "state.json")
    state = StateManager()
    monkeypatch.setattr(state, "get_mod_manager", lambda: mods)
    monkeypatch.setattr(state, "get_rule_manager", lambda: mod_rules)
    page = ModSelectionPage(state)
    qtbot.addWidget(page)
    page.load_state()
    _write(tmp_path / "custom" / "custom.json", _mod("custom"))
    custom = ComponentReference.for_component("custom", "0")

    page._on_custom_mod_added("custom")
    page._selection_controller.select_bulk([custom])
    page._trigger_validation()

    assert mod_rules.get_selection_violations(custom)
