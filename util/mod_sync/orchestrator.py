"""Main orchestrator for mod synchronization."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable

from core.CompactJSONEncoder import CompactJSONEncoder
from util.mod_sync.extractors.selective_extractor import SelectiveExtractor
from util.mod_sync.mergers.definition_merger import DefinitionMerger
from util.mod_sync.models.sync_result import (
    ModSyncResult,
    SyncConfig,
    SyncStats,
    SyncStatus,
)
from util.mod_sync.processors.github_processor import GitHubProcessor
from util.mod_sync.sources.archive_source import ArchiveSource
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

        # 0. Extract archives if requested
        if self.config.extract_archives and self.selective_extractor:
            logger.info("Extracting archives for parsing...")
            self._extract_archives_for_parsing(progress_callback)

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

        # 3. Check GitHub versions if requested
        if self.config.check_github_versions:
            logger.info("Checking GitHub versions...")

            if progress_callback:
                progress_callback(0, 1, "Preparing GitHub version check...")

            self._check_github_versions(progress_callback)

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

        # Gather data from all sources
        merger = DefinitionMerger(tp2_name.lower())
        sources_used = ["lcc"]
        merger.add_source("lcc", lcc_data)

        # TP2 data
        if self.config.extract_tp2 and self.tp2_source:
            stored_games = (existing or {}).get("games")
            supported_games = set(stored_games) if stored_games else None

            tp2_data = self.tp2_source.extract(tp2_name, supported_games)

            if tp2_data:
                merger.add_source("tp2", tp2_data)
                sources_used.append("tp2")

        # INI data
        if self.config.extract_ini and self.ini_source:
            ini_data = self.ini_source.extract(tp2_name)
            if ini_data:
                merger.add_source("ini", ini_data)
                sources_used.append("ini")
        # Merge
        merged = merger.merge(existing)

        # Resolve GitHub link if needed
        if self._should_resolve_github(merged):
            logger.debug(f"Resolving GitHub link for {tp2_name}")
            self._resolve_github_link(merged)

        # Calculate file hash if needed
        if self.config.calculate_hashes and self.archive_source:
            files = merged.get("files", [])

            if files:
                updated_files = []
                any_file_updated = False

                for file_entry in files:
                    download_url = file_entry.get("download")

                    if not download_url:
                        # Keep file without download URL as-is
                        updated_files.append(file_entry)
                        continue

                    if (
                        file_entry.get("platforms")
                        and len(file_entry.get("platforms")) > 0
                        and "windows" not in file_entry.get("platforms")
                    ):
                        continue

                    logger.debug(
                        f"Processing archive for {tp2_name}: {file_entry.get('filename')}"
                    )

                    # Process the archive
                    file_info = self.archive_source.process(
                        download_url, expected_file=file_entry
                    )

                    if file_info:
                        # Check if file info changed (new download or updated hash)
                        # Compare relevant fields: filename, size, sha256
                        file_changed = (
                            file_entry.get("filename") != file_info.get("filename")
                            or file_entry.get("size") != file_info.get("size")
                            or file_entry.get("sha256") != file_info.get("sha256")
                        )

                        if file_changed:
                            logger.info(
                                f"File info updated for {tp2_name}: {file_info.get('filename')}"
                            )
                            updated_files.append(file_info)
                            any_file_updated = True
                        else:
                            logger.debug(
                                f"File info unchanged for {tp2_name}: "
                                f"{file_entry.get('filename')}"
                            )
                            updated_files.append(file_entry)
                    else:
                        # Archive processing failed, keep original
                        logger.warning(
                            f"Failed to process archive for {tp2_name}: "
                            f"{file_entry.get('filename')}"
                        )
                        updated_files.append(file_entry)

                # Update merged data if any file changed
                if any_file_updated:
                    merger.add_source("archive", {"files": updated_files})
                    merged = merger.merge(merged)
                    sources_used.append("archive")
                else:
                    sources_used.append("archive (validated)")

        # Check if anything changed
        if existing and not merger.has_changes(existing, merged):
            return ModSyncResult(
                tp2_name=tp2_name,
                status=SyncStatus.UNCHANGED,
                sources_used=sources_used,
            )

        # Save definition
        try:
            self._save_definition(definition_path, merged)
            status = SyncStatus.CREATED if is_new else SyncStatus.UPDATED
            return ModSyncResult(
                tp2_name=tp2_name,
                status=status,
                sources_used=sources_used,
            )
        except Exception as e:
            logger.error(f"Error saving definition {tp2_name}: {e}")
            return ModSyncResult(
                tp2_name=tp2_name,
                status=SyncStatus.ERROR,
                message=f"Save failed: {e}",
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

    def _is_filtered_out(self, json_file: Path, definition: dict) -> bool:
        """Tell whether a stored definition is excluded by the mod filter."""
        tp2_name = definition.get("tp2") or json_file.stem
        skip_reason = self.config.mod_filter.skip_reason(tp2_name, definition.get("games") or [])

        if skip_reason:
            logger.debug(f"Skipping {json_file.stem}: {skip_reason}")
        return skip_reason is not None

    def _should_resolve_github(self, definition: dict) -> bool:
        """Check if GitHub link needs resolution.

        Args:
            definition: Mod definition

        Returns:
            True if GitHub link should be resolved
        """
        links = definition.get("links", {})
        homepage = links.get("homepage", "")
        download = links.get("download", "")

        # Resolve if homepage is GitHub but download is incomplete or missing
        if self.github_processor.is_github_link(homepage):
            return not download or download == homepage

        # Don't resolve if homepage is not GitHub (even if download is)
        return False

    def _resolve_github_link(self, definition: dict) -> None:
        """Resolve GitHub download link.

        Args:
            definition: Mod definition to update
        """
        homepage = definition["links"]["homepage"]
        download_url = self.github_processor.resolve_download_link(homepage)

        if download_url:
            # definition["links"]["download"] = download_url
            # definition["files"] = [{"download": download_url}]
            logger.info(f"Resolved GitHub link: {download_url}")
        else:
            logger.warning(f"Could not resolve GitHub link for {homepage}")

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

    def _check_github_versions(
        self, progress_callback: Callable[[int, int, str], None] | None = None
    ) -> None:
        """Check for outdated GitHub mods and update download links with OS-specific files.

        For each GitHub mod:
        - Check if version is outdated
        - Fetch latest release assets
        - Detect OS-specific files (lin-, mac-, win- prefixes)
        - Get SHA256 from GitHub API or preserve existing ones
        - Update definition with multiple files entries if multiple OS detected

        Args:
            progress_callback: Optional callback for progress updates
        """
        # Get all definition files
        json_files = list(self.config.definitions_dir.glob("*.json"))
        github_mods = []

        # First pass: identify GitHub mods
        for json_file in json_files:
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    definition = json.load(f)

                if self._is_filtered_out(json_file, definition):
                    continue

                links = definition.get("links", {})
                homepage = links.get("homepage", "")

                # Check homepage first
                github_url = None
                if self.github_processor.is_github_link(homepage):
                    github_url = homepage
                else:
                    files = definition.get("files", [])
                    for file_entry in files:
                        file_download = file_entry.get("download", "")
                        if self.github_processor.is_github_link(file_download):
                            github_url = self.github_processor.extract_repo_url(file_download)
                            break  # Stop at first GitHub link found

                if github_url:
                    github_mods.append((json_file, definition, github_url))

            except Exception as e:
                logger.error(f"Error reading {json_file.name}: {e}")

        if not github_mods:
            logger.info("No GitHub mods found to check")
            return

        logger.info(f"Checking versions for {len(github_mods)} GitHub mods...")

        # Second pass: check versions with progress (0-100%)
        for i, (json_file, definition, github_url) in enumerate(github_mods, 1):
            if progress_callback:
                progress_callback(i, len(github_mods), f"GitHub: {json_file.stem}")

            try:
                current_version = definition.get("version", "")

                # Extract repo info
                repo_info = self.github_processor.extract_repo_info(github_url)
                if not repo_info:
                    logger.debug(f"Invalid GitHub URL for {json_file.stem}")
                    continue

                owner, repo = repo_info

                # Fetch latest release (SHA256 comes from GitHub API)
                release = self.github_processor.get_latest_release(owner, repo)

                if not release:
                    logger.debug(f"No release found for {json_file.stem}")
                    continue

                latest_version = release.tag_name

                # Check if version changed
                is_outdated = self.github_processor.compare_versions(
                    str(current_version), str(latest_version)
                )

                if is_outdated:
                    self.stats.github_outdated.append(
                        (json_file.stem, current_version, latest_version)
                    )
                    logger.info(
                        f"Outdated: {json_file.stem} {current_version} → {latest_version}"
                    )

                # Group assets by OS
                grouped_assets = self.github_processor.group_assets_by_os(release.assets)

                # Determine if we need to update the definition
                needs_update = False
                new_files = []

                # Build a map of existing files for comparison
                existing_files_map = {}
                for existing_file in definition.get("files", []):
                    key = (existing_file.get("filename"), existing_file.get("size"))
                    existing_files_map[key] = existing_file

                if len(grouped_assets) > 1 or (
                    None not in grouped_assets and len(grouped_assets) == 1
                ):
                    # Multiple OS-specific files or only OS-specific files found
                    logger.info(
                        f"Found OS-specific assets for {json_file.stem}: {list(grouped_assets.keys())}"
                    )

                    for os_name, assets in grouped_assets.items():
                        if os_name is None:
                            # No prefix - treat as generic/all platforms
                            continue

                        best_asset = self.github_processor.select_best_asset_for_os(assets)
                        if best_asset:
                            file_entry = {
                                "filename": best_asset.name,
                                "size": best_asset.size,
                            }

                            # Check if file already exists with same name and size
                            existing_key = (best_asset.name, best_asset.size)
                            if existing_key in existing_files_map:
                                # File unchanged - preserve existing sha256
                                existing_sha = existing_files_map[existing_key].get("sha256")
                                if existing_sha:
                                    file_entry["sha256"] = existing_sha
                                    logger.debug(
                                        f"Preserved existing SHA256 for {best_asset.name}"
                                    )
                            elif best_asset.sha256:
                                # New file or changed file with SHA256 from API
                                file_entry["sha256"] = best_asset.sha256

                            # Add download URL last
                            file_entry["download"] = best_asset.url
                            file_entry["platforms"] = [os_name]

                            new_files.append(file_entry)
                            needs_update = True

                    # If we have OS-specific files, sort them by filename for consistency
                    if new_files:
                        # Sort by filename to get consistent order (lin-, mac-, win-)
                        new_files.sort(key=lambda x: x["filename"])

                elif None in grouped_assets:
                    # Only generic files (no OS prefix) - single file entry
                    generic_assets = grouped_assets[None]
                    best_asset = self.github_processor.select_best_asset_for_os(generic_assets)

                    if best_asset:
                        file_entry = {
                            "filename": best_asset.name,
                            "size": best_asset.size,
                        }

                        # Check if file already exists with same name and size
                        existing_key = (best_asset.name, best_asset.size)
                        if existing_key in existing_files_map:
                            # File unchanged - preserve existing sha256
                            existing_sha = existing_files_map[existing_key].get("sha256")
                            if existing_sha:
                                file_entry["sha256"] = existing_sha
                                logger.debug(f"Preserved existing SHA256 for {best_asset.name}")
                        elif best_asset.sha256:
                            # New file or changed file with SHA256 from API
                            file_entry["sha256"] = best_asset.sha256

                        # Add download URL last
                        file_entry["download"] = best_asset.url

                        new_files.append(file_entry)
                        needs_update = True

                # Update definition if needed
                if needs_update and new_files:
                    # Update version
                    definition["version"] = latest_version.lstrip("vV")

                    # Update files structure (switch from "file" to "files" if needed)
                    if "file" in definition:
                        del definition["file"]
                    definition["files"] = new_files

                    # Save updated definition
                    self._save_definition(json_file, definition)
                    logger.info(f"Updated {json_file.stem} with {len(new_files)} file(s)")

            except Exception as e:
                logger.error(f"Error checking version for {json_file.name}: {e}", exc_info=True)

    def _extract_archives_for_parsing(
        self, progress_callback: Callable[[int, int, str], None] | None = None
    ) -> None:
        """Extract archives to enable TP2/TRA parsing.

        Only extracts .tp2 and .tra files.
        Skips mods already extracted.
        For mods with multiple platform-specific files, extracts the first compatible one.

        Args:
            progress_callback: Optional callback for progress updates
        """
        if (
            not self.selective_extractor
            or not self.config.archives_dir
            or not self.config.mods_dir
        ):
            return

        # Get all archives with their tp2 names
        archives_to_extract = []

        for json_file in self.config.definitions_dir.glob("*.json"):
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    definition = json.load(f)

                tp2_name = definition.get("tp2")
                files = definition.get("files", [])

                if not tp2_name or not files:
                    continue

                if self._is_filtered_out(json_file, definition):
                    continue

                # Try to find the first available archive file
                # Priority: first file found in archives_dir
                archive_found = None
                for file_entry in files:
                    filename = file_entry.get("filename")
                    if not filename:
                        continue

                    archive_path = self.config.archives_dir / filename
                    if archive_path.exists():
                        archive_found = archive_path
                        break

                if not archive_found:
                    logger.debug(f"No archive found for {tp2_name}")
                    continue

                archives_to_extract.append((archive_found, tp2_name))

            except Exception as e:
                logger.error(f"Error reading {json_file.name}: {e}")

        if not archives_to_extract:
            logger.info("No archives to extract")
            return

        logger.info(f"Extracting {len(archives_to_extract)} archives for parsing...")

        # Extract with progress tracking
        def extraction_progress(current: int, total: int, tp2_name: str):
            if progress_callback:
                progress_callback(current, total, f"Extract: {tp2_name}")

        results = self.selective_extractor.extract_multiple(
            archives_to_extract, self.config.mods_dir, extraction_progress
        )

        # Log results
        success_count = sum(1 for success in results.values() if success)
        logger.info(f"Extracted {success_count}/{len(archives_to_extract)} archives")
