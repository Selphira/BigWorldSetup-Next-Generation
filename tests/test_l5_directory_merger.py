"""L5: conflict strategies of DirectoryMerger never lose the original destination."""

import os
from pathlib import Path
import shutil

import pytest

from core.DirectoryMerger import ConflictResolution, DirectoryMerger, MergeResult

ITEM = "item"
REPLACING = (ConflictResolution.MERGE, ConflictResolution.OVERWRITE)
ALL_MODES = tuple(ConflictResolution)


class Layout:
    """Source and destination trees of a merge, with one conflicting item."""

    def __init__(self, root: Path) -> None:
        self.source = root / "source"
        self.destination = root / "destination"
        self.source.mkdir()
        self.destination.mkdir()

    @property
    def source_item(self) -> Path:
        return self.source / ITEM

    @property
    def target(self) -> Path:
        return self.destination / ITEM

    def new_file(self) -> None:
        self.source_item.write_text("new")

    def new_directory(self) -> None:
        self.source_item.mkdir()
        (self.source_item / "new.txt").write_text("new")
        (self.source_item / "shared.txt").write_text("new")

    def old_file(self) -> None:
        self.target.write_text("old")

    def old_directory(self) -> None:
        self.target.mkdir()
        (self.target / "old.txt").write_text("old")
        (self.target / "shared.txt").write_text("old")

    def merge(self, mode: ConflictResolution) -> MergeResult:
        return DirectoryMerger(mode).merge_directories(self.source, self.destination)

    def destination_names(self) -> set[str]:
        return {path.name for path in self.destination.iterdir()}


def tree(folder: Path) -> dict[str, str]:
    return {
        path.relative_to(folder).as_posix(): path.read_text()
        for path in folder.rglob("*")
        if path.is_file()
    }


OLD_TREE = {"old.txt": "old", "shared.txt": "old"}
NEW_TREE = {"new.txt": "new", "shared.txt": "new"}


@pytest.fixture
def layout(tmp_path: Path) -> Layout:
    return Layout(tmp_path)


# ----------------------------------------------------------------------
# Destination absent: every strategy simply moves the item
# ----------------------------------------------------------------------


@pytest.mark.parametrize("mode", ALL_MODES)
def test_absent_file_is_moved(layout: Layout, mode: ConflictResolution) -> None:
    layout.new_file()

    result = layout.merge(mode)

    assert result.success and result.moved_files == 1
    assert layout.target.read_text() == "new"
    assert not layout.source.exists()


@pytest.mark.parametrize("mode", ALL_MODES)
def test_absent_directory_is_moved(layout: Layout, mode: ConflictResolution) -> None:
    layout.new_directory()

    result = layout.merge(mode)

    assert result.success and result.moved_dirs == 1
    assert tree(layout.target) == NEW_TREE
    assert not layout.source.exists()


# ----------------------------------------------------------------------
# File over file
# ----------------------------------------------------------------------


@pytest.mark.parametrize("mode", REPLACING)
def test_replaced_file_is_traced(layout: Layout, mode: ConflictResolution) -> None:
    layout.new_file()
    layout.old_file()

    result = layout.merge(mode)

    assert result.success
    assert (result.moved_files, result.overwritten, result.backed_up) == (1, 1, 0)
    assert result.overwritten_paths == [layout.target]
    assert layout.target.read_text() == "new"
    assert layout.destination_names() == {ITEM}


def test_backed_up_file_keeps_original_copy(layout: Layout) -> None:
    layout.new_file()
    layout.old_file()

    result = layout.merge(ConflictResolution.BACKUP)

    assert result.success
    assert (result.moved_files, result.overwritten, result.backed_up) == (1, 0, 1)
    assert layout.target.read_text() == "new"
    assert (layout.destination / f"{ITEM}.bak").read_text() == "old"


def test_skipped_file_keeps_original(layout: Layout) -> None:
    layout.new_file()
    layout.old_file()

    result = layout.merge(ConflictResolution.SKIP)

    assert result.success and result.skipped == 1
    assert layout.target.read_text() == "old"


def test_fail_mode_refuses_existing_file(layout: Layout) -> None:
    layout.new_file()
    layout.old_file()

    result = layout.merge(ConflictResolution.FAIL)

    assert not result.success
    assert layout.target.read_text() == "old"
    assert layout.source_item.read_text() == "new"


