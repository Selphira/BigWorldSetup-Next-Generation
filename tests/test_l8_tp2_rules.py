"""L8: mod_sync turns the requirements of a TP2 into the rules of the mod definition."""

import json
import logging
from pathlib import Path

import pytest

from constants import INI_RULE_ORIGIN, TP2_RULE_ORIGIN
from util.mod_sync.mergers.definition_merger import DefinitionMerger
from util.mod_sync.models.catalogue_components import CatalogueComponents
from util.mod_sync.sources.tp2_rules import Tp2Rules
from util.mod_sync.sources.tp2_source import TP2Source
from validation.catalogue import validate_catalogue

DEMO_HEADER = "BACKUP ~demo/backup~\nAUTHOR ~author~\n"

LANGUAGES = (
    "LANGUAGE ~English~ ~english~ ~demo/english/setup.tra~\n"
    "LANGUAGE ~Francais~ ~french~ ~demo/french/setup.tra~\n"
)

MESSAGES = {
    "english": {
        "1": "Needs the main component",
        "2": "Needs it differently",
        "3": "Not with it",
    },
    "french": {"1": "Requiert le composant principal", "3": "Pas avec lui"},
}

MANUAL_DEPENDENCY = {"rule": "demo(1):other(-)", "severity": "warning"}


def write_mod(base_dir: Path, components: str, languages: str = LANGUAGES) -> None:
    mod_dir = base_dir / "demo"
    mod_dir.mkdir()
    (mod_dir / "demo.tp2").write_text(DEMO_HEADER + languages + components, encoding="utf-8")
    for folder, entries in MESSAGES.items():
        (mod_dir / folder).mkdir()
        content = "\n".join(f"@{ref} = ~{text}~" for ref, text in entries.items())
        (mod_dir / folder / "setup.tra").write_text(content, encoding="utf-8")


def tp2_rules(
    base_dir: Path, catalogue: CatalogueComponents | None = None, labels_language: str = "en_US"
) -> dict[str, list[dict]]:
    extracted = TP2Source(base_dir, catalogue).extract("demo", labels_language=labels_language)
    assert extracted is not None
    return extracted["requirement_rules"].sections()


def catalogue_of(definitions: dict[str, dict]) -> CatalogueComponents:
    catalogue = CatalogueComponents()
    for mod_name, definition in definitions.items():
        catalogue.update(mod_name, definition)
    return catalogue


def tp2_rule(rule: str, **fields: object) -> dict:
    return {"rule": rule, "severity": "error", **fields, "origin": TP2_RULE_ORIGIN}


# ========================================
# GENERATION
# ========================================


def test_components_with_the_same_requirement_share_a_rule(tmp_path: Path) -> None:
    write_mod(
        tmp_path,
        "BEGIN ~Main~ DESIGNATED 0\n"
        "BEGIN ~First~ DESIGNATED 1 REQUIRE_COMPONENT ~demo.tp2~ ~0~ @1\n"
        "BEGIN ~Second~ DESIGNATED 2 REQUIRE_COMPONENT ~setup-demo.tp2~ ~0~ @1\n",
    )

    assert tp2_rules(tmp_path)["dependencies"] == [
        tp2_rule(
            "demo(1,2):demo(0)",
            translations={
                "en_US": "Needs the main component",
                "fr_FR": "Requiert le composant principal",
            },
        )
    ]


def test_components_with_different_messages_get_their_own_rule(tmp_path: Path) -> None:
    write_mod(
        tmp_path,
        "BEGIN ~Main~ DESIGNATED 0\n"
        "BEGIN ~First~ DESIGNATED 1 REQUIRE_COMPONENT ~demo.tp2~ ~0~ @1\n"
        "BEGIN ~Second~ DESIGNATED 2 REQUIRE_COMPONENT ~demo.tp2~ ~0~ @2\n",
    )

    assert [rule["rule"] for rule in tp2_rules(tmp_path)["dependencies"]] == [
        "demo(1):demo(0)",
        "demo(2):demo(0)",
    ]


