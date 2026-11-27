"""Isolated, offline fixtures for the L0 regression suite."""

import atexit
from collections.abc import Iterator
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile

os.environ["QT_QPA_PLATFORM"] = "offscreen"
# Read once by `constants` (VAR_DIR_ENVIRONMENT_VARIABLE), hence set before any
# project import: the suite never writes in var/ of the checkout.
os.environ["BWS_NG_VAR_DIR"] = tempfile.mkdtemp(prefix="bws-ng-tests-var-")

import pytest

from core import InstallationExecutor as installation_executor_module
from core.ComponentReference import IndexManager, ReferenceIndexes
from core.GameManager import GameManager
from core.Mod import Mod
from core.ModManager import ModManager
from core.RuleManager import RuleManager

# Match application import order until the parser/engine cycle is removed.
from core.WeiDUInstallerEngine import WeiDUInstallerEngine  # noqa: F401
from util.bench_core import wait_for_cache
from validation.catalogue import ROOT

atexit.register(shutil.rmtree, os.environ["BWS_NG_VAR_DIR"], ignore_errors=True)


class ModCatalogue:
    """Small in-memory catalogue implementing the manager read interface."""

    def __init__(self, mods: list[Mod]) -> None:
        self.mods: dict[str, Mod] = {mod.id: mod for mod in mods}

    def get_all_mods(self) -> dict[str, Mod]:
        return self.mods

    def get_mod_by_id(self, mod_id: str) -> Mod | None:
        return self.mods.get(mod_id.lower())


@pytest.fixture(autouse=True)
def isolated_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Prevent network/process IO and confine relative writes and the run journal to tmp."""

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Real network and external processes are forbidden in tests")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.chdir(tmp_path)
    # One run journal and lock per test, never shared through the suite var/ root
    monkeypatch.setattr(
        installation_executor_module,
        "INSTALLATION_JOURNAL_FILE",
        tmp_path / installation_executor_module.INSTALLATION_JOURNAL_FILE.name,
    )
    monkeypatch.setattr(installation_executor_module, "RUN_HISTORY_DIR", tmp_path / "history")
    IndexManager.reset()
    yield
    IndexManager.reset()


@pytest.fixture
def catalogue() -> ModCatalogue:
    mods = [
        Mod(
            {
                "id": name,
                "name": name,
                "links": {},
                "tp2": name,
                "games": ["eet"],
                "components": {"0": {"type": "std"}},
            }
        )
        for name in ("a", "b")
    ]
    mods.append(
        Mod(
            {
                "id": "demo",
                "name": "Demo",
                "links": {},
                "tp2": "demo",
                "games": ["eet"],
                "components": {
                    "choice_0": {"type": "muc", "components": ["10", "11"]},
                    "1": {
                        "type": "sub",
                        "prompts": {"1": {"options": ["A", "B"], "default": "A"}},
                    },
                    "2": {
                        "type": "sub",
                        "prompts": {"1": {"options": ["A", "B"], "default": "B"}},
                    },
                },
            }
        )
    )
    return ModCatalogue(mods)


@pytest.fixture
def rules(catalogue: ModCatalogue, tmp_path: Path) -> RuleManager:
    return RuleManager(
        catalogue,
        rules_dir=tmp_path / "rules",
        cache_dir=tmp_path / "cache",
        mods_dir=tmp_path / "mods",
        custom_mods_dir=tmp_path / "custom",
    )


@dataclass(frozen=True)
class ShippedCatalogue:
    """Mods, rules and games of the shipped catalogue, loaded once for the whole suite."""

    rules: RuleManager
    games: GameManager
    # Catalogue part of the global indexes, which every test starts by clearing
    references: ReferenceIndexes

    def publish_references(self) -> None:
        """Make the catalogue components resolvable again, as after its loading."""
        IndexManager.get_indexes().replace_catalogue(self.references)


@pytest.fixture(scope="session")
def shipped_catalogue(
    qapp: object, tmp_path_factory: pytest.TempPathFactory
) -> ShippedCatalogue:
    cache_dir = tmp_path_factory.mktemp("shipped-cache")
    mod_manager = ModManager(ROOT / "data" / "mods", cache_dir=cache_dir / "mods")
    rule_manager = RuleManager(
        mod_manager,
        ROOT / "data" / "rules",
        mods_dir=mod_manager.mods_dir,
        cache_dir=cache_dir / "rules",
    )
    wait_for_cache(mod_manager)
    wait_for_cache(rule_manager)

    references = ReferenceIndexes()
    references.replace_catalogue(IndexManager.get_indexes())
    games = GameManager(ROOT / "data" / "games")
    games.load_games()
    return ShippedCatalogue(rule_manager, games, references)


@pytest.fixture
def shipped_rules(shipped_catalogue: ShippedCatalogue) -> RuleManager:
    """Rules of the shipped catalogue, indexed against its mods."""
    return shipped_catalogue.rules


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
