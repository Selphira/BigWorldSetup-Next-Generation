"""Offline installation checkpoints, WeiDU evidence, decisions and shutdown boundaries."""

from dataclasses import replace
import io
import json
import os
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

from PySide6.QtCore import Qt, QThread, Slot
from PySide6.QtWidgets import QMessageBox
import pytest
from pytestqt.qtbot import QtBot

from constants import EXTRACT_DIR, PROGRESS_IDLE_FORMAT
from core import DownloadManager as downloads
from core import WeiDULogParser as weidu_log_module
from core.ExtractionManifest import ExtractionManifest
from core.InstallationExecutor import InstallationError, InstallationExecutor
from core.InstallationJournal import (
    InstallationJournal,
    Operation,
    OperationDecision,
    RunDivergenceError,
    RunOutcome,
    RunPlan,
    RunSequence,
)
from core.InstallationReconciler import InstallationReconciler
from core.InstallationWorker import InstallationWorker, ProcessRunner, UserDecision
from core.Mod import Mod
from core.OrderImportExportManager import FileReadError, OrderImportExportManager
from core.TranslationManager import tr
from core.weidu_types import (
    DEFAULT_STRINGS,
    AttemptEvidence,
    ComponentInfo,
    ComponentStatus,
    InstallResult,
    ProcessCallbacks,
    ProcessResult,
)
from core.WeiDUDebugParser import WeiDUDebugParser
from core.WeiDUInstallerEngine import WeiDUInstallerEngine
from core.WeiDULogParser import WeiDULogParser
from tests.conftest import ModCatalogue
from ui.MainWindow import MainWindow
from ui.pages.DownloadPage import DownloadPage
from ui.pages.ExtractionPage import ExtractionPage
from ui.pages.InstallationPage import InstallationPage

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def journal(tmp_path: Path) -> InstallationJournal:
    components = [f"demo:{n}" for n in range(3)]
    plan = RunPlan(
        "eet", {"sod": str(tmp_path)}, {0: components}, components, "fr_FR", ["fr_FR"], True
    )
    seq = RunSequence(
        str(tmp_path),
        RunSequence.identify(tmp_path),
        [Operation(ref, "demo", 0, 0, ["A"], [], [], "std") for ref in components],
    )
    run = InstallationJournal(tmp_path / "run.json", plan, [seq])
    run.save()
    return run


@pytest.fixture(autouse=True)
def demo_tp2(tmp_path: Path) -> Path:
    """WeiDU is only launched for a TP2 present in the game folder."""
    tp2 = tmp_path / "demo" / "demo.tp2"
    tp2.parent.mkdir()
    tp2.write_text("BACKUP ~demo/backup~\nAUTHOR ~me~\n", encoding="utf-8")
    return tp2


def prepare_mods(folder: Path, catalogue: ModCatalogue) -> None:
    """Write the extraction manifests proving every mod was prepared."""
    for mod in catalogue.mods.values():
        ExtractionManifest(mod.id, "a" * 64, 0).save(
            ExtractionManifest.path_for(folder, mod.id)
        )


@pytest.fixture
def no_rules() -> Mock:
    rules = Mock()
    rules.blocked_positions.return_value = set()
    return rules


def result(status: ComponentStatus, in_log: bool = True) -> InstallResult:
    return InstallResult(
        status, 0, "", "", [], [], evidence=AttemptEvidence(0, status, in_log=in_log)
    )


def reconciler() -> InstallationReconciler:
    return InstallationReconciler(WeiDULogParser())


def state_for(
    tmp_path: Path, catalogue: ModCatalogue, rules, orders: dict, selection: list[str]
) -> Mock:
    state = Mock()
    state.STATE_FILE = tmp_path / "state.json"
    state.get_selected_game.return_value = "eet"
    state.get_languages_order.return_value = ["fr_FR"]
    state.get_page_option.side_effect = lambda _page, _key, default=None: (
        ["fr_FR"] if _key == "selected_languages" else default
    )
    state.get_selected_components.return_value = selection
    state.get_install_order.return_value = orders
    state.get_game_folders.return_value = {"game": str(tmp_path)}
    state.get_mod_manager.return_value = catalogue
    state.get_rule_manager.return_value = rules
    game = Mock(sequence_count=len(orders))
    game.get_sequence.return_value = SimpleNamespace(game="game", name="Game")
    state.get_game_manager.return_value.get.return_value = game
    for mod in catalogue.mods.values():
        mod.languages["fr_FR"] = 0
    prepare_mods(tmp_path, catalogue)
    return state


def components(*keys: str) -> list[SimpleNamespace]:
    return [
        SimpleNamespace(comp_id=f"demo:{key}", component=SimpleNamespace(key=key))
        for key in keys
    ]


def fake_runner(engine: WeiDUInstallerEngine, code: int | None, debug: str, log: str) -> Mock:
    runner = Mock()
    runner.result = ProcessResult(code, "", "")

    def execute() -> None:
        engine.weidu_log.write_text(log, encoding="utf-8")
        engine.debug_file("demo").write_text(debug, encoding="utf-8")

    runner.start.side_effect = execute
    return runner


# ============================================================================
# Journal
# ============================================================================


def test_progress_is_durable_before_launch_and_before_ui_notification(
    journal: InstallationJournal,
) -> None:
    journal.begin(0, [0, 1, 2])
    disk = InstallationJournal.load(journal.path)
    assert [op.status for op in disk.sequences[0].operations] == [ComponentStatus.RUNNING] * 3

    journal.record(
        0,
        {
            0: result(ComponentStatus.SUCCESS),
            1: result(ComponentStatus.WARNING),
            2: result(ComponentStatus.STOPPED),
        },
    )
    disk = InstallationJournal.load(journal.path)
    assert [op.status.value for op in disk.sequences[0].operations] == [
        "success",
        "warning",
        "stopped",
    ]
    assert disk.outcome() == RunOutcome.INCOMPLETE
    assert disk.stats() == {"success": 1, "warnings": 1, "errors": 1, "skipped": 0}
    assert disk.undetermined_positions() == [(0, 2)]