def test_each_requirement_of_a_component_is_a_rule(tmp_path: Path) -> None:
    write_mod(
        tmp_path,
        "BEGIN ~Main~ DESIGNATED 0\nBEGIN ~Other~ DESIGNATED 1\n"
        "BEGIN ~Both~ DESIGNATED 2\n"
        "REQUIRE_PREDICATE MOD_IS_INSTALLED ~demo.tp2~ ~0~ AND MOD_IS_INSTALLED ~demo.tp2~ ~1~ @1\n",
    )

    assert [rule["rule"] for rule in tp2_rules(tmp_path)["dependencies"]] == [
        "demo(2):demo(0)",
        "demo(2):demo(1)",
    ]


def test_alternatives_are_grouped_by_mod(tmp_path: Path) -> None:
    write_mod(
        tmp_path,
        "BEGIN ~Main~ DESIGNATED 0\n"
        "REQUIRE_PREDICATE MOD_IS_INSTALLED ~setup-a.tp2~ ~1~ OR MOD_IS_INSTALLED ~b.tp2~ ~3~"
        " OR MOD_IS_INSTALLED ~A/setup-A.tp2~ ~2~ @1\n",
    )

    assert [rule["rule"] for rule in tp2_rules(tmp_path)["dependencies"]] == [
        "demo(0):a(1,2)|b(3)"
    ]


def test_forbidden_component_is_an_incompatibility(tmp_path: Path) -> None:
    write_mod(tmp_path, "BEGIN ~Main~ DESIGNATED 0 FORBID_COMPONENT ~other.tp2~ ~5~ @3\n")

    rules = tp2_rules(tmp_path)

    assert rules["dependencies"] == []
    assert rules["incompatibilities"] == [
        tp2_rule(
            "demo(0):other(5)", translations={"en_US": "Not with it", "fr_FR": "Pas avec lui"}
        )
    ]


def test_body_condition_is_an_optional_warning_without_text(tmp_path: Path) -> None:
    write_mod(
        tmp_path,
        "BEGIN ~Main~ DESIGNATED 0\n"
        "ACTION_IF MOD_IS_INSTALLED ~other.tp2~ ~2~ THEN BEGIN\n  PRINT ~adapted~\nEND\n",
    )

    assert tp2_rules(tmp_path)["dependencies"] == [
        {
            "rule": "demo(0):other(2)",
            "severity": "warning",
            "optional": True,
            "origin": TP2_RULE_ORIGIN,
        }
    ]


def test_choices_of_a_muc_are_rule_sources(tmp_path: Path) -> None:
    write_mod(
        tmp_path,
        "BEGIN ~Main~ DESIGNATED 0\n"
        "BEGIN ~Choice~ DESIGNATED 10 SUBCOMPONENT ~Group~ REQUIRE_COMPONENT ~demo.tp2~ ~0~ @1\n"
        "BEGIN ~Choice~ DESIGNATED 11 SUBCOMPONENT ~Group~ REQUIRE_COMPONENT ~demo.tp2~ ~0~ @1\n",
    )

    assert [rule["rule"] for rule in tp2_rules(tmp_path)["dependencies"]] == [
        "demo(10,11):demo(0)"
    ]


def test_literal_message_without_language_takes_the_labels_language(tmp_path: Path) -> None:
    write_mod(
        tmp_path,
        "BEGIN ~Principal~ DESIGNATED 0\n"
        "BEGIN ~Autre~ DESIGNATED 1 REQUIRE_COMPONENT ~demo.tp2~ ~0~ ~Il faut le principal~\n",
        languages="",
    )

    rules = tp2_rules(tmp_path, labels_language="fr_FR")

    assert rules["dependencies"][0]["translations"] == {"fr_FR": "Il faut le principal"}


def test_messages_of_languages_a_definition_cannot_hold_are_left_out(tmp_path: Path) -> None:
    write_mod(
        tmp_path,
        "BEGIN ~Main~ DESIGNATED 0\nBEGIN ~Other~ DESIGNATED 1 REQUIRE_COMPONENT ~demo.tp2~ ~0~ @1\n",
        languages=LANGUAGES + "LANGUAGE ~Klingon~ ~klingon~ ~demo/english/setup.tra~\n",
    )

    translations = tp2_rules(tmp_path)["dependencies"][0]["translations"]

    assert sorted(translations) == ["en_US", "fr_FR"]


