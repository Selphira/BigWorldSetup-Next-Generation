"""Category navigation only lists mods with components to select there."""

from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from constants import ROLE_COMPONENT, ROLE_MOD
from core.Mod import Mod
from core.RuleManager import RuleManager
from tests.conftest import ModCatalogue
from ui.pages.mod_selection.ComponentSelector import ComponentSelector
from ui.pages.mod_selection.SelectionController import SelectionController


def make_mod(mod_id: str, categories: list[str], components: dict, **fields: object) -> Mod:
    return Mod(
        {
            "id": mod_id,
            "name": mod_id,
            "links": {},
            "tp2": mod_id,
            "games": ["bg", "iwd"],
            "categories": categories,
            "components": components,
            **fields,
        }
    )


@pytest.fixture
def selector(qtbot: QtBot, tmp_path: Path) -> ComponentSelector:
    catalogue = ModCatalogue(
        [
            # Every component moved to another category (ImprovedBAMs).
            make_mod("bams", ["cosm"], {"0": {"type": "std", "category": "portrait"}}),
            # Declared category only holds the component without its own.
            make_mod("mixed", ["quest"], {"0": {"type": "std"}, "1": {"category": "npcx"}}),
            # Declared category only reachable through an iwd component.
            make_mod(
                "iwdonly",
                ["kit"],
                {"0": {"type": "std", "games": ["iwd"]}, "1": {"category": "spell"}},
            ),
        ]
    )
    rules = RuleManager(
        catalogue,
        rules_dir=tmp_path / "rules",
        cache_dir=tmp_path / "cache",
        mods_dir=tmp_path / "mods",
        custom_mods_dir=tmp_path / "custom",
    )
    widget = ComponentSelector(catalogue, SelectionController(rules))
    qtbot.addWidget(widget)
    return widget


def visible_components(selector: ComponentSelector) -> dict[str, list[str]]:
    model = selector.model()
    visible = {}
    for row in range(model.rowCount()):
        mod_index = model.index(row, 0)
        visible[mod_index.data(ROLE_MOD).id] = sorted(
            model.index(child, 0, mod_index).data(ROLE_COMPONENT).key
            for child in range(model.rowCount(mod_index))
        )
    return visible


def test_listed_categories_ignore_declared_categories_left_unused() -> None:
    bams = make_mod("bams", ["cosm"], {"0": {"type": "std", "category": "portrait"}})
    mixed = make_mod("mixed", ["quest"], {"0": {"type": "std"}, "1": {"category": "npcx"}})
    empty = make_mod("empty", ["util"], {})

    assert bams.categories == ("portrait",)
    assert mixed.categories == ("npcx", "quest")
    assert empty.categories == ("util",)
    assert bams.declared_categories == ("cosm",)


@pytest.mark.parametrize(
    "category,game,expected",
    [
        ("cosm", "", {}),
        ("portrait", "", {"bams": ["0"]}),
        ("quest", "", {"mixed": ["0"]}),
        ("npcx", "", {"mixed": ["1"]}),
        ("npc", "", {}),
        ("kit", "bg", {}),
        ("kit", "iwd", {"iwdonly": ["0"]}),
    ],
    ids=[
        "declared category without component",
        "component category",
        "declared category holds uncategorized component",
        "component kept out of declared category",
        "category names compared exactly",
        "no component for the filtered game",
        "component for the filtered game",
    ],
)
def test_category_lists_only_mods_with_components_there(
    selector: ComponentSelector,
    category: str,
    game: str,
    expected: dict[str, list[str]],
) -> None:
    selector.apply_filters(category=category, game=game)

    assert visible_components(selector) == expected


def test_category_counts_match_visible_mods(selector: ComponentSelector) -> None:
    selector.apply_filters(game="bg")

    counts = selector.get_filtered_mod_count_by_category()

    assert counts == {"all": 3, "portrait": 1, "quest": 1, "npcx": 1, "spell": 1}
