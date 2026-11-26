"""Acceptance contracts for the thirteen reproduced audit defects.

Known defects are strict xfails restricted to their specific failure type.
Remove the marker when the owning delivery lot fixes the behavior.
"""

from pathlib import Path
from unittest.mock import Mock

from PySide6.QtCore import Qt
import pytest
from pytestqt.qtbot import QtBot

from core.ArchiveExtractor import ArchiveExtractor, ExtractionInfo
from core.BackupManager import BackupManager
from core.ComponentReference import ComponentReference, IndexManager
from core.DirectoryMerger import MergeResult
from core.InstallationExecutor import InstallationExecutor
from core.Mod import Mod
from core.RuleManager import RuleManager
from core.Rules import DependencyRule, OrderRule
from core.validators.StructureValidator import StructureValidator
from core.weidu_types import ComponentStatus
from core.WeiDUDebugParser import WeiDUDebugParser
from tests.conftest import ModCatalogue
from ui.pages.DownloadPage import DownloadPage
from ui.pages.ExtractionPage import ExtractionWorker
from ui.pages.mod_selection.ComponentSelector import ComponentSelector
from ui.pages.mod_selection.SelectionController import SelectionController
from util.mod_sync.mergers.definition_merger import DefinitionMerger
from util.mod_sync.processors.github_processor import GitHubProcessor


def test_b06_dependency_target_does_not_require_itself(rules: RuleManager) -> None:
    rule = DependencyRule.from_dict({"source": ["a:0"], "target": ["b:0"]})
    rules._all_rules = [rule]
    rules.rebuild_indexes()
    assert rules.get_requirements(ComponentReference("a", "0")) == (rule,)
    assert not rules.get_requirements(ComponentReference("b", "0"))


def test_b07_sub_answers_belong_to_their_component(catalogue: ModCatalogue) -> None:
    state = Mock()
    state.get_mod_manager.return_value = catalogue
    state.get_rule_manager.return_value.active_requirements.return_value = set()
    executor = InstallationExecutor(state, journal_path=Path("run.json"))
    components = executor.resolve_components(
        ["demo:1", "demo:2"], ["demo:1.1.A", "demo:2.1.B"], {}
    )
    assert [item.subcomponent_answers for item in components] == [["A"], ["B"]]


def test_b08_unfinished_debug_is_not_success(tmp_path: Path) -> None:
    debug = tmp_path / "setup-demo.DEBUG"
    debug.write_text("Installing [A]\n", encoding="utf-8")
    assert ComponentStatus.SUCCESS not in WeiDUDebugParser.parse(debug).values()


def test_b09_failed_merge_does_not_emit_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    qtbot: QtBot,
) -> None:
    extraction = ExtractionInfo("demo", "demo", "Demo", "demo", tmp_path / "a.zip", tmp_path)
    monkeypatch.setattr(ArchiveExtractor, "extract_archive", Mock(return_value=True))
    monkeypatch.setattr(StructureValidator, "normalize_structure", Mock(return_value=True))
    worker = ExtractionWorker([extraction])
    result = MergeResult()
    result.errors.append((tmp_path / "locked", "Permission denied"))
    monkeypatch.setattr(worker._merger, "merge_directories", Mock(return_value=result))
    completed, errors = [], []
    worker.extraction_completed.connect(completed.append)
    worker.extraction_error.connect(lambda *args: errors.append(args))
    worker.run()
    assert completed == [] and errors


def test_b11_cross_volume_directory_transfer(tmp_path: Path) -> None:
    source, destination = tmp_path / "source", tmp_path / "destination"
    source.mkdir()
    (source / "épreuve.txt").write_bytes(b"original")
    BackupManager._transfer(None, source, destination, move=False)
    assert (destination / "épreuve.txt").read_bytes() == b"original"


def test_b13_manual_archive_remains_visible() -> None:
    mod = Mod({"id": "manual", "links": {}, "files": [{"filename": "manual.zip"}]})
    archive = DownloadPage._get_archive_info_from_mod(mod)
    assert archive.filename == "manual.zip" and archive.url is None


@pytest.mark.xfail(
    reason="B19 / L6: bulk signal restores deselected MUC option", raises=AssertionError
)
def test_b19_bulk_selection_matches_visible_checkboxes(
    catalogue: ModCatalogue,
    rules: RuleManager,
    qtbot: QtBot,
) -> None:
    controller = SelectionController(rules)
    indexes = IndexManager.get_indexes()
    mod = catalogue.get_mod_by_id("demo")
    parent = mod.get_component("choice_0")
    mod_ref = indexes.register_mod(mod)
    parent_ref = indexes.register_component(parent)
    options = [indexes.register_component(option) for option in parent.components.values()]
    indexes.register_parent_child(mod_ref, [parent_ref])
    indexes.register_parent_child(parent_ref, options)
    widget = ComponentSelector(catalogue, controller)
    qtbot.addWidget(widget)
    refs = [ComponentReference("demo", key) for key in ("10", "11")]
    controller.select_bulk(refs)
    assert sum(indexes.is_selected(ref) for ref in refs) == 1
    for ref in refs:
        item = indexes.get_tree_item(ref)
        assert (item.checkState() == Qt.CheckState.Checked) == indexes.is_selected(ref)


def test_b20_corrected_order_clears_index(rules: RuleManager) -> None:
    rules._all_rules = [
        OrderRule.from_dict(
            {
                "source": ["a:0"],
                "target": ["b:0"],
                "direction": "before",
            }
        )
    ]
    rules.rebuild_indexes()
    a, b = ComponentReference("a", "0"), ComponentReference("b", "0")
    assert rules.validate_order("eet", 0, [b, a])
    assert not rules.validate_order("eet", 0, [a, b])
    assert not rules.get_order_violations(a)


def test_b29_archive_metadata_applied() -> None:
    merger = DefinitionMerger("demo")
    merger.add_source("archive", {"files": [{"filename": "new.zip"}]})
    result = merger.merge({"games": [], "files": [{"filename": "old.zip"}]})
    assert result["files"] == [{"filename": "new.zip"}]


def test_b30_zero_is_a_valid_safety_value() -> None:
    merger = DefinitionMerger("demo")
    merger.add_source("lcc", {"safe": 0})
    assert merger.merge({"games": [], "safe": 2})["safe"] == 0


@pytest.mark.xfail(reason="B32 / L8: downgrade considered update", raises=AssertionError)
def test_b32_older_release_is_not_an_update() -> None:
    assert not GitHubProcessor.compare_versions("6", "5")
