"""L8: literal labels of a TP2 declaring no LANGUAGE reach the definition translations."""

import json
from pathlib import Path

import pytest

from core.WeiDUTp2Parser import WeiDUTp2Parser
from util.mod_sync import orchestrator as orchestrator_module
from util.mod_sync.models.sync_result import ModFilter, SyncConfig
from util.mod_sync.orchestrator import ModSyncOrchestrator
from util.mod_sync.sources.tp2_source import labels_language

LITERAL_TP2 = """BACKUP ~weidu_external/backup/demo~
AUTHOR ~author~

BEGIN ~Demo Core~
DESIGNATED 0

BEGIN ~Demo Alternate Graphics~
DESIGNATED 4
"""

TRANSLATED_TP2 = """BACKUP ~weidu_external/backup/demo~
AUTHOR ~author~
LANGUAGE ~English~ ~english~ ~demo/english/setup.tra~

BEGIN @1
DESIGNATED 0
"""


def test_tp2_without_language_exposes_its_literal_labels(tmp_path: Path) -> None:
    tp2 = WeiDUTp2Parser(tmp_path).parse_string(LITERAL_TP2, "demo", None, tmp_path)

    assert tp2.untranslated_component_labels == {
        "0": "Demo Core",
        "4": "Demo Alternate Graphics",
    }
    assert tp2.component_translations == {}


def test_tp2_with_language_has_no_untranslated_labels(tmp_path: Path) -> None:
    (tmp_path / "demo" / "english").mkdir(parents=True)
    (tmp_path / "demo" / "english" / "setup.tra").write_text(
        "@1 = ~Demo Core~", encoding="utf-8"
    )

    tp2 = WeiDUTp2Parser(tmp_path).parse_string(TRANSLATED_TP2, "demo", None, tmp_path)

    assert tp2.untranslated_component_labels == {}
    assert tp2.component_translations == {"en_US": {"0": "Demo Core"}}


@pytest.mark.parametrize(
    ("languages", "expected"),
    [
        (None, "en_US"),
        ({"all": 0}, "en_US"),
        ({"fr_FR": 0, "en_US": 1}, "en_US"),
        ({"fr_FR": 0, "all": 0}, "fr_FR"),
        ({"pl_PL": 0, "fr_FR": 1}, "pl_PL"),
    ],
)
def test_literal_labels_take_english_or_else_the_first_mod_language(
    languages: dict | None, expected: str
) -> None:
    assert labels_language(languages) == expected


class OfflineLCCSource:
    def __init__(self, force_refresh: bool = False) -> None:
        pass

    def fetch_all(self) -> bool:
        return True

    def get_all_mods(self) -> dict[str, dict]:
        return {"demo": {"tp2": "demo", "games": ["bg2ee"]}}


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(orchestrator_module, "LCCSource", OfflineLCCSource)
    (tmp_path / "mods").mkdir()
    (tmp_path / "extracted" / "demo").mkdir(parents=True)
    (tmp_path / "extracted" / "demo" / "demo.tp2").write_text(LITERAL_TP2, encoding="utf-8")
    return tmp_path


def synchronize(workspace: Path, definition: dict) -> dict:
    definition_path = workspace / "mods" / "demo.json"
    definition_path.write_text(json.dumps(definition), encoding="utf-8")
    config = SyncConfig(
        definitions_dir=workspace / "mods",
        mods_dir=workspace / "extracted",
        extract_ini=False,
        mod_filter=ModFilter(),
    )
    ModSyncOrchestrator(config).run()
    return json.loads(definition_path.read_text(encoding="utf-8"))


def test_literal_labels_replace_and_complete_english_labels(workspace: Path) -> None:
    definition = synchronize(
        workspace,
        {
            "games": ["bg2ee"],
            "languages": {"en_US": 0, "all": 0},
            "components": {"0": {"type": "std"}},
            "translations": {
                "en_US": {"description": "Kept", "components": {"0": "Old label"}}
            },
        },
    )

    assert definition["translations"]["en_US"] == {
        "description": "Kept",
        "components": {"0": "Demo Core", "4": "Demo Alternate Graphics"},
    }


def test_literal_labels_of_a_single_language_mod_go_to_that_language(workspace: Path) -> None:
    definition = synchronize(
        workspace,
        {
            "games": ["bg2ee"],
            "languages": {"fr_FR": 0},
            "components": {"0": {"type": "std"}},
            "translations": {"fr_FR": {"components": {"0": "Composant principal"}}},
        },
    )

    assert definition["translations"]["fr_FR"]["components"] == {
        "0": "Demo Core",
        "4": "Demo Alternate Graphics",
    }
    assert "en_US" not in definition["translations"]