@pytest.mark.parametrize(
    ("last", "outcome"),
    [
        (ComponentStatus.SUCCESS, RunOutcome.COMPLETE),
        (ComponentStatus.WARNING, RunOutcome.WARNINGS),
        (ComponentStatus.SKIPPED, RunOutcome.INCOMPLETE),
        (ComponentStatus.BLOCKED, RunOutcome.INCOMPLETE),
        (ComponentStatus.UNKNOWN, RunOutcome.INCOMPLETE),
    ],
)
def test_final_outcome(
    journal: InstallationJournal, last: ComponentStatus, outcome: RunOutcome
) -> None:
    journal.record(
        0,
        {
            0: result(ComponentStatus.SUCCESS),
            1: result(ComponentStatus.SUCCESS),
            2: result(last),
        },
    )
    assert journal.outcome() == outcome


def test_installed_component_missing_from_log_is_reopened_for_a_decision(
    journal: InstallationJournal,
) -> None:
    log = journal.path.parent / "WeiDU.log"
    journal.record(0, {0: result(ComponentStatus.SUCCESS)})

    report = reconciler().reconcile(InstallationJournal.load(journal.path))
    assert report.reopened == ["demo:0"]
    assert report.undetermined == [(0, 0)]

    journal.record(0, {0: result(ComponentStatus.SUCCESS)})
    log.write_text("~directory/setup-demo.tp2~ #0 #0 // component\n~other.tp2~ #0 #4")
    report = reconciler().reconcile(journal)
    assert not report.reopened and not report.undetermined
    assert report.unexpected == [f"{journal.path.parent}: other #0 #4"]
    assert [op.reference for op in journal.sequences[0].operations if op.needs_work] == [
        "demo:1",
        "demo:2",
    ]


def test_replaced_game_folder_stops_the_resume(journal: InstallationJournal) -> None:
    journal.sequences[0].base = "another:folder"
    with pytest.raises(RunDivergenceError):
        reconciler().reconcile(journal)
    with pytest.raises(RunDivergenceError):
        journal.begin(0, [0])


def test_partial_batch_changing_chitin_key_can_be_retried(
    journal: InstallationJournal,
) -> None:
    key = journal.path.parent / "chitin.key"
    key.write_bytes(b"original")
    journal.begin(0, [0, 1])
    key.write_bytes(b"merged by the successful component")
    journal.record(0, {0: result(ComponentStatus.SUCCESS), 1: result(ComponentStatus.ERROR)})
    journal.decide(0, 1, OperationDecision.RETRY, in_log=False)
    journal.begin(0, [1])
    assert journal.sequences[0].operations[1].status == ComponentStatus.RUNNING


@pytest.mark.parametrize(
    ("decision", "in_log", "status", "needs_work"),
    [
        (OperationDecision.CONFIRM_INSTALLED, True, ComponentStatus.SUCCESS, False),
        (OperationDecision.RETRY, True, ComponentStatus.PENDING, True),
        (OperationDecision.ACCEPT_FAILURE, False, ComponentStatus.ERROR, False),
    ],
)
def test_user_decision_settles_an_interrupted_operation(
    journal: InstallationJournal,
    decision: OperationDecision,
    in_log: bool,
    status: ComponentStatus,
    needs_work: bool,
) -> None:
    journal.record(0, {0: InstallResult(ComponentStatus.STOPPED, 1, "", "", [], [])})
    journal.decide(0, 0, decision, in_log)
    saved = InstallationJournal.load(journal.path).sequences[0].operations[0]
    assert saved.status == status
    assert saved.decision == decision
    assert saved.needs_work is needs_work
    assert not saved.status.is_undetermined


def test_unreadable_journal_can_be_set_aside(tmp_path: Path) -> None:
    path = tmp_path / "installation-run.json"
    path.write_text("{ truncated", encoding="utf-8")
    executor = InstallationExecutor(Mock(), journal_path=path)
    executor.load()
    assert executor.load_error and not executor.is_active

    kept = executor.set_aside_unreadable_journal()
    assert kept.read_text(encoding="utf-8") == "{ truncated"
    assert not path.exists()
    executor.load()
    assert executor.load_error is None


# ============================================================================
# WeiDU evidence
# ============================================================================


@pytest.mark.parametrize(
    ("debug", "entries", "code", "expected"),
    [
        ("Installing [A]", [0], 0, ["unknown"]),
        (
            "Installing [A]\nERROR: BIFF unavailable\nSUCCESSFULLY INSTALLED A",
            [0],
            0,
            ["success"],
        ),
        ("Installing [A]\nERROR: BIFF unavailable", [0], 0, ["unknown"]),
        ("Installing [A]\nINSTALLATION ABORTED\nSUCCESSFULLY INSTALLED A", [0], 0, ["unknown"]),
        ("Installing [A]\nWARNING: unfinished", [0], 0, ["unknown"]),
        ("Installing [A]\nSUCCESSFULLY INSTALLED A", [], 0, ["unknown"]),
        ("Installing [A]\nSUCCESSFULLY INSTALLED A", [0], 1, ["unknown"]),
        ("Installing [A]\nSUCCESSFULLY INSTALLED A", [0], 0, ["success"]),
        ("Installing [A] [1.0]\nINSTALLED WITH WARNINGS     A", [0], 3, ["warning"]),
        (
            "Installing [A]\nInstalling [B]\nERROR: broken\nSUCCESSFULLY INSTALLED A\n"
            "NOT INSTALLED DUE TO ERRORS B",
            [0],
            2,
            ["success", "error"],
        ),
        (
            "Installing [A]\nERROR: broken\nERROR Installing [A], rolling back to previous state",
            [],
            1,
            ["error"],
        ),
        ("ERROR: parsing [demo.tp2]: syntax error", [], 1, ["error", "error"]),
        ("Installing [A]\nSUCCESSFULLY INSTALLED A", [0], None, ["error"]),
    ],
)
def test_engine_cross_checks_fresh_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    debug: str,
    entries: list[int],
    code: int | None,
    expected: list[str],
) -> None:
    engine = WeiDUInstallerEngine(tmp_path, WeiDULogParser(), WeiDUDebugParser())
    monkeypatch.setattr(engine, "_create_or_update_setup", lambda _: True)
    monkeypatch.setattr(engine, "get_weidu_strings", lambda *_: DEFAULT_STRINGS)
    engine.debug_file("demo").write_text("Installing [A]\nSUCCESSFULLY INSTALLED A")
    log = "\n".join(f"~other/setup-demo.tp2~ #0 #{i}" for i in entries)
    runner = fake_runner(engine, code, debug, log)

    batch = components(*(str(i) for i in range(len(expected))))
    results = engine.install_components("demo", batch, "0", [], [], lambda *_: runner)
    assert [r.status.value for r in results.values()] == expected


