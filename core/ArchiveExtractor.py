"""Extract mod archives into a staging directory without trusting their content.

Every archive is listed before anything is written: entries leaving the
destination, links, special files and archives exceeding the size or entry
quotas are refused. The written tree is checked again afterwards, which also
covers self-extracting executables run through an explicit recipe.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import logging
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil
import stat
import subprocess
import tarfile
from typing import TYPE_CHECKING
import zipfile

from constants import (
    EXTRACT_DISK_MARGIN_BYTES,
    EXTRACT_MAX_ENTRIES,
    EXTRACT_MAX_UNPACKED_BYTES,
    EXTRACT_RECIPE_DESTINATION,
    EXTRACT_TOOL_TIMEOUT_S,
    SEVEN_Z_PATH,
)

if TYPE_CHECKING:
    from core.Mod import Mod

logger = logging.getLogger(__name__)

# Keep 7-Zip and recipe executables from opening a console window on Windows
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# 7-Zip reports a plain executable (not an archive) with this type
_SEVEN_Z_EXECUTABLE_TYPE = "PE"
_SEVEN_Z_LISTING_SEPARATOR = "----------"
_TOOL_OUTPUT_TAIL = 500


# ============================================================================
# Extraction Status
# ============================================================================


class ExtractionStatus(Enum):
    """Status of archive extraction."""

    TO_EXTRACT = "to_extract"
    EXTRACTING = "extracting"
    EXTRACTED = "extracted"
    ERROR = "error"
    MISSING_ARCHIVE = "missing_archive"
    ARCHIVE_NOT_FOUND = "archive_not_found"

    @property
    def needs_extraction(self) -> bool:
        """Check if archive needs extraction."""
        return self in {ExtractionStatus.TO_EXTRACT, ExtractionStatus.ERROR}


# ============================================================================
# Extraction Info
# ============================================================================


class ExtractionInfo:
    """Information about an archive to extract."""

    def __init__(
        self,
        extraction_id: str,
        mod_id: str,
        mod_name: str,
        tp2_name: str,
        archive_path: Path,
        destination_path: Path,
        mod: Mod | None = None,
        manifest_path: Path | None = None,
    ):
        """Initialize extraction info.

        Args:
            extraction_id: Extraction identifier
            mod_id: Mod identifier
            mod_name: Display name of the mod
            tp2_name: TP2 filename (without extension)
            archive_path: Path to archive file
            destination_path: Target extraction directory
            mod: Catalogue mod owning the archive, used to prove its identity
            manifest_path: Proof of preparation written after a full success
        """
        self.extraction_id = extraction_id
        self.mod_id = mod_id
        self.mod_name = mod_name
        self.tp2_name = tp2_name
        self.archive_path = archive_path
        self.destination_path = destination_path
        self.mod = mod
        self.manifest_path = manifest_path
        # Planned component numbers per TP2, compared after an accepted mismatch
        self.planned_components: dict[str, set[str]] = {}
        self.status = ExtractionStatus.TO_EXTRACT
        self.error_message: str | None = None

    @property
    def staging_path(self) -> Path:
        """Directory receiving the raw archive content before the merge."""
        return self.destination_path / f"_temp_{self.tp2_name or self.mod_id}"


# ============================================================================
# Extraction Result
# ============================================================================


class ExtractionFailure(str, Enum):
    """Reason why an archive was not extracted."""

    UNSUPPORTED = "unsupported"
    UNREADABLE = "unreadable"
    UNSAFE_PATH = "unsafe_path"
    LINK = "link"
    QUOTA = "quota"
    DISK_SPACE = "disk_space"
    TOOL = "tool"
    NOT_AN_ARCHIVE = "not_an_archive"


@dataclass(frozen=True)
class ExtractionResult:
    """Outcome of an extraction; truthy only on success.

    Attributes:
        failure: Reason of the failure, None on success
        detail: Offending entry or tool output
        entries: Number of files and directories written
        size: Total size of the files written
    """

    failure: ExtractionFailure | None = None
    detail: str = ""
    entries: int = 0
    size: int = 0

    @property
    def ok(self) -> bool:
        return self.failure is None

    def __bool__(self) -> bool:
        return self.ok

    @classmethod
    def failed(cls, failure: ExtractionFailure, detail: str = "") -> ExtractionResult:
        return cls(failure=failure, detail=detail)


@dataclass(frozen=True)
class ArchiveEntry:
    """Member of an archive as declared by its listing."""

    name: str
    size: int = 0
    is_dir: bool = False
    is_link: bool = False
    is_special: bool = False


@dataclass(frozen=True)
class ExtractionLimits:
    """Quotas applied to the declared and the written content."""

    max_bytes: int = EXTRACT_MAX_UNPACKED_BYTES
    max_entries: int = EXTRACT_MAX_ENTRIES
    disk_margin: int = EXTRACT_DISK_MARGIN_BYTES


@dataclass
class _SevenZipListing:
    types: list[str] = field(default_factory=list)
    entries: list[ArchiveEntry] = field(default_factory=list)


# ============================================================================
# Archive Extractor
# ============================================================================


class ArchiveExtractor:
    """Handles extraction of various archive formats."""

    @classmethod
    def extract_archive(
        cls,
        archive_path: Path,
        destination: Path,
        recipe: tuple[str, ...] | None = None,
        limits: ExtractionLimits | None = None,
    ) -> ExtractionResult:
        """Extract an archive into an empty destination directory.

        Args:
            archive_path: Path to archive file
            destination: Target extraction directory
            recipe: Arguments of a self-extracting executable declared by the
                mod, the only way an executable is ever run
            limits: Quotas, defaults from constants

        Returns:
            Typed result, truthy when every entry was written safely
        """
        limits = limits or ExtractionLimits()
        name = archive_path.name.lower()

        try:
            destination.mkdir(parents=True, exist_ok=True)
            if name.endswith((".tar.gz", ".tgz", ".tar")):
                result = cls._extract_tar(archive_path, destination, limits)
            elif name.endswith(".zip") and zipfile.is_zipfile(archive_path):
                result = cls._extract_zip(archive_path, destination, limits)
            elif name.endswith((".zip", ".7z", ".rar")):
                result = cls._extract_7z(archive_path, destination, limits)
            elif name.endswith(".exe"):
                result = cls._extract_exe(archive_path, destination, limits, recipe)
            else:
                result = ExtractionResult.failed(
                    ExtractionFailure.UNSUPPORTED, archive_path.suffix
                )
        except (OSError, zipfile.BadZipFile, tarfile.TarError, EOFError) as error:
            result = ExtractionResult.failed(ExtractionFailure.UNREADABLE, str(error))

        if result.ok:
            result = cls._verify_tree(destination, limits)
        if not result.ok:
            logger.error(
                "Extraction refused for %s: %s %s",
                archive_path,
                result.failure.value,
                result.detail,
            )
        return result

    # ------------------------------------------------------------------
    # Entry checks
    # ------------------------------------------------------------------

    @staticmethod
    def is_unsafe_name(name: str) -> bool:
        """Tell whether an archive member could be written outside the destination."""
        if not name or "\x00" in name:
            return True
        windows = PureWindowsPath(name)
        if windows.drive or windows.root:
            return True
        parts = PurePosixPath(name.replace("\\", "/")).parts
        if parts and parts[0] == "/":
            return True
        # ':' would also address an NTFS alternate data stream
        return any(part == ".." or ":" in part for part in parts)

    @classmethod
    def check_entries(
        cls, entries: list[ArchiveEntry], destination: Path, limits: ExtractionLimits
    ) -> ExtractionResult:
        """Refuse a listing before anything is written."""
        total = 0
        for entry in entries:
            if entry.is_link:
                return ExtractionResult.failed(ExtractionFailure.LINK, entry.name)
            if entry.is_special or cls.is_unsafe_name(entry.name):
                return ExtractionResult.failed(ExtractionFailure.UNSAFE_PATH, entry.name)
            total += max(entry.size, 0)

        if len(entries) > limits.max_entries:
            return ExtractionResult.failed(ExtractionFailure.QUOTA, str(len(entries)))
        if total > limits.max_bytes:
            return ExtractionResult.failed(ExtractionFailure.QUOTA, str(total))

        free = shutil.disk_usage(destination).free
        if total + limits.disk_margin > free:
            return ExtractionResult.failed(ExtractionFailure.DISK_SPACE, str(total))
        return ExtractionResult(entries=len(entries), size=total)

    @classmethod
    def _verify_tree(cls, destination: Path, limits: ExtractionLimits) -> ExtractionResult:
        """Check what was actually written: no link and within the quotas."""
        root = destination.resolve()
        entries = size = 0
        for current, dirs, files in os.walk(destination, followlinks=False):
            directory = Path(current)

            for name in dirs:
                refused = cls._refuse_entry(directory / name, destination, root)
                if refused is not None:
                    return refused

            for name in files:
                path = directory / name
                refused = cls._refuse_entry(path, destination)
                if refused is not None:
                    return refused
                size += path.stat().st_size

            entries += len(dirs) + len(files)
            if entries > limits.max_entries or size > limits.max_bytes:
                return ExtractionResult.failed(ExtractionFailure.QUOTA, str(destination))
        return ExtractionResult(entries=entries, size=size)

    @staticmethod
    def _refuse_entry(
        path: Path, destination: Path, root: Path | None = None
    ) -> ExtractionResult | None:
        """Return the refusal of a written entry, or ``None`` when it is acceptable.

        The walk is top-down and refuses links before descending, so every parent of
        ``path`` is a confined real directory. Only directories need the costly
        ``resolve()`` (given ``root``): a regular file inside them cannot escape.
        """
        if path.is_symlink() or path.is_junction():
            return ExtractionResult.failed(
                ExtractionFailure.LINK, str(path.relative_to(destination))
            )
        if root is not None and not path.resolve().is_relative_to(root):
            return ExtractionResult.failed(ExtractionFailure.UNSAFE_PATH, str(path))
        return None

    # ------------------------------------------------------------------
    # Formats
    # ------------------------------------------------------------------

    @classmethod
    def _extract_zip(
        cls, archive_path: Path, destination: Path, limits: ExtractionLimits
    ) -> ExtractionResult:
        with zipfile.ZipFile(archive_path, "r") as archive:
            members = archive.infolist()
            entries = [cls._zip_entry(member) for member in members]
            checked = cls.check_entries(entries, destination, limits)
            if checked.ok:
                archive.extractall(destination)
        return checked

    @staticmethod
    def _zip_entry(member: zipfile.ZipInfo) -> ArchiveEntry:
        mode = member.external_attr >> 16
        return ArchiveEntry(
            name=member.filename,
            size=member.file_size,
            is_dir=member.is_dir(),
            is_link=stat.S_ISLNK(mode),
            is_special=any(
                check(mode)
                for check in (stat.S_ISCHR, stat.S_ISBLK, stat.S_ISFIFO, stat.S_ISSOCK)
            ),
        )

    @classmethod
    def _extract_tar(
        cls, archive_path: Path, destination: Path, limits: ExtractionLimits
    ) -> ExtractionResult:
        with tarfile.open(archive_path, "r:*") as archive:
            members = archive.getmembers()
            entries = [
                ArchiveEntry(
                    name=member.name,
                    size=member.size,
                    is_dir=member.isdir(),
                    is_link=member.issym() or member.islnk(),
                    is_special=not (member.isfile() or member.isdir() or member.issym()),
                )
                for member in members
            ]
            checked = cls.check_entries(entries, destination, limits)
            if checked.ok:
                archive.extractall(destination, filter="data")
        return checked

    @classmethod
    def _extract_7z(
        cls, archive_path: Path, destination: Path, limits: ExtractionLimits
    ) -> ExtractionResult:
        listing = cls._list_7z(archive_path)
        if isinstance(listing, ExtractionResult):
            return listing
        return cls._extract_listed_7z(archive_path, destination, limits, listing)

    @classmethod
    def _extract_listed_7z(
        cls,
        archive_path: Path,
        destination: Path,
        limits: ExtractionLimits,
        listing: _SevenZipListing,
    ) -> ExtractionResult:
        checked = cls.check_entries(listing.entries, destination, limits)
        if not checked.ok:
            return checked
        completed = cls._run_7z(["x", "-y", f"-o{destination}", "--", str(archive_path)])
        if isinstance(completed, ExtractionResult):
            return completed
        if completed.returncode != 0:
            return ExtractionResult.failed(ExtractionFailure.TOOL, cls._tool_output(completed))
        return checked

    @classmethod
    def _extract_exe(
        cls,
        archive_path: Path,
        destination: Path,
        limits: ExtractionLimits,
        recipe: tuple[str, ...] | None,
    ) -> ExtractionResult:
        """Read a self-extracting archive with 7-Zip; never run it without a recipe."""
        listing = cls._list_7z(archive_path)
        if isinstance(listing, _SevenZipListing):
            if _SEVEN_Z_EXECUTABLE_TYPE not in listing.types or len(listing.types) > 1:
                result = cls._extract_listed_7z(archive_path, destination, limits, listing)
                if result.ok or result.failure is not ExtractionFailure.TOOL:
                    return result
            shutil.rmtree(destination, ignore_errors=True)
            destination.mkdir(parents=True, exist_ok=True)
        elif listing.failure is ExtractionFailure.TOOL and not recipe:
            return listing

        if not recipe:
            return ExtractionResult.failed(ExtractionFailure.NOT_AN_ARCHIVE, archive_path.name)
        return cls._run_recipe(archive_path, destination, recipe)

    @classmethod
    def _run_recipe(
        cls, archive_path: Path, destination: Path, recipe: tuple[str, ...]
    ) -> ExtractionResult:
        arguments = [
            arg.replace(EXTRACT_RECIPE_DESTINATION, str(destination)) for arg in recipe
        ]
        logger.info("Running extraction recipe of %s: %s", archive_path.name, arguments)
        completed = cls._run([str(archive_path), *arguments], cwd=destination)
        if isinstance(completed, ExtractionResult):
            return completed
        if completed.returncode != 0:
            return ExtractionResult.failed(ExtractionFailure.TOOL, cls._tool_output(completed))
        return ExtractionResult()

    # ------------------------------------------------------------------
    # 7-Zip
    # ------------------------------------------------------------------

    @classmethod
    def _list_7z(cls, archive_path: Path) -> _SevenZipListing | ExtractionResult:
        completed = cls._run_7z(["l", "-slt", "--", str(archive_path)])
        if isinstance(completed, ExtractionResult):
            return completed
        if completed.returncode != 0:
            return ExtractionResult.failed(
                ExtractionFailure.UNREADABLE, cls._tool_output(completed)
            )
        return cls.parse_7z_listing(completed.stdout)

    @staticmethod
    def parse_7z_listing(output: str) -> _SevenZipListing:
        """Parse the technical listing (`7z l -slt`) of an archive."""
        lines = output.replace("\r\n", "\n").split("\n")
        try:
            separator = lines.index(_SEVEN_Z_LISTING_SEPARATOR)
        except ValueError:
            separator = len(lines)

        listing = _SevenZipListing()
        for line in lines[:separator]:
            key, _, value = line.partition(" = ")
            if key.strip() == "Type" and value.strip():
                listing.types.append(value.strip())

        block: dict[str, str] = {}
        for line in [*lines[separator + 1 :], ""]:
            if line.strip():
                key, _, value = line.partition("=")
                block[key.strip()] = value.strip()
                continue
            if block.get("Path"):
                listing.entries.append(ArchiveExtractor._seven_zip_entry(block))
            block = {}
        return listing

    @staticmethod
    def _seven_zip_entry(block: dict[str, str]) -> ArchiveEntry:
        attributes = block.get("Attributes", "").split()
        windows_flags = attributes[0] if attributes else ""
        unix_mode = next((token for token in attributes if len(token) == 10), "")
        size = block.get("Size", "")
        return ArchiveEntry(
            name=block["Path"],
            size=int(size) if size.isdigit() else 0,
            is_dir=block.get("Folder") == "+" or "D" in windows_flags,
            is_link=bool(block.get("Symbolic Link") or block.get("Hard Link"))
            or unix_mode.startswith("l"),
            is_special=unix_mode[:1] in {"c", "b", "p", "s"},
        )

    @classmethod
    def _run_7z(cls, arguments: list[str]) -> subprocess.CompletedProcess | ExtractionResult:
        return cls._run([str(SEVEN_Z_PATH), "-sccUTF-8", *arguments])

    @staticmethod
    def _run(
        command: list[str], cwd: Path | None = None
    ) -> subprocess.CompletedProcess | ExtractionResult:
        """Run a tool without shell, console window or standard input."""
        try:
            return subprocess.run(
                command,
                cwd=cwd,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=EXTRACT_TOOL_TIMEOUT_S,
                creationflags=_NO_WINDOW,
                check=False,
            )
        except FileNotFoundError:
            return ExtractionResult.failed(ExtractionFailure.TOOL, command[0])
        except subprocess.TimeoutExpired:
            return ExtractionResult.failed(ExtractionFailure.TOOL, f"timeout: {command[0]}")

    @staticmethod
    def _tool_output(completed: subprocess.CompletedProcess) -> str:
        output = (completed.stderr or completed.stdout or "").strip()
        return output[-_TOOL_OUTPUT_TAIL:] or f"exit code {completed.returncode}"
