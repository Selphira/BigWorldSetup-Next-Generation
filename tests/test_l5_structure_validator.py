import os
from pathlib import Path
import random

import pytest

from constants import TP2_EXTENSION, WEIDU_SETUP_PREFIX
from core.validators.StructureValidator import StructureValidator

MOD = "demo"
PLAIN_TP2 = f"{MOD}{TP2_EXTENSION}"
SETUP_TP2 = f"{WEIDU_SETUP_PREFIX}{MOD}{TP2_EXTENSION}"
CASE_INSENSITIVE_PLATFORM = os.path.normcase("A") == os.path.normcase("a")


def _write(root: Path, relative: str, content: str = "") -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _two_rglob_tp2(temp_dir: Path, tp2_name: str) -> Path | None:
    """Previous lookup kept as an oracle: one rglob per accepted name."""
    return next(
        (
            max(matches, key=lambda p: len(p.parts))
            for tp2 in (tp2_name, f"{WEIDU_SETUP_PREFIX}{tp2_name}")
            if (matches := list(temp_dir.rglob(f"{tp2}{TP2_EXTENSION}")))
        ),
        None,
    )


def test_deepest_plain_tp2_is_moved_to_mod_folder(tmp_path: Path) -> None:
    _write(tmp_path, f"other/{PLAIN_TP2}", "shallow")
    _write(tmp_path, f"wrap/{MOD}/{PLAIN_TP2}", "deep")
    _write(tmp_path, "wrap/readme.txt")

    assert StructureValidator.normalize_structure(tmp_path, MOD)

    assert (tmp_path / MOD / PLAIN_TP2).read_text(encoding="utf-8") == "deep"
    assert (tmp_path / "readme.txt").is_file()
    assert not (tmp_path / "wrap").exists()


def test_plain_tp2_wins_over_deeper_setup_tp2(tmp_path: Path) -> None:
    _write(tmp_path, f"a/b/c/{MOD}/{SETUP_TP2}", "setup")
    _write(tmp_path, f"wrap/{MOD}/{PLAIN_TP2}", "plain")

    assert StructureValidator.normalize_structure(tmp_path, MOD)

    assert (tmp_path / MOD / PLAIN_TP2).read_text(encoding="utf-8") == "plain"


def test_setup_tp2_is_used_without_plain_tp2(tmp_path: Path) -> None:
    _write(tmp_path, f"wrap/{MOD}/{SETUP_TP2}", "setup")

    assert StructureValidator.normalize_structure(tmp_path, MOD)

    assert (tmp_path / MOD / SETUP_TP2).read_text(encoding="utf-8") == "setup"


def test_expected_layout_is_left_untouched(tmp_path: Path) -> None:
    _write(tmp_path, f"{MOD}/{PLAIN_TP2}", "in place")
    _write(tmp_path, f"{SETUP_TP2}", "root setup")

    assert StructureValidator.normalize_structure(tmp_path, MOD)

    assert sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*")) == [
        MOD,
        f"{MOD}/{PLAIN_TP2}",
        SETUP_TP2,
    ]


def test_missing_tp2_is_reported(tmp_path: Path) -> None:
    _write(tmp_path, f"wrap/other{TP2_EXTENSION}")
    _write(tmp_path, f"wrap/{MOD}.txt")

    assert StructureValidator.normalize_structure(tmp_path, MOD) is False
    assert (tmp_path / "wrap" / f"other{TP2_EXTENSION}").is_file()


def test_tp2_name_case_follows_platform(tmp_path: Path) -> None:
    _write(tmp_path, f"wrap/{MOD}/{PLAIN_TP2.upper()}", "upper")

    assert StructureValidator.normalize_structure(tmp_path, MOD) is CASE_INSENSITIVE_PLATFORM


@pytest.mark.parametrize("seed", range(40))
def test_single_walk_matches_two_rglob_lookup(tmp_path: Path, seed: int) -> None:
    rng = random.Random(seed)
    file_names = [
        PLAIN_TP2,
        SETUP_TP2,
        PLAIN_TP2.upper(),
        f"other{TP2_EXTENSION}",
        "readme.txt",
    ]
    for index in range(rng.randint(1, 12)):
        folders = [rng.choice(["a", "b", MOD, "Wrap"]) for _ in range(rng.randint(0, 4))]
        _write(tmp_path, "/".join([*folders, rng.choice(file_names)]), str(index))

    assert StructureValidator._find_tp2(tmp_path, MOD) == _two_rglob_tp2(tmp_path, MOD)
