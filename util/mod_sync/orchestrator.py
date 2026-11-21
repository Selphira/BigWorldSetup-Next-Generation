"""Main orchestrator for mod synchronization."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable

from core.CompactJSONEncoder import CompactJSONEncoder
from util.mod_sync.extractors.selective_extractor import SelectiveExtractor
from util.mod_sync.mergers.definition_merger import DefinitionMerger
from util.mod_sync.models.sync_result import (
    ComponentChanges,
    ModSyncResult,
    SyncConfig,
    SyncStats,
    SyncStatus,
)
from util.mod_sync.processors.github_processor import GitHubProcessor
from util.mod_sync.sources.archive_source import ArchiveFile, ArchiveSource
from util.mod_sync.sources.github_source import GitHubSource
from util.mod_sync.sources.ini_source import IniSource
from util.mod_sync.sources.lcc_source import LCCSource
from util.mod_sync.sources.tp2_source import TP2Source

logger = logging.getLogger(__name__)


class ModSyncOrchestrator:
    """Main orchestrator for mod definition synchronization."""

    def __init__(self, config: SyncConfig):
        """Initialize orchestrator.

        Args:
            config: Synchronization configuration
        """
        self.config = config
        self.stats = SyncStats()
        self._definition_paths: dict[str, Path] = {}

        # Initialize sources
        self.lcc_source = LCCSource(force_refresh=config.force_lcc_refresh)
        self.tp2_source = TP2Source(config.mods_dir) if config.mods_dir else None
        self.ini_source = IniSource(config.mods_dir) if config.mods_dir else None
        self.archive_source = (
            ArchiveSource(config.archives_dir) if config.archives_dir else None
        )
        self.github_processor = GitHubProcessor(config.github_token)
        self.github_source = GitHubSource(self.github_processor)
        self.selective_extractor = (
            SelectiveExtractor()
            if config.extract_archives and config.archives_dir and config.mods_dir
            else None
        )

        # Ensure definitions directory exists
        self.config.definitions_dir.mkdir(parents=True, exist_ok=True)

    def run(
        self, progress_callback: Callable[[int, int, str], None] | None = None
    ) -> SyncStats:
        """Run full synchronization.

        Args:
            progress_callback: Optional callback(current, total, mod_name)

        Returns:
            Synchronization statistics
        """
        logger.info("Starting mod synchronization...")
        self._index_definitions()

        # 1. Fetch LCC data
        if not self.lcc_source.fetch_all():
            raise RuntimeError("Failed to fetch LCC data")

        lcc_mods = self.lcc_source.get_all_mods()
        self.stats.total_lcc_mods = len(lcc_mods)
        logger.info(f"Processing {self.stats.total_lcc_mods} LCC mods")

        # 2. Process each LCC mod
        for i, (tp2_name, lcc_data) in enumerate(lcc_mods.items(), 1):
            if progress_callback:
                progress_callback(i, self.stats.total_lcc_mods, tp2_name)

            try:
                result = self._process_mod(tp2_name, lcc_data)
                self.stats.add_result(result)
                logger.debug(str(result))
            except Exception as e:
                logger.error(f"Error processing {tp2_name}: {e}", exc_info=True)
                result = ModSyncResult(
                    tp2_name=tp2_name, status=SyncStatus.ERROR, message=str(e)
                )
                self.stats.add_result(result)

        logger.info("Synchronization complete")
        logger.info(self.stats.get_summary())

        return self.stats

    def _process_mod(self, tp2_name: str, lcc_data: dict) -> ModSyncResult:
        """Process a single mod.

        Args:
            tp2_name: TP2 filename
            lcc_data: Data from LCC

        Returns:
            Sync result for this mod
        """
        definition_path = self._definition_path(tp2_name)

        # Load existing definition
        existing = None
        read_error = None
        is_new = not definition_path.exists()

        if not is_new:
            try:
                with open(definition_path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            except Exception as e:
                read_error = str(e)
            else:
                if not isinstance(existing, dict):
                    read_error = "definition is not a JSON object"
                    existing = None

        # Stored games include those derived by the merger (sod, eet) that LCC omits.
        mod_games = {*(lcc_data.get("games") or []), *((existing or {}).get("games") or [])}
        if skip_reason := self.config.mod_filter.skip_reason(tp2_name, mod_games):
            return ModSyncResult(
                tp2_name=tp2_name,
                status=SyncStatus.SKIPPED,
                message=skip_reason,
            )

        # An unreadable definition is never replaced: it may hold manual work.
        if read_error:
            logger.error(f"Error reading existing definition {tp2_name}: {read_error}")
            return ModSyncResult(
                tp2_name=tp2_name,
                status=SyncStatus.ERROR,
                message=f"Unreadable definition: {read_error}",
            )

        # Check if we should process this mod
        if is_new and not self.config.create_new:
            return ModSyncResult(
                tp2_name=tp2_name,
                status=SyncStatus.SKIPPED,
                message="create_new disabled",
            )

        if not is_new and not self.config.update_existing:
            return ModSyncResult(
                tp2_name=tp2_name,
                status=SyncStatus.SKIPPED,
                message="update_existing disabled",
            )

        # Gather data from all sources, the release first: it decides the files
        merger = DefinitionMerger(tp2_name.lower())
        sources_used = ["lcc"]
        merger.add_source("lcc", lcc_data)

        stored_files = (existing or {}).get("files")
        files = stored_files if isinstance(stored_files, list) else []

        if self.config.check_github_versions:
            github_data = self._github_data(tp2_name, existing, lcc_data)
            if github_data:
                merger.add_source("github", github_data)
                sources_used.append("github")
                files = github_data.get("files") or files

        # Archive of the machine: hashed, and downloaded when missing or divergent
        machine_archive = None
        if self.archive_source and files and (self.config.calculate_hashes or self.selective_extractor):
            files, machine_archive = self._process_archives(tp2_name, files)
            if self.config.calculate_hashes:
                merger.add_source("archive", {"files": files})
                sources_used.append("archive" if files != stored_files else "archive (validated)")

        # Extracted files are read only when they come from the current archive
        if self._refresh_extraction(tp2_name, machine_archive):
            self._add_parsed_sources(merger, sources_used, tp2_name, existing)

        # Merge; a new definition has no stored components to compare with
        merged = merger.merge(existing)
        component_changes = merger.component_changes if existing else ComponentChanges()

        # Check if anything changed
        if existing and not merger.has_changes(existing, merged):
            return ModSyncResult(
                tp2_name=tp2_name,
                status=SyncStatus.UNCHANGED,
                sources_used=sources_used,
                component_changes=component_changes,
            )

        # Save definition
        try:
            self._save_definition(definition_path, merged)
            status = SyncStatus.CREATED if is_new else SyncStatus.UPDATED
            return ModSyncResult(
                tp2_name=tp2_name,
                status=status,
                sources_used=sources_used,
                component_changes=component_changes,
            )
        except Exception as e:
            logger.error(f"Error saving definition {tp2_name}: {e}")
            return ModSyncResult(
                tp2_name=tp2_name,
                status=SyncStatus.ERROR,
                message=f"Save failed: {e}",
            )

    def _add_parsed_sources(
        self,
        merger: DefinitionMerger,
        sources_used: list[str],
        tp2_name: str,
        existing: dict | None,
    ) -> None:
        """Add the data parsed from the extracted TP2 and INI files of a mod."""
        if self.config.extract_tp2 and self.tp2_source:
            stored_games = (existing or {}).get("games")
            supported_games = set(stored_games) if stored_games else None

            tp2_data = self.tp2_source.extract(tp2_name, supported_games)
            if tp2_data:
                merger.add_source("tp2", tp2_data)
                sources_used.append("tp2")

        if self.config.extract_ini and self.ini_source:
            ini_data = self.ini_source.extract(tp2_name)
            if ini_data:
                merger.add_source("ini", ini_data)
                sources_used.append("ini")

    def _github_data(self, tp2_name: str, existing: dict | None, lcc_data: dict) -> dict[str, Any] | None:
        """Return the latest GitHub release of a mod as merger source data."""
        update = self.github_source.fetch(existing, lcc_data)
        if update is None:
            return None

        current_version = str((existing or {}).get("version") or "")
        if self.github_processor.compare_versions(current_version, update.tag_name):
            self.stats.github_outdated.append((tp2_name, current_version, update.tag_name))
            logger.info(f"Outdated: {tp2_name} {current_version} → {update.tag_name}")

        if not update.files:
            logger.warning(f"No downloadable asset in {update.repository} {update.tag_name}")
        return {"version": update.version, "files": update.files}

    def _process_archives(self, tp2_name: str, files: list) -> tuple[list, ArchiveFile | None]:
        """Return the files and the archive of the machine platform.

        With hashing enabled, the archive is obtained (downloaded when missing
        or divergent) and its entry describes it; otherwise only a local
        archive is looked up. Entries of other platforms, and entries whose
        archive cannot be obtained, are returned unchanged.
        """
        processed_files = []
        machine_archive = None
        for file_entry in files:
            if not isinstance(file_entry, dict) or not self.archive_source.applies_to_platform(file_entry):
                processed_files.append(file_entry)
                continue

            if not self.config.calculate_hashes:
                machine_archive = machine_archive or self.archive_source.local_archive(file_entry)
                processed_files.append(file_entry)
                continue

            archive = self.archive_source.process(file_entry)
            if archive is None:
                logger.warning(f"Failed to process archive for {tp2_name}: {file_entry.get('filename')}")
                processed_files.append(file_entry)
                continue

            updated_entry = archive.apply_to(file_entry)
            if updated_entry != file_entry:
                logger.info(f"File info updated for {tp2_name}: {archive.filename}")
            processed_files.append(updated_entry)
            machine_archive = machine_archive or archive

        return processed_files, machine_archive

    def _refresh_extraction(self, tp2_name: str, machine_archive: ArchiveFile | None) -> bool:
        """Extract the machine archive again if needed; tell whether extracted files may be read.

        Without archive extraction, the extracted mods directory is maintained
        by hand and always read. Otherwise an extraction that cannot be checked
        against the current archive is never read.
        """
        if not self.selective_extractor:
            return True

        if machine_archive is None:
            if self.selective_extractor.read_stamp(self.config.mods_dir, tp2_name):
                logger.warning(f"No current archive for {tp2_name}: its previous extraction is not read")
                return False
            return True

        return self.selective_extractor.extract_for_parsing(
            self.archive_source.archive_path(machine_archive),
            self.config.mods_dir,
            tp2_name,
            machine_archive.sha256,
        )

    def _index_definitions(self) -> None:
        """Index existing definition files by lowercase stem.

        LCC identifiers are lowercase while definition files keep the mod's
        own casing; the index keeps lookups working on case-sensitive systems.
        """
        self._definition_paths = {
            path.stem.lower(): path for path in self.config.definitions_dir.glob("*.json")
        }

    def _definition_path(self, tp2_name: str) -> Path:
        """Return the existing definition file for a mod, or the path to create."""
        existing_path = self._definition_paths.get(tp2_name.lower())
        return existing_path or self.config.definitions_dir / f"{tp2_name}.json"

    def _save_definition(self, path: Path, data: dict) -> None:
        """Save definition to JSON file.

        Args:
            path: Target file path
            data: Definition data
        """
        with open(path, "w", encoding="utf-8") as f:
            json_str = CompactJSONEncoder(indent=2, ensure_ascii=False).encode(data)
            f.write(json_str)
            f.write("\n")

        logger.debug(f"Saved definition: {path.name}")
