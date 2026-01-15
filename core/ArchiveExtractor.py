from enum import Enum
import logging
from pathlib import Path
import subprocess

from constants import SEVEN_Z_PATH

logger = logging.getLogger(__name__)


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
    ):
        """Initialize extraction info.

        Args:
            extraction_id: Extraction identifier
            mod_id: Mod identifier
            mod_name: Display name of the mod
            tp2_name: TP2 filename (without extension)
            archive_path: Path to archive file
            destination_path: Target extraction directory
        """
        self.extraction_id = extraction_id
        self.mod_id = mod_id
        self.mod_name = mod_name
        self.tp2_name = tp2_name
        self.archive_path = archive_path
        self.destination_path = destination_path
        self.status = ExtractionStatus.TO_EXTRACT
        self.error_message: str | None = None


# ============================================================================
# Archive Extractor
# ============================================================================


class ArchiveExtractor:
    """Handles extraction of various archive formats."""

    @staticmethod
    def extract_archive(archive_path: Path, destination: Path) -> bool:
        """Extract archive to destination directory.

        Args:
            archive_path: Path to archive file
            destination: Target extraction directory

        Returns:
            True if extraction successful, False otherwise
        """
        destination.mkdir(parents=True, exist_ok=True)

        ext = archive_path.suffix.lower()

        try:
            if ext == ".zip":
                return ArchiveExtractor._extract_zip(archive_path, destination)
            elif ext == ".rar":
                return ArchiveExtractor._extract_rar(archive_path, destination)
            elif ext == ".7z":
                return ArchiveExtractor._extract_7z(archive_path, destination)
            elif ext in {".tar", ".gz"} or archive_path.name.endswith(".tar.gz"):
                return ArchiveExtractor._extract_tar(archive_path, destination)
            elif ext == ".exe":
                return ArchiveExtractor._extract_exe(archive_path, destination)
            else:
                logger.error(f"Unsupported archive format: {ext}")
                return False

        except Exception as e:
            logger.error(f"Extraction failed for {archive_path}: {e}")
            return False

    @staticmethod
    def _extract_zip(archive_path: Path, destination: Path) -> bool:
        """Extract ZIP archive.

        Args:
            archive_path: Path to 7z archive
            destination: Target extraction directory

        Returns:
            True if extraction successful
        """
        import zipfile

        with zipfile.ZipFile(archive_path, "r") as zip_ref:
            zip_ref.extractall(destination)
        return True

    @staticmethod
    def _extract_rar(archive_path: Path, destination: Path) -> bool:
        """Extract RAR archive.

        Args:
            archive_path: Path to 7z archive
            destination: Target extraction directory

        Returns:
            True if extraction successful
        """
        return ArchiveExtractor._extract_7z(archive_path, destination)

    @staticmethod
    def _extract_7z(archive_path: Path, destination: Path) -> bool:
        """Extract 7z archive.

        Args:
            archive_path: Path to 7z archive
            destination: Target extraction directory

        Returns:
            True if extraction successful
        """
        try:
            result = subprocess.run(
                [SEVEN_Z_PATH, "x", str(archive_path), f"-o{destination}", "-y"],
                capture_output=True,
                shell=True,
                text=True,
                timeout=300,
            )
            return result.returncode == 0
        except FileNotFoundError:
            logger.error("7z command not found")
            return False
        except subprocess.TimeoutExpired:
            logger.error("7z extraction timeout")
            return False

    @staticmethod
    def _extract_tar(archive_path: Path, destination: Path) -> bool:
        """Extract TAR/GZ archive.

        Args:
            archive_path: Path to 7z archive
            destination: Target extraction directory

        Returns:
            True if extraction successful
        """
        import tarfile

        with tarfile.open(archive_path, "r:*") as tar_ref:
            tar_ref.extractall(destination)
        return True

    @staticmethod
    def _extract_exe(archive_path: Path, destination: Path) -> bool:
        """Extract self-extracting EXE archive.

        Tries multiple methods:
        1. 7z to extract (most EXE are 7z-compressed)
        2. Direct execution with silent flags (avoiding DOS window)

        Args:
            archive_path: Path to EXE archive
            destination: Target extraction directory

        Returns:
            True if extraction successful
        """
        # Try 7z first (most reliable for self-extracting archives)
        if ArchiveExtractor._extract_7z(archive_path, destination):
            return True

        # Try running the EXE with silent extraction flags
        # Use CREATE_NO_WINDOW flag to prevent DOS window from appearing
        try:
            # Try common silent extraction parameters
            for params in [
                ["-s", f"-d{destination}"],
                ["/S", f"/D={destination}", "\\NSIS"],
                ["/SILENT", f"/DIR={destination}"],
            ]:
                result = subprocess.run(
                    [str(archive_path)] + params, capture_output=True, text=True, timeout=300
                )
                if result.returncode == 0:
                    return True

            return False
        except subprocess.TimeoutExpired:
            logger.error("EXE extraction timeout")
            return False
