"""Publication gate scenarios using a disposable catalogue."""

import json
from pathlib import Path
import sys

import pytest

from tests.conftest import write_json
from validation.catalogue import ROOT, main, validate_catalogue


def test_valid_catalogue(small_catalogue: tuple[Path, Path]) -> None:
    report = validate_catalogue(*small_catalogue)
    assert report.valid, report.diagnostics
    assert report.counts == {"mods": 1, "games": 1, "rules": 3}


def test_added_invalid_file_blocks_publication(small_catalogue: tuple[Path, Path]) -> None:
    data, _ = small_catalogue
    write_json(data / "mods" / "added.json", {"name": "Incomplete"})
    report = validate_catalogue(*small_catalogue)
    assert not report.valid
    assert any(
        item.file == "mods/added.json" and item.code == "schema" for item in report.diagnostics
    )


@pytest.mark.parametrize(
    "rule,code",
    [
        ("demo(0):demo(99)", "missing_component"),
        ("demo(0):demo(0)&absent(0)|demo(-)", "rule"),
    ],
    ids=["component missing from a known mod", "malformed rule"],
)
def test_broken_rule_blocks_publication(
    small_catalogue: tuple[Path, Path],
    rule: str,
    code: str,
) -> None:
    data, _ = small_catalogue
    write_json(data / "rules" / "dependencies.json", {"rules": [{"rule": rule}]})
    assert any(item.code == code for item in validate_catalogue(*small_catalogue).diagnostics)


def test_rule_waiting_for_an_uncatalogued_mod_is_a_dormant_notice(
    small_catalogue: tuple[Path, Path],
) -> None:
    data, _ = small_catalogue
    write_json(data / "rules" / "dependencies.json", {"rules": [{"rule": "demo(0):absent(0)"}]})

    report = validate_catalogue(*small_catalogue)

    assert report.valid, report.diagnostics
    assert ("rules/dependencies.json", "rules/0", "dormant_rule", "absent:0") in [
        (item.file, item.location, item.code, item.message) for item in report.notices
    ]


def test_dormant_rule_still_checks_components_of_known_mods(
    small_catalogue: tuple[Path, Path],
) -> None:
    data, _ = small_catalogue
    write_json(data / "rules" / "dependencies.json", {"rules": [{"rule": "absent(0):demo(99)"}]})

    report = validate_catalogue(*small_catalogue)

    assert [item.code for item in report.diagnostics] == ["missing_component"]


def test_deleted_mod_invalidates_existing_order(small_catalogue: tuple[Path, Path]) -> None:
    data, _ = small_catalogue
    (data / "mods" / "demo.json").unlink()
    assert any(
        item.code == "missing_mod" for item in validate_catalogue(*small_catalogue).diagnostics
    )


def test_schema_change_revalidates_unchanged_files(small_catalogue: tuple[Path, Path]) -> None:
    _, schemas = small_catalogue
    path = schemas / "mod-definition.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    schema["required"].append("new_required_field")
    write_json(path, schema)
    assert any(
        item.code == "schema" for item in validate_catalogue(*small_catalogue).diagnostics
    )


def test_empty_catalogue_fails(tmp_path: Path) -> None:
    assert not validate_catalogue(tmp_path, ROOT / "validation" / "schemas").valid


def test_missing_rule_file_fails(small_catalogue: tuple[Path, Path]) -> None:
    data, _ = small_catalogue
    (data / "rules" / "order.json").unlink()
    assert any(
        item.code == "missing" for item in validate_catalogue(*small_catalogue).diagnostics
    )


def test_duplicate_order_fails(small_catalogue: tuple[Path, Path]) -> None:
    data, _ = small_catalogue
    path = data / "games" / "eet.json"
    game = json.loads(path.read_text(encoding="utf-8"))
    game["sequences"][0]["order"].append("demo:0")
    write_json(path, game)
    assert any(
        item.code == "duplicate_order"
        for item in validate_catalogue(*small_catalogue).diagnostics
    )


def test_malformed_json_fails(small_catalogue: tuple[Path, Path]) -> None:
    data, _ = small_catalogue
    (data / "mods" / "demo.json").write_text("{", encoding="utf-8")
    assert any(item.code == "load" for item in validate_catalogue(*small_catalogue).diagnostics)


