"""L8: mod_sync selects mods explicitly and finds existing definitions."""

import json
from pathlib import Path

import pytest

from util.mod_sync import orchestrator as orchestrator_module
from util.mod_sync.models.sync_result import ModFilter, SyncConfig, SyncStatus
from util.mod_sync.orchestrator import ModSyncOrchestrator


def lcc_mod(tp2: str, games: list[str]) -> dict:
    return {
        "name": tp2.title(),
        "tp2": tp2,
        "safe": 2,
        "games": games,
        "categories": ["quest"],
        "authors": ["Author"],
        "descriptions": {},
        "links": {"forums": []},
    }


LCC_MODS = {
    "c#brage": lcc_mod("c#brage", ["bg2ee", "eet"]),
    "ascension": lcc_mod("ascension", ["bg2ee"]),
    "dlcmerger": lcc_mod("dlcmerger", ["bgee"]),
}


class OfflineLCCSource:
    """LCC source serving a fixed catalogue without network or cache."""

    def __init__(self, force_refresh: bool = False) -> None:
        self.force_refresh = force_refresh

    def fetch_all(self) -> bool:
        return True

    def get_all_mods(self) -> dict[str, dict]:
        return LCC_MODS


@pytest.fixture
def definitions_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(orchestrator_module, "LCCSource", OfflineLCCSource)
    directory = tmp_path / "mods"
    directory.mkdir()
    return directory


def synchronize(definitions_dir: Path, mod_filter: ModFilter) -> dict[str, SyncStatus]:
    config = SyncConfig(definitions_dir=definitions_dir, mod_filter=mod_filter)
    stats = ModSyncOrchestrator(config).run()
    return {result.tp2_name: result.status for result in stats.results}


def test_default_filter_processes_every_lcc_mod(definitions_dir: Path) -> None:
    statuses = synchronize(definitions_dir, ModFilter())

    assert set(statuses.values()) == {SyncStatus.CREATED}
    assert len(list(definitions_dir.glob("*.json"))) == len(LCC_MODS)


def test_mods_outside_only_selection_are_skipped_not_errors(definitions_dir: Path) -> None:
    statuses = synchronize(definitions_dir, ModFilter(tp2_names=frozenset({"C#Brage"})))

    assert statuses == {
        "c#brage": SyncStatus.CREATED,
        "ascension": SyncStatus.SKIPPED,
        "dlcmerger": SyncStatus.SKIPPED,
    }
    assert [path.name for path in definitions_dir.glob("*.json")] == ["c#brage.json"]


def test_game_filter_keeps_mods_supporting_a_selected_game(definitions_dir: Path) -> None:
    statuses = synchronize(definitions_dir, ModFilter(games=frozenset({"EET", "iwdee"})))

    assert statuses == {
        "c#brage": SyncStatus.CREATED,
        "ascension": SyncStatus.SKIPPED,
        "dlcmerger": SyncStatus.SKIPPED,
    }


def test_existing_definition_with_other_casing_is_updated_in_place(
    definitions_dir: Path,
) -> None:
    existing_path = definitions_dir / "C#Brage.json"
    existing_path.write_text(
        json.dumps({"tp2": "C#Brage", "name": "Old name", "games": ["bg2ee"]}),
        encoding="utf-8",
    )

    statuses = synchronize(definitions_dir, ModFilter(tp2_names=frozenset({"c#brage"})))

    assert statuses["c#brage"] == SyncStatus.UPDATED
    assert [path.name for path in definitions_dir.glob("*.json")] == ["C#Brage.json"]
    assert json.loads(existing_path.read_text(encoding="utf-8"))["games"] == ["bg2ee", "eet"]


def test_unreadable_definition_is_reported_and_left_untouched(definitions_dir: Path) -> None:
    existing_path = definitions_dir / "c#brage.json"
    existing_path.write_text('{"name": "Manual work",', encoding="utf-8")

    statuses = synchronize(definitions_dir, ModFilter(tp2_names=frozenset({"c#brage"})))

    assert statuses["c#brage"] == SyncStatus.ERROR
    assert existing_path.read_text(encoding="utf-8") == '{"name": "Manual work",'


class FixedTP2Source:
    """TP2 source returning the same parsed data for every mod."""

    def __init__(self, data: dict) -> None:
        self.data = data

    def extract(
        self,
        tp2_name: str,
        supported_games: set[str] | None = None,
        labels_language: str = "en_US",
    ) -> dict:
        return self.data


def test_component_changes_are_reported_for_existing_definitions(definitions_dir: Path) -> None:
    stored = {
        "games": ["bg2ee", "eet"],
        "components": {"0": {"type": "std"}, "1": {"type": "std"}},
    }
    (definitions_dir / "c#brage.json").write_text(json.dumps(stored), encoding="utf-8")
    config = SyncConfig(
        definitions_dir=definitions_dir,
        mod_filter=ModFilter(frozenset({"c#brage", "ascension"})),
    )
    orchestrator = ModSyncOrchestrator(config)
    orchestrator.tp2_source = FixedTP2Source(
        {"components": {"0": {"type": "std"}, "2": {"type": "std"}}}
    )

    stats = orchestrator.run()

    changes = {result.tp2_name: result.component_changes for result in stats.results}
    assert (changes["c#brage"].added, changes["c#brage"].removed) == (("2",), ("1",))
    assert not changes["ascension"]
    assert "c#brage: components added: 2; components removed: 1" in stats.get_component_report()
