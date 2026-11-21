"""L8: field contract of the mod_sync definition merger."""

import pytest

from util.mod_sync.mergers.definition_merger import DefinitionMerger


def merge(existing: dict | None, **sources: dict) -> dict:
    merger = DefinitionMerger("demo")
    for name, data in sources.items():
        merger.add_source(name, data)
    return merger.merge(existing)


STORED = {
    "name": "Stored name",
    "safe": 2,
    "games": ["bg2ee"],
    "categories": ["quest"],
    "authors": ["Stored author"],
    "links": {"homepage": "https://example.org/stored"},
    "translations": {"en_US": {"description": "Stored text", "components": {"0": "Main"}}},
}


@pytest.mark.parametrize("value", [0, False])
def test_zero_and_false_are_applied(value: int | bool) -> None:
    assert merge(STORED, lcc={"safe": value})["safe"] is value


@pytest.mark.parametrize(
    ("field", "empty_value"),
    [
        ("name", None),
        ("name", ""),
        ("safe", None),
        ("games", []),
        ("categories", []),
        ("authors", []),
    ],
)
def test_null_or_empty_source_value_keeps_stored_value(field: str, empty_value: object) -> None:
    assert merge(STORED, lcc={field: empty_value})[field] == STORED[field]


def test_lcc_changes_replace_stored_metadata() -> None:
    lcc = {
        "name": "New name",
        "safe": 1,
        "games": ["bg2ee", "eet"],
        "categories": ["npc"],
        "authors": ["New author"],
        "descriptions": {"en_US": "New text", "fr_FR": "Nouveau texte"},
    }

    merged = merge(STORED, lcc=lcc)

    assert {field: merged[field] for field in ("name", "safe", "games", "categories", "authors")} == {
        "name": "New name",
        "safe": 1,
        "games": ["bg2ee", "eet"],
        "categories": ["npc"],
        "authors": ["New author"],
    }
    assert merged["translations"] == {
        "en_US": {"description": "New text", "components": {"0": "Main"}},
        "fr_FR": {"description": "Nouveau texte"},
    }


def test_empty_lcc_description_keeps_stored_text() -> None:
    merged = merge(STORED, lcc={"name": "Demo", "descriptions": {"en_US": ""}})

    assert merged["translations"]["en_US"]["description"] == "Stored text"


def test_empty_source_links_do_not_overwrite_stored_links() -> None:
    merged = merge({**STORED, "links": {}}, lcc={"links": {"forums": [], "homepage": ""}})

    assert merged["links"] == {}


def test_merge_leaves_sources_and_existing_definition_intact() -> None:
    lcc = {"games": ["bgee"], "authors": ["Author"]}
    tp2 = {"components": {"0": {"type": "std", "games": ["bgee"]}}}
    existing = {"games": ["bgee"], "components": {"0": {"type": "std"}}}

    merged = merge(existing, lcc=lcc, tp2=tp2)
    merged["authors"].append("Other")

    assert merged["games"] == ["bgee", "sod"]
    assert merged["components"]["0"]["games"] == ["bgee", "sod"]
    assert lcc == {"games": ["bgee"], "authors": ["Author"]}
    assert tp2 == {"components": {"0": {"type": "std", "games": ["bgee"]}}}
    assert existing == {"games": ["bgee"], "components": {"0": {"type": "std"}}}


def test_incomplete_definition_is_completed_without_losing_stored_values() -> None:
    existing = {
        "name": "Stored name",
        "safe": 0,
        "games": None,
        "translations": {"en_US": None},
        "components": {"5": None},
    }
    tp2 = {
        "version": "2",
        "components": {"0": {"type": "std", "games": None}, "5": {"type": "std"}},
        "component_translations": {"en_US": {"0": "Main", "5": "Extra"}},
    }

    merged = merge(existing, lcc={"games": ["bg2ee"]}, tp2=tp2)

    assert merged["name"] == "Stored name"
    assert merged["safe"] == 0
    assert merged["version"] == "2"
    assert merged["games"] == ["bg2ee"]
    assert merged["components"] == {"0": {"type": "std"}, "5": {"type": "std"}}
    assert merged["translations"] == {"en_US": {"components": {"0": "Main", "5": "Extra"}}}
    assert merged["links"] == {}
    assert merged["files"] == []
