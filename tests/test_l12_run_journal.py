"""L12: format 4 of the installation journal, the closure of the run and its history archive."""

from dataclasses import asdict, fields
from datetime import UTC, datetime
import json
from pathlib import Path
import re
from unittest.mock import Mock
import zipfile

import pytest

from constants import (
    DIAGNOSTIC_PLAN_ENTRY,
    DIAGNOSTIC_SUMMARY_ENTRY,
    INSTALLATION_JOURNAL_FORMAT,
)
from core import InstallationJournal as journal_module
from core.InstallationExecutor import InstallationError, InstallationExecutor
from core.InstallationJournal import (
    InstallationJournal,
    JournalDecision,
    JournalError,
    Operation,
    OperationDecision,
    RunClosure,
    RunLifecycle,
    RunPlan,
    RunSequence,
)
from core.weidu_types import AttemptEvidence, ComponentStatus, InstallResult

COMPONENT = "demo:0"
HISTORY_NAME = re.compile(r"\d{8}-\d{6}-eet-(?P<outcome>[a-z]+)\.zip")


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


def archived_journal(executor: InstallationExecutor) -> InstallationJournal:
    """Journal of the only run archived in the history, rebuilt from its archive."""
    (archive,) = executor.history_dir.glob("*.zip")
    with zipfile.ZipFile(archive) as content:
        document = content.read(executor.journal_path.name)
    copy = executor.history_dir / executor.journal_path.name
    copy.write_bytes(document)
    try:
        return InstallationJournal.load(copy)
    finally:
        copy.unlink()


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

    closed = archived_journal(executor)
    assert not closed.active and not executor.is_active
    assert not journal.path.exists()
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

    closed = archived_journal(executor)
    assert closed.lifecycle.closure == RunClosure.CANCELLED
    assert closed.lifecycle.closed_at is not None
    assert closed.sequences[0].operations[0].reference == COMPONENT


def test_closed_run_is_not_closed_again(journal: InstallationJournal) -> None:
    executor = executor_for(journal)
    executor.cancel()
    first = archived_journal(executor).lifecycle

    assert executor.cancel() is None and executor.finish() is None

    assert archived_journal(executor).lifecycle == first


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


# ============================================================================
# Decisions and serialization
# ============================================================================


FAILED_COMPONENTS = ["demo:0", "demo:1", "demo:2"]


@pytest.fixture
def failed_journal(tmp_path: Path, journal_path: Path) -> InstallationJournal:
    """Saved journal whose three operations failed and wait for a decision."""
    plan = RunPlan(
        "eet",
        {"sod": str(tmp_path)},
        {0: FAILED_COMPONENTS},
        FAILED_COMPONENTS,
        "fr_FR",
        ["fr_FR"],
        True,
    )
    operations = [
        Operation(
            reference,
            "demo",
            0,
            0,
            [],
            [],
            [],
            "std",
            status=ComponentStatus.ERROR,
            evidence=AttemptEvidence(1, ComponentStatus.ERROR, in_log=False),
            messages=["broken"],
        )
        for reference in FAILED_COMPONENTS
    ]
    journal_path.parent.mkdir(parents=True)
    run = InstallationJournal(
        journal_path,
        plan,
        [RunSequence(str(tmp_path), RunSequence.identify(tmp_path), operations)],
    )
    run.save()
    return run


def journal_writes(monkeypatch: pytest.MonkeyPatch, *, fail: bool = False) -> list[Path]:
    """Paths written by the journal; with `fail`, every write fails as on a full disk."""
    written: list[Path] = []
    write = journal_module.write_json_atomic

    def tracked(path: Path, value: object) -> None:
        written.append(path)
        if fail:
            raise OSError("disk full")
        write(path, value)

    monkeypatch.setattr(journal_module, "write_json_atomic", tracked)
    return written


def decision_state(journal: InstallationJournal) -> list[tuple]:
    return [(op.status, op.decision, op.evidence) for op in journal.sequences[0].operations]


