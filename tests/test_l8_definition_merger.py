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


def component_merge(existing: dict, tp2: dict) -> tuple[dict, DefinitionMerger]:
    merger = DefinitionMerger("demo")
    merger.add_source("tp2", tp2)
    return merger.merge(existing), merger


MUC_DEFINITION = {
    "components": {
        "0": {"type": "std"},
        "choice_0": {"type": "muc", "components": ["1", "2"]},
    },
    "translations": {
        "en_US": {
            "components": {"0": "Old main", "choice_0": "Pick a portrait style", "1": "Old A", "2": "B"}
        }
    },
}


def test_muc_group_label_is_kept_while_other_labels_follow_tp2() -> None:
    tp2 = {
        "components": {"0": {"type": "std"}, "choice_0": {"type": "muc", "components": ["1", "2"]}},
        "component_translations": {
            "en_US": {"0": "New main", "choice_0": "Portraits", "1": "New A", "2": "B"}
        },
    }

    merged, merger = component_merge(MUC_DEFINITION, tp2)

    assert merged["translations"]["en_US"]["components"] == {
        "0": "New main",
        "choice_0": "Pick a portrait style",
        "1": "New A",
        "2": "B",
    }
    assert not merger.component_changes


def test_new_muc_group_takes_its_tp2_label() -> None:
    tp2 = {
        "components": {"choice_0": {"type": "muc", "components": ["1"]}},
        "component_translations": {"en_US": {"choice_0": "Portraits", "1": "A"}},
    }

    merged, _ = component_merge({"components": {}}, tp2)

    assert merged["translations"]["en_US"]["components"] == {"choice_0": "Portraits", "1": "A"}


def test_sub_prompt_labels_survive_tp2_labels() -> None:
    existing = {
        "components": {"0": {"type": "sub", "prompts": {"1": {"options": ["0", "1"]}}}, "5": {"type": "std"}},
        "translations": {
            "fr_FR": {
                "components": {"0": "Ancien", "0.1": "Question", "0.1.0": "Non", "0.1.1": "Oui", "5": "Autre"}
            }
        },
    }
    tp2 = {
        "components": {"0": {"type": "std"}, "5": {"type": "std"}},
        "component_translations": {"fr_FR": {"0": "Nouveau", "5": "Autre"}},
    }

    merged, merger = component_merge(existing, tp2)

    assert merged["components"]["0"]["type"] == "sub"
    assert list(merged["translations"]["fr_FR"]["components"].items()) == [
        ("0", "Nouveau"),
        ("0.1", "Question"),
        ("0.1.0", "Non"),
        ("0.1.1", "Oui"),
        ("5", "Autre"),
    ]
    assert not merger.component_changes


def test_added_removed_components_and_kept_subs_are_reported() -> None:
    existing = {
        "components": {
            "0": {"type": "std"},
            "3": {"type": "std"},
            "9": {"type": "sub", "prompts": {}},
            "choice_0": {"type": "muc", "components": ["10", "11"]},
        },
        "translations": {"en_US": {"components": {"3": "Removed", "9": "Manual SUB", "9.1": "Prompt"}}},
    }
    tp2 = {
        "components": {
            "choice_0": {"type": "muc", "components": ["4"]},
            "0": {"type": "std"},
            "choice_1": {"type": "muc", "components": ["10", "11"]},
        },
        "component_translations": {"en_US": {"choice_0": "New group", "4": "New", "0": "Main"}},
    }

    merged, merger = component_merge(existing, tp2)

    assert merger.component_changes.added == ("4",)
    assert merger.component_changes.removed == ("3",)
    assert merger.component_changes.kept_subs == ("9",)
    assert "9" in merged["components"]
    assert merged["translations"]["en_US"]["components"] == {
        "choice_0": "New group",
        "4": "New",
        "0": "Main",
        "9": "Manual SUB",
        "9.1": "Prompt",
    }


STORED_FILES = [
    {"filename": "lin-demo-v1.zip", "size": 1, "sha256": "lin-hash", "download": "https://x/v1/lin.zip", "platforms": ["linux"]},
    {"filename": "win-demo-v1.zip", "size": 1, "sha256": "win-hash", "download": "https://x/v1/win.zip", "platforms": ["windows"]},
]


def test_archive_files_replace_stored_files() -> None:
    archive_files = [STORED_FILES[0], {**STORED_FILES[1], "size": 2, "sha256": "new-win-hash"}]

    assert merge({"files": STORED_FILES}, archive={"files": archive_files})["files"] == archive_files


def test_stored_files_are_kept_without_archive_source() -> None:
    assert merge({"files": STORED_FILES}, lcc={"name": "Demo"})["files"] == STORED_FILES
