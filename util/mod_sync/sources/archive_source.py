"""Archive download and hash calculation source."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import logging
from pathlib import Path
import re
import shutil
from typing import Any, Callable
import urllib.error
from urllib.parse import unquote
import urllib.request

from core.Platform import Platform

logger = logging.getLogger(__name__)

# Every platform a file entry without "platforms" applies to.
ALL_PLATFORMS = ("windows", "linux", "macos")

# Retired archives go to <archives_dir>/_old/<stem>.<sha8><ext>.
RETIRED_ARCHIVES_DIR = "_old"
RETIRED_HASH_LENGTH = 8

# Suffix of an archive being downloaded, before it replaces the one in place.
PARTIAL_DOWNLOAD_SUFFIX = ".part"

DOWNLOAD_TIMEOUT_SECONDS = 30


@dataclass(frozen=True, slots=True)
class ArchiveFile:
    """Identity of an archive on disk, as stored in a definition file entry."""

    filename: str
    size: int
    sha256: str

    def apply_to(self, file_entry: dict[str, Any]) -> dict[str, Any]:
        """Return a copy of a file entry describing this archive."""
        return {**file_entry, "filename": self.filename, "size": self.size, "sha256": self.sha256}

    def matches(self, file_entry: dict[str, Any]) -> bool:
        """Tell whether this archive is the one a file entry describes.

        The stored hash decides; without one, a stored size can still reveal
        a different archive, but never prove it is the same.
        """
        if stored_sha256 := file_entry.get("sha256"):
            return self.sha256 == stored_sha256

        stored_size = file_entry.get("size")
        return stored_size is None or self.size == stored_size


class ArchiveSource:
    """Hash the archives of one platform, downloading them when needed.

    Only entries applying to that platform (the machine one by default) are
    processed: the archive of another platform cannot be checked here, so
    its entry is left intact.
    """

    CHUNK_SIZE = 8192  # 8KB chunks for reading files

    def __init__(self, archives_dir: Path | None, platform: str | None = None):
        """Initialize archive source.

        Args:
            archives_dir: Directory where archives are stored
            platform: Platform whose archives are processed (machine one by default)
        """
        self.archives_dir = Path(archives_dir) if archives_dir else None
        self.platform = platform or Platform.get_current()
        if self.archives_dir:
            self.archives_dir.mkdir(exist_ok=True, parents=True)

    # ------------------------------------------------------------------
    # Public operations
    # ------------------------------------------------------------------

    def applies_to_platform(self, file_entry: dict[str, Any]) -> bool:
        """Tell whether a file entry is the archive of the processed platform."""
        platforms = file_entry.get("platforms") or ALL_PLATFORMS
        return self.platform in platforms

    def process(
        self,
        file_entry: dict[str, Any],
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> ArchiveFile | None:
        """Return the archive matching a file entry, downloading it when needed.

        A local archive matching the stored hash is used as is; without stored
        hash, so is one matching the stored size, if any. A divergent one is
        downloaded again and the previous archive is retired to ``_old``.

        Args:
            file_entry: Stored entry {"filename", "size", "sha256", "download"}
            progress_callback: Optional callback(bytes_downloaded, total_bytes)

        Returns:
            The archive now in the archives directory, or None on failure
        """
        filename = file_entry.get("filename")
        download_url = file_entry.get("download")

        local_path = self.find_archive(filename) if filename else None
        local_archive = self._describe(local_path) if local_path else None

        if local_archive and local_archive.matches(file_entry):
            logger.debug(f"Archive valid: {filename}")
            return local_archive

        if local_archive:
            logger.warning(f"Archive hash mismatch for {filename}, downloading it again")

        if not download_url:
            logger.warning(f"No download URL for {filename}")
            return None

        downloaded = self._download(download_url, filename, progress_callback)
        if downloaded is None:
            return None

        # The divergent archive was replaced in place unless the server renamed it
        if local_archive and local_path.exists() and local_path.name != downloaded.filename:
            self._retire(local_path, local_archive.sha256)

        return downloaded

    def local_archive(self, file_entry: dict[str, Any]) -> ArchiveFile | None:
        """Return the archive of a file entry present on disk, without downloading it."""
        filename = file_entry.get("filename")
        local_path = self.find_archive(filename) if filename else None
        return self._describe(local_path) if local_path else None

    def archive_path(self, archive: ArchiveFile) -> Path:
        """Return where an archive of the archives directory is stored."""
        return self.archives_dir / archive.filename

    def find_archive(self, filename: str) -> Path | None:
        """Find archive file in archives directory.

        Args:
            filename: Archive filename to look for

        Returns:
            Path to archive or None if not found
        """
        if not self.archives_dir or not self.archives_dir.exists():
            return None

        archive_path = self.archives_dir / filename
        if archive_path.is_file():
            logger.debug(f"Found archive: {archive_path}")
            return archive_path

        logger.debug(f"Archive not found: {filename}")
        return None

    def calculate_sha256(self, file_path: Path) -> str:
        """Calculate SHA256 hash of a file.

        Args:
            file_path: Path to file

        Returns:
            SHA256 hex digest or empty string if error
        """
        sha256_hash = hashlib.sha256()

        try:
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(self.CHUNK_SIZE), b""):
                    sha256_hash.update(chunk)

            result = sha256_hash.hexdigest()
            logger.debug(f"Calculated SHA256 for {file_path.name}: {result[:16]}...")
            return result

        except FileNotFoundError:
            logger.error(f"File not found: {file_path}")
            return ""
        except Exception as e:
            logger.error(f"Error calculating SHA256 for {file_path}: {e}")
            return ""

    @staticmethod
    def extract_filename_from_headers(response) -> str | None:
        """Extract filename from HTTP response headers.

        Tries multiple methods:
        1. Content-Disposition header
        2. URL path (fallback)

        Args:
            response: urllib.request response object

        Returns:
            Extracted filename or None
        """
        content_disposition = response.headers.get("Content-Disposition")
        if content_disposition:
            # RFC 5987 format: filename*=UTF-8''file.zip
            match = re.search(r"filename\*=(?:UTF-8'')?([^;\s]+)", content_disposition)
            if match:
                filename = unquote(match.group(1))
                logger.debug(
                    f"Extracted filename from Content-Disposition (RFC 5987): {filename}"
                )
                return filename.strip('"')

            # Standard format: filename="file.zip" or filename=file.zip
            match = re.search(r'filename="?([^";\s]+)"?', content_disposition)
            if match:
                filename = match.group(1)
                logger.debug(f"Extracted filename from Content-Disposition: {filename}")
                return filename.strip('"')

        # Fallback: extract from URL
        url = response.url
        filename = url.split("/")[-1].split("?")[0]  # Remove query params
        if filename and not filename.startswith("download"):
            logger.debug(f"Extracted filename from URL: {filename}")
            return filename

        return None

    # ------------------------------------------------------------------
    # Download and replacement
    # ------------------------------------------------------------------

    def _download(
        self,
        url: str,
        expected_filename: str | None,
        progress_callback: Callable[[int, int], None] | None,
    ) -> ArchiveFile | None:
        """Download an archive and put it in place under its served name.

        The download goes to a partial file first, so a failure never touches
        the archive in place; a different archive already holding the final
        name is retired before being replaced.
        """
        if not self.archives_dir:
            logger.error("Archives directory not configured")
            return None

        partial_path = self.archives_dir / f"{expected_filename or 'download'}{PARTIAL_DOWNLOAD_SUFFIX}"
        try:
            served_filename = self._fetch(url, partial_path, progress_callback)
        except urllib.error.HTTPError as e:
            logger.error(f"HTTP error downloading {url}: {e.code} {e.reason}")
        except urllib.error.URLError as e:
            logger.error(f"URL error downloading {url}: {e.reason}")
        except Exception as e:
            logger.error(f"Error downloading {url}: {e}", exc_info=True)
        else:
            return self._install_download(partial_path, served_filename or expected_filename)

        partial_path.unlink(missing_ok=True)
        return None

    def _install_download(self, partial_path: Path, filename: str | None) -> ArchiveFile | None:
        """Give a downloaded archive its final name, retiring a different one in place."""
        downloaded_sha256 = self.calculate_sha256(partial_path)
        if not filename or not downloaded_sha256:
            logger.error(f"Could not identify the downloaded archive {partial_path.name}")
            partial_path.unlink(missing_ok=True)
            return None

        target_path = self.archives_dir / filename
        if target_path.is_file():
            target_sha256 = self.calculate_sha256(target_path)
            if target_sha256 == downloaded_sha256:
                logger.info(f"Downloaded file is identical to existing {filename}")
                partial_path.unlink()
                return ArchiveFile(filename, target_path.stat().st_size, target_sha256)
            self._retire(target_path, target_sha256)

        partial_path.replace(target_path)
        size = target_path.stat().st_size
        logger.info(f"Downloaded {filename} ({size} bytes)")
        return ArchiveFile(filename, size, downloaded_sha256)

    def _fetch(
        self,
        url: str,
        target_path: Path,
        progress_callback: Callable[[int, int], None] | None,
    ) -> str | None:
        """Write the content served at a URL to a file.

        Returns:
            The filename announced by the server, if any
        """
        with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
            served_filename = self.extract_filename_from_headers(response)
            total_size = int(response.headers.get("Content-Length", 0))
            downloaded = 0

            logger.info(f"Downloading {url}")
            with open(target_path, "wb") as f:
                while chunk := response.read(self.CHUNK_SIZE):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback and total_size > 0:
                        progress_callback(downloaded, total_size)

        return served_filename

    def _retire(self, archive_path: Path, sha256: str) -> Path:
        """Move a replaced archive to ``_old/<stem>.<sha8><ext>``."""
        retired_dir = archive_path.parent / RETIRED_ARCHIVES_DIR
        retired_dir.mkdir(exist_ok=True)

        retired_name = f"{archive_path.stem}.{sha256[:RETIRED_HASH_LENGTH]}{archive_path.suffix}"
        retired_path = retired_dir / retired_name
        shutil.move(archive_path, retired_path)

        logger.info(f"Retired previous archive {archive_path.name} → {retired_path}")
        return retired_path

    def _describe(self, archive_path: Path) -> ArchiveFile | None:
        """Return the identity of an archive on disk, or None if it cannot be read."""
        sha256 = self.calculate_sha256(archive_path)
        if not sha256:
            return None
        return ArchiveFile(archive_path.name, archive_path.stat().st_size, sha256)