def test_report_is_deterministic(small_catalogue: tuple[Path, Path]) -> None:
    assert (
        validate_catalogue(*small_catalogue).to_dict()
        == validate_catalogue(*small_catalogue).to_dict()
    )


@pytest.mark.parametrize("valid", [True, False])
def test_cli_exit_code_and_report(
    small_catalogue: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    valid: bool,
) -> None:
    data, schemas = small_catalogue
    if not valid:
        write_json(data / "mods" / "invalid.json", {})
    output = data.parent / "report.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "catalogue",
            "--data-dir",
            str(data),
            "--schemas-dir",
            str(schemas),
            "--report",
            str(output),
        ],
    )
    assert main() == (0 if valid else 1)
    assert json.loads(output.read_text(encoding="utf-8"))["valid"] is valid


def mod_document(**fields: object) -> dict[str, object]:
    return {
        "name": "Bundled",
        "version": "1",
        "tp2": "bundled",
        "games": ["eet"],
        "categories": ["npc"],
        "components": {"0": {"type": "std"}},
        "translations": {"en_US": {"description": "Fixture", "components": {"0": "B"}}},
        "links": {},
        "authors": ["Test"],
        "safe": 2,
        **fields,
    }


PACK_FILES = [{"filename": "pack.zip", "size": 1, "download": "https://example.invalid/p.zip"}]


@pytest.mark.parametrize(
    "bundled,pack,code",
    [
        ({"provided_by": "absent"}, None, "missing_mod"),
        ({"provided_by": "pack"}, {}, "provider_without_archive"),
        ({"provided_by": "pack"}, {"provided_by": "demo"}, "provider_without_archive"),
        ({"provided_by": "pack", "files": PACK_FILES}, {"files": PACK_FILES}, "schema"),
    ],
)
def test_invalid_archive_provider_blocks_publication(
    small_catalogue: tuple[Path, Path],
    bundled: dict[str, object],
    pack: dict[str, object] | None,
    code: str,
) -> None:
    data, _ = small_catalogue
    write_json(data / "mods" / "bundled.json", mod_document(**bundled))
    if pack is not None:
        write_json(data / "mods" / "pack.json", mod_document(tp2="pack", **pack))

    report = validate_catalogue(*small_catalogue)

    assert any(
        item.file == "mods/bundled.json" and item.code == code for item in report.diagnostics
    ), report.diagnostics


@pytest.mark.parametrize(
    "components,empty_categories",
    [
        ({"0": {"type": "std", "category": "portrait"}}, ["npc", "quest"]),
        (
            {"0": {"type": "std", "category": "portrait"}, "1": {"type": "std", "category": "npc"}},
            ["quest"],
        ),
        ({"0": {"type": "std", "category": "portrait"}, "1": {"type": "std"}}, []),
    ],
    ids=[
        "every component moved elsewhere",
        "one declared category left empty",
        "component inheriting the mod categories",
    ],
)
def test_declared_category_without_component_is_a_notice(
    small_catalogue: tuple[Path, Path],
    components: dict[str, object],
    empty_categories: list[str],
) -> None:
    data, _ = small_catalogue
    translations = {"en_US": {"description": "Fixture", "components": dict.fromkeys(components, "B")}}
    write_json(
        data / "mods" / "bundled.json",
        mod_document(
            categories=["npc", "quest"],
            components=components,
            translations=translations,
            files=PACK_FILES,
        ),
    )

    report = validate_catalogue(*small_catalogue)

    assert report.valid, report.diagnostics
    assert [
        item.message
        for item in report.notices
        if item.file == "mods/bundled.json" and item.code == "empty_category"
    ] == empty_categories


def test_provided_and_manual_archives_are_valid(small_catalogue: tuple[Path, Path]) -> None:
    data, _ = small_catalogue
    write_json(data / "mods" / "pack.json", mod_document(tp2="pack", files=PACK_FILES))
    write_json(data / "mods" / "bundled.json", mod_document(provided_by="Pack"))

    report = validate_catalogue(*small_catalogue)

    assert report.valid, report.diagnostics
    assert [(item.file, item.code) for item in report.notices] == [
        ("mods/demo.json", "manual_archive")
    ]
