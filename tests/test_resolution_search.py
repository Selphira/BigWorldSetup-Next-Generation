"""Resolution search off the interface thread: frozen request, cancellation, catalogue pool."""

from collections.abc import Callable
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pytestqt.qtbot import QtBot

from core.ComponentReference import ComponentReference, IndexManager
from core.ModManager import ModManager
from core.RuleManager import RuleManager
from core.Rules import DependencyRule, IncompatibilityRule
from core.SelectionCascade import SelectionCascade, SelectionConstraints
from core.ValidationOrchestrator import (
    ResolutionCancelled,
    ResolutionRequest,
    ValidationOrchestrator,
)
from tests.conftest import ModCatalogue
from ui.pages.mod_selection.SelectionController import SelectionController

SEARCH_TIMEOUT_S = 5


def refs(*values: str) -> list[ComponentReference]:
    return [ComponentReference.from_string(value) for value in values]


def register(catalogue: ModCatalogue, *references: ComponentReference) -> None:
    indexes = IndexManager.get_indexes()
    for reference in references:
        mod = catalogue.get_mod_by_id(reference.mod_id)
        indexes.register_component(mod.get_component(reference.comp_key))


@pytest.fixture
def keep_a(rules: RuleManager, catalogue: ModCatalogue) -> ValidationOrchestrator:
    """``a:0`` needs ``b:0`` or ``demo:1``; ``b:0`` conflicts with the selected ``demo:2``."""
    a, b, c, d = refs("a:0", "b:0", "demo:1", "demo:2")
    register(catalogue, a, b, c, d)
    rules._all_rules = [
        DependencyRule.from_dict({"source": str(a), "target": [str(b), str(c)]}),
        IncompatibilityRule.from_dict({"source": str(b), "target": str(d)}),
    ]
    rules.rebuild_indexes()
    controller = SelectionController(rules)
    controller.select_bulk([a, d])
    return ValidationOrchestrator(rules, controller)


# ========================================
# CORE SEARCH
# ========================================


def test_cancelled_search_is_distinct_from_a_missing_resolution(
    keep_a: ValidationOrchestrator, rules: RuleManager, catalogue: ModCatalogue
) -> None:
    (a,) = refs("a:0")
    request = keep_a.prepare_resolution(a)

    with pytest.raises(ResolutionCancelled):
        keep_a.search_resolution(request, lambda: True)
    assert keep_a.search_resolution(request) is not None

    rules._all_rules = [DependencyRule.from_dict({"source": str(a), "target": "gone:0"})]
    rules.rebuild_indexes()
    assert keep_a.search_resolution(keep_a.prepare_resolution(a)) is None


def test_search_keeps_the_snapshot_and_a_changed_selection_refuses_it(
    keep_a: ValidationOrchestrator,
) -> None:
    a, c, d = refs("a:0", "demo:1", "demo:2")
    source = keep_a._selection_source
    request = keep_a.prepare_resolution(a)

    source.unselect(d)
    proposal = keep_a.search_resolution(request)

    assert proposal.original == {a, d}
    assert proposal.selection == {a, c, d}
    assert not keep_a.apply_resolution(proposal)
    assert source.selection_snapshot() == {a}


def test_search_reads_no_selection_source(keep_a: ValidationOrchestrator) -> None:
    (a,) = refs("a:0")
    request = keep_a.prepare_resolution(a)
    keep_a._selection_source = Mock(side_effect=AssertionError("read during the search"))

    proposal = keep_a.search_resolution(request)

    assert proposal is not None and proposal.selection == set(refs("a:0", "demo:1", "demo:2"))


def test_search_runs_on_another_thread(keep_a: ValidationOrchestrator) -> None:
    (a,) = refs("a:0")
    expected = keep_a.auto_resolve(a)
    request = keep_a.prepare_resolution(a)
    found = []

    worker = threading.Thread(target=lambda: found.append(keep_a.search_resolution(request)))
    worker.start()
    worker.join(SEARCH_TIMEOUT_S)

    assert found == [expected]


# ========================================
# CATALOGUE POOL
# ========================================


def test_component_pool_is_rebuilt_only_after_a_catalogue_change(
    catalogue: ModCatalogue,
) -> None:
    indexes = IndexManager.get_indexes()
    ModManager._register_components(
        {
            "id": "pool",
            "name": "Pool",
            "links": {},
            "components": {
                "0": {"type": "std"},
                "choice_0": {"type": "muc", "components": ["10", "11"]},
                "1": {"type": "sub", "prompts": {"1": {"options": ["A"], "default": "A"}}},
            },
        },
        {},
        indexes,
    )
    pool = indexes.component_pool()

    assert indexes.component_pool() is pool
    assert set(pool.components_of("pool")) == set(
        refs("pool:0", "pool:10", "pool:11", "pool:1")
    )
    assert all(
        reference in pool for reference in refs("pool:choice_0", "pool:1.1", "pool:1.1.A")
    )

    register(catalogue, *refs("a:0"))
    assert indexes.component_pool() is not pool
    assert refs("a:0")[0] in indexes.component_pool()

    indexes.remove_mod("pool")
    assert not indexes.component_pool().components_of("pool")


