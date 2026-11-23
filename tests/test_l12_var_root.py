"""L12: every generated file lives under the single var/ root."""

import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

import constants

# The suite forbids external processes; this test needs exactly one interpreter.
ORIGINAL_POPEN = subprocess.Popen
PROBE_MODULE = "tests.var_root_probe"
PROBE_TIMEOUT_SECONDS = 120
# Entries rewritten by Python and pytest themselves, unrelated to the application
UNTRACKED_PROJECT_DIRECTORIES = {".git", "__pycache__", ".pytest_cache"}


def project_files() -> set[Path]:
    files: set[Path] = set()
    for root, directories, names in os.walk(constants.PROJECT_ROOT):
        directories[:] = [
            name for name in directories if name not in UNTRACKED_PROJECT_DIRECTORIES
        ]
        files.update(Path(root, name) for name in names)
    return files


def tree_files(root: Path) -> set[Path]:
    return {path for path in root.rglob("*") if path.is_file()}


@pytest.fixture
def published_package(small_catalogue: tuple[Path, Path], tmp_path: Path) -> Path:
    data, _ = small_catalogue
    package = tmp_path / "published" / "data.zip"
    package.parent.mkdir()
    with zipfile.ZipFile(package, "w") as archive:
        for path in data.rglob("*.json"):
            archive.write(path, path.relative_to(data))
    return package


def test_application_writes_only_under_its_var_root(
    published_package: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    var_root = tmp_path / "var"
    working_dir = tmp_path / "cwd"
    working_dir.mkdir()
    user_archive = tmp_path / "chosen" / "demo.zip"
    user_archive.parent.mkdir()
    user_archive.write_bytes(b"user archive")

    files_before = tree_files(tmp_path)
    project_before = project_files()

    monkeypatch.setattr(subprocess, "Popen", ORIGINAL_POPEN)
    environment = {
        **os.environ,
        constants.VAR_DIR_ENVIRONMENT_VARIABLE: str(var_root),
        constants.CATALOGUE_UPDATE_ENVIRONMENT_VARIABLE: "1",
        "PYTHONPATH": str(constants.PROJECT_ROOT),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    probe = subprocess.run(
        [sys.executable, "-m", PROBE_MODULE, str(published_package), str(user_archive)],
        cwd=working_dir,
        env=environment,
        capture_output=True,
        text=True,
        timeout=PROBE_TIMEOUT_SECONDS,
    )

    assert probe.returncode == 0, probe.stdout + probe.stderr
    new_files = tree_files(tmp_path) - files_before
    assert {path for path in new_files if not path.is_relative_to(var_root)} == set()
    assert project_files() == project_before
    assert not any(working_dir.iterdir())

    written = {path.relative_to(var_root).as_posix() for path in new_files}
    assert {
        "user/state.json",
        "user/settings.ini",
        "user/archives.json",
        "catalogue/data_version.json",
        f"logs/{constants.LOG_FILE_NAME}",
    } <= written
    assert any(name.startswith("cache/") for name in written)
    assert (var_root / "user" / "mods").is_dir()
    assert (var_root / "runs" / "current").is_dir()