def test_locked_debug_is_a_failure_before_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = WeiDUInstallerEngine(tmp_path, WeiDULogParser(), WeiDUDebugParser())
    monkeypatch.setattr(engine, "_create_or_update_setup", lambda _: True)
    monkeypatch.setattr(engine, "_backup_debug", Mock(side_effect=PermissionError("locked")))
    launch = Mock()
    factory = Mock()
    results = engine.install_components(
        "demo", components("0"), "0", [], [], factory, callbacks=Mock(before_launch=launch)
    )
    assert results["demo:0"].status == ComponentStatus.ERROR
    assert "locked" in results["demo:0"].errors[0]
    launch.assert_not_called()
    factory.assert_not_called()


@pytest.mark.parametrize(
    ("debug", "strings", "expected"),
    [
        (
            "Installation de [Premier]\nInstallation de [Second]\nINSTALLÉ Premier",
            {"installing": "Installation de", "successfully_installed": "INSTALLÉ"},
            {0: ComponentStatus.SUCCESS, 1: ComponentStatus.UNKNOWN},
        ),
        (
            "Installation en cours [A] [v1]\n"
            "ERREUR pendant l'installation de [A], retour a l'etat anterieur\n"
            "INSTALLATION ECHOUEE A CAUSE D'ERREURS A",
            {
                "installing": "Installation en cours",
                "error_installing": "ERREUR pendant l'installation de [",
                "rolling_back": "], retour a l'etat anterieur",
                "not_installed_due_to_errors": "INSTALLATION ECHOUEE A CAUSE D'ERREURS",
            },
            {0: ComponentStatus.ERROR},
        ),
        (
            # A mod translation is ignored by WeiDU when it prints English messages.
            "Installing [A]\nSUCCESSFULLY INSTALLED      A",
            {"installing": "Installation en cours", "successfully_installed": "REUSSIE"},
            {0: ComponentStatus.SUCCESS},
        ),
        (
            "SKIPPING: [A]\nInstalling [B]\nSUCCESSFULLY INSTALLED B",
            {},
            {
                0: ComponentStatus.SKIPPED,
                1: ComponentStatus.SUCCESS,
            },
        ),
        (
            "Installing [A]\nWARNING: odd\nSUCCESSFULLY INSTALLED A\nSUCCESSFULLY INSTALLED A",
            {},
            {0: ComponentStatus.WARNING},
        ),
        (
            "Installing [A]\nERROR Installing [A], rolling back to previous state\n"
            "SUCCESSFULLY INSTALLED A",
            {},
            {0: ComponentStatus.UNKNOWN},
        ),
    ],
)
def test_debug_parser_reads_weidu_messages(
    tmp_path: Path, debug: str, strings: dict[str, str], expected: dict
) -> None:
    file = tmp_path / "setup.DEBUG"
    file.write_text(debug, encoding="utf-8")
    assert WeiDUDebugParser.parse(file, DEFAULT_STRINGS | strings) == expected


def test_debug_parser_keeps_the_diagnostics_of_each_component(tmp_path: Path) -> None:
    file = tmp_path / "setup.DEBUG"
    file.write_text(
        "WARNING: parsing log [WeiDU.log]: Sys_error\n"
        "Installing [A] [v1]\n"
        "Copied [blank] to [data/sfo_warnings.txt]\n"
        "UA WARNING: dagg03 is missing from hentol.cre, skipping!\n"
        "Near Text: )\n"
        "\t[ForceSpell] argument [X] not found in [Spell.IDS]\n"
        "Near Text: )\n"
        "Compiling 3 scripts ...\n"
        "INSTALLED WITH WARNINGS     A\n"
        "Installing [B] [v1]\n"
        "User did not select a gender. This warning is harmless.\n"
        "SUCCESSFULLY INSTALLED      B\n",
        encoding="utf-8",
    )
    report = WeiDUDebugParser.read_report(file)
    assert report.messages == {
        0: [
            "UA WARNING: dagg03 is missing from hentol.cre, skipping!",
            "Near Text: ) (×2)",
            "[ForceSpell] argument [X] not found in [Spell.IDS]",
        ],
        1: ["User did not select a gender. This warning is harmless."],
    }


def test_debug_attempt_with_invalid_bytes_matches_both_readings(tmp_path: Path) -> None:
    file = tmp_path / "setup.DEBUG"
    file.write_bytes(
        b"Installing [A\xe9] [v1]\r\n"
        b"WARNING: bad \xff byte\r\n"
        b"ERROR: missing \xfe file\r"
        b"SUCCESSFULLY INSTALLED A\xe9\r\n"
    )

    attempt = WeiDUDebugParser.read_attempt(file)

    assert attempt.report == WeiDUDebugParser.read_report(file)
    assert (attempt.warnings, attempt.errors, attempt.content) == (
        WeiDUDebugParser.extract_warnings_errors(file)
    )
    assert attempt.report.statuses == {0: ComponentStatus.WARNING}
    assert attempt.report.messages == {0: ["WARNING: bad � byte", "ERROR: missing � file"]}
    assert attempt.errors == ["ERROR: missing  file"]
    assert attempt.content == (
        "Installing [A] [v1]\nWARNING: bad  byte\nERROR: missing  file\nSUCCESSFULLY INSTALLED A\n"
    )


def test_debug_attempt_of_a_missing_file_is_empty(tmp_path: Path) -> None:
    attempt = WeiDUDebugParser.read_attempt(tmp_path / "setup.DEBUG")

    assert attempt.report.statuses == {}
    assert (attempt.warnings, attempt.errors, attempt.content) == ([], [], "")


