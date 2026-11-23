"""L12: format 4 of the installation journal, with the identity and closure of the run."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from constants import INSTALLATION_JOURNAL_FORMAT
from core.InstallationExecutor import InstallationExecutor
from core.InstallationJournal import (
    InstallationJournal,
    JournalError,
    Operation,
    RunClosure,
    RunPlan,
    RunSequence,
)
from core.weidu_types import AttemptEvidence, ComponentStatus, InstallResult

COMPONENT = "demo:0"


@pytest.fixture
def journal_path(tmp_path: Path) -> Path:
    return tmp_path / "runs" / "current" / "installation-run.json"


@pytest.fixture
def journal(tmp_path: Path, journal_path: Path) -> InstallationJournal:
    plan = RunPlan(
        "eet", {"sod": str(tmp_path)}, {0: [COMPONENT]}, [COMPONENT], "fr_FR", ["fr_FR"], True
    )
    sequence = RunSequence(
        str(tmp_path),
        RunSequence.identify(tmp_path),
        [Operation(COMPONENT, "demo", 0, 0, [], [], [], "std")],
    )
    journal_path.parent.mkdir(parents=True)
    run = InstallationJournal(journal_path, plan, [sequence])
    run.save()
    return run


def saved_document(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def rewrite_run(path: Path, changes: dict) -> None:
    document = saved_document(path)
    document["run"] |= changes
    path.write_text(json.dumps(document), encoding="utf-8")


def install_component(journal: InstallationJournal, folder: Path) -> None:
    success = InstallResult(
        ComponentStatus.SUCCESS,
        0,
        "",
        "",
        [],
        [],
        AttemptEvidence(0, ComponentStatus.SUCCESS, in_log=True),
    )
    journal.record(0, {0: success})
    (folder / "WeiDU.log").write_text("~DEMO/DEMO.TP2~ #0 #0 // Demo\n", encoding="utf-8")


def executor_for(journal: InstallationJournal) -> InstallationExecutor:
    executor = InstallationExecutor(Mock(), journal_path=journal.path)
    executor.load()
    return executor


def test_new_run_is_saved_open_with_its_identity_and_start_date(
    journal: InstallationJournal,
) -> None:
    run = saved_document(journal.path)["run"]

    assert saved_document(journal.path)["format"] == INSTALLATION_JOURNAL_FORMAT == 4
    assert run["run_id"] and run["closed_at"] is None and run["closure"] is None
    reloaded = InstallationJournal.load(journal.path)
    assert reloaded.active
    assert reloaded.lifecycle == journal.lifecycle
    assert reloaded.lifecycle.started_at.tzinfo is not None


def test_two_runs_have_distinct_identities(journal: InstallationJournal) -> None:
    other = InstallationJournal(journal.path, journal.plan, journal.sequences)

    assert other.lifecycle.run_id != journal.lifecycle.run_id


def test_played_run_stays_open_until_finished(
    journal: InstallationJournal, tmp_path: Path
) -> None:
    install_component(journal, tmp_path)
    executor = executor_for(journal)

    assert executor.verify_played().undetermined == []

    assert InstallationJournal.load(journal.path).active and executor.is_played


def test_finished_run_is_closed_as_finished_and_dated(
    journal: InstallationJournal, tmp_path: Path
) -> None:
    install_component(journal, tmp_path)
    executor = executor_for(journal)

    executor.finish()

    closed = InstallationJournal.load(journal.path)
    assert not closed.active and not executor.is_active
    assert closed.lifecycle.closure == RunClosure.FINISHED
    assert closed.lifecycle.closed_at >= closed.lifecycle.started_at
    assert closed.lifecycle.run_id == journal.lifecycle.run_id


def test_run_with_work_left_cannot_be_finished(journal: InstallationJournal) -> None:
    executor = executor_for(journal)

    executor.finish()

    assert not executor.is_played
    assert InstallationJournal.load(journal.path).active


def test_cancelled_run_is_closed_as_cancelled_and_keeps_its_operations(
    journal: InstallationJournal,
) -> None:
    executor = executor_for(journal)

    executor.cancel()

    closed = InstallationJournal.load(journal.path)
    assert closed.lifecycle.closure == RunClosure.CANCELLED
    assert closed.lifecycle.closed_at is not None
    assert closed.sequences[0].operations[0].reference == COMPONENT


def test_closed_run_is_not_closed_again(journal: InstallationJournal) -> None:
    executor = executor_for(journal)
    executor.cancel()
    first = InstallationJournal.load(journal.path).lifecycle

    executor.cancel()

    assert InstallationJournal.load(journal.path).lifecycle == first


def as_format_3(document: dict) -> dict:
    return {key: value for key, value in document.items() if key != "run"} | {
        "format": 3,
        "active": True,
    }


def as_unversioned_draft(document: dict) -> dict:
    return {"plan": document["plan"], "sequences": document["sequences"], "active": True}


@pytest.mark.parametrize("downgrade", [as_format_3, as_unversioned_draft])
def test_journal_of_an_earlier_format_is_unreadable_and_can_be_set_aside(
    journal: InstallationJournal, downgrade
) -> None:
    document = downgrade(saved_document(journal.path))
    journal.path.write_text(json.dumps(document), encoding="utf-8")
    executor = InstallationExecutor(Mock(), journal_path=journal.path)

    executor.load()

    assert executor.load_error and not executor.is_active
    kept = executor.set_aside_unreadable_journal()
    assert kept.exists() and not journal.path.exists()


@pytest.mark.parametrize(
    "run",
    [
        pytest.param(
            {"closed_at": "2026-11-23T10:00:00+01:00", "closure": None}, id="date-only"
        ),
        pytest.param({"closed_at": None, "closure": "finished"}, id="mode-only"),
        pytest.param(
            {"closed_at": "2026-11-23T10:00:00+01:00", "closure": "abandoned"},
            id="unknown-mode",
        ),
        pytest.param({"run_id": ""}, id="no-identity"),
        pytest.param({"started_at": "yesterday"}, id="invalid-date"),
    ],
)
def test_inconsistent_run_lifecycle_is_unreadable(
    journal: InstallationJournal, run: dict
) -> None:
    rewrite_run(journal.path, run)

    with pytest.raises(JournalError):
        InstallationJournal.load(journal.path)
