"""Publication gate scenarios using a disposable catalogue."""

import json
from pathlib import Path
import shutil
import sys

import pytest

from validation.catalogue import ROOT, main, validate_catalogue


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def small_catalogue(tmp_path: Path) -> tuple[Path, Path]:
    data, schemas = tmp_path / "data", tmp_path / "schemas"
    shutil.copytree(ROOT / "validation" / "schemas", schemas)
    write_json(
        data / "mods" / "demo.json",
        {
            "name": "Demo",
            "version": "1",
            "tp2": "demo",
            "games": ["eet"],
            "categories": ["npc"],
            "components": {"0": {"type": "std"}},
            "translations": {
                "en_US": {"description": "Synthetic fixture", "components": {"0": "Demo"}}
            },
            "links": {},
            "authors": ["Test"],
            "safe": 2,
        },
    )
    write_json(
        data / "games" / "eet.json",
        {
            "id": "eet",
            "name": "EET",
            "sequences": [{"game": "bg2ee", "order": ["demo:0"]}],
        },
    )
    for name in ("dependencies", "incompatibilities", "order"):
        write_json(data / "rules" / f"{name}.json", {"rules": []})
    return data, schemas


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
    "reference,code", [("absent(0)", "missing_mod"), ("demo(99)", "missing_component")]
)
def test_broken_rule_blocks_publication(
    small_catalogue: tuple[Path, Path],
    reference: str,
    code: str,
) -> None:
    data, _ = small_catalogue
    write_json(
        data / "rules" / "dependencies.json", {"rules": [{"rule": f"demo(0):{reference}"}]}
    )
    assert any(item.code == code for item in validate_catalogue(*small_catalogue).diagnostics)


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