def test_warning_result_carries_its_own_messages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = WeiDUInstallerEngine(tmp_path, WeiDULogParser(), WeiDUDebugParser())
    monkeypatch.setattr(engine, "_create_or_update_setup", lambda _: True)
    monkeypatch.setattr(engine, "get_weidu_strings", lambda *_: DEFAULT_STRINGS)
    debug = (
        "Installing [A]\nWARN text\nINSTALLED WITH WARNINGS A\n"
        "Installing [B]\nWARNING: odd\nSUCCESSFULLY INSTALLED B\n"
    )
    log = "~demo/setup-demo.tp2~ #0 #0\n~demo/setup-demo.tp2~ #0 #1"
    runner = fake_runner(engine, 3, debug, log)
    results = engine.install_components(
        "demo", components("0", "1"), "0", [], [], lambda *_: runner
    )
    assert results["demo:0"].warnings == [
        tr("page.installation.warning_no_details", file="SETUP-DEMO.DEBUG")
    ]
    assert results["demo:1"].warnings == ["WARNING: odd"]


def test_output_reports_each_component_reached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = WeiDUInstallerEngine(tmp_path, WeiDULogParser(), WeiDUDebugParser())
    monkeypatch.setattr(engine, "_create_or_update_setup", lambda _: True)
    monkeypatch.setattr(engine, "get_weidu_strings", lambda *_: DEFAULT_STRINGS)
    runner = fake_runner(engine, 0, "", "")
    relays = []
    runner.output_received.connect.side_effect = lambda relay, *_: relays.append(relay)

    def execute() -> None:
        for chunk in ("Installing [A]\r\nInsta", "lling [B] [v1]\n", "SKIPPING: [C]\n"):
            relays[0](chunk, "stdout")
        relays[0]("Installing [D]\n", "stderr")

    runner.start.side_effect = execute
    output, reached = [], []
    callbacks = ProcessCallbacks(
        output=lambda text, _stream: output.append(text), component_reached=reached.append
    )
    engine.install_components(
        "demo", components("0", "1", "2"), "0", [], [], lambda *_: runner, callbacks
    )
    assert reached == [0, 1, 2]
    assert len(output) == 4


def test_warning_dialog_lists_every_warned_component(
    catalogue: ModCatalogue, journal: InstallationJournal, no_rules: Mock
) -> None:
    mod = catalogue.get_mod_by_id("demo")
    batch = [
        ComponentInfo("demo:" + key, mod, mod.get_component(key), "demo", n)
        for n, key in enumerate(["1", "2"])
    ]
    for op, comp in zip(journal.sequences[0].operations, batch, strict=False):
        op.reference = comp.comp_id
    worker = InstallationWorker(Mock(), [batch], journal, no_rules)
    asked = []

    def answer(reference: str, messages: list[str]) -> None:
        asked.append((reference, messages))
        worker.set_user_decision(UserDecision.SKIP)

    worker.warning_occurred.connect(answer)
    worker._install_batch = lambda _: {
        "demo:1": replace(result(ComponentStatus.WARNING), warnings=["first"]),
        "demo:2": replace(result(ComponentStatus.WARNING), warnings=["second"]),
    }
    worker.run()
    assert asked == [("demo:1, demo:2", ["[demo:1]", "  first", "", "[demo:2]", "  second"])]


def test_progress_bar_names_the_component_being_installed(
    qtbot: QtBot, tmp_path: Path, catalogue: ModCatalogue, rules
) -> None:
    state = state_for(tmp_path, catalogue, rules, {0: ["a:0"]}, ["a:0"])
    page = InstallationPage(state)
    qtbot.addWidget(page)
    page._on_component_started("a:0", "Mod A", "Bonus 50%")
    assert page._progress_bar.format() == "Mod A — Bonus 50%% - %p%"
    page._refresh_controls()
    assert page._progress_bar.format() == PROGRESS_IDLE_FORMAT


def test_weidu_log_import_keeps_the_mod_folder_name(tmp_path: Path) -> None:
    log = tmp_path / "WeiDU.log"
    log.write_text("~CDTWEAKS/SETUP-CDTWEAKS.TP2~ #0 #1 // Tweaks\n", encoding="utf-8")
    parser = WeiDULogParser()
    assert parser.parse_file(log).get_component_ids() == ["cdtweaks:1"]
    assert parser.installed_identities(log) == [("cdtweaks", "1", "0")]


MIXED_WEIDU_LOG = (
    "// Log of Currently Installed WeiDU Mods\n"
    "~CDTWEAKS/SETUP-CDTWEAKS.TP2~ #0 #1 // Tweaks\n"
    "this line is not a WeiDU entry\n"
    "~BROKEN.TP2 #0\n"
    "~ASCENSION/ASCENSION.TP2~ #0 #0 // Ascension\n"
)


