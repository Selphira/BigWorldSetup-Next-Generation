"""Installation diagnostic archive: contents, unreadable journal and safe writing."""

from dataclasses import asdict
import json
from pathlib import Path
from unittest.mock import Mock
import zipfile

from PySide6.QtWidgets import QFileDialog, QMessageBox
import pytest
from pytestqt.qtbot import QtBot

from constants import (
    DIAGNOSTIC_PLAN_ENTRY,
    DIAGNOSTIC_SUMMARY_ENTRY,
    DIAGNOSTIC_VERSIONS_ENTRY,
)
from core.InstallationDiagnostic import InstallationDiagnostic
from core.InstallationJournal import (
    InstallationJournal,
    JournalError,
    Operation,
    OperationDecision,
    RunPlan,
    RunSequence,
)
from core.TranslationManager import tr
from core.weidu_types import AttemptEvidence, ComponentStatus
from ui.pages.InstallationPage import InstallationPage

WEIDU_LOG = "// Log of Currently Installed WeiDU Mods\n~DEMO/DEMO.TP2~ #0 #0 // Demo\n"
FAILED_DEBUG = "Installing [Broken]\nERROR: cannot find FOO.ITM\nNOT INSTALLED DUE TO ERRORS\n"


def operation(reference: str, status: ComponentStatus, **changes: object) -> Operation:
    op = Operation(reference, "demo", 0, 0, [], [], [], "std", status=status)
    for name, value in changes.items():
        setattr(op, name, value)
    return op


@pytest.fixture
def games(tmp_path: Path) -> tuple[Path, Path]:
    """Two game folders, as in a run installing SoD then BG2 for EET."""
    first, second = tmp_path / "Baldur's Gate", tmp_path / "BG2 — EE"
    for folder in (first, second):
        folder.mkdir()
        (folder / "WeiDU.log").write_text(WEIDU_LOG, encoding="utf-8")
    (first / "SETUP-BROKEN.DEBUG").write_text(FAILED_DEBUG, encoding="utf-8")
    (first / "SETUP-DEMO.DEBUG").write_text("successful attempt", encoding="utf-8")
    return first, second


@pytest.fixture
def incomplete_run(tmp_path: Path, games: tuple[Path, Path]) -> InstallationJournal:
    first, second = games
    plan = RunPlan(
        "eet",
        {"sod": str(first), "bg2": str(second)},
        {0: ["demo:0", "broken:1"], 1: ["lost:2"], 2: ["demo:3"]},
        ["demo:0", "broken:1", "lost:2", "demo:3"],
        "fr_FR",
        ["fr_FR", "en_US"],
        True,
    )
    sequences = [
        RunSequence(
            str(first),
            RunSequence.identify(first),
            [
                operation("demo:0", ComponentStatus.SUCCESS),
                operation("pause:1", ComponentStatus.PENDING),
                operation(
                    "broken:1",
                    ComponentStatus.ERROR,
                    tp2="broken",
                    decision=OperationDecision.ACCEPT_FAILURE,
                    evidence=AttemptEvidence(1, ComponentStatus.ERROR, False),
                    messages=["ERROR: cannot find FOO.ITM"],
                ),
            ],
        ),
        RunSequence(
            str(second),
            RunSequence.identify(second),
            [operation("lost:2", ComponentStatus.RUNNING, tp2="lost")],
        ),
        RunSequence(
            str(first),
            RunSequence.identify(first),
            [operation("demo:3", ComponentStatus.PENDING)],
        ),
    ]
    run = InstallationJournal(tmp_path / "installation-run.json", plan, sequences)
    run.save()
    return run


@pytest.fixture
def catalogue_version(tmp_path: Path) -> Path:
    path = tmp_path / "data_version.json"
    path.write_text(
        json.dumps({"version": "data-abc1234", "date": "2026-11-20", "commit_hash": "abc"}),
        encoding="utf-8",
    )
    return path


