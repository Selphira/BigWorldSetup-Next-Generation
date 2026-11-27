"""Progress signals sent only when the integer percentage changes."""

import importlib
import itertools
import json
from pathlib import Path
import zipfile

import pytest

from constants import MOD_CACHE_LOCALIZATION_PROGRESS
from core.DataUpdater import DataUpdater
from core.ModManager import CacheBuilderThread
from core.ProgressEmitter import ProgressEmitter

updater_module = importlib.import_module("core.DataUpdater")


def _repeated_values(values: list[int]) -> list[int]:
    return [current for previous, current in itertools.pairwise(values) if previous == current]


# ========================================
# EMITTER
# ========================================


def test_repeated_values_are_reported_once() -> None:
    emitted: list[int] = []
    progress = ProgressEmitter(emitted.append)

    for value in [0, 0, 1, 1, 1, 2, 1]:
        progress.report(value)

    assert emitted == [0, 1, 2, 1]


def test_forced_value_is_emitted_even_when_unchanged() -> None:
    emitted: list[int] = []
    progress = ProgressEmitter(emitted.append)

    progress.report(100)
    progress.force(100)
    progress.report(100)

    assert emitted == [100, 100]


def test_new_phase_emits_its_first_value() -> None:
    emitted: list[int] = []
    progress = ProgressEmitter(emitted.append)

    progress.report(0)
    progress.restart()
    progress.report(0)

    assert emitted == [0, 0]


# ========================================
# LOCALIZED CACHE BUILD
# ========================================


def test_cache_build_emits_each_percentage_once_then_its_phases(tmp_path: Path) -> None:
    mods_dir = tmp_path / "mods"
    mods_dir.mkdir()
    for index in range(300):
        mod = {"name": f"mod{index}", "tp2": f"mod{index}", "components": {}, "links": {}}
        (mods_dir / f"mod{index}.json").write_text(json.dumps(mod), encoding="utf-8")

    builder = CacheBuilderThread(mods_dir, tmp_path / "custom", tmp_path / "cache", ["en_US"])
    emitted: list[int] = []
    builder.progress.connect(emitted.append)
    builder.run()

    # Only the start of localization and the end of the build repeat a value
    assert _repeated_values(emitted) == [MOD_CACHE_LOCALIZATION_PROGRESS, 100]
    assert emitted[-1] == 100
    assert len(emitted) <= 103


# ========================================
# CATALOGUE UPDATE
# ========================================


def test_extraction_emits_each_percentage_once(tmp_path: Path) -> None:
    archive = tmp_path / "catalogue.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for index in range(500):
            zf.writestr(f"data/file{index}.json", "{}")

    updater = DataUpdater()
    emitted: list[int] = []
    updater.progress.connect(emitted.append)

    assert updater._extract_zip(archive, tmp_path / "extracted")

    assert emitted == list(range(50, 90))


class _Response:
    def __init__(self, chunks: list[bytes], fail_after: int | None = None) -> None:
        self.headers = {"content-length": str(sum(len(chunk) for chunk in chunks))}
        self._chunks = chunks
        self._fail_after = fail_after

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *exc_info: object) -> None:
        pass

    def raise_for_status(self) -> None:
        pass

    def iter_content(self, chunk_size: int):
        for index, chunk in enumerate(self._chunks):
            if index == self._fail_after:
                raise ConnectionError("connection lost")
            yield chunk


def test_retried_download_reports_its_progress_from_the_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chunks = [b"x"] * 200
    responses = iter([_Response(chunks, fail_after=10), _Response(chunks)])
    monkeypatch.setattr(updater_module.requests, "get", lambda *args, **kwargs: next(responses))

    updater = DataUpdater()
    emitted: list[int] = []
    updater.progress.connect(emitted.append)

    assert updater._download_file("https://example.invalid/catalogue.zip", tmp_path / "c.zip")

    assert emitted == [0, 1, 2] + list(range(51))
