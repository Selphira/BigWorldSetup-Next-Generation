"""Selection, installation and import contracts across catalogue revisions."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pytestqt.qtbot import QtBot

from core.ComponentReference import ComponentReference, IndexManager
from core.InstallationJournal import InstallationJournal, Operation, RunPlan, RunSequence
from core.InstallationWorker import InstallationWorker, UserDecision
from core.Mod import Mod, SubComponent
from core.RuleManager import RuleManager
from core.Rules import DependencyRule, IncompatibilityRule, OrderRule
from core.SelectionImport import SelectionImport
from core.StateManager import StateManager
from core.ValidationOrchestrator import ValidationOrchestrator
from core.weidu_types import ComponentInfo, ComponentStatus, InstallResult
from tests.conftest import ModCatalogue
from ui.pages.InstallOrder import InstallOrderPage, SequenceData, ValidationResult
from ui.pages.mod_selection.SelectionController import SelectionController


def refs(*values: str) -> list[ComponentReference]:
    return [ComponentReference.from_string(value) for value in values]


@pytest.mark.parametrize("listed", [[], ["b:0", "a:0"]])
def test_default_order_leaves_unlisted_components_on_the_right(
    qtbot: QtBot, rules: RuleManager, listed: list[str]
) -> None:
    state = Mock()
    state.get_rule_manager.return_value = rules
    state.get_selected_components.return_value = ["a:0", "b:0", "demo:1"]
    page = InstallOrderPage(state)
    qtbot.addWidget(page)
    page._game_def = SimpleNamespace(sequences=[SimpleNamespace(order=listed)])
    sequence = SequenceData(refs("demo:1"), refs("a:0", "b:0"), ValidationResult())
    page._sequences_data = {0: sequence}

    page._load_default_order()

    assert sequence.ordered == refs(*listed)
    assert set(sequence.unordered) == set(refs("demo:1", "a:0", "b:0")) - set(refs(*listed))


@pytest.mark.parametrize("apply_changes", [False, True])
def test_keep_preview_lists_named_components_without_wildcard(
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
    apply_changes: bool,
) -> None:
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtWidgets import QMenu, QPushButton, QTableWidget

    from core.TranslationManager import tr
    from core.ValidationOrchestrator import ResolutionProposal
    from ui.pages.mod_selection.ComponentContextMenu import ComponentContextMenu
    from ui.pages.mod_selection.ResolutionPreviewDialog import ResolutionPreviewDialog

    mod = Mod(
        {
            "id": "leui",
            "name": "LeUI Enhanced",
            "links": {},
            "components": {key: {"type": "std"} for key in ("0", "1", "2")},
            "translations": {
                "components": {"0": "Classic menus", "1": "New interface", "2": "Maps & icons"}
            },
        }
    )
    indexes = IndexManager.get_indexes()
    indexes.register_mod(mod)
    for component in mod.get_components():
        indexes.register_component(component)
    kept, parent, first, second = refs("leui:1", "leui:*", "leui:0", "leui:2")
    indexes.select(kept)
    orchestrator = Mock()
    orchestrator.auto_resolve.return_value = ResolutionProposal(
        kept, frozenset({kept, parent, first}), frozenset({kept, second})
    )

    def review(dialog: ResolutionPreviewDialog) -> int:
        qtbot.addWidget(dialog)
        dialog.show()
        assert dialog.windowTitle() == tr(
            "page.selection.resolution_title", component="New interface"
        )
        assert "New interface" in dialog.windowTitle()
        table = dialog.findChild(QTableWidget)
        assert table.rowCount() == 2 and table.columnCount() == 3
        assert table.item(0, 0).text() == table.item(1, 0).text() == "LeUI Enhanced"
        assert table.item(0, 1).text() == "Classic menus"
        assert table.item(1, 1).text() == "Maps & icons"
        assert table.item(0, 2).toolTip() == tr("page.selection.resolution_remove")
        assert table.item(1, 2).toolTip() == tr("page.selection.resolution_add")
        assert table.item(0, 2).text() != table.item(1, 2).text()
        assert not table.verticalScrollBar().isVisible()
        buttons = {button.text(): button for button in dialog.findChildren(QPushButton)}
        apply, cancel = buttons[tr("button.apply")], buttons[tr("button.cancel")]
        assert apply.geometry().left() > cancel.geometry().right()
        qtbot.mouseClick(apply if apply_changes else cancel, Qt.MouseButton.LeftButton)
        return dialog.result()

    monkeypatch.setattr(ResolutionPreviewDialog, "exec", review)

    def choose_keep(menu: QMenu, position: QPoint) -> None:
        actions = [
            a
            for a in menu.actions()
            if a.text() == tr("page.selection.violation.keep_this_component")
        ]
        assert len(actions) == 1
        actions[0].trigger()

    class TestMenu(QMenu):
        exec = choose_keep

    monkeypatch.setattr("ui.pages.mod_selection.ComponentContextMenu.QMenu", TestMenu)
    rule_type = DependencyRule if apply_changes else IncompatibilityRule
    violation = SimpleNamespace(
        rule=rule_type.from_dict({"source": str(kept), "target": str(second)}),
        affected_components=(kept, second),
    )
    ComponentContextMenu(Mock(), orchestrator).show_menu(
        SimpleNamespace(reference=kept), QPoint(), specific_violation=violation
    )
    orchestrator.auto_resolve.assert_called_once_with(kept)
    if apply_changes:
        orchestrator.apply_resolution.assert_called_once_with(
            orchestrator.auto_resolve.return_value
        )
    else:
        orchestrator.apply_resolution.assert_not_called()


def test_long_keep_preview_scrolls_to_last_component(qtbot: QtBot) -> None:
    from PySide6.QtWidgets import QPushButton, QTableWidget

    from ui.pages.mod_selection.ResolutionPreviewDialog import ResolutionPreviewDialog

    changes = [("LeUI Enhanced", f"Component {i}", False) for i in range(100)]
    dialog = ResolutionPreviewDialog(changes, "Classic menus")
    qtbot.addWidget(dialog)
    dialog.show()
    table = dialog.findChild(QTableWidget)
    qtbot.waitUntil(table.verticalScrollBar().isVisible)
    table.scrollToBottom()
    assert table.viewport().rect().contains(table.visualItemRect(table.item(99, 1)).center())
    assert all(button.isVisible() for button in dialog.findChildren(QPushButton))


def test_load_selection_without_catalogue_revision(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    catalogue: ModCatalogue,
    rules: RuleManager,
) -> None:
    from ui.pages.ModSelection import ModSelectionPage

    monkeypatch.setattr(StateManager, "STATE_FILE", tmp_path / "state.json")
    state = StateManager()
    monkeypatch.setattr(state, "get_mod_manager", lambda: catalogue)
    monkeypatch.setattr(state, "get_rule_manager", lambda: rules)
    IndexManager.get_indexes().register_component(
        catalogue.get_mod_by_id("a").get_component("0")
    )
    state.set_selected_components(["a:0"])
    page = ModSelectionPage(state)
    qtbot.addWidget(page)
    report = Mock()
    monkeypatch.setattr(page, "_show_import_report", report)

    page.load_state()

    assert page.export_selection() == ["a:0"]
    report.assert_not_called()


@pytest.mark.parametrize(
    "saved_state",
    [None, {"configuration": {"selected_components": {}}}],
    ids=["first launch", "state saved before any selection by an earlier version"],
)
def test_selection_page_loads_without_any_saved_selection(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    catalogue: ModCatalogue,
    rules: RuleManager,
    saved_state: dict | None,
) -> None:
    from ui.pages.ModSelection import ModSelectionPage

    state_file = tmp_path / "state.json"
    if saved_state is not None:
        state_file.write_text(json.dumps(saved_state), encoding="utf-8")
    monkeypatch.setattr(StateManager, "STATE_FILE", state_file)
    state = StateManager()
    monkeypatch.setattr(state, "get_mod_manager", lambda: catalogue)
    monkeypatch.setattr(state, "get_rule_manager", lambda: rules)
    page = ModSelectionPage(state)
    qtbot.addWidget(page)

    page.load_state()

    assert page.export_selection() == []


def test_b05_failure_propagates_only_until_an_available_alternative(rules: RuleManager) -> None:
    a, b, c, d = refs("a:0", "b:0", "demo:1", "demo:2")
    rules._all_rules = [
        DependencyRule.from_dict({"source": "b:0", "target": ["a:*", "demo:2"]}),
        DependencyRule.from_dict({"source": "demo:1", "target": "b:0"}),
    ]
    rules._build_indexes()
    selection = {a, b, c, d}
    assert rules.blocked_positions([[a], [b, c, d]], {(0, 0)}, (0, 0), selection) == {
        (1, 0),
        (1, 1),
    }
    assert not rules.blocked_positions([[a, d], [b, c]], {(0, 0)}, (0, 1), selection)
    assert not rules.blocked_positions([[a], [a, b, c]], {(0, 0)}, (0, 0), selection)


def test_b06_group_activation_absent_wildcard_and_cycle(rules: RuleManager) -> None:
    a, b, c, d = refs("a:0", "b:0", "demo:1", "demo:2")
    grouped = DependencyRule.from_dict(
        {
            "source_groups": [["a:0"], ["b:0"]],
            "target_groups": [["demo:1", "demo:2"], ["unknown:*"]],
        }
    )
    rules._all_rules = [grouped]
    rules._build_indexes()
    assert not rules.selection_violations({a, c})
    violations = rules.selection_violations({a, b, c, d})
    assert {v.affected_components for v in violations} == {
        (a, *refs("unknown:*")),
        (b, *refs("unknown:*")),
    }
    rules._all_rules = [
        DependencyRule.from_dict({"source": str(source), "target": str(target)})
        for source, target in [(a, b), (b, a)]
    ]
    rules._build_indexes()
    suggestion = rules.preferred_order([a, b], set(), {a, b})
    assert suggestion.cycle in ((a, b, a), (b, a, b))
    assert rules.validate_order("eet", 0, suggestion.components)


def test_b06_order_accepts_any_preceding_provider(rules: RuleManager) -> None:
    a, b, c = refs("a:0", "b:0", "demo:1")
    rules._all_rules = [
        DependencyRule.from_dict({"source": "a:0", "target": ["b:*", "demo:1"]})
    ]
    rules._build_indexes()
    assert rules.preferred_order([a, c, b], set(), {a, b, c}).components == [c, a, b]
    assert not rules.validate_order("eet", 1, [a, b], earlier={c})
    assert rules.validate_order("eet", 1, [a, b])


def test_b07_ten_declared_questions_and_invalid_answers() -> None:
    prompts = {str(i): {"options": [str(i), "x"]} for i in range(1, 11)}
    mod = Mod(
        {"id": "demo", "links": {}, "components": {"0": {"type": "sub", "prompts": prompts}}}
    )
    component = mod.get_component("0")
    assert isinstance(component, SubComponent)
    selected = {f"0.{key}.{key}" for key in prompts}
    assert component.ordered_answers(selected) == list(prompts)
    assert mod.get_component("0.10.10")
    assert mod.get_component("0.10.invalid") is None
    for invalid in [selected - {"0.2.2"}, selected | {"0.2.x"}]:
        with pytest.raises(ValueError, match="demo:0.2"):
            component.ordered_answers(invalid)


def test_b21_preview_preserves_existing_choice_and_checks_staleness(
    rules: RuleManager,
    catalogue: ModCatalogue,
) -> None:
    a, b, c, d = refs("a:0", "b:0", "demo:1", "demo:2")
    indexes = IndexManager.get_indexes()
    for ref in (a, b, c, d):
        indexes.register_component(
            catalogue.get_mod_by_id(ref.mod_id).get_component(ref.comp_key)
        )
    rules._all_rules = [
        DependencyRule.from_dict({"source": str(a), "target": [str(b), str(c)]}),
        IncompatibilityRule.from_dict({"source": str(b), "target": str(d)}),
    ]
    rules._build_indexes()
    controller = SelectionController(rules)
    controller.select_bulk([a, d])
    orchestrator = ValidationOrchestrator(rules, controller)
    proposal = orchestrator.auto_resolve(a)
    assert proposal and proposal.selection == {a, c, d}
    assert indexes.selection_index == {a, d}
    assert orchestrator.apply_resolution(proposal)
    assert not orchestrator.validate_current_selection()
    assert not orchestrator.apply_resolution(proposal)


def test_b23_import_reports_missing_and_exclusive_choices(catalogue: ModCatalogue) -> None:
    indexes = IndexManager.get_indexes()
    mod = catalogue.get_mod_by_id("demo")
    parent = indexes.register_component(mod.get_component("choice_0"))
    choices = refs("demo:10", "demo:11")
    indexes.register_parent_child(parent, choices)
    indexes.register_component(mod.get_component("1"))
    report = SelectionImport.reconcile(["DEMO:1", "gone:1", "demo:10", "demo:11"], indexes)
    assert report.missing == {"gone:1"}
    assert report.ambiguous == {"demo:10", "demo:11"}
    assert report.kept == set(refs("demo:1"))
    assert report.remapped == {"DEMO:1": refs("demo:1")[0]}


def test_exported_selection_keeps_unresolved_references_and_revision(
    tmp_path: Path, catalogue: ModCatalogue
) -> None:
    indexes = IndexManager.get_indexes()
    indexes.register_component(catalogue.get_mod_by_id("demo").get_component("1"))
    destination = tmp_path / "bgee_selection.json"

    SelectionImport.export(destination, ["demo:1", "gone:1"], indexes)

    exported = json.loads(destination.read_text(encoding="utf-8"))
    assert exported == {
        "catalogue_revision": SelectionImport.catalogue_revision(indexes),
        "components": ["demo:1", "gone:1"],
    }
    assert SelectionImport.reconcile(exported["components"], indexes).missing == {"gone:1"}


def test_b23_load_order_with_removed_mod(
    catalogue: ModCatalogue, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = SimpleNamespace(
        state_manager=Mock(),
        _mod_manager=catalogue,
        _game_def=SimpleNamespace(sequence_count=1),
        _sequences_data={},
        _refresh_all_tables=Mock(),
        _place_component_in_sequences=Mock(return_value=True),
    )
    page.state_manager.get_selected_components.return_value = ["gone:0", "a:0"]
    monkeypatch.setattr("ui.pages.InstallOrder.QMessageBox.warning", Mock())
    InstallOrderPage._load_components(page)
    assert page._missing_selection == ["gone:0"]
    page._place_component_in_sequences.assert_called_once_with(refs("a:0")[0])


def test_accepted_failure_blocks_dependent_before_next_batch(
    rules: RuleManager,
    catalogue: ModCatalogue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    a, b = refs("a:0", "b:0")
    rules._all_rules = [DependencyRule.from_dict({"source": str(b), "target": str(a)})]
    rules._build_indexes()
    batches = []
    for idx, ref in enumerate((a, b)):
        mod = catalogue.get_mod_by_id(ref.mod_id)
        batches.append([ComponentInfo(str(ref), mod, mod.get_component("0"), mod.tp2, idx)])
    operations = [
        Operation(str(ref), ref.mod_id, 0, idx, [], [], [], "std")
        for idx, ref in enumerate((a, b))
    ]
    plan = RunPlan("eet", {}, {}, [str(a), str(b)], "en_US", [], True)
    journal = InstallationJournal(Path("run.json"), plan, [RunSequence(".", "", operations)])
    worker = InstallationWorker(
        Mock(), batches, pause_on_error=False, journal=journal, rules=rules
    )
    failed = InstallResult(ComponentStatus.ERROR, 1, "", "", [], [])
    install = Mock(return_value={str(a): failed})
    monkeypatch.setattr(worker, "_install_batch", install)
    previews = []

    def accept(_reference: str, messages: list[str]) -> None:
        previews.append(messages)
        worker.set_user_decision(UserDecision.SKIP)

    # Even without pause on error, the blocked dependents are shown before applying.
    worker.error_occurred.connect(accept)
    worker.run()
    install.assert_called_once_with(batches[0])
    assert any(str(b) in message for message in previews[0])
    assert journal.sequences[0].operations[1].status == ComponentStatus.BLOCKED


def test_sub_option_can_be_a_source_or_a_provider(rules: RuleManager) -> None:
    a, base, answer, b = refs("a:0", "demo:1", "demo:1.1.A", "b:0")
    rules._all_rules = [
        DependencyRule.from_dict({"source": str(answer), "target": str(a)}),
        DependencyRule.from_dict({"source": str(b), "target": str(answer)}),
    ]
    rules._build_indexes()
    selection = {a, base, answer, b}
    assert rules.preferred_order([b, base, a], set(), selection).components == [a, base, b]
    assert not rules.validate_order("eet", 1, [b], earlier={a, base}, selection=selection)
    assert rules.blocked_positions([[a, base], [b]], {(0, 0)}, (0, 0), selection) == {
        (0, 1),
        (1, 0),
    }


def test_import_maps_only_unique_tp2_aliases() -> None:
    indexes = IndexManager.get_indexes()
    for name in ("first", "second"):
        mod = Mod(
            {"id": name, "links": {}, "tp2": "shared", "components": {"0": {"type": "std"}}}
        )
        indexes.register_mod(mod)
        indexes.register_component(mod.get_component("0"))
        report = SelectionImport.reconcile(["setup-shared.tp2:0"], indexes)
        if name == "first":
            assert report.remapped == {"setup-shared.tp2:0": refs("first:0")[0]}
        else:
            assert report.ambiguous == {"setup-shared.tp2:0"}
            assert not report.kept


def test_explicit_order_across_sequences(rules: RuleManager) -> None:
    a, b = refs("a:0", "b:0")
    rules._all_rules = [
        OrderRule.from_dict({"source": str(a), "target": str(b), "direction": "before"})
    ]
    rules._build_indexes()
    assert not rules.validate_order("eet", 0, [a], later={b})
    assert rules.validate_order("eet", 1, [a], earlier={b})


def test_imported_sub_without_answer_stays_unanswered(
    catalogue: ModCatalogue, rules: RuleManager
) -> None:
    from ui.pages.ModSelection import ModSelectionPage

    indexes = IndexManager.get_indexes()
    mod = catalogue.get_mod_by_id("demo")
    base = indexes.register_component(mod.get_component("1"))
    prompt, first, second = refs("demo:1.1", "demo:1.1.A", "demo:1.1.B")
    indexes.register_parent_child(base, [prompt])
    indexes.register_parent_child(prompt, [first, second])
    controller = SelectionController(rules)
    page = SimpleNamespace(
        _selection_controller=controller,
        _unresolved_selection=set(),
        _validation_orchestrator=Mock(),
        _trigger_validation=Mock(),
    )
    report = ModSelectionPage.import_selection(page, ["demo:1", "removed:0"], replace=True)
    assert report.unanswered == {prompt}
    assert ModSelectionPage.export_selection(page) == ["demo:1", "removed:0"]
    report = ModSelectionPage.import_selection(page, ["demo:1", "demo:1.1.B"], replace=True)
    assert not report.unanswered
    assert ModSelectionPage.export_selection(page) == ["demo:1", "demo:1.1.B"]


def test_mod_wildcard_is_symmetric_for_matching_but_not_for_covering() -> None:
    mod, component, other = refs("a:*", "a:0", "a:1")

    assert mod.matches(component) and component.matches(mod)
    assert not component.matches(other)

    assert mod.covers(component)
    assert not component.covers(mod)

    assert mod.is_selected_in({component})
    assert not component.is_selected_in({mod})