def test_label_of_another_mod_is_resolved_by_the_catalogue(tmp_path: Path) -> None:
    write_mod(
        tmp_path,
        "BEGIN ~Main~ DESIGNATED 0\n"
        "REQUIRE_PREDICATE MOD_IS_INSTALLED ~tnt.tp2~ (ID_OF_LABEL ~tnt.tp2~ ~tnt-core~) @1\n"
        "ACTION_IF MOD_IS_INSTALLED ~ua.tp2~ (ID_OF_LABEL ~ua.tp2~ ~ua-choice~) THEN BEGIN END\n",
    )
    catalogue = catalogue_of(
        {
            "tnt": {"tp2": "TnT", "components": {"4": {"type": "std", "label": "tnt-core"}}},
            "ua": {
                "components": {
                    "choice_0": {
                        "type": "muc",
                        "components": ["7", "8"],
                        "labels": {"8": "ua-choice"},
                    }
                }
            },
        }
    )

    rules = tp2_rules(tmp_path, catalogue)["dependencies"]

    assert [rule["rule"] for rule in rules] == ["demo(0):tnt(4)", "demo(0):ua(8)"]


@pytest.mark.parametrize(
    "catalogue",
    [CatalogueComponents(), catalogue_of({"tnt": {"components": {"4": {"type": "std"}}}})],
    ids=["mod not catalogued", "label not catalogued"],
)
def test_requirement_on_an_unknown_label_is_skipped_until_its_mod_is_synced(
    tmp_path: Path, catalogue: CatalogueComponents, caplog: pytest.LogCaptureFixture
) -> None:
    write_mod(
        tmp_path,
        "BEGIN ~Main~ DESIGNATED 0\n"
        "REQUIRE_PREDICATE MOD_IS_INSTALLED ~tnt.tp2~ (ID_OF_LABEL ~tnt.tp2~ ~tnt-core~)"
        " OR MOD_IS_INSTALLED ~other.tp2~ ~1~ @1\n",
    )

    with caplog.at_level(logging.INFO):
        rules = tp2_rules(tmp_path, catalogue)

    assert rules["dependencies"] == []
    assert "label 'tnt-core' of tnt not catalogued yet" in caplog.text


