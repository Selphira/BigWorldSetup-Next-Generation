"""Application update detection: release ordering and unreadable versions."""

import pytest

from core.VersionChecker import VersionChecker


@pytest.mark.parametrize(
    ("older", "newer"),
    [
        ("1.0.0-beta", "1.0.0"),
        ("1.0.0", "1.0.1-alpha"),
        ("1.0.1-alpha", "1.0.1"),
        ("1.0-alpha", "1.0-beta"),
        ("1.0-beta", "1.0-rc1"),
        ("0.9.11-beta2", "0.9.11-BETA10"),
        ("1.0-dev", "1.0-alpha"),
        ("0.9.11", "0.10.0"),
    ],
)
def test_release_ordering(older: str, newer: str) -> None:
    assert VersionChecker._is_newer_version(newer, older)
    assert not VersionChecker._is_newer_version(older, newer)


def test_trailing_zeros_are_the_same_release() -> None:
    assert not VersionChecker._is_newer_version("1.0.0", "1")
    assert not VersionChecker._is_newer_version("1", "1.0.0")


@pytest.mark.parametrize("unreadable", ["", "x.y", "1..0", None])
def test_unreadable_version_is_never_an_update(unreadable: str | None) -> None:
    assert not VersionChecker._is_newer_version(unreadable, "1.0.0")
    assert not VersionChecker._is_newer_version("1.0.0", unreadable)
