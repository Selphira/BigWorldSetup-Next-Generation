"""L12: lifecycle of a run on the installation page, closed only by Finish or Cancel."""

from pathlib import Path
from threading import Event
from unittest.mock import Mock

from PySide6.QtWidgets import QFileDialog, QMessageBox
import pytest
from pytestqt.qtbot import QtBot

from core.InstallationExecutor import InstallationExecutor
from core.InstallationJournal import InstallationJournal, OperationDecision, RunClosure
from core.InstallationWorker import InstallationWorker
from core.TranslationManager import tr
from core.weidu_types import ComponentStatus, InstallResult
from core.WeiDUInstallerEngine import WeiDUInstallerEngine
from tests.conftest import ModCatalogue
from tests.test_l4_installation import result, state_for
from tests.test_l12_run_journal import archived_journal, disk_full, history
from ui.pages.InstallationPage import DecisionDialog, InstallationPage, RunAction

COMPONENT = "a:0"


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def state(tmp_path: Path, catalogue: ModCatalogue, rules) -> Mock:
    (tmp_path / "weidu.exe").touch()
    return state_for(tmp_path, catalogue, rules, {0: [COMPONENT]}, [COMPONENT])


@pytest.fixture
def questions(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Confirm every question; the texts asked are recorded."""
    asked = []

    def question(_parent, _title, text, *_args, **_kwargs):
        asked.append(text)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", question)
    return asked


def start_run(state: Mock) -> InstallationJournal:
    """Freeze the plan into a new run, as the Start button does."""
    executor = InstallationExecutor(state)
    executor.start(batch=True)
    return executor.journal


def open_page(qtbot: QtBot, state: Mock, monkeypatch: pytest.MonkeyPatch) -> InstallationPage:
    """Installation page as shown when the application starts."""
    page = InstallationPage(state)
    qtbot.addWidget(page)
    monkeypatch.setattr(page, "_show_installation_summary", Mock())
    page.load_state()
    page._refresh_controls()
    return page


def install_successfully(monkeypatch: pytest.MonkeyPatch, launched: list[str]) -> None:
    def install(
        engine, tp2, components, language, extra_args, input_lines, runner_factory, callbacks
    ):
        callbacks.before_launch()
        launched.append(tp2)
        with engine.weidu_log.open("a", encoding="utf-8") as log:
            log.write(f"~{tp2}.tp2~ #0 #0\n")
        return {components[0].comp_id: result(ComponentStatus.SUCCESS)}

    monkeypatch.setattr(WeiDUInstallerEngine, "install_components", install)


def played_run(state: Mock, status: ComponentStatus) -> InstallationJournal:
    """Run whose only component was installed, or failed then accepted as failed."""
    journal = start_run(state)
    if status == ComponentStatus.SUCCESS:
        journal.record(0, {0: result(status)})
    else:
        journal.record(0, {0: InstallResult(status, 1, "", "", ["broken"], [])})
        journal.decide(0, 0, OperationDecision.ACCEPT_FAILURE, in_log=False)
    return journal


def closure(page: InstallationPage) -> RunClosure | None:
    """None while the run is current, then its closure as kept in the history."""
    executor = page._executor
    if executor.journal_path.exists():
        return InstallationJournal.load(executor.journal_path).lifecycle.closure
    return archived_journal(executor).lifecycle.closure


# ============================================================================
# Controls by run state
# ============================================================================


def test_without_run_the_page_offers_to_start_and_allows_going_back(
    qtbot: QtBot, state: Mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = open_page(qtbot, state, monkeypatch)

    assert page._run_action() == RunAction.START
    assert page._btn_run_action.text() == tr("page.installation.btn_start")
    assert not page._btn_run_action.icon().isNull()
    assert not page._btn_cancel.isEnabled() and not page._btn_stop.isEnabled()
    assert page.can_go_to_previous_page()


def test_pause_is_pending_until_the_worker_reaches_the_end_of_its_batch(
    qtbot: QtBot, state: Mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    start_run(state)
    page = open_page(qtbot, state, monkeypatch)
    page._is_installing = True
    page._worker = Mock()
    page._refresh_controls()
    assert page._run_action() == RunAction.PAUSE

    page._on_run_action()

    page._worker.pause.assert_called_once()
    assert page._btn_run_action.text() == tr("page.installation.btn_pause_pending")
    assert not page._btn_run_action.isEnabled()

    page._on_installation_paused(1, "")

    assert page._btn_run_action.text() == tr("page.installation.btn_resume")
    assert page._btn_run_action.isEnabled()
    assert not page._input_text.isEnabled()

    page._on_run_action()

    page._worker.resume.assert_called_once()
    assert page._run_action() == RunAction.PAUSE
    page._worker = None


# ============================================================================
# Interrupted run
# ============================================================================


def test_crashed_run_is_resumed_from_the_installation_page_until_played(
    qtbot: QtBot, state: Mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = start_run(state)
    # WeiDU was about to modify the game when the application crashed.
    journal.begin(0, [0])
    launched = []
    install_successfully(monkeypatch, launched)
    monkeypatch.setattr(
        DecisionDialog,
        "exec",
        lambda dialog: setattr(dialog, "decision", OperationDecision.RETRY),
    )

    page = open_page(qtbot, state, monkeypatch)

    assert page.is_resume()
    assert page._btn_run_action.text() == tr("page.installation.btn_resume")
    assert page._btn_cancel.isEnabled()
    assert not page.can_go_to_previous_page()

    page._on_run_action()
    qtbot.waitUntil(lambda: not page._is_installing and page._worker is None)

    assert launched == ["a"]
    assert closure(page) is None
    assert page._run_action() == RunAction.FINISH
    assert page._btn_run_action.text() == tr("page.installation.btn_finish")
    assert not page._btn_cancel.isEnabled()
    assert not page.can_go_to_previous_page()


def test_cancel_during_weidu_terminates_it_then_closes_the_run_as_cancelled(
    qtbot: QtBot, state: Mock, monkeypatch: pytest.MonkeyPatch, questions: list[str]
) -> None:
    weidu_running = Event()
    terminated = []
    page = open_page(qtbot, state, monkeypatch)

    def install(
        engine, tp2, components, language, extra_args, input_lines, runner_factory, callbacks
    ):
        callbacks.before_launch()
        weidu_running.set()
        while not terminated:
            page._worker.msleep(1)
        return {components[0].comp_id: result(ComponentStatus.UNKNOWN)}

    monkeypatch.setattr(WeiDUInstallerEngine, "install_components", install)
    monkeypatch.setattr(InstallationWorker, "force_stop", lambda worker: terminated.append(1))

    page._on_run_action()
    qtbot.waitUntil(weidu_running.is_set)
    assert page._run_action() == RunAction.PAUSE
    assert page._btn_stop.isEnabled() and page._btn_cancel.isEnabled()
    assert not page.can_go_to_previous_page()

    page._cancel_installation()
    qtbot.waitUntil(lambda: page._worker is None)

    assert questions == [tr("page.installation.cancel_message")]
    assert terminated == [1]
    assert closure(page) == RunClosure.CANCELLED
    state.reset_workflow.assert_called_once()
    assert page._run_action() == RunAction.START
    assert page.can_go_to_previous_page()


def test_stop_interrupts_weidu_at_once_after_a_single_question(
    qtbot: QtBot, state: Mock, monkeypatch: pytest.MonkeyPatch, questions: list[str]
) -> None:
    weidu_running = Event()
    terminated = []
    page = open_page(qtbot, state, monkeypatch)

    def install(
        engine, tp2, components, language, extra_args, input_lines, runner_factory, callbacks
    ):
        callbacks.before_launch()
        weidu_running.set()
        while not terminated:
            page._worker.msleep(1)
        return {components[0].comp_id: result(ComponentStatus.UNKNOWN)}

    monkeypatch.setattr(WeiDUInstallerEngine, "install_components", install)
    monkeypatch.setattr(InstallationWorker, "force_stop", lambda worker: terminated.append(1))
    information = Mock()
    monkeypatch.setattr(QMessageBox, "information", information)

    page._on_run_action()
    qtbot.waitUntil(weidu_running.is_set)
    page._stop_installation()
    qtbot.waitUntil(lambda: page._worker is None)

    assert questions == [tr("page.installation.stop_message")]
    assert terminated == [1]
    information.assert_not_called()
    assert tr("page.installation.stopped_message", index=1) in page._output_text.toPlainText()
    assert closure(page) is None
    assert page._run_action() == RunAction.RESUME


# ============================================================================
# Finish
# ============================================================================


@pytest.mark.parametrize(
    ("status", "message"),
    [
        (ComponentStatus.SUCCESS, tr("page.installation.finish_message")),
        (ComponentStatus.ERROR, tr("page.installation.finish_incomplete_message", count=1)),
    ],
    ids=["success", "accepted-failure"],
)
def test_finish_closes_the_played_run_resets_the_workflow_and_quits(
    qtbot: QtBot,
    state: Mock,
    monkeypatch: pytest.MonkeyPatch,
    questions: list[str],
    status: ComponentStatus,
    message: str,
) -> None:
    played_run(state, status)
    page = open_page(qtbot, state, monkeypatch)
    window = Mock()
    monkeypatch.setattr(page, "window", lambda: window)
    assert page._run_action() == RunAction.FINISH
    assert not page.can_go_to_previous_page()

    page._on_run_action()

    assert questions == [message]
    assert closure(page) == RunClosure.FINISHED
    state.reset_workflow.assert_called_once()
    window.close.assert_called_once()


def test_declined_finish_keeps_the_run_open(
    qtbot: QtBot, state: Mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    played_run(state, ComponentStatus.SUCCESS)
    page = open_page(qtbot, state, monkeypatch)
    window = Mock()
    monkeypatch.setattr(page, "window", lambda: window)
    monkeypatch.setattr(
        QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.StandardButton.No
    )

    page._on_run_action()

    assert closure(page) is None
    state.reset_workflow.assert_not_called()
    window.close.assert_not_called()
    assert page._run_action() == RunAction.FINISH


# ============================================================================
# History and export of the current run
# ============================================================================


def test_failed_archive_keeps_the_run_open_on_the_page(
    qtbot: QtBot,
    state: Mock,
    monkeypatch: pytest.MonkeyPatch,
    questions: list[str],
) -> None:
    played_run(state, ComponentStatus.SUCCESS)
    page = open_page(qtbot, state, monkeypatch)
    window = Mock()
    monkeypatch.setattr(page, "window", lambda: window)
    shown: list[str] = []
    monkeypatch.setattr(QMessageBox, "exec", lambda box: shown.append(box.windowTitle()))
    disk_full(monkeypatch)

    page._on_run_action()

    assert shown == [tr("page.installation.close_failed_title")]
    window.close.assert_not_called()
    state.reset_workflow.assert_not_called()
    assert page._run_action() == RunAction.FINISH
    assert page._btn_diagnostic.isEnabled()


def test_export_is_limited_to_the_current_run(
    qtbot: QtBot,
    state: Mock,
    monkeypatch: pytest.MonkeyPatch,
    questions: list[str],
) -> None:
    played_run(state, ComponentStatus.SUCCESS)
    page = open_page(qtbot, state, monkeypatch)
    monkeypatch.setattr(page, "window", Mock())
    assert page._btn_diagnostic.isEnabled()
    page._on_run_action()
    choose = Mock(return_value=("", ""))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", choose)

    page._refresh_controls()
    page._export_diagnostic()

    assert len(history(page._executor)) == 1
    assert not page._btn_diagnostic.isEnabled()
    choose.assert_not_called()
