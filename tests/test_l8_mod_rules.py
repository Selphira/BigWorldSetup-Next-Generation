"""L8 (B31): rules declared in mod definitions and the INI rules maintained by mod_sync."""

import json
import os
from pathlib import Path

import pytest

from core.RuleManager import RuleManager
from core.TranslationManager import SUPPORTED_LANGUAGES
from util.mod_sync.mergers.definition_merger import DefinitionMerger
from util.mod_sync.sources.ini_source import INI_RULE_ORIGIN, IniData, IniSource
from validation.catalogue import validate_catalogue

MANUAL_RULE = {"rule": "demo(-):other(-)", "direction": "after"}


def _write(path: Path, value: object, mtime: float | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def _ini_rule(target: str, direction: str = "before") -> dict:
    return IniData(**{direction: [target]}).order_rules("demo")[0]


# ========================================
# APPLICATION
# ========================================


def test_rules_of_mod_definitions_join_those_of_rule_files(
    rules: RuleManager, tmp_path: Path
) -> None:
    _write(tmp_path / "rules" / "order.json", {"rules": [{"rule": "file(-):other(-)", "direction": "before"}]})
    _write(tmp_path / "mods" / "demo.json", {"rules": {"order": [MANUAL_RULE]}})
    _write(tmp_path / "mods" / "plain.json", {"name": "No rules"})

    order_rules = rules._create_cache_builder(["en_US"])._load_sources()["order"]

    assert [(rule["source"], rule["target"], rule["direction"]) for rule in order_rules] == [
        (["file:*"], ["other:*"], "before"),
        (["demo:*"], ["other:*"], "after"),
    ]


def test_dependencies_and_incompatibilities_of_mod_definitions_are_loaded(
    rules: RuleManager, tmp_path: Path
) -> None:
    _write(
        tmp_path / "mods" / "demo.json",
        {
            "rules": {
                "dependencies": [{"rule": "demo(0):other(-)"}],
                "incompatibilities": [{"rule": "demo(1):other(2)"}],
            }
        },
    )

    loaded = rules._create_cache_builder(["en_US"])._load_sources()

    assert [(rule["source"], rule["target"]) for rule in loaded["dependencies"]] == [
        (["demo:0"], ["other:*"])
    ]
    assert [(rule["source"], rule["target"]) for rule in loaded["incompatibilities"]] == [
        (["demo:1"], ["other:2"]),
        (["other:2"], ["demo:1"]),
    ]


# ========================================
# CATALOGUE
# ========================================


def _set_mod_rules(data_dir: Path, section: object) -> None:
    path = data_dir / "mods" / "demo.json"
    definition = json.loads(path.read_text(encoding="utf-8"))
    definition["rules"] = section
    _write(path, definition)


def test_valid_rules_section_passes(small_catalogue: tuple[Path, Path]) -> None:
    _set_mod_rules(
        small_catalogue[0],
        {
            "dependencies": [{"rule": "demo(0):demo(-)", "mode": "all"}],
            "incompatibilities": [{"rule": "demo(0):demo(-):demo(0)"}],
            "order": [{"rule": "demo(0):demo(-)", "origin": INI_RULE_ORIGIN}],
        },
    )

    report = validate_catalogue(*small_catalogue)

    assert report.valid, report.diagnostics


@pytest.mark.parametrize(
    ("rule_type", "rule"),
    [
        ("dependencies", {"rule": "demo(0):absent(-)"}),
        ("incompatibilities", {"rule": "demo(0):absent(-)"}),
        ("order", _ini_rule("absent")),
    ],
)
def test_rule_of_a_definition_referring_to_a_missing_mod_stays_dormant(
    small_catalogue: tuple[Path, Path], rule_type: str, rule: dict
) -> None:
    _set_mod_rules(small_catalogue[0], {rule_type: [rule]})

    report = validate_catalogue(*small_catalogue)

    assert report.valid, report.diagnostics
    assert ("mods/demo.json", f"rules/{rule_type}/0", "dormant_rule") in [
        (item.file, item.location, item.code) for item in report.notices
    ]


@pytest.mark.parametrize(
    "section",
    [
        {"order": [{**MANUAL_RULE, "origin": "lcc"}]},
        {"dependencies": [{"rule": "demo(0):other(-)", "origin": INI_RULE_ORIGIN}]},
        {"order": [{**_ini_rule("demo"), "translations": {"en_US": "Stored text"}}]},
        {"order": [{**_ini_rule("demo"), "description": "Stored text"}]},
        {"dependencies": [{"rule": "demo(0):other(-):third(-)"}]},
        {"unknown": []},
    ],
    ids=[
        "unknown rule origin",
        "generated dependency",
        "generated rule with translations",
        "generated rule with description",
        "dependency with three sides",
        "unknown rule type",
    ],
)
def test_malformed_rules_section_fails_schema(
    small_catalogue: tuple[Path, Path], section: dict
) -> None:
    _set_mod_rules(small_catalogue[0], section)

    assert any(
        item.file == "mods/demo.json" and item.code == "schema"
        for item in validate_catalogue(*small_catalogue).diagnostics
    )


# ========================================
# MOD_SYNC
# ========================================


def _merge(existing: dict, ini: dict | None) -> dict:
    merger = DefinitionMerger("demo")
    merger.add_source("lcc", {"name": "Demo"})
    if ini is not None:
        merger.add_source("ini", ini)
    return merger.merge(existing)


def test_ini_rules_are_replaced_and_manual_rules_kept() -> None:
    existing = {"rules": {"order": [MANUAL_RULE, _ini_rule("old")]}}

    merged = _merge(existing, {"order_rules": [_ini_rule("new", "after")]})

    assert merged["rules"]["order"] == [MANUAL_RULE, _ini_rule("new", "after")]


def test_ini_rules_are_kept_without_a_parsed_ini_file() -> None:
    existing = {"rules": {"order": [MANUAL_RULE, _ini_rule("old")]}}

    assert _merge(existing, None)["rules"] == existing["rules"]


def test_ini_without_declarations_removes_its_rules_and_empty_section() -> None:
    merged = _merge({"rules": {"order": [_ini_rule("old")]}}, {"order_rules": []})

    assert "rules" not in merged


def test_definition_without_rules_gets_no_empty_section() -> None:
    assert "rules" not in _merge({}, {"order_rules": []})


def test_ini_declarations_become_marked_order_rules(tmp_path: Path) -> None:
    ini_file = tmp_path / "demo" / "demo.ini"
    ini_file.parent.mkdir()
    ini_file.write_text("[Metadata]\nBefore = first, second\nAfter = third\n", encoding="utf-8")

    order_rules = IniSource(tmp_path).extract("demo")["order_rules"]

    assert [(rule["rule"], rule["direction"], rule["origin"]) for rule in order_rules] == [
        ("demo(-):first(-)|second(-)", "before", INI_RULE_ORIGIN),
        ("demo(-):third(-)", "after", INI_RULE_ORIGIN),
    ]


def test_ini_entries_that_are_not_mod_identifiers_are_ignored(tmp_path: Path) -> None:
    ini_file = tmp_path / "demo" / "demo.ini"
    ini_file.parent.mkdir()
    ini_file.write_text(
        "[Metadata]\n"
        "Before = The Improved Volcano! Pack\n"
        "After = bg2fixpack, EET, BG1_NPC_in_SoA&ToB, Coran in BG2\n",
        encoding="utf-8",
    )

    order_rules = IniSource(tmp_path).extract("demo")["order_rules"]

    assert [(rule["rule"], rule["direction"]) for rule in order_rules] == [
        ("demo(-):bg2fixpack(-)|EET(-)", "after"),
    ]


def test_ini_file_in_windows_encoding_is_read(tmp_path: Path) -> None:
    ini_file = tmp_path / "demo" / "demo.ini"
    ini_file.parent.mkdir()
    ini_file.write_text("[Metadata]\nName = Demo’s mod\nAfter = third\n", encoding="cp1252")

    ini_data = IniSource(tmp_path).extract("demo")

    assert ini_data is not None
    assert [rule["rule"] for rule in ini_data["order_rules"]] == ["demo(-):third(-)"]


def test_undecodable_ini_file_is_no_source(tmp_path: Path) -> None:
    ini_file = tmp_path / "demo" / "demo.json"
    ini_file.parent.mkdir()
    ini_file.write_text("{ not json", encoding="utf-8")

    assert IniSource(tmp_path).extract("demo") is None


def test_ini_rules_carry_no_text() -> None:
    rule = _ini_rule("other")

    assert "translations" not in rule and "description" not in rule


@pytest.mark.parametrize(
    ("definition", "mod_name"),
    [({"name": "Demo Mod"}, "Demo Mod"), ({}, "demo")],
    ids=["definition name", "file name without definition name"],
)
def test_ini_rule_description_is_generated_at_loading_in_every_language(
    rules: RuleManager, tmp_path: Path, definition: dict, mod_name: str
) -> None:
    rules_section = {"order": [_ini_rule("other"), MANUAL_RULE]}
    _write(tmp_path / "mods" / "demo.json", {**definition, "rules": rules_section})
    builder = rules._create_cache_builder(["fr_FR"])

    ini_rule, manual_rule = builder._load_sources()["order"]

    assert list(ini_rule["translations"]) == [code for code, _ in SUPPORTED_LANGUAGES]
    assert all(mod_name in text for text in ini_rule["translations"].values())
    assert builder._localize_entry(ini_rule, "fr_FR")["description"] == (
        f"Règle définie dans l'ini de {mod_name}"
    )
    assert "translations" not in manual_rule


def test_ini_origin_survives_rule_loading(rules: RuleManager, tmp_path: Path) -> None:
    _write(tmp_path / "mods" / "demo.json", {"rules": {"order": [{"rule": "demo(0)&other(1):third(-)", "origin": INI_RULE_ORIGIN}]}})

    (loaded_rule,) = rules._create_cache_builder(["en_US"])._load_sources()["order"]

    assert loaded_rule["origin"] == INI_RULE_ORIGIN
    assert "source_groups" in loaded_rule and "source" not in loaded_rule