# ----------------------------------------------------------------------
# Directory over directory
# ----------------------------------------------------------------------


def test_merged_directory_combines_contents(layout: Layout) -> None:
    layout.new_directory()
    layout.old_directory()

    result = layout.merge(ConflictResolution.MERGE)

    assert result.success
    assert tree(layout.target) == {"old.txt": "old", "new.txt": "new", "shared.txt": "new"}
    assert result.overwritten_paths == [layout.target / "shared.txt"]


def test_overwritten_directory_is_replaced_without_leftover(layout: Layout) -> None:
    layout.new_directory()
    layout.old_directory()

    result = layout.merge(ConflictResolution.OVERWRITE)

    assert result.success
    assert (result.moved_dirs, result.overwritten, result.backed_up) == (1, 1, 0)
    assert result.overwritten_paths == [layout.target]
    assert tree(layout.target) == NEW_TREE
    assert layout.destination_names() == {ITEM}


def test_backed_up_directory_has_no_nested_copy(layout: Layout) -> None:
    layout.new_directory()
    layout.old_directory()

    result = layout.merge(ConflictResolution.BACKUP)

    assert result.success
    assert (result.moved_dirs, result.overwritten, result.backed_up) == (1, 0, 1)
    assert tree(layout.target) == NEW_TREE
    assert tree(layout.destination / f"{ITEM}.bak") == OLD_TREE
    assert layout.destination_names() == {ITEM, f"{ITEM}.bak"}


def test_backup_name_does_not_replace_previous_backup(layout: Layout) -> None:
    layout.new_directory()
    layout.old_directory()
    (layout.destination / f"{ITEM}.bak").write_text("previous backup")

    result = layout.merge(ConflictResolution.BACKUP)

    assert result.success
    assert (layout.destination / f"{ITEM}.bak").read_text() == "previous backup"
    assert tree(layout.destination / f"{ITEM}.bak.1") == OLD_TREE


def test_skipped_directory_keeps_original(layout: Layout) -> None:
    layout.new_directory()
    layout.old_directory()

    result = layout.merge(ConflictResolution.SKIP)

    assert result.success and result.skipped == 1
    assert tree(layout.target) == OLD_TREE


def test_fail_mode_refuses_existing_directory(layout: Layout) -> None:
    layout.new_directory()
    layout.old_directory()

    result = layout.merge(ConflictResolution.FAIL)

    assert not result.success
    assert tree(layout.target) == OLD_TREE


# ----------------------------------------------------------------------
# Item of another type at the destination
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "mode", (*REPLACING, ConflictResolution.BACKUP, ConflictResolution.FAIL)
)
def test_file_over_directory_is_a_failure(layout: Layout, mode: ConflictResolution) -> None:
    layout.new_file()
    layout.old_directory()

    result = layout.merge(mode)

    assert not result.success
    assert tree(layout.target) == OLD_TREE
    assert layout.destination_names() == {ITEM}


@pytest.mark.parametrize(
    "mode", (*REPLACING, ConflictResolution.BACKUP, ConflictResolution.FAIL)
)
def test_directory_over_file_is_a_failure(layout: Layout, mode: ConflictResolution) -> None:
    layout.new_directory()
    layout.old_file()

    result = layout.merge(mode)

    assert not result.success
    assert layout.target.read_text() == "old"
    assert layout.destination_names() == {ITEM}


@pytest.mark.parametrize("old_item", ["old_file", "old_directory"])
@pytest.mark.parametrize("new_item", ["new_file", "new_directory"])
def test_skip_keeps_item_of_any_type(layout: Layout, new_item: str, old_item: str) -> None:
    getattr(layout, new_item)()
    getattr(layout, old_item)()
    before = layout.target.is_dir()

    result = layout.merge(ConflictResolution.SKIP)

    assert result.success and result.skipped == 1
    assert layout.target.is_dir() == before


# ----------------------------------------------------------------------
# Injected failures leave the original destination intact
# ----------------------------------------------------------------------