def unreadable_weidu_log(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every WeiDU.log read fail as on a locked or protected file."""

    def deny(path: Path, *_args, **_kwargs) -> str:
        raise PermissionError(f"Access denied: {path}")

    monkeypatch.setattr(weidu_log_module, "safe_read", deny)


def test_strict_weidu_log_parse_keeps_valid_entries_among_malformed_lines(
    tmp_path: Path,
) -> None:
    log = tmp_path / "WeiDU.log"
    log.write_text(MIXED_WEIDU_LOG, encoding="utf-8")
    result = WeiDULogParser().parse_file(log, strict=True)
    assert result.get_component_ids() == ["cdtweaks:1", "ascension:0"]


def test_strict_weidu_log_parse_of_an_empty_log_is_empty(tmp_path: Path) -> None:
    log = tmp_path / "WeiDU.log"
    log.write_text("", encoding="utf-8")
    assert WeiDULogParser().parse_file(log, strict=True).entries == []


def test_strict_weidu_log_parse_reports_a_missing_log(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        WeiDULogParser().parse_file(tmp_path / "WeiDU.log", strict=True)


def test_strict_weidu_log_parse_reports_a_folder(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        WeiDULogParser().parse_file(tmp_path, strict=True)


def test_strict_weidu_log_parse_reports_an_unreadable_log(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log = tmp_path / "WeiDU.log"
    log.write_text(MIXED_WEIDU_LOG, encoding="utf-8")
    unreadable_weidu_log(monkeypatch)
    with pytest.raises(PermissionError):
        WeiDULogParser().parse_file(log, strict=True)


def test_lenient_weidu_log_parse_still_tolerates_an_unreadable_log(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log = tmp_path / "WeiDU.log"
    log.write_text(MIXED_WEIDU_LOG, encoding="utf-8")
    unreadable_weidu_log(monkeypatch)
    assert WeiDULogParser().parse_file(log).entries == []


@pytest.mark.parametrize("problem", ["missing", "folder", "unreadable"])
def test_order_import_from_an_unusable_weidu_log_is_a_read_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, problem: str
) -> None:
    log = tmp_path / "WeiDU.log"
    if problem == "folder":
        log.mkdir()
    elif problem == "unreadable":
        log.write_text(MIXED_WEIDU_LOG, encoding="utf-8")
        unreadable_weidu_log(monkeypatch)

    with pytest.raises(FileReadError):
        OrderImportExportManager(WeiDULogParser()).import_from_weidu_log(log)


def test_order_import_from_weidu_log_keeps_the_installation_order(tmp_path: Path) -> None:
    log = tmp_path / "WeiDU.log"
    log.write_text(MIXED_WEIDU_LOG, encoding="utf-8")
    references = OrderImportExportManager(WeiDULogParser()).import_from_weidu_log(log)
    assert [str(reference) for reference in references] == ["cdtweaks:1", "ascension:0"]


@pytest.mark.parametrize(
    ("code", "terminal", "expected"),
    [
        (0, "SUCCESSFULLY INSTALLED A", "success"),
        (1, "SUCCESSFULLY INSTALLED A", "unknown"),
        (0, "", "unknown"),
        (3, "INSTALLED WITH WARNINGS A", "warning"),
    ],
)
def test_no_log_record_requires_explicit_component_and_terminal_evidence(
    tmp_path: Path,
    journal: InstallationJournal,
    monkeypatch: pytest.MonkeyPatch,
    code: int,
    terminal: str,
    expected: str,
) -> None:
    engine = WeiDUInstallerEngine(tmp_path, WeiDULogParser(), WeiDUDebugParser())
    (tmp_path / "demo.tp2").write_text(
        "BACKUP ~demo/backup~\nAUTHOR ~test~\nBEGIN ~A~ DESIGNATED 0 NO_LOG_RECORD\n"
        "BEGIN ~NO_LOG_RECORD~ DESIGNATED 1 // NO_LOG_RECORD\nPRINT ~NO_LOG_RECORD~\n"
        "OUTER_SET NO_LOG_RECORD = 1\n"
    )
    assert engine.read_definition("demo").unlogged_components() == {"0"}
    monkeypatch.setattr(engine, "_create_or_update_setup", lambda _: True)
    runner = fake_runner(engine, code, "Installing [A]\n" + terminal, "")

    outcome = engine.install_components("demo", components("0"), "0", [], [], lambda *_: runner)
    assert outcome["demo:0"].status.value == expected
    if expected != "unknown":
        journal.record(0, {0: outcome["demo:0"]})
        loaded = InstallationJournal.load(journal.path)
        assert not reconciler().reconcile(loaded).reopened
        assert loaded.sequences[0].operations[0].confirmed_without_log


# ============================================================================
# Worker
# ============================================================================


def test_retry_only_failed_component_and_keep_warning(
    catalogue: ModCatalogue, journal: InstallationJournal, no_rules: Mock
) -> None:
    mod = catalogue.get_mod_by_id("demo")
    batch = [
        ComponentInfo("demo:" + key, mod, mod.get_component(key), "demo", n)
        for n, key in enumerate(["1", "2"])
    ]
    for op, comp in zip(journal.sequences[0].operations, batch, strict=False):
        op.reference = comp.comp_id
    worker = InstallationWorker(Mock(), [batch], journal, no_rules)
    worker.error_occurred.connect(lambda *_: worker.set_user_decision(UserDecision.RETRY))
    calls = []

    def install(components: list[ComponentInfo]) -> dict[str, InstallResult]:
        calls.append([c.comp_id for c in components])
        if len(calls) == 1:
            return {
                "demo:1": result(ComponentStatus.WARNING),
                "demo:2": result(ComponentStatus.ERROR, in_log=False),
            }
        return {"demo:2": result(ComponentStatus.SUCCESS)}

    worker._install_batch = install
    worker.run()
    assert calls == [["demo:1", "demo:2"], ["demo:2"]]
    statuses = [
        op.status for op in InstallationJournal.load(journal.path).sequences[0].operations
    ]
    assert statuses[:2] == [ComponentStatus.WARNING, ComponentStatus.SUCCESS]


def test_retried_component_is_reinstalled_even_if_logged(
    catalogue: ModCatalogue, journal: InstallationJournal, no_rules: Mock
) -> None:
    mod = catalogue.get_mod_by_id("a")
    comp = ComponentInfo("demo:0", mod, mod.get_component("0"), "demo", 0)
    journal.decide(0, 0, OperationDecision.RETRY, in_log=True)
    engine = Mock()
    engine.installed_identities.return_value = {("demo", "0", "0")}
    engine.install_components.return_value = {"demo:0": result(ComponentStatus.SUCCESS)}
    worker = InstallationWorker(engine, [[comp]], journal, no_rules)
    worker.run()
    engine.install_components.assert_called_once()
    assert journal.sequences[0].operations[0].status == ComponentStatus.SUCCESS


def test_next_batch_sees_components_logged_by_the_previous_process(
    catalogue: ModCatalogue,
    journal: InstallationJournal,
    no_rules: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mod = catalogue.get_mod_by_id("a")
    first, second = (
        ComponentInfo(f"demo:{key}", mod, mod.get_component(key), "demo", position)
        for position, key in enumerate(["0", "1"])
    )
    engine = WeiDUInstallerEngine(journal.path.parent, WeiDULogParser(), WeiDUDebugParser())
    launched = []

    def install(**kwargs) -> dict[str, InstallResult]:
        # The process also records the component planned for the next batch.
        launched.append([comp.comp_id for comp in kwargs["components"]])
        engine.weidu_log.write_text(
            "~DEMO/DEMO.TP2~ #0 #0 // Zero\n~DEMO/DEMO.TP2~ #0 #1 // One\n", encoding="utf-8"
        )
        return {"demo:0": result(ComponentStatus.SUCCESS)}

    monkeypatch.setattr(engine, "install_components", install)
    worker = InstallationWorker(engine, [[first], [second]], journal, no_rules)
    worker.run()
    assert launched == [["demo:0"]]
    # Logged without having been confirmed by this run: the user has to decide.
    statuses = [op.status for op in journal.sequences[0].operations[:2]]
    assert statuses == [ComponentStatus.SUCCESS, ComponentStatus.UNKNOWN]


def test_user_pause_survives_restart_until_acknowledged(
    journal: InstallationJournal, no_rules: Mock
) -> None:
    pause = ComponentInfo("pause:1::Review", None, None, "pause", 0)
    journal.sequences[0].operations[0].reference = pause.comp_id
    worker = InstallationWorker(Mock(), [[pause]], journal, no_rules)
    worker.installation_paused.connect(lambda *_: worker.stop())
    worker.run()
    assert journal.sequences[0].operations[0].needs_work
    resumed = InstallationWorker(Mock(), [[pause]], journal, no_rules)
    resumed.installation_paused.connect(lambda *_: resumed.resume())
    resumed.run()
    saved = InstallationJournal.load(journal.path).sequences[0].operations[0]
    assert saved.status == ComponentStatus.SKIPPED


def test_failed_checkpoint_prevents_launch(
    catalogue: ModCatalogue,
    journal: InstallationJournal,
    no_rules: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = journal.path.parent
    mod = catalogue.get_mod_by_id("demo")
    comp = ComponentInfo(
        "demo:0",
        mod,
        SimpleNamespace(key="0", is_dwn=lambda: False, get_name=lambda: "Zero"),
        "demo",
        0,
    )
    engine = WeiDUInstallerEngine(root, WeiDULogParser(), WeiDUDebugParser())
    monkeypatch.setattr(engine, "_create_or_update_setup", lambda _: True)
    factory = Mock()
    monkeypatch.setattr("core.InstallationWorker.ProcessRunner", factory)
    monkeypatch.setattr(journal, "save", Mock(side_effect=OSError("disk full")))
    problems = []
    worker = InstallationWorker(engine, [[comp]], journal, no_rules)
    worker.diagnostic.connect(problems.append)
    worker.run()
    assert worker.is_stopped and problems == ["disk full"]
    factory.assert_not_called()
    saved = InstallationJournal.load(journal.path).sequences[0].operations[0]
    assert saved.status == ComponentStatus.PENDING


def test_interactive_process_emits_prompt_and_stops_on_eof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, qtbot: QtBot
) -> None:
    reader, writer = os.pipe()
    os.write(writer, "Réponse ? ".encode())
    os.close(writer)
    err_reader, err_writer = os.pipe()
    os.close(err_writer)
    process = Mock()
    process.stdin = io.StringIO()
    process.stdout = os.fdopen(reader, encoding="utf-8")
    process.stderr = os.fdopen(err_reader, encoding="utf-8")
    process.returncode = 0
    process.poll.side_effect = lambda: 0 if process.stdin.closed else None
    monkeypatch.setattr("subprocess.Popen", Mock(return_value=process))
    runner = ProcessRunner(["fixture"], tmp_path)
    try:
        with qtbot.waitSignal(runner.output_received) as signal:
            runner.start()
        assert signal.args == ["Réponse ? ", "stdout"]
        with qtbot.waitSignal(runner.finished):
            runner.stop()
        assert process.stdin.closed
        assert not runner.send_input("late answer")
    finally:
        runner.stop()
        runner.wait(1000)


def finished_process(stdout: bytes, stderr: bytes, code: int) -> Mock:
    """Process already exited, whose pipes still hold its whole output."""
    streams = []
    for content in (stdout, stderr):
        reader, writer = os.pipe()
        os.write(writer, content)
        os.close(writer)
        streams.append(os.fdopen(reader, encoding="utf-8"))
    process = Mock(returncode=code, stdin=io.StringIO())
    process.stdout, process.stderr = streams
    process.poll.return_value = code
    return process


def test_process_result_holds_whole_output_and_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    process = finished_process("Installing\nRéponse ? ".encode(), b"oops\n", 3)
    monkeypatch.setattr("subprocess.Popen", Mock(return_value=process))
    runner = ProcessRunner(["fixture"], tmp_path)
    assert runner.result is None
    runner.run()
    assert runner.result == ProcessResult(3, "Installing\nRéponse ? ", "oops\n")


def test_launch_failure_is_reported_in_process_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("subprocess.Popen", Mock(side_effect=OSError("weidu missing")))
    runner = ProcessRunner(["fixture"], tmp_path)
    errors = []
    runner.process_error.connect(errors.append)
    runner.run()
    assert runner.result == ProcessResult(None, "", "", "weidu missing")
    assert errors == ["weidu missing"]


def test_queued_outputs_are_emitted_in_order_without_blocking_readers(tmp_path: Path) -> None:
    runner = ProcessRunner(["fixture"], tmp_path)
    received = []

    def record(text: str, stream: str) -> None:
        received.append((text, stream, runner._queue_lock.locked()))

    runner.output_received.connect(record, Qt.ConnectionType.DirectConnection)
    for text, stream in (("one\n", "stdout"), ("err", "stderr"), ("Réponse ? ", "stdout")):
        runner._queue_output(text, stream)
    runner._emit_queued_outputs()
    assert received == [
        ("one\n", "stdout", False),
        ("err", "stderr", False),
        ("Réponse ? ", "stdout", False),
    ]


def test_download_only_registers_extracted_payload_and_reconciles(
    journal: InstallationJournal, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = journal.path.parent
    mod = Mod(
        {"id": "demo", "links": {}, "components": {"0": {"type": "dwn"}, "2": {"type": "dwn"}}}
    )
    component = ComponentInfo("demo:2", mod, mod.get_component("2"), "", 2)
    op = journal.sequences[0].operations[2]
    op.kind, op.tp2, op.language = "dwn", "", 4
    engine = WeiDUInstallerEngine(root, WeiDULogParser(), WeiDUDebugParser())
    engine.weidu_log.touch()
    register = Mock(return_value={"demo:2": result(ComponentStatus.SUCCESS)})
    monkeypatch.setattr(engine, "install_components", register)
    worker = InstallationWorker(engine, [[component]], journal, Mock())

    outcome = worker._install_batch([component])
    assert outcome["demo:2"].status == ComponentStatus.ERROR
    register.assert_not_called()

    folder = root / EXTRACT_DIR / "demo"
    folder.mkdir(parents=True)
    (folder / "tool.txt").write_text("payload")
    outcome = worker._install_batch([component])
    assert register.call_args.args[:3] == ("bws-dwn-demo", [component], "0")
    script = (root / "setup-bws-dwn-demo.tp2").read_text()
    assert "DESIGNATED 0" in script and "DESIGNATED 2" in script

    journal.record(0, {2: outcome["demo:2"]})
    engine.weidu_log.write_text("~setup-bws-dwn-demo.tp2~ #0 #2")
    assert not reconciler().reconcile(InstallationJournal.load(journal.path)).reopened


# ============================================================================
# Page and executor
# ============================================================================


def test_page_resumes_saved_plan_after_selection_changes(
    qtbot: QtBot,
    tmp_path: Path,
    catalogue: ModCatalogue,
    rules,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = state_for(tmp_path, catalogue, rules, {0: ["demo:1"]}, ["demo:1", "demo:1.1.A"])
    page = InstallationPage(state)
    qtbot.addWidget(page)
    monkeypatch.setattr(page, "_start_next_sequence", Mock())
    page._start_installation()
    page._executor.release()

    state.get_selected_components.return_value = ["b:0"]
    state.get_install_order.return_value = {0: ["b:0"]}
    resumed = InstallationPage(state)
    qtbot.addWidget(resumed)
    monkeypatch.setattr(resumed, "_start_next_sequence", Mock())
    resumed._start_installation()
    prepared = resumed._executor.next_sequence(0)
    assert prepared.batches[0][0].subcomponent_answers == ["A"]
    assert resumed._journal.plan.orders == {0: ["demo:1"]}
    resumed._executor.release()


def test_catalogue_change_is_reported_without_blocking_resume(
    tmp_path: Path, catalogue: ModCatalogue, rules
) -> None:
    state = state_for(tmp_path, catalogue, rules, {0: ["a:0", "b:0"]}, ["a:0", "b:0"])
    executor = InstallationExecutor(state)
    assert not executor.start(batch=True).resumed

    catalogue.get_mod_by_id("a").version = "2.0"
    report = InstallationExecutor(state).start(batch=True)
    assert report.resumed and report.catalogue_changes == ["a:0"]


def test_cancelled_run_on_a_modded_game_cannot_be_restarted(
    tmp_path: Path, catalogue: ModCatalogue, rules
) -> None:
    state = state_for(tmp_path, catalogue, rules, {0: ["a:0"]}, ["a:0"])
    (tmp_path / "WeiDU.log").write_text("// comments only\n")
    executor = InstallationExecutor(state)
    assert not executor.start(batch=True).resumed

    (tmp_path / "WeiDU.log").write_text("~A/A.TP2~ #0 #0\n")
    executor.cancel()
    with pytest.raises(InstallationError):
        executor.start(batch=True)


def test_restart_between_sequences_runs_only_remaining_operations(
    qtbot: QtBot,
    tmp_path: Path,
    catalogue: ModCatalogue,
    rules,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = state_for(tmp_path, catalogue, rules, {0: ["a:0"], 1: ["b:0"]}, ["a:0", "b:0"])
    (tmp_path / "weidu.exe").touch()
    launched = []

    def install(
        engine, tp2, components, language, extra_args, input_lines, runner_factory, callbacks
    ):
        callbacks.before_launch()
        launched.append(tp2)
        with engine.weidu_log.open("a", encoding="utf-8") as log:
            log.write(f"~{tp2}.tp2~ #0 #0\n")
        return {components[0].comp_id: result(ComponentStatus.SUCCESS)}

    monkeypatch.setattr(WeiDUInstallerEngine, "install_components", install)
    page = InstallationPage(state)
    qtbot.addWidget(page)
    # Simulate losing the UI after the worker committed the first sequence.
    monkeypatch.setattr(page, "_on_sequence_complete", lambda *_: None)
    page._start_installation()
    qtbot.waitUntil(lambda: page._worker is None)
    page._executor.release()
    assert launched == ["a"]
    assert InstallationJournal.load(page._executor.journal_path).active

    state.get_install_order.return_value = {0: ["a:0"]}
    resumed = InstallationPage(state)
    qtbot.addWidget(resumed)
    monkeypatch.setattr(resumed, "_show_installation_summary", Mock())
    resumed._start_installation()
    qtbot.waitUntil(lambda: not resumed._is_installing and resumed._worker is None)
    assert launched == ["a", "b"]
    final = InstallationJournal.load(page._executor.journal_path)
    assert final.played
    assert final.outcome() == RunOutcome.COMPLETE


# ============================================================================
# Shutdown
# ============================================================================


def test_close_waits_for_active_thread_without_blocking(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = Mock()
    state.get_ui_language.return_value = "en_US"
    monkeypatch.setattr(MainWindow, "_on_language_button_changed", lambda *_: None)
    window = MainWindow(state)
    qtbot.addWidget(window)
    window.show()
    thread = QThread(window)
    thread.start()
    page = Mock()
    page.has_critical_task.return_value = True
    page.request_shutdown.side_effect = thread.quit
    window.pages["test"] = page
    replies = iter([QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes])
    monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kwargs: next(replies))
    try:
        assert not window.close()
        assert window.isVisible()
        page.request_shutdown.assert_not_called()
        assert not window.close()
        qtbot.waitUntil(lambda: not window.isVisible())
        assert not thread.isRunning()
        page.request_shutdown.assert_called_once()
    finally:
        thread.quit()
        thread.wait(1000)


def test_close_without_critical_task_does_not_ask_and_waits_for_threads(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = Mock()
    state.get_ui_language.return_value = "en_US"
    monkeypatch.setattr(MainWindow, "_on_language_button_changed", lambda *_: None)
    window = MainWindow(state)
    qtbot.addWidget(window)
    window.show()
    thread = QThread(window)
    thread.start()
    page = Mock()
    page.has_critical_task.return_value = False
    page.request_shutdown.side_effect = thread.quit
    window.pages["test"] = page
    question = Mock()
    monkeypatch.setattr(QMessageBox, "question", question)
    try:
        assert not window.close()
        qtbot.waitUntil(lambda: not window.isVisible())
        question.assert_not_called()
    finally:
        thread.quit()
        thread.wait(1000)


@pytest.mark.parametrize("kind", ["download", "extract"])
def test_close_cancels_page_work_and_prevents_followup(
    kind: str, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stopped = Event()

    class SlowTask(QThread):
        def run(self) -> None:
            stopped.wait(5)

        def cancel(self) -> None:
            stopped.set()

    state = Mock()
    state.STATE_FILE = tmp_path / "state.json"
    state.get_ui_language.return_value = "en_US"
    monkeypatch.setattr(MainWindow, "_on_language_button_changed", lambda *_: None)
    window = MainWindow(state)
    qtbot.addWidget(window)
    page = DownloadPage(state) if kind == "download" else ExtractionPage(state)
    page.setParent(window)
    window.pages[kind] = page
    window.show()
    task = SlowTask()
    if kind == "download":
        page._verification_worker = task
        start_downloads = Mock()
        monkeypatch.setattr(page, "_start_downloads", start_downloads)
        task.finished.connect(lambda: page._on_verifications_completed({}))
    else:
        page._extraction_worker = task
        task.finished.connect(lambda: page._on_all_extractions_completed([]))
    page.start_task(task)
    question = Mock(return_value=QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "question", question)
    try:
        assert not window.close()
        qtbot.waitUntil(lambda: not window.isVisible())
        question.assert_called_once()
        assert stopped.is_set()
        if kind == "download":
            start_downloads.assert_not_called()
    finally:
        stopped.set()


def test_shutdown_cancels_active_download_in_its_own_thread(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ready = []
    cancelled = []

    class OfflineWorker(downloads.DownloadWorker):
        @Slot()
        def start_download(self) -> None:
            ready.append(self.archive_info.mod_id)

        @Slot()
        def cancel(self) -> None:
            cancelled.append(QThread.currentThread() == self.thread())
            self.error.emit("cancelled")

    monkeypatch.setattr(downloads, "DownloadWorker", OfflineWorker)
    manager = downloads.DownloadManager()
    manager.MAX_CONCURRENT_DOWNLOADS = 1
    manager.set_download_path(tmp_path)
    for name in ("first", "queued"):
        manager.add_to_queue(downloads.ArchiveInfo(name, name, "offline", ""))
    qtbot.waitUntil(lambda: ready == ["first"])
    thread = manager.findChild(QThread)
    with qtbot.waitSignal(thread.finished):
        manager.cancel_all()
    assert ready == ["first"]
    assert cancelled == [True]
    assert not manager.get_active_downloads()


@pytest.fixture
def single_slot_downloads(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[downloads.DownloadManager, list[str]]:
    """Offline manager running one download at a time; cancelling frees the slot."""
    started: list[str] = []

    class OfflineWorker(downloads.DownloadWorker):
        @Slot()
        def start_download(self) -> None:
            started.append(self.archive_info.mod_id)

        @Slot()
        def cancel(self) -> None:
            self.error.emit("cancelled")

    monkeypatch.setattr(downloads, "DownloadWorker", OfflineWorker)
    manager = downloads.DownloadManager()
    manager.MAX_CONCURRENT_DOWNLOADS = 1
    manager.set_download_path(tmp_path)
    return manager, started


def _finish_active_download(qtbot: QtBot, manager: downloads.DownloadManager) -> None:
    (progress,) = manager.get_active_downloads()
    with qtbot.waitSignal(manager.download_canceled):
        manager.cancel_download(progress.archive_info.mod_id)


def test_queued_downloads_start_in_arrival_order(
    qtbot: QtBot, single_slot_downloads: tuple[downloads.DownloadManager, list[str]]
) -> None:
    manager, started = single_slot_downloads
    names = ["first", "second", "third", "fourth"]
    for name in names:
        manager.add_to_queue(downloads.ArchiveInfo(name, name, "offline", ""))

    for expected in range(1, len(names)):
        qtbot.waitUntil(lambda count=expected: len(started) == count)
        _finish_active_download(qtbot, manager)

    qtbot.waitUntil(lambda: started == names)
    with qtbot.waitSignal(manager.all_completed):
        _finish_active_download(qtbot, manager)


def test_equal_archive_is_queued_once_until_it_starts(
    qtbot: QtBot, single_slot_downloads: tuple[downloads.DownloadManager, list[str]]
) -> None:
    manager, started = single_slot_downloads
    manager.add_to_queue(downloads.ArchiveInfo("first", "first", "offline", ""))
    for _ in range(2):
        manager.add_to_queue(downloads.ArchiveInfo("queued", "queued", "offline", ""))
    manager.add_to_queue(downloads.ArchiveInfo("queued", "queued", "mirror", ""))

    qtbot.waitUntil(lambda: started == ["first"])
    _finish_active_download(qtbot, manager)
    qtbot.waitUntil(lambda: started == ["first", "queued"])
    _finish_active_download(qtbot, manager)
    qtbot.waitUntil(lambda: started == ["first", "queued", "queued"])
    _finish_active_download(qtbot, manager)

    manager.add_to_queue(downloads.ArchiveInfo("queued", "queued", "offline", ""))
    qtbot.waitUntil(lambda: started == ["first", "queued", "queued", "queued"])
    with qtbot.waitSignal(manager.all_completed):
        _finish_active_download(qtbot, manager)


# ============================================================================
# Translations
# ============================================================================


@pytest.mark.parametrize("language", ["fr_FR", "en_US"])
def test_every_status_and_outcome_is_translated(language: str) -> None:
    root = Path(__file__).resolve().parents[1] / "i18n"
    texts = json.loads((root / f"{language}.json").read_text(encoding="utf-8"))
    section = texts["page"]["installation"]
    assert {status.value for status in ComponentStatus} <= set(section["status"])
    assert {outcome.value for outcome in RunOutcome} <= set(section)