def read_archive(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


# ========================================
# CONTENTS
# ========================================


def test_incomplete_run_diagnostic_holds_plan_logs_and_failed_debug_only(
    tmp_path: Path, incomplete_run: InstallationJournal, catalogue_version: Path
) -> None:
    target = tmp_path / "export" / "diagnostic.zip"

    InstallationDiagnostic(incomplete_run.path, catalogue_version).export(target)

    entries = read_archive(target)
    assert entries["installation-run.json"] == incomplete_run.path.read_bytes()
    assert json.loads(entries[DIAGNOSTIC_PLAN_ENTRY]) == json.loads(
        json.dumps(asdict(incomplete_run.plan))
    )
    first, second = (Path(seq.folder) for seq in incomplete_run.sequences[:2])
    assert entries["game-1/WeiDU.log"] == (first / "WeiDU.log").read_bytes()
    assert entries["game-2/WeiDU.log"] == (second / "WeiDU.log").read_bytes()
    assert entries["game-1/SETUP-BROKEN.DEBUG"] == (first / "SETUP-BROKEN.DEBUG").read_bytes()
    # A successful operation has nothing to explain.
    assert "game-1/SETUP-DEMO.DEBUG" not in entries

    versions = json.loads(entries[DIAGNOSTIC_VERSIONS_ENTRY])
    assert versions["catalogue"]["version"] == "data-abc1234"
    assert versions["application"]


def test_summary_explains_each_operation_without_reading_weidu_output(
    tmp_path: Path, incomplete_run: InstallationJournal, catalogue_version: Path
) -> None:
    target = tmp_path / "diagnostic.zip"

    InstallationDiagnostic(incomplete_run.path, catalogue_version).export(target)

    summary = read_archive(target)[DIAGNOSTIC_SUMMARY_ENTRY].decode("utf-8")
    assert tr("page.installation.incomplete") in summary
    assert "data-abc1234" in summary
    assert f"broken:1 — {tr('page.installation.status.error')}" in summary
    assert f"lost:2 — {tr('page.installation.status.running')}" in summary
    assert tr("page.installation.decision_failed") in summary
    assert "ERROR: cannot find FOO.ITM" in summary
    assert "pause:1" not in summary
    # The DEBUG of the interrupted operation was never written: it is reported.
    assert str(Path(incomplete_run.sequences[1].folder) / "SETUP-LOST.DEBUG") in summary


def test_unreadable_journal_is_attached_as_is(tmp_path: Path, catalogue_version: Path) -> None:
    journal_path = tmp_path / "installation-run.json"
    journal_path.write_bytes(b"{truncated")
    target = tmp_path / "diagnostic.zip"

    InstallationDiagnostic(journal_path, catalogue_version).export(target)

    entries = read_archive(target)
    assert entries["installation-run.json"] == b"{truncated"
    assert DIAGNOSTIC_PLAN_ENTRY not in entries
    with pytest.raises(JournalError) as error:
        InstallationJournal.load(journal_path)
    summary = entries[DIAGNOSTIC_SUMMARY_ENTRY].decode("utf-8")
    assert tr("diagnostic.journal_unreadable", error=str(error.value)) in summary


def test_missing_catalogue_version_is_reported_as_unknown(
    tmp_path: Path, incomplete_run: InstallationJournal
) -> None:
    target = tmp_path / "diagnostic.zip"

    InstallationDiagnostic(incomplete_run.path, tmp_path / "absent.json").export(target)

    entries = read_archive(target)
    assert json.loads(entries[DIAGNOSTIC_VERSIONS_ENTRY])["catalogue"] is None
    assert tr("diagnostic.unknown") in entries[DIAGNOSTIC_SUMMARY_ENTRY].decode("utf-8")


# ========================================
# SAFE WRITING
# ========================================


def test_failed_export_keeps_the_previous_archive(
    tmp_path: Path,
    incomplete_run: InstallationJournal,
    catalogue_version: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "diagnostic.zip"
    target.write_bytes(b"previous export")

    def disk_full(*args: object, **kwargs: object) -> None:
        raise OSError("No space left on device")

    monkeypatch.setattr(zipfile.ZipFile, "write", disk_full)
    with pytest.raises(OSError):
        InstallationDiagnostic(incomplete_run.path, catalogue_version).export(target)

    assert target.read_bytes() == b"previous export"
    assert [path.name for path in tmp_path.glob(".diagnostic.zip.*")] == []


# ========================================
# INSTALLATION PAGE
# ========================================


def page_for(tmp_path: Path, qtbot: QtBot) -> InstallationPage:
    state = Mock()
    state.STATE_FILE = tmp_path / "state.json"
    state.get_page_option.side_effect = lambda _page, _key, default=None: default
    page = InstallationPage(state)
    qtbot.addWidget(page)
    page.retranslate_ui()
    return page


def test_export_button_needs_a_journal(tmp_path: Path, qtbot: QtBot) -> None:
    page = page_for(tmp_path, qtbot)

    assert not page._btn_diagnostic.isEnabled()


def test_export_button_writes_the_chosen_archive(
    tmp_path: Path,
    qtbot: QtBot,
    incomplete_run: InstallationJournal,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = page_for(tmp_path, qtbot)
    assert page._btn_diagnostic.isEnabled()
    chosen = tmp_path / "support" / "my-diagnostic"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(chosen), ""))
    information = Mock()
    monkeypatch.setattr(QMessageBox, "information", information)

    page._btn_diagnostic.click()

    exported = chosen.with_name("my-diagnostic.zip")
    assert "installation-run.json" in read_archive(exported)
    information.assert_called_once()


def test_final_report_offers_the_export(
    tmp_path: Path,
    qtbot: QtBot,
    incomplete_run: InstallationJournal,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = page_for(tmp_path, qtbot)
    page._executor.load()
    monkeypatch.setattr(QMessageBox, "exec", lambda box: None)
    monkeypatch.setattr(
        QMessageBox,
        "clickedButton",
        lambda box: next(
            button
            for button in box.buttons()
            if button.text() == tr("page.installation.btn_diagnostic")
        ),
    )
    export = Mock()
    monkeypatch.setattr(page, "_export_diagnostic", export)

    page._show_installation_summary()

    export.assert_called_once()
