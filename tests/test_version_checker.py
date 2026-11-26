"""Application update detection: release ordering and unreadable versions."""

from pathlib import Path

import pytest
import requests

from core import VersionChecker as VersionChecker_module
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


# ============================================================================
# Last known release kept when GitHub cannot be reached
# ============================================================================


class FakeResponse:
    """GitHub answer with a status code and a JSON body (or an undecodable one)."""

    def __init__(self, payload: object = None, status: int = 200) -> None:
        self._payload = payload
        self._status = status

    def raise_for_status(self) -> None:
        if self._status >= 400:
            raise requests.HTTPError(f"{self._status} error")

    def json(self) -> object:
        if self._payload is None:
            raise requests.JSONDecodeError("Expecting value", "", 0)
        return self._payload


def release(tag: str) -> dict[str, str]:
    return {
        "tag_name": f"v{tag}",
        "html_url": f"https://example.invalid/releases/{tag}",
        "published_at": "2026-11-01T00:00:00Z",
    }


@pytest.fixture
def checker(tmp_path: Path) -> VersionChecker:
    version_checker = VersionChecker()
    version_checker._cache_file = tmp_path / "last_update_check.json"
    return version_checker


def answer_with(monkeypatch: pytest.MonkeyPatch, outcome: object) -> None:
    """Make every GitHub request return the response, or raise the exception, given."""

    def fake_get(*_args, **_kwargs) -> FakeResponse:
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(VersionChecker_module.requests, "get", fake_get)


@pytest.mark.parametrize(
    "failure",
    [
        requests.ConnectionError("offline"),
        requests.Timeout("too slow"),
        FakeResponse(status=403),
        FakeResponse(payload=None),
        FakeResponse(payload={"html_url": "x"}),
        FakeResponse(payload=["not", "an", "object"]),
        FakeResponse(payload={**release("1.0.0"), "tag_name": None}),
    ],
    ids=[
        "offline",
        "timeout",
        "rate-limited",
        "not-json",
        "no-tag",
        "not-an-object",
        "null-tag",
    ],
)
def test_failed_check_returns_and_keeps_the_last_known_release(
    monkeypatch: pytest.MonkeyPatch, checker: VersionChecker, failure: object
) -> None:
    answer_with(monkeypatch, FakeResponse(release("1.0.0")))
    assert checker.check_for_update().version == "1.0.0"
    cache_before = checker._cache_file.read_bytes()

    answer_with(monkeypatch, failure)
    cached = checker.check_for_update()

    assert cached.version == "1.0.0"
    assert cached.release_url == release("1.0.0")["html_url"]
    assert checker._cache_file.read_bytes() == cache_before


def test_failed_first_check_reports_nothing_and_writes_no_cache(
    monkeypatch: pytest.MonkeyPatch, checker: VersionChecker
) -> None:
    answer_with(monkeypatch, requests.ConnectionError("offline"))
    assert checker.check_for_update() is None
    assert not checker._cache_file.exists()


def test_successful_check_replaces_the_cached_release(
    monkeypatch: pytest.MonkeyPatch, checker: VersionChecker
) -> None:
    answer_with(monkeypatch, FakeResponse(release("1.0.0")))
    checker.check_for_update()
    answer_with(monkeypatch, FakeResponse(release("1.1.0")))
    checker.check_for_update()

    answer_with(monkeypatch, requests.ConnectionError("offline"))
    assert checker.check_for_update().version == "1.1.0"


def test_cached_release_is_compared_to_the_running_version(
    monkeypatch: pytest.MonkeyPatch, checker: VersionChecker
) -> None:
    monkeypatch.setattr(VersionChecker_module, "APP_VERSION", "0.9.0")
    answer_with(monkeypatch, FakeResponse(release("1.0.0")))
    assert checker.check_for_update().is_newer

    monkeypatch.setattr(VersionChecker_module, "APP_VERSION", "1.0.0")
    answer_with(monkeypatch, requests.ConnectionError("offline"))
    assert not checker.check_for_update().is_newer
