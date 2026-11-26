"""L5: the L4 run only installs proven preparations and stops if they change."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from core.ExtractionManifest import ExtractionManifest
from core.InstallationExecutor import InstallationError, InstallationExecutor
from core.InstallationJournal import (
    InstallationJournal,
    Operation,
    RunDivergenceError,
    RunSequence,
)
from core.Mod import Mod
from core.weidu_types import ComponentInfo, ComponentStatus
from core.WeiDUDebugParser import WeiDUDebugParser
from core.WeiDUInstallerEngine import WeiDUInstallerEngine
from core.WeiDULogParser import WeiDULogParser
from tests.conftest import ModCatalogue
from tests.test_l4_installation import state_for

SHA_V1 = "1" * 64
SHA_V2 = "2" * 64


def manifest_path(folder: Path, mod_id: str) -> Path:
    return ExtractionManifest.path_for(folder, mod_id)


def write_manifest(folder: Path, mod_id: str, sha256: str) -> None:
    ExtractionManifest(mod_id, sha256, 0).save(manifest_path(folder, mod_id))


def test_run_requires_a_proven_preparation(
    tmp_path: Path, catalogue: ModCatalogue, rules
) -> None:
    state = state_for(tmp_path, catalogue, rules, {0: ["a:0", "b:0"]}, ["a:0", "b:0"])
    manifest_path(tmp_path, "b").unlink()

    with pytest.raises(InstallationError, match="b:0") as error:
        InstallationExecutor(state).start(batch=True)
    assert "a:0" not in str(error.value)
    assert not (tmp_path / "installation-run.json").exists()


def test_run_freezes_the_archive_of_each_operation(
    tmp_path: Path, catalogue: ModCatalogue, rules
) -> None:
    catalogue.get_mod_by_id("b").provided_by = "a"
    state = state_for(tmp_path, catalogue, rules, {0: ["a:0", "b:0"]}, ["a:0", "b:0"])
    write_manifest(tmp_path, "a", SHA_V1)
    manifest_path(tmp_path, "b").unlink()

    executor = InstallationExecutor(state)
    executor.start(batch=True)

    operations = InstallationJournal.load(executor.journal_path).sequences[0].operations
    assert [(op.archive_owner, op.archive_sha256) for op in operations] == [
        ("a", SHA_V1),
        ("a", SHA_V1),
    ]


def test_resume_with_another_archive_is_suspended(
    tmp_path: Path, catalogue: ModCatalogue, rules
) -> None:
    state = state_for(tmp_path, catalogue, rules, {0: ["a:0"]}, ["a:0"])
    write_manifest(tmp_path, "a", SHA_V1)
    executor = InstallationExecutor(state)
    executor.start(batch=True)
    executor.release()

    write_manifest(tmp_path, "a", SHA_V2)
    with pytest.raises(InstallationError, match="a:0"):
        InstallationExecutor(state).start(batch=True)
    assert InstallationJournal.load(executor.journal_path).active

    manifest_path(tmp_path, "a").unlink()
    with pytest.raises(InstallationError):
        InstallationExecutor(state).start(batch=True)

    write_manifest(tmp_path, "a", SHA_V1)
    assert InstallationExecutor(state).start(batch=True).resumed


def test_archive_changed_during_the_run_prevents_the_launch(
    tmp_path: Path, catalogue: ModCatalogue, rules
) -> None:
    state = state_for(tmp_path, catalogue, rules, {0: ["a:0"]}, ["a:0"])
    write_manifest(tmp_path, "a", SHA_V1)
    executor = InstallationExecutor(state)
    executor.start(batch=True)

    write_manifest(tmp_path, "a", SHA_V2)
    with pytest.raises(RunDivergenceError):
        executor.journal.begin(0, [0])
    assert executor.journal.sequences[0].operations[0].status is ComponentStatus.PENDING


def shared_archive_sequence(folder: Path, sha256: str | None) -> RunSequence:
    """Two operations prepared from the archive of mod "a"."""
    operations = [
        Operation(
            reference, "a", 0, 0, [], [], [], "std", archive_owner="a", archive_sha256=sha256
        )
        for reference in ("a:0", "b:0")
    ]
    return RunSequence(str(folder), RunSequence.identify(folder), operations)


def test_archive_changed_between_two_verifications_is_detected(tmp_path: Path) -> None:
    write_manifest(tmp_path, "a", SHA_V1)
    sequence = shared_archive_sequence(tmp_path, SHA_V1)
    sequence.verify_preparation()

    write_manifest(tmp_path, "a", SHA_V2)
    with pytest.raises(RunDivergenceError, match="a:0"):
        sequence.verify_preparation()


def test_shared_archive_manifest_is_read_once_per_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_manifest(tmp_path, "a", SHA_V1)
    sequence = shared_archive_sequence(tmp_path, None)
    reads: list[Path] = []
    load = ExtractionManifest.load

    def counting_load(_cls: type[ExtractionManifest], path: Path) -> ExtractionManifest | None:
        reads.append(path)
        return load(path)

    monkeypatch.setattr(ExtractionManifest, "load", classmethod(counting_load))

    InstallationExecutor._freeze_preparations([sequence])
    assert [op.archive_sha256 for op in sequence.operations] == [SHA_V1, SHA_V1]
    assert reads == [manifest_path(tmp_path, "a")]

    reads.clear()
    sequence.verify_preparation()
    assert reads == [manifest_path(tmp_path, "a")]


@pytest.mark.parametrize(("provided_by", "expected"), [(None, "end"), ("eet", "eet")])
def test_missing_tp2_fails_before_weidu_starts(
    tmp_path: Path, provided_by: str | None, expected: str
) -> None:
    mod = Mod(
        {
            "id": "end",
            "name": "End",
            "tp2": "end",
            "links": {},
            "components": {"0": {"type": "std"}},
            "provided_by": provided_by,
        }
    )
    engine = WeiDUInstallerEngine(tmp_path, WeiDULogParser(), WeiDUDebugParser())
    runner_factory = Mock()
    component = ComponentInfo("end:0", mod, mod.get_component("0"), "end", 0)

    results = engine.install_components("end", [component], "0", [], [], runner_factory)

    assert results["end:0"].status is ComponentStatus.ERROR
    assert expected in results["end:0"].errors[0]
    runner_factory.assert_not_called()
