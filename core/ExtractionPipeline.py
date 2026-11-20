"""Prepare a mod in the game folder from its proven archive, or fail explicitly."""

from __future__ import annotations

import logging
from pathlib import Path
import shutil

from core.ArchiveExtractor import ArchiveExtractor, ExtractionInfo
from core.ArchiveRegistry import ArchiveRecord
from core.ArchiveService import ArchiveService
from core.DirectoryMerger import DirectoryMerger
from core.ExtractionManifest import ExtractionManifest
from core.TranslationManager import tr
from core.validators.StructureValidator import StructureValidator
from core.WeiDUTp2Parser import MucComponent, Tp2ParseError, WeiDUTp2Parser

logger = logging.getLogger(__name__)


class PreparationError(Exception):
    """A preparation step failed; the message is meant for the user."""


class ExtractionPipeline:
    """Prove, extract, normalize, check and merge one archive.

    The manifest of a mod is removed before anything else and written back
    only after every step succeeded, so a failure never looks like a success.
    A failed merge keeps the staging directory for diagnosis.
    """

    def __init__(
        self,
        merger: DirectoryMerger,
        archive_service: ArchiveService | None = None,
        platform: str | None = None,
    ) -> None:
        self._merger = merger
        self._archive_service = archive_service
        self._platform = platform

    # ------------------------------------------------------------------
    # Proof of preparation
    # ------------------------------------------------------------------

    @staticmethod
    def is_prepared(info: ExtractionInfo, record: ArchiveRecord | None) -> bool:
        """Tell whether a mod was prepared from its current archive."""
        if info.manifest_path is None:
            return False
        manifest = ExtractionManifest.load(info.manifest_path)
        if manifest is None or manifest.mod_id != info.mod_id:
            return False
        if not manifest.matches(info.archive_path, record):
            return False
        if not info.tp2_name:
            return info.destination_path.is_dir()
        # Mods provided by this archive are not looked for: their TP2 may only
        # appear when the provider is installed (checked before installation).
        return StructureValidator.validate_structure(info.destination_path, info.tp2_name)[0]

    # ------------------------------------------------------------------
    # Preparation
    # ------------------------------------------------------------------

    def prepare(self, info: ExtractionInfo) -> None:
        """Run every step of the preparation.

        Raises:
            PreparationError: The first failing step, with a user-facing message
        """
        record = self._prove_archive(info)
        if info.manifest_path is not None:
            ExtractionManifest.discard(info.manifest_path)

        staging = info.staging_path
        self._reset_staging(staging)

        recipe = info.mod.extract_recipe if info.mod is not None else None
        result = ArchiveExtractor.extract_archive(info.archive_path, staging, recipe)
        if not result:
            raise PreparationError(
                tr(
                    "page.extraction.error_extraction_refused",
                    reason=tr(f"page.extraction.failure.{result.failure.value}"),
                    detail=result.detail,
                )
            )

        if not StructureValidator.normalize_structure(staging, info.tp2_name):
            raise PreparationError(tr("page.extraction.error_structure_invalid"))
        if record is not None and record.accepted_mismatch:
            self._check_planned_components(info, staging)

        self._merge(info, staging)

        if record is not None and info.manifest_path is not None:
            ExtractionManifest.from_record(record).save(info.manifest_path)
        logger.info("Prepared %s from %s", info.mod_id, info.archive_path)

    def _prove_archive(self, info: ExtractionInfo) -> ArchiveRecord | None:
        """Hash an archive not proven since its last change and refuse an unusable one."""
        if self._archive_service is None or info.mod is None or self._platform is None:
            return None
        resolution = self._archive_service.ensure_verified(
            info.mod, info.archive_path, self._platform
        )
        if not resolution.status.is_usable:
            raise PreparationError(
                tr(
                    "page.extraction.error_archive_unproven",
                    status=tr(f"page.download.status.{resolution.status.value}"),
                )
            )
        return resolution.record

    @staticmethod
    def _reset_staging(staging: Path) -> None:
        """Start from an empty staging directory, dropping a previous failed attempt."""
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)

    @staticmethod
    def _check_planned_components(info: ExtractionInfo, staging: Path) -> None:
        """Compare the planned components with the TP2 of an archive accepted despite a mismatch."""
        absent: list[str] = []
        for tp2, planned in sorted(info.planned_components.items()):
            _, path = StructureValidator.validate_structure(staging, tp2)
            if path is None:
                # A provided mod's TP2 may be created by the provider's installation
                continue
            try:
                definition = WeiDUTp2Parser(path.parent).parse_file(path)
            except Tp2ParseError as error:
                logger.error("Cannot compare components of %s: %s", path, error)
                raise PreparationError(
                    tr("page.extraction.error_tp2_unreadable", tp2=tp2)
                ) from error
            available = {
                item.designated
                for component in definition.components
                for item in (
                    component.components if isinstance(component, MucComponent) else [component]
                )
            }
            missing = sorted(planned - available, key=lambda number: int(number))
            if missing:
                absent.append(f"{tp2} ({', '.join(missing)})")
        if absent:
            raise PreparationError(
                tr("page.extraction.error_components_missing", components="; ".join(absent))
            )

    def _merge(self, info: ExtractionInfo, staging: Path) -> None:
        merge = self._merger.merge_directories(staging, info.destination_path)
        for path in merge.overwritten_paths:
            logger.info("Extraction of %s overwrote %s", info.mod_id, path)
        if merge.success:
            return
        for path, message in merge.errors:
            logger.error("Merge of %s failed on %s: %s", info.mod_id, path, message)
        path, message = merge.errors[0]
        raise PreparationError(
            tr(
                "page.extraction.error_merge_failed",
                count=len(merge.errors),
                path=path,
                message=message,
                staging=staging,
            )
        )