# ========================================
# CASCADES WITHOUT TREE
# ========================================


def test_cascade_selects_the_declared_defaults_without_tree_items() -> None:
    indexes = IndexManager.get_indexes()
    ModManager._register_components(
        {
            "id": "defaults",
            "name": "Defaults",
            "links": {},
            "components": {
                "choice_0": {"type": "muc", "components": ["10", "11"], "default": "11"},
                "1": {
                    "type": "sub",
                    "prompts": {
                        "1": {"options": ["A", "B"], "default": "B"},
                        "2": {"options": ["A", "B"]},
                    },
                },
            },
        },
        {},
        indexes,
    )
    assert not indexes.tree_item_index
    changes: list[tuple[ComponentReference, bool]] = []
    cascade = SelectionCascade(
        indexes,
        set(),
        SelectionConstraints(),
        lambda ref, selected: changes.append((ref, selected)),
    )

    cascade.select(refs("defaults:choice_0")[0])
    cascade.select(refs("defaults:1")[0])

    assert cascade.selection >= set(refs("defaults:11", "defaults:1.1.B", "defaults:1.2.A"))
    assert not cascade.selection & set(refs("defaults:10", "defaults:1.1.A", "defaults:1.2.B"))
    assert {ref for ref, selected in changes if selected} == cascade.selection


# ========================================
# INTERFACE
# ========================================


def open_keep_action(
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
    search: Callable[[ResolutionRequest, Callable[[], bool]], object],
) -> tuple[Mock, list[str]]:
    """Trigger "keep this component" with a scripted search; return the orchestrator and messages."""
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QMenu

    from core.TranslationManager import tr
    from ui.pages.mod_selection import ComponentContextMenu as menu_module

    (kept,) = refs("a:0")
    IndexManager.get_indexes().select(kept)
    orchestrator = Mock()
    orchestrator.search_resolution.side_effect = search
    messages: list[str] = []
    monkeypatch.setattr(
        menu_module.QMessageBox,
        "information",
        lambda parent, title, text: messages.append(text),
    )
    monkeypatch.setattr(
        menu_module,
        "ResolutionPreviewDialog",
        Mock(side_effect=AssertionError("no proposal to preview")),
    )

    class KeepMenu(QMenu):
        def exec(self, position: QPoint) -> None:
            keep = tr("page.selection.violation.keep_this_component")
            next(action for action in self.actions() if action.text() == keep).trigger()

    monkeypatch.setattr(menu_module, "QMenu", KeepMenu)
    violation = SimpleNamespace(
        rule=DependencyRule.from_dict({"source": "a:0", "target": "b:0"}),
        affected_components=tuple(refs("a:0", "b:0")),
    )
    menu_module.ComponentContextMenu(Mock(), orchestrator).show_menu(
        SimpleNamespace(reference=kept), QPoint(), specific_violation=violation
    )
    return orchestrator, messages


def test_cancelling_the_search_reports_nothing_and_changes_nothing(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from ui.pages.mod_selection.ResolutionSearchDialog import ResolutionSearchDialog

    started = threading.Event()

    def cancel_once_started() -> None:
        assert started.wait(SEARCH_TIMEOUT_S)
        dialog = next(
            widget
            for widget in QApplication.topLevelWidgets()
            if isinstance(widget, ResolutionSearchDialog)
        )
        dialog.canceled.emit()

    def search_until_cancelled(request: ResolutionRequest, is_cancelled: Callable[[], bool]):
        started.set()
        cancelled = threading.Event()
        while not cancelled.wait(0.01):
            if is_cancelled():
                cancelled.set()
        raise ResolutionCancelled()

    QTimer.singleShot(0, cancel_once_started)
    orchestrator, messages = open_keep_action(qtbot, monkeypatch, search_until_cancelled)

    assert not messages
    orchestrator.apply_resolution.assert_not_called()


def test_search_without_solution_is_reported(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core.TranslationManager import tr

    orchestrator, messages = open_keep_action(qtbot, monkeypatch, lambda request, cancel: None)

    assert messages == [tr("page.selection.no_resolution")]
    orchestrator.apply_resolution.assert_not_called()


def test_search_failure_reaches_the_interface_thread(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing(request: ResolutionRequest, is_cancelled: Callable[[], bool]) -> None:
        raise RuntimeError("search failed")

    # Raised in the menu slot, as the search raised it before the worker
    with qtbot.capture_exceptions() as exceptions:
        orchestrator, messages = open_keep_action(qtbot, monkeypatch, failing)

    assert [str(error) for _, error, _ in exceptions] == ["search failed"]
    assert not messages
    orchestrator.apply_resolution.assert_not_called()
