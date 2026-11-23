"""L5: safe extraction, proof of preparation and explicit merge failures."""

from dataclasses import replace
import hashlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
from typing import Callable
from unittest.mock import Mock
import zipfile

import pytest
from pytestqt.qtbot import QtBot

from core import ArchiveExtractor as extractor_module
from core.ArchiveExtractor import (
    ArchiveExtractor,
    ExtractionFailure,
    ExtractionInfo,
    ExtractionLimits,
    ExtractionStatus,
)
from core.ArchiveRegistry import ArchiveRecord, ArchiveRegistry, ArchiveSource
from core.ArchiveService import ArchiveService
from core.DirectoryMerger import ConflictResolution, DirectoryMerger
from core.ExtractionManifest import ExtractionManifest
from core.ExtractionPipeline import ExtractionPipeline, PreparationError
from core.Mod import Mod
from core.StateManager import StateManager
from tests.conftest import ModCatalogue
from ui.pages.ExtractionPage import COL_MOD_NAME, COL_ORDER, ExtractionPage

PLATFORM = "windows"
TP2_V1 = "BACKUP ~demo/backup~\nAUTHOR ~me~\nBEGIN ~Main~ DESIGNATED 0\n"
TP2_V2 = TP2_V1 + "BEGIN ~Extra~ DESIGNATED 3\n"