def test_decisions_of_several_operations_are_saved_in_a_single_write(
    failed_journal: InstallationJournal, monkeypatch: pytest.MonkeyPatch
) -> None:
    held = list(failed_journal.sequences[0].operations)
    written = journal_writes(monkeypatch)

    failed_journal.decide_many(
        [
            JournalDecision(0, 0, OperationDecision.ACCEPT_FAILURE),
            JournalDecision(0, 1, OperationDecision.RETRY),
            JournalDecision(0, 2, OperationDecision.CONFIRM_INSTALLED, in_log=True),
        ]
    )

    assert written == [failed_journal.path]
    assert failed_journal.sequences[0].operations == held
    assert all(
        current is kept
        for current, kept in zip(failed_journal.sequences[0].operations, held, strict=True)
    )
    assert decision_state(failed_journal) == [
        (ComponentStatus.ERROR, OperationDecision.ACCEPT_FAILURE, held[0].evidence),
        (ComponentStatus.PENDING, OperationDecision.RETRY, held[1].evidence),
        (
            ComponentStatus.SUCCESS,
            OperationDecision.CONFIRM_INSTALLED,
            AttemptEvidence(1, ComponentStatus.ERROR, in_log=True),
        ),
    ]
    assert decision_state(InstallationJournal.load(failed_journal.path)) == decision_state(
        failed_journal
    )


def test_decisions_of_one_operation_apply_in_order(
    failed_journal: InstallationJournal,
) -> None:
    failed_journal.decide_many(
        [
            JournalDecision(0, 0, OperationDecision.CONFIRM_INSTALLED, in_log=False),
            JournalDecision(0, 0, OperationDecision.RETRY),
        ]
    )

    operation = InstallationJournal.load(failed_journal.path).sequences[0].operations[0]
    assert operation.status == ComponentStatus.PENDING
    assert operation.decision == OperationDecision.RETRY
    assert operation.evidence == AttemptEvidence(1, ComponentStatus.ERROR, in_log=False)


def test_failed_decision_write_publishes_nothing(
    failed_journal: InstallationJournal, monkeypatch: pytest.MonkeyPatch
) -> None:
    saved = failed_journal.path.read_bytes()
    before = decision_state(failed_journal)
    journal_writes(monkeypatch, fail=True)

    with pytest.raises(OSError, match="disk full"):
        failed_journal.decide_many(
            JournalDecision(0, position, OperationDecision.RETRY) for position in range(3)
        )
    with pytest.raises(OSError, match="disk full"):
        failed_journal.decide(0, 0, OperationDecision.ACCEPT_FAILURE, in_log=False)

    assert decision_state(failed_journal) == before
    assert failed_journal.path.read_bytes() == saved


def test_no_decision_writes_nothing(
    failed_journal: InstallationJournal, monkeypatch: pytest.MonkeyPatch
) -> None:
    written = journal_writes(monkeypatch)

    failed_journal.decide_many([])

    assert written == []


def test_operation_is_serialized_field_by_field_like_its_dataclass() -> None:
    operation = Operation(
        "demo:0",
        "demo",
        2,
        3,
        ["A", "B"],
        ["--force"],
        ['{"type": "requires"}'],
        "std",
        version="v1",
        status=ComponentStatus.ERROR,
        decision=OperationDecision.ACCEPT_FAILURE,
        evidence=AttemptEvidence(1, ComponentStatus.ERROR, in_log=False),
        messages=["broken"],
        archive_owner="pack",
        archive_sha256="a" * 64,
    )
    expected = asdict(operation) | {
        "status": "error",
        "decision": "accept_failure",
        "evidence": operation.evidence.to_dict(),
    }

    serialized = operation.to_dict()

    assert list(serialized) == [field.name for field in fields(Operation)]
    assert json.dumps(serialized) == json.dumps(expected)
    assert Operation.from_dict(json.loads(json.dumps(serialized))) == operation


