"""L8: the LCC source chooses which entry describes each tp2."""

import json
from pathlib import Path

from util.mod_sync.sources.lcc_source import LCCSource


def lcc_entry(mod_id: int, name: str, tp2: str, status: list[str]) -> dict:
    return {"id": mod_id, "name": name, "tp2": tp2, "games": ["BG2EE"], "status": status}


def cached_lcc(cache_dir: Path, entries: list[dict]) -> LCCSource:
    """Return an LCC source reading the given entries from its cache, without network."""
    for filename in set(LCCSource.FILES.values()):
        (cache_dir / filename).write_text(json.dumps(entries), encoding="utf-8")

    source = LCCSource(cache_dir)
    assert source.fetch_all()
    return source


def test_obsolete_entry_never_replaces_the_maintained_one_sharing_its_tp2(
    tmp_path: Path,
) -> None:
    source = cached_lcc(
        tmp_path,
        [
            lcc_entry(959, "Six's Kitpack", "kitpack", ["stable"]),
            lcc_entry(129, "BG:EE Kitpack", "kitpack", ["obsolete"]),
        ],
    )

    assert source.get_all_mods()["kitpack"]["name"] == "Six's Kitpack"


def test_embedded_entry_never_replaces_the_maintained_one_sharing_its_tp2(
    tmp_path: Path,
) -> None:
    source = cached_lcc(
        tmp_path,
        [
            lcc_entry(2232, "Revised Enhanced Kits", "improvekit", ["stable"]),
            lcc_entry(2212, "Enhanced Kit MOD", "improvekit", ["embed"]),
        ],
    )

    assert source.get_all_mods()["improvekit"]["name"] == "Revised Enhanced Kits"


def test_mod_without_usable_entry_is_not_listed(tmp_path: Path) -> None:
    source = cached_lcc(
        tmp_path,
        [
            lcc_entry(1, "Old mod", "old", ["obsolete"]),
            lcc_entry(3, "Absorbed mod", "absorbed", ["stable", "embed"]),
            lcc_entry(4, "Abandoned mod", "abandoned", ["archived"]),
            lcc_entry(5, "Finished mod", "finished", ["archived", "stable"]),
            lcc_entry(2, "Hidden mod", "hidden_mod", ["stable", "hidden"]),
        ],
    )

    assert set(source.get_all_mods()) == {"hidden_mod", "finished"}
