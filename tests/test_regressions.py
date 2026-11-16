"""Acceptance contracts for the thirteen reproduced audit defects.

Known defects are strict xfails restricted to their specific failure type.
Remove the marker when the owning delivery lot fixes the behavior.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from PySide6.QtCore import Qt
import pytest
from pytestqt.qtbot import QtBot

from core.ArchiveExtractor import ArchiveExtractor, ExtractionInfo
from core.BackupManager import BackupManager
from core.ComponentReference import ComponentReference, IndexManager
from core.DataUpdater import DataUpdater
from core.DirectoryMerger import MergeResult
from core.Mod import Mod
from core.RuleManager import RuleManager
from core.Rules import DependencyRule, OrderRule
from core.validators.StructureValidator import StructureValidator
from core.weidu_types import ComponentInfo, ComponentStatus
from core.WeiDUDebugParser import WeiDUDebugParser
from tests.conftest import ModCatalogue
from ui.pages.DownloadPage import DownloadPage
from ui.pages.ExtractionPage import ExtractionWorker
from ui.pages.InstallationPage import InstallationPage
from ui.pages.mod_selection.ComponentSelector import ComponentSelector
from ui.pages.mod_selection.SelectionController import SelectionController
from util.mod_sync.mergers.definition_merger import DefinitionMerger
from util.mod_sync.processors.github_processor import GitHubProcessor


@pytest.mark.xfail(
    reason="B01 / L1: failed replacement loses recovery copy", raises=AssertionError
)
def test_b01_failed_catalogue_replacement_retains_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backup = tmp_path / "backup"
    backup.mkdir()
    (backup / "original.json").write_text('{"original": true}', encoding="utf-8")
    updater = DataUpdater()
    for name, value in {
        "_fetch_remote_version": None,
        "_download_file": True,
        "_validate_zip": True,
        "_backup_custom_mods": None,
        "_create_backup": backup,
        "_extract_zip": True,
        "_replace_data_directory": False,
    }.items():
        monkeypatch.setattr(updater, name, Mock(return_value=value))
    restore = Mock()
    monkeypatch.setattr(updater, "_restore_backup", restore)
    assert updater.update_data() is False
    assert restore.called or (backup / "original.json").exists()


@pytest.mark.xfail(
    reason="B05 / L4: tuple requirements compared with string", raises=AssertionError
)
def test_b05_failed_prerequisite_is_detected() -> None:
    component = ComponentInfo("b:0", None, None, "b", 0, {("a", "0")})
    assert InstallationPage._component_depends_on(None, component, "a:0")


@pytest.mark.xfail(
    reason="B06 / L3: reverse dependency treated as prerequisite", raises=AssertionError
)
def test_b06_dependency_target_does_not_require_itself(rules: RuleManager) -> None:
    rules._all_rules = [DependencyRule.from_dict({"source": ["a:0"], "target": ["b:0"]})]
    rules._build_indexes()
    assert rules.get_requirements("a", "0") == {("b", "0")}
    assert rules.get_requirements("b", "0") == set()


@pytest.mark.xfail(
    reason="B07 / L3: SUB answers leak between components", raises=AssertionError
)
def test_b07_sub_answers_belong_to_their_component(catalogue: ModCatalogue) -> None:
    state = Mock()
    state.get_selected_components.return_value = ["demo:1.1.A", "demo:2.1.B"]
    state.get_rule_manager.return_value.get_requirements.return_value = set()
    page = SimpleNamespace(_mod_manager=catalogue, state_manager=state)
    components = InstallationPage._convert_order_to_component_info(page, ["demo:1", "demo:2"])
    assert [item.subcomponent_answers for item in components] == [["A"], ["B"]]


@pytest.mark.xfail(
    reason="B08 / L4: truncated DEBUG reported successful", raises=AssertionError
)
def test_b08_unfinished_debug_is_not_success(tmp_path: Path) -> None:
    debug = tmp_path / "setup-demo.DEBUG"
    debug.write_text("Installing [A]\n", encoding="utf-8")
    assert ComponentStatus.SUCCESS not in WeiDUDebugParser.parse(debug).values()


@pytest.mark.xfail(reason="B09 / L5: merge errors ignored", raises=AssertionError)
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


@pytest.mark.xfail(reason="B11 / L2: copy2 cannot transfer directory", raises=OSError)
def test_b11_cross_volume_directory_transfer(tmp_path: Path) -> None:
    source, destination = tmp_path / "source", tmp_path / "destination"
    source.mkdir()
    (source / "épreuve.txt").write_bytes(b"original")
    BackupManager._transfer(None, source, destination, move=False)
    assert (destination / "épreuve.txt").read_bytes() == b"original"


@pytest.mark.xfail(reason="B13 / L5: manual archive omitted", raises=AssertionError)
def test_b13_manual_archive_remains_visible() -> None:
    mod = Mod({"id": "manual", "links": {}, "files": [{"filename": "manual.zip"}]})
    archive = DownloadPage._get_archive_info_from_mod(None, mod)
    assert archive is not None and archive.filename == "manual.zip"


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


@pytest.mark.xfail(
    reason="B20 / L6: corrected order leaves stale violations", raises=AssertionError
)
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
    rules._build_indexes()
    a, b = ComponentReference("a", "0"), ComponentReference("b", "0")
    assert rules.validate_order("eet", 0, [b, a])
    assert not rules.validate_order("eet", 0, [a, b])
    assert not rules.get_order_violations(a)


@pytest.mark.xfail(reason="B29 / L8: archive source is not merged", raises=AssertionError)
def test_b29_archive_metadata_applied() -> None:
    merger = DefinitionMerger("demo")
    merger.add_source("archive", {"files": [{"filename": "new.zip"}]})
    result = merger.merge({"games": [], "files": [{"filename": "old.zip"}]})
    assert result["files"] == [{"filename": "new.zip"}]


@pytest.mark.xfail(reason="B30 / L8: safe zero is discarded", raises=AssertionError)
def test_b30_zero_is_a_valid_safety_value() -> None:
    merger = DefinitionMerger("demo")
    merger.add_source("lcc", {"safe": 0})
    assert merger.merge({"games": [], "safe": 2})["safe"] == 0


@pytest.mark.xfail(reason="B32 / L8: downgrade considered update", raises=AssertionError)
def test_b32_older_release_is_not_an_update() -> None:
    assert not GitHubProcessor.compare_versions("6", "5")