def test_plan_is_serialized_field_by_field_like_its_dataclass(
    failed_journal: InstallationJournal,
) -> None:
    plan = failed_journal.plan

    serialized = plan.to_dict()

    assert list(serialized) == [field.name for field in fields(RunPlan)]
    assert json.dumps(serialized) == json.dumps(asdict(plan))
    assert RunPlan.from_dict(json.loads(json.dumps(serialized))) == plan
    serialized["orders"][0].append("changed:0")
    assert plan.orders[0] == FAILED_COMPONENTS


def test_serialized_operation_does_not_share_its_lists(
    failed_journal: InstallationJournal,
) -> None:
    operation = failed_journal.sequences[0].operations[0]

    operation.to_dict()["messages"].append("changed")

    assert operation.messages == ["broken"]


# ============================================================================
# History archive of a closed run
# ============================================================================


def history(executor: InstallationExecutor) -> list[Path]:
    return sorted(executor.history_dir.glob("*.zip"))


def archive_entries(archive: Path) -> list[str]:
    with zipfile.ZipFile(archive) as content:
        return content.namelist()


def disk_full(monkeypatch: pytest.MonkeyPatch) -> None:
    def write(*args: object, **kwargs: object) -> None:
        raise OSError("No space left on device")

    monkeypatch.setattr(zipfile.ZipFile, "writestr", write)


def test_finished_run_is_archived_by_date_game_and_outcome(
    journal: InstallationJournal, tmp_path: Path
) -> None:
    install_component(journal, tmp_path)
    executor = executor_for(journal)

    archive = executor.finish()

    assert history(executor) == [archive]
    assert HISTORY_NAME.fullmatch(archive.name)["outcome"] == "complete"
    entries = archive_entries(archive)
    assert {journal.path.name, DIAGNOSTIC_SUMMARY_ENTRY, DIAGNOSTIC_PLAN_ENTRY} <= set(entries)
    assert archived_journal(executor).lifecycle.closure == RunClosure.FINISHED


def test_cancelled_run_is_archived_as_cancelled(journal: InstallationJournal) -> None:
    executor = executor_for(journal)

    archive = executor.cancel()

    assert HISTORY_NAME.fullmatch(archive.name)["outcome"] == "cancelled"


def test_closing_empties_the_current_run_and_archives_the_set_aside_journals(
    journal: InstallationJournal,
) -> None:
    set_aside = journal.path.with_name(f"{journal.path.name}.unreadable-20261101-120000")
    set_aside.write_text("{unreadable", encoding="utf-8")
    executor = executor_for(journal)

    archive = executor.cancel()

    assert set_aside.name in archive_entries(archive)
    assert list(journal.path.parent.iterdir()) == []
    assert not executor.is_active and not executor.has_current_run


def test_archive_of_a_same_second_closure_does_not_replace_the_previous_one(
    journal: InstallationJournal, monkeypatch: pytest.MonkeyPatch
) -> None:
    closed_at = datetime(2026, 11, 23, 22, 0, tzinfo=UTC)
    monkeypatch.setattr(RunLifecycle, "_now", staticmethod(lambda: closed_at))
    executor = executor_for(journal)
    first = executor.cancel()
    journal.save()
    executor.load()

    second = executor.cancel()

    assert second != first and second.name.endswith("-2.zip")
    assert set(history(executor)) == {first, second}


# ============================================================================
# Failed archive
# ============================================================================


def test_failed_archive_leaves_the_current_run_intact(
    journal: InstallationJournal, monkeypatch: pytest.MonkeyPatch
) -> None:
    saved = journal.path.read_bytes()
    executor = executor_for(journal)
    disk_full(monkeypatch)

    with pytest.raises(InstallationError, match="No space left on device"):
        executor.cancel()

    assert journal.path.read_bytes() == saved
    assert executor.is_active and InstallationJournal.load(journal.path).active
    assert list(executor.history_dir.iterdir()) == []


def test_failed_archive_can_be_retried(
    journal: InstallationJournal, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor = executor_for(journal)
    disk_full(monkeypatch)
    with pytest.raises(InstallationError):
        executor.cancel()

    monkeypatch.undo()
    archive = executor.cancel()

    assert history(executor) == [archive] and not journal.path.exists()