def test_targets_missing_from_the_catalogue_are_reported_and_kept(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    write_mod(
        tmp_path,
        "BEGIN ~Main~ DESIGNATED 0\n"
        "REQUIRE_COMPONENT ~demo.tp2~ ~9~ @1\n"
        "REQUIRE_COMPONENT ~absent.tp2~ ~0~ @1\n"
        "REQUIRE_COMPONENT ~known.tp2~ ~3~ @1\n",
    )
    catalogue = catalogue_of({"known": {"components": {"0": {"type": "std"}}}})

    with caplog.at_level(logging.WARNING):
        rules = tp2_rules(tmp_path, catalogue)["dependencies"]

    assert [rule["rule"] for rule in rules] == [
        "demo(0):demo(9)",
        "demo(0):absent(0)",
        "demo(0):known(3)",
    ]
    assert "demo(9) not in the TP2" in caplog.text
    assert "Target mod absent not catalogued" in caplog.text
    assert "known(3) not catalogued" in caplog.text


# ========================================
# CATALOGUE
# ========================================


def test_catalogue_indexes_definitions_by_normalized_tp2(tmp_path: Path) -> None:
    (tmp_path / "TnT.json").write_text(
        json.dumps({"tp2": "Setup-TnT", "components": {"4": {"type": "std", "label": "core"}}}),
        encoding="utf-8",
    )
    (tmp_path / "Plain.json").write_text(json.dumps({"components": {}}), encoding="utf-8")
    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
    catalogue = CatalogueComponents()

    catalogue.load(tmp_path)

    assert catalogue.component_of_label("tnt", "core") == "4"
    assert catalogue.has_component("tnt", "4")
    assert catalogue.has_mod("plain")
    assert not catalogue.has_mod("broken")


def test_catalogue_follows_a_saved_definition() -> None:
    catalogue = catalogue_of({"tnt": {"components": {"4": {"type": "std"}}}})

    catalogue.update("tnt", {"components": {"4": {"type": "std", "label": "core"}}})

    assert catalogue.component_of_label("tnt", "core") == "4"


# ========================================
# MERGE
# ========================================


def _merge(existing: dict, tp2: dict | None, ini: dict | None = None) -> dict:
    merger = DefinitionMerger("demo")
    merger.add_source("lcc", {"name": "Demo"})
    if tp2 is not None:
        merger.add_source("tp2", tp2)
    if ini is not None:
        merger.add_source("ini", ini)
    return merger.merge(existing)


def _tp2_source(tmp_path: Path, components: str) -> dict:
    write_mod(tmp_path, components)
    extracted = TP2Source(tmp_path).extract("demo")
    assert extracted is not None
    return extracted


def test_tp2_rules_are_replaced_and_manual_rules_kept(tmp_path: Path) -> None:
    stale = tp2_rule("demo(1):demo(5)")
    existing = {
        "rules": {
            "dependencies": [MANUAL_DEPENDENCY, stale],
            "incompatibilities": [tp2_rule("demo(0):old(0)")],
        }
    }
    tp2 = _tp2_source(
        tmp_path,
        "BEGIN ~Main~ DESIGNATED 0\nBEGIN ~Other~ DESIGNATED 1 REQUIRE_COMPONENT ~demo.tp2~ ~0~ @3\n",
    )

    merged = _merge(existing, tp2)

    assert merged["rules"]["dependencies"] == [
        MANUAL_DEPENDENCY,
        tp2_rule(
            "demo(1):demo(0)", translations={"en_US": "Not with it", "fr_FR": "Pas avec lui"}
        ),
    ]
    assert "incompatibilities" not in merged["rules"]


def test_tp2_rules_are_kept_without_a_parsed_tp2() -> None:
    existing = {"rules": {"dependencies": [MANUAL_DEPENDENCY, tp2_rule("demo(1):demo(0)")]}}

    assert _merge(existing, None)["rules"] == existing["rules"]


def test_tp2_without_requirements_removes_its_rules_and_empty_section() -> None:
    existing = {"rules": {"incompatibilities": [tp2_rule("demo(0):old(0)")]}}

    assert "rules" not in _merge(existing, {"requirement_rules": Tp2Rules()})


def test_tp2_and_ini_rules_are_merged_together() -> None:
    ini_rule = {"rule": "demo(-):other(-)", "direction": "after", "origin": INI_RULE_ORIGIN}
    tp2_rules_found = Tp2Rules()
    existing = {"rules": {"order": [{**ini_rule, "rule": "demo(-):old(-)"}]}}

    merged = _merge(
        existing, {"requirement_rules": tp2_rules_found}, {"order_rules": [ini_rule]}
    )

    assert merged["rules"] == {"order": [ini_rule]}


def test_generated_rules_pass_the_catalogue_validation(
    tmp_path: Path, small_catalogue: tuple[Path, Path]
) -> None:
    data_dir = small_catalogue[0]
    write_mod(
        tmp_path,
        "BEGIN ~Main~ DESIGNATED 0\n"
        "REQUIRE_PREDICATE MOD_IS_INSTALLED ~demo.tp2~ ~0~ OR MOD_IS_INSTALLED ~other.tp2~ ~1~ @1\n"
        "FORBID_COMPONENT ~other.tp2~ ~2~ @3\n"
        "ACTION_IF MOD_IS_INSTALLED ~other.tp2~ ~3~ THEN BEGIN END\n",
    )
    definition_path = data_dir / "mods" / "demo.json"
    definition = json.loads(definition_path.read_text(encoding="utf-8"))
    definition_path.write_text(
        json.dumps(_merge(definition, TP2Source(tmp_path).extract("demo"))), encoding="utf-8"
    )

    report = validate_catalogue(*small_catalogue)

    assert report.valid, report.diagnostics