def test_failed_file_backup_prevents_replacement(
    layout: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout.new_file()
    layout.old_file()

    def broken_copy(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("backup refused")

    monkeypatch.setattr("core.DirectoryMerger.shutil.copy2", broken_copy)
    result = layout.merge(ConflictResolution.BACKUP)

    assert not result.success and result.backed_up == 0
    assert layout.target.read_text() == "old"
    assert layout.source_item.read_text() == "new"
    assert layout.destination_names() == {ITEM}


def test_failed_directory_backup_prevents_move(
    layout: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout.new_directory()
    layout.old_directory()

    def broken_rename(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("rename refused")

    monkeypatch.setattr("core.DirectoryMerger.os.rename", broken_rename)
    result = layout.merge(ConflictResolution.BACKUP)

    assert not result.success and result.backed_up == 0
    assert tree(layout.target) == OLD_TREE
    assert tree(layout.source_item) == NEW_TREE
    assert layout.destination_names() == {ITEM}


@pytest.mark.parametrize("mode", REPLACING)
def test_failed_file_replacement_keeps_both_files(
    layout: Layout, mode: ConflictResolution, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout.new_file()
    layout.old_file()

    def locked_replace(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("locked")

    monkeypatch.setattr("core.DirectoryMerger.os.replace", locked_replace)
    result = layout.merge(mode)

    assert not result.success and result.overwritten_paths == []
    assert layout.target.read_text() == "old"
    assert layout.source_item.read_text() == "new"
    assert layout.destination_names() == {ITEM}


@pytest.mark.parametrize("mode", REPLACING)
def test_failed_copy_across_volumes_keeps_destination(
    layout: Layout, mode: ConflictResolution, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout.new_file()
    layout.old_file()

    def partial_copy(source: str, destination: str, **_kwargs: object) -> None:
        Path(destination).write_text("partial")
        raise OSError("disk full")

    monkeypatch.setattr(DirectoryMerger, "_is_same_volume", staticmethod(lambda *_: False))
    monkeypatch.setattr("core.DirectoryMerger.shutil.copy2", partial_copy)
    result = layout.merge(mode)

    assert not result.success
    assert layout.target.read_text() == "old"
    assert layout.source_item.read_text() == "new"
    assert layout.destination_names() == {ITEM}


@pytest.mark.parametrize("mode", REPLACING)
def test_copy_across_volumes_replaces_file(
    layout: Layout, mode: ConflictResolution, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout.new_file()
    layout.old_file()

    monkeypatch.setattr(DirectoryMerger, "_is_same_volume", staticmethod(lambda *_: False))
    result = layout.merge(mode)

    assert result.success and result.overwritten_paths == [layout.target]
    assert layout.target.read_text() == "new"
    assert layout.destination_names() == {ITEM}
    assert not layout.source.exists()


@pytest.mark.parametrize("mode", (ConflictResolution.OVERWRITE, ConflictResolution.BACKUP))
def test_failed_directory_move_restores_original(
    layout: Layout, mode: ConflictResolution, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout.new_directory()
    layout.old_directory()

    def locked_move(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("locked")

    monkeypatch.setattr("core.DirectoryMerger.shutil.move", locked_move)
    result = layout.merge(mode)

    assert not result.success
    assert (result.moved_dirs, result.overwritten, result.backed_up) == (0, 0, 0)
    assert tree(layout.target) == OLD_TREE
    assert tree(layout.source_item) == NEW_TREE
    assert layout.destination_names() == {ITEM}


def test_failed_removal_of_overwritten_directory_is_reported(
    layout: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout.new_directory()
    layout.old_directory()

    def locked_rmtree(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("locked")

    monkeypatch.setattr("core.DirectoryMerger.shutil.rmtree", locked_rmtree)
    result = layout.merge(ConflictResolution.OVERWRITE)

    assert not result.success
    assert tree(layout.target) == NEW_TREE


def test_unremovable_source_does_not_fail_completed_merge(
    layout: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout.new_file()
    real_rmtree = shutil.rmtree

    def locked_source(path: str | os.PathLike[str], *args: object, **kwargs: object) -> None:
        if Path(path) == layout.source:
            raise PermissionError("locked")
        real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr("core.DirectoryMerger.shutil.rmtree", locked_source)
    result = layout.merge(ConflictResolution.MERGE)

    assert result.success
    assert layout.target.read_text() == "new"