def make_zip(path: Path, members: dict[str, str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return path


def make_mod(archive: Path | None = None, **extra: object) -> Mod:
    files = []
    if archive is not None:
        content = archive.read_bytes()
        files.append(
            {
                "filename": archive.name,
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    data = {
        "id": "demo",
        "name": "Demo",
        "links": {},
        "tp2": "demo",
        "components": {"0": {"type": "std"}},
        "files": files,
    }
    data.update(extra)
    return Mod(data)


def set_mtime(path: Path, mtime_ns: int) -> None:
    os.utime(path, ns=(mtime_ns, mtime_ns))


@pytest.fixture
def registry(tmp_path: Path) -> ArchiveRegistry:
    return ArchiveRegistry.load(tmp_path / "archives.json")


@pytest.fixture
def game(tmp_path: Path) -> Path:
    folder = tmp_path / "game"
    folder.mkdir()
    return folder


def extraction_for(archive: Path, game: Path, mod: Mod | None = None) -> ExtractionInfo:
    return ExtractionInfo(
        "demo_seq0",
        "demo",
        "Demo",
        "demo",
        archive,
        game,
        mod=mod,
        manifest_path=ExtractionManifest.path_for(game, "demo"),
    )


def pipeline_for(registry: ArchiveRegistry) -> ExtractionPipeline:
    merger = DirectoryMerger(ConflictResolution.MERGE)
    return ExtractionPipeline(merger, ArchiveService(registry), PLATFORM)


# ----------------------------------------------------------------------
# Archive content is checked before anything is written
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "member", ["../evil.txt", "demo/../../evil.txt", "/evil.txt", "C:/evil.txt", "a:stream"]
)
def test_member_leaving_destination_is_refused(tmp_path: Path, member: str) -> None:
    archive = tmp_path / "slip.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        stream.writestr("demo/demo.tp2", TP2_V1)
        stream.writestr(zipfile.ZipInfo(member), "payload")
    destination = tmp_path / "out" / "staging"

    result = ArchiveExtractor.extract_archive(archive, destination)

    assert not result and result.failure is ExtractionFailure.UNSAFE_PATH
    assert not (tmp_path / "evil.txt").exists()
    assert not (tmp_path / "out" / "evil.txt").exists()
    assert not any(destination.iterdir())


def test_link_in_tar_is_refused(tmp_path: Path) -> None:
    archive = tmp_path / "link.tar.gz"
    with tarfile.open(archive, "w:gz") as stream:
        data = b"payload"
        regular = tarfile.TarInfo("demo/readme.txt")
        regular.size = len(data)
        stream.addfile(regular, io.BytesIO(data))
        link = tarfile.TarInfo("demo/escape")
        link.type = tarfile.SYMTYPE
        link.linkname = "../../outside"
        stream.addfile(link)
    destination = tmp_path / "staging"

    result = ArchiveExtractor.extract_archive(archive, destination)

    assert result.failure is ExtractionFailure.LINK and result.detail == "demo/escape"
    assert not any(destination.iterdir())


def test_archive_beyond_quotas_is_refused(tmp_path: Path) -> None:
    archive = make_zip(tmp_path / "bomb.zip", {"a.txt": "x" * 2_000, "b.txt": "y"})

    by_size = ArchiveExtractor.extract_archive(
        archive, tmp_path / "size", limits=ExtractionLimits(max_bytes=1_000)
    )
    by_count = ArchiveExtractor.extract_archive(
        archive, tmp_path / "count", limits=ExtractionLimits(max_entries=1)
    )

    assert by_size.failure is ExtractionFailure.QUOTA
    assert by_count.failure is ExtractionFailure.QUOTA
    assert not any((tmp_path / "size").iterdir())


def test_insufficient_disk_space_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = make_zip(tmp_path / "demo.zip", {"demo/demo.tp2": TP2_V1})
    monkeypatch.setattr(
        extractor_module.shutil, "disk_usage", lambda _: shutil._ntuple_diskusage(1, 1, 0)
    )

    result = ArchiveExtractor.extract_archive(archive, tmp_path / "staging")

    assert result.failure is ExtractionFailure.DISK_SPACE


def test_safe_zip_is_extracted_with_its_counts(tmp_path: Path) -> None:
    archive = make_zip(tmp_path / "demo.zip", {"demo/demo.tp2": TP2_V1, "readme.txt": "r"})

    result = ArchiveExtractor.extract_archive(archive, tmp_path / "staging")

    assert result and result.entries == 3
    assert (tmp_path / "staging" / "demo" / "demo.tp2").read_text() == TP2_V1


# ----------------------------------------------------------------------
# 7-Zip and executables
# ----------------------------------------------------------------------

LISTING_HEADER = "Listing archive: x\n\n--\nPath = x\nType = {type}\n\n----------\n"


class FakeRun:
    """Record the commands and answer 7-Zip listings."""

    def __init__(self, listing: str | None, extract_code: int = 0) -> None:
        self.listing = listing
        self.extract_code = extract_code
        self.commands: list[list[str]] = []
        self.options: list[dict] = []

    def __call__(self, command: list[str], **options: object) -> subprocess.CompletedProcess:
        self.commands.append(command)
        self.options.append(options)
        if "l" in command:
            if self.listing is None:
                return subprocess.CompletedProcess(command, 2, "", "Cannot open as archive")
            return subprocess.CompletedProcess(command, 0, self.listing, "")
        return subprocess.CompletedProcess(command, self.extract_code, "", "boom")


def seven_zip_listing(kind: str, *blocks: str) -> str:
    return LISTING_HEADER.format(type=kind) + "\n\n".join(blocks) + "\n"


def test_7z_listing_with_link_is_refused_before_extraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    listing = seven_zip_listing(
        "7z",
        "Path = demo\nFolder = +\nSize = 0\nAttributes = D",
        "Path = demo/demo.tp2\nFolder = -\nSize = 10\nAttributes = A",
        "Path = demo/escape\nSize = 0\nAttributes = A lrwxrwxrwx\nSymbolic Link = ../..",
    )
    fake = FakeRun(listing)
    monkeypatch.setattr(extractor_module.subprocess, "run", fake)

    result = ArchiveExtractor.extract_archive(tmp_path / "demo.7z", tmp_path / "staging")

    assert result.failure is ExtractionFailure.LINK
    assert [command[2] for command in fake.commands] == ["l"]
    assert all("shell" not in options for options in fake.options)
    assert all(options["stdin"] is subprocess.DEVNULL for options in fake.options)


def test_7z_listing_is_parsed() -> None:
    listing = ArchiveExtractor.parse_7z_listing(
        seven_zip_listing(
            "7z",
            "Path = demo\r\nFolder = +\r\nAttributes = D",
            "Path = demo/demo.tp2\r\nSize = 42\r\nAttributes = A",
        ).replace("\n", "\r\n")
    )
    assert listing.types == ["7z"]
    assert [(entry.name, entry.size, entry.is_dir) for entry in listing.entries] == [
        ("demo", 0, True),
        ("demo/demo.tp2", 42, False),
    ]


@pytest.mark.parametrize("listing", [None, seven_zip_listing("PE")])
def test_executable_is_never_run_without_recipe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, listing: str | None
) -> None:
    archive = tmp_path / "demo.exe"
    archive.write_bytes(b"MZ")
    fake = FakeRun(listing)
    monkeypatch.setattr(extractor_module.subprocess, "run", fake)

    result = ArchiveExtractor.extract_archive(archive, tmp_path / "staging")

    assert result.failure is ExtractionFailure.NOT_AN_ARCHIVE
    assert all(command[0] != str(archive) for command in fake.commands)


def test_failed_7z_extraction_of_executable_does_not_run_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "demo.exe"
    archive.write_bytes(b"MZ")
    fake = FakeRun(seven_zip_listing("7z", "Path = demo.tp2\nSize = 1"), extract_code=2)
    monkeypatch.setattr(extractor_module.subprocess, "run", fake)

    result = ArchiveExtractor.extract_archive(archive, tmp_path / "staging")

    assert not result
    assert all(command[0] != str(archive) for command in fake.commands)


def test_declared_recipe_runs_the_executable_into_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "demo.exe"
    archive.write_bytes(b"MZ")
    fake = FakeRun(None)
    monkeypatch.setattr(extractor_module.subprocess, "run", fake)
    staging = tmp_path / "staging"
    mod = make_mod(extract_recipe={"args": ["/S", "/D={destination}"]})

    result = ArchiveExtractor.extract_archive(archive, staging, mod.extract_recipe)

    assert result
    assert fake.commands[-1] == [str(archive), "/S", f"/D={staging}"]
    assert fake.options[-1]["cwd"] == staging


# ----------------------------------------------------------------------
# Proof of preparation
# ----------------------------------------------------------------------


def test_preparation_writes_a_manifest_matching_the_archive(
    tmp_path: Path, game: Path, registry: ArchiveRegistry
) -> None:
    archive = make_zip(tmp_path / "downloads" / "demo.zip", {"Demo-v1/demo/demo.tp2": TP2_V1})
    info = extraction_for(archive, game, make_mod(archive))

    pipeline_for(registry).prepare(info)

    record = registry.get("demo")
    manifest = ExtractionManifest.load(info.manifest_path)
    assert (game / "demo" / "demo.tp2").read_text() == TP2_V1
    assert manifest == ExtractionManifest("demo", record.sha256, record.mtime_ns)
    assert not info.staging_path.exists()
    assert ExtractionPipeline.is_prepared(info, record)


def test_new_version_with_same_name_is_extracted_again(
    tmp_path: Path, game: Path, registry: ArchiveRegistry
) -> None:
    archive = make_zip(tmp_path / "downloads" / "demo.zip", {"demo/demo.tp2": TP2_V1})
    set_mtime(archive, 1_000_000_000)
    info = extraction_for(archive, game, make_mod(archive))
    pipeline_for(registry).prepare(info)

    make_zip(archive, {"demo/demo.tp2": TP2_V2})
    set_mtime(archive, 2_000_000_000)
    info = extraction_for(archive, game, make_mod(archive))

    assert not ExtractionPipeline.is_prepared(info, registry.get("demo"))
    pipeline_for(registry).prepare(info)
    assert (game / "demo" / "demo.tp2").read_text() == TP2_V2
    assert ExtractionPipeline.is_prepared(info, registry.get("demo"))


def test_folder_without_manifest_is_not_prepared(tmp_path: Path, game: Path) -> None:
    (game / "demo").mkdir()
    (game / "demo" / "demo.tp2").write_text(TP2_V1)
    archive = make_zip(tmp_path / "demo.zip", {"demo/demo.tp2": TP2_V1})

    assert not ExtractionPipeline.is_prepared(extraction_for(archive, game), None)


def test_unverified_manual_archive_is_hashed_before_use(
    tmp_path: Path, game: Path, registry: ArchiveRegistry
) -> None:
    archive = make_zip(tmp_path / "elsewhere" / "other-name.zip", {"demo/demo.tp2": TP2_V1})
    mod = make_mod(archive)
    registry.put(ArchiveRecord.unverified("demo", str(archive), ArchiveSource.MANUAL))

    pipeline_for(registry).prepare(extraction_for(archive, game, mod))

    record = registry.get("demo")
    assert record.is_verified and record.source is ArchiveSource.MANUAL


def test_archive_differing_from_catalogue_is_not_extracted(
    tmp_path: Path, game: Path, registry: ArchiveRegistry
) -> None:
    archive = make_zip(tmp_path / "demo.zip", {"demo/demo.tp2": TP2_V1})
    mod = make_mod(archive)
    make_zip(archive, {"demo/demo.tp2": TP2_V2})
    info = extraction_for(archive, game, mod)

    with pytest.raises(PreparationError):
        pipeline_for(registry).prepare(info)
    assert not (game / "demo").exists()
    assert not info.manifest_path.exists()


def test_accepted_mismatch_requires_the_planned_components(
    tmp_path: Path, game: Path, registry: ArchiveRegistry
) -> None:
    archive = make_zip(tmp_path / "demo.zip", {"demo/demo.tp2": TP2_V1})
    mod = make_mod(archive)
    make_zip(archive, {"demo/demo.tp2": TP2_V2})
    service = ArchiveService(registry)
    rejected = service.verify(mod, archive, ArchiveSource.DOWNLOAD, PLATFORM)
    service.accept_mismatch(mod, rejected.record, PLATFORM)

    info = extraction_for(archive, game, mod)
    info.planned_components = {"demo": {"0", "3", "7"}}
    with pytest.raises(PreparationError, match="7"):
        pipeline_for(registry).prepare(info)
    assert not info.manifest_path.exists()

    info.planned_components = {"demo": {"0", "3"}}
    pipeline_for(registry).prepare(info)
    assert ExtractionPipeline.is_prepared(info, registry.get("demo"))


def test_provider_is_prepared_without_the_tp2_it_provides(
    tmp_path: Path, game: Path, registry: ArchiveRegistry
) -> None:
    """A provided TP2 may only appear when the provider is installed (lolfixer)."""
    archive = make_zip(tmp_path / "demo.zip", {"demo/demo.tp2": TP2_V2})
    mod = make_mod(archive)
    make_zip(archive, {"demo/demo.tp2": TP2_V1})
    service = ArchiveService(registry)
    rejected = service.verify(mod, archive, ArchiveSource.DOWNLOAD, PLATFORM)
    service.accept_mismatch(mod, rejected.record, PLATFORM)
    info = extraction_for(archive, game, mod)
    info.planned_components = {"demo": {"0"}, "demo_end": {"2"}}

    pipeline_for(registry).prepare(info)

    assert not (game / "demo_end").exists()
    assert ExtractionPipeline.is_prepared(info, registry.get("demo"))


# ----------------------------------------------------------------------
# Merge failures are terminal and retryable
# ----------------------------------------------------------------------


def test_locked_file_fails_then_retry_succeeds(
    tmp_path: Path, game: Path, registry: ArchiveRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = make_zip(tmp_path / "demo.zip", {"demo/demo.tp2": TP2_V1, "override/a.itm": "a"})
    (game / "override").mkdir()
    (game / "override" / "a.itm").write_text("old")
    info = extraction_for(archive, game, make_mod(archive))
    real_move = shutil.move

    def locked_move(source: str, destination: str) -> str:
        if Path(source).name == "a.itm":
            raise PermissionError("locked")
        return real_move(source, destination)

    monkeypatch.setattr("core.DirectoryMerger.shutil.move", locked_move)
    with pytest.raises(PreparationError, match="a.itm"):
        pipeline_for(registry).prepare(info)
    assert info.staging_path.exists()
    assert not info.manifest_path.exists()

    monkeypatch.setattr("core.DirectoryMerger.shutil.move", real_move)
    pipeline_for(registry).prepare(info)
    assert (game / "override" / "a.itm").read_text() == "a"
    assert not info.staging_path.exists()
    assert ExtractionPipeline.is_prepared(info, registry.get("demo"))


def test_file_where_archive_has_a_directory_is_a_failure(
    tmp_path: Path, game: Path, registry: ArchiveRegistry
) -> None:
    archive = make_zip(tmp_path / "demo.zip", {"demo/demo.tp2": TP2_V1, "override/a.itm": "a"})
    (game / "override").write_text("not a directory")
    info = extraction_for(archive, game, make_mod(archive))

    with pytest.raises(PreparationError):
        pipeline_for(registry).prepare(info)
    assert not info.manifest_path.exists()


def test_overwritten_files_are_traced(
    tmp_path: Path, game: Path, registry: ArchiveRegistry, caplog: pytest.LogCaptureFixture
) -> None:
    archive = make_zip(tmp_path / "demo.zip", {"demo/demo.tp2": TP2_V1, "override/a.itm": "a"})
    (game / "override").mkdir()
    (game / "override" / "a.itm").write_text("old")

    with caplog.at_level("INFO", logger="core.ExtractionPipeline"):
        pipeline_for(registry).prepare(extraction_for(archive, game, make_mod(archive)))

    assert any("a.itm" in message for message in caplog.messages)


def test_manifest_of_another_format_proves_nothing(tmp_path: Path) -> None:
    path = tmp_path / "demo.json"
    path.write_text('{"format_version": 99, "mod_id": "demo"}')
    assert ExtractionManifest.load(path) is None
    path.write_text("{truncated")
    assert ExtractionManifest.load(path) is None


def test_record_from_other_file_does_not_contradict_manifest(tmp_path: Path) -> None:
    archive = make_zip(tmp_path / "demo.zip", {"demo/demo.tp2": TP2_V1})
    mtime = archive.stat().st_mtime_ns
    other = replace(
        ArchiveRecord.unverified("demo", str(tmp_path / "other.zip"), ArchiveSource.MANUAL),
        sha256="f" * 64,
    )
    assert ExtractionManifest("demo", "a" * 64, mtime).matches(archive, other)
    assert not ExtractionManifest("demo", "a" * 64, mtime + 1).matches(archive, other)


# ----------------------------------------------------------------------
# Extraction page
# ----------------------------------------------------------------------


@pytest.fixture
def extraction_page(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, game: Path
) -> Callable[[list[Mod], list[str]], ExtractionPage]:
    monkeypatch.setattr(StateManager, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(StateManager, "ARCHIVE_REGISTRY_FILE", tmp_path / "archives.json")

    def build(mods: list[Mod], order: list[str]) -> ExtractionPage:
        state = StateManager()
        sequence = Mock(game="eet")
        sequence.is_mod_allowed.return_value = True
        game_def = Mock(sequences=[sequence], has_multiple_sequences=False)
        game_def.get_folder_keys.return_value = ["eet"]
        games = Mock()
        games.get.return_value = game_def
        monkeypatch.setattr(state, "get_mod_manager", lambda: ModCatalogue(mods))
        monkeypatch.setattr(state, "get_game_manager", lambda: games)
        monkeypatch.setattr(state, "get_install_order", lambda: {0: order})
        monkeypatch.setattr(state, "get_game_folders", lambda: {"eet": str(game)})
        state.set_download_folder(str(tmp_path / "downloads"))
        page = ExtractionPage(state)
        qtbot.addWidget(page)
        page.retranslate_ui()
        return page

    return build


def test_provided_mods_extend_their_provider_extraction(
    tmp_path: Path, game: Path, extraction_page: Callable[..., ExtractionPage]
) -> None:
    archive = make_zip(tmp_path / "downloads" / "demo.zip", {"demo/demo.tp2": TP2_V1})
    provider = make_mod(archive)
    provided = Mod(
        {
            "id": "demo_end",
            "name": "End",
            "tp2": "demo_end",
            "links": {},
            "provided_by": "demo",
            "components": {"2": {"type": "std"}},
        }
    )
    page = extraction_page([provider, provided], ["demo_end:2"])

    page.on_page_shown()

    assert list(page._extractions) == ["demo_seq0"]
    info = page._extractions["demo_seq0"]
    assert info.planned_components == {"demo_end": {"2"}}
    assert info.manifest_path == ExtractionManifest.path_for(game, "demo")
    assert page._extraction_status["demo_seq0"] is ExtractionStatus.TO_EXTRACT


def test_extractions_are_listed_in_extraction_order(
    tmp_path: Path, extraction_page: Callable[..., ExtractionPage]
) -> None:
    first = make_mod(make_zip(tmp_path / "downloads" / "zeta.zip", {}), id="zeta", name="Zeta")
    second = make_mod(
        make_zip(tmp_path / "downloads" / "alpha.zip", {}), id="alpha", name="Alpha"
    )
    page = extraction_page([first, second], ["zeta:0", "alpha:0"])

    page.on_page_shown()

    table = page._extraction_table
    rows = [
        (table.item(row, COL_ORDER).text(), table.item(row, COL_MOD_NAME).text())
        for row in range(table.rowCount())
    ]
    assert rows == [("1", "Zeta"), ("2", "Alpha")]
