"""Archive download and hash calculation source."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Callable
import urllib.request

logger = logging.getLogger(__name__)


class ArchiveSource:
    """Handle archive download and hash calculation."""

    CHUNK_SIZE = 8192  # 8KB chunks for reading files

    def __init__(self, archives_dir: Path | None):
        """Initialize archive source.

        Args:
            archives_dir: Directory where archives are stored
        """
        self.archives_dir = Path(archives_dir) if archives_dir else None
        if self.archives_dir:
            self.archives_dir.mkdir(exist_ok=True, parents=True)

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
        if archive_path.exists():
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

    def get_file_size(self, file_path: Path) -> int:
        """Get file size in bytes.

        Args:
            file_path: Path to file

        Returns:
            File size in bytes or 0 if error
        """
        try:
            size = file_path.stat().st_size
            logger.debug(f"File size for {file_path.name}: {size} bytes")
            return size
        except Exception as e:
            logger.error(f"Error getting file size for {file_path}: {e}")
            return 0

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
        # Try Content-Disposition header
        content_disposition = response.headers.get("Content-Disposition")
        if content_disposition:
            # Parse: attachment; filename="file.zip" or filename=file.zip
            import re

            # Try RFC 5987 format: filename*=UTF-8''file.zip
            match = re.search(r"filename\*=(?:UTF-8'')?([^;\s]+)", content_disposition)
            if match:
                filename = match.group(1)
                # URL decode if needed
                try:
                    from urllib.parse import unquote

                    filename = unquote(filename)
                except Exception:
                    pass
                logger.debug(
                    f"Extracted filename from Content-Disposition (RFC 5987): {filename}"
                )
                return filename.strip('"')

            # Try standard format: filename="file.zip" or filename=file.zip
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

    def download_archive(
        self,
        url: str,
        target_filename: str | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> tuple[Path, str] | tuple[None, None]:
        """Download archive from URL with filename detection.

        Args:
            url: Download URL
            target_filename: Optional target filename (if None, auto-detect from headers)
            progress_callback: Optional callback(bytes_downloaded, total_bytes)

        Returns:
            Tuple of (downloaded_path, detected_filename) or (None, None) if error
        """
        if not self.archives_dir:
            logger.error("Archives directory not configured")
            return None, None

        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                # Detect filename from headers
                detected_filename = self.extract_filename_from_headers(response)

                # Use target_filename if provided, otherwise use detected
                filename = target_filename or detected_filename

                if not filename:
                    logger.error(f"Could not determine filename from URL: {url}")
                    return None, None

                target_path = self.archives_dir / filename
                total_size = int(response.headers.get("Content-Length", 0))
                downloaded = 0

                logger.info(f"Downloading {filename} from {url}")

                with open(target_path, "wb") as f:
                    while chunk := response.read(self.CHUNK_SIZE):
                        f.write(chunk)
                        downloaded += len(chunk)

                        if progress_callback and total_size > 0:
                            progress_callback(downloaded, total_size)

                logger.info(f"Downloaded {filename} ({downloaded} bytes)")
                return target_path, detected_filename or filename

        except urllib.error.HTTPError as e:
            logger.error(f"HTTP error downloading {url}: {e.code} {e.reason}")
            return None, None
        except urllib.error.URLError as e:
            logger.error(f"URL error downloading {url}: {e.reason}")
            return None, None
        except Exception as e:
            logger.error(f"Error downloading {url}: {e}", exc_info=True)
            return None, None

    def process(
        self,
        download_url: str | None,
        expected_file: dict | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> dict | None:
        """Process archive: validate, download if needed, and calculate hash.

        Implements smart validation:
        1. File exists with same hash → Skip (optimal)
        2. File exists with different hash → Re-download + Update
        3. File doesn't exist → Download + Detect name

        Args:
            download_url: URL to download from
            expected_file: Expected file info from definition
                          {"filename": "...", "size": ..., "sha256": "..."}
            progress_callback: Optional progress callback for download

        Returns:
            Dictionary with file info (filename, size, sha256)
            Returns updated info if file was re-downloaded
        """
        if not download_url:
            logger.debug("No download URL provided")
            return None

        expected_filename = expected_file.get("filename") if expected_file else None
        expected_sha256 = expected_file.get("sha256") if expected_file else None
        expected_size = expected_file.get("size") if expected_file else None

        # Case 1: Check if file already exists and is valid
        if expected_filename:
            archive_path = self.find_archive(expected_filename)

            if archive_path:
                # File exists, validate hash if we have it
                if expected_sha256:
                    actual_sha256 = self.calculate_sha256(archive_path)
                    actual_size = self.get_file_size(archive_path)

                    if actual_sha256 == expected_sha256 and actual_size == expected_size:
                        logger.info(f"Archive valid (same hash): {expected_filename}")
                    else:
                        logger.warning(
                            f"Archive hash/size mismatch for {expected_filename}, update"
                        )
                        # Delete corrupted file
                        # archive_path.unlink()
                    return {
                        "filename": expected_filename,
                        "size": actual_size,
                        "sha256": actual_sha256,
                    }
                else:
                    # No hash to validate, assume valid
                    actual_sha256 = self.calculate_sha256(archive_path)
                    actual_size = self.get_file_size(archive_path)
                    logger.info(f"Archive exists: {expected_filename}")
                    return {
                        "filename": expected_filename,
                        "size": actual_size,
                        "sha256": actual_sha256,
                    }

        # Case 2 & 3: Download file (doesn't exist or hash mismatch)
        logger.info(f"Downloading archive from {download_url}")
        downloaded_path, detected_filename = self.download_archive(
            download_url, expected_filename, progress_callback
        )

        if not downloaded_path:
            return None

        # Calculate hash and size of downloaded file
        actual_sha256 = self.calculate_sha256(downloaded_path)
        actual_size = self.get_file_size(downloaded_path)

        if not actual_sha256:
            logger.error(f"Failed to calculate hash for {downloaded_path.name}")
            return None

        # Use detected filename if different from expected
        final_filename = detected_filename or downloaded_path.name

        # Rename if detected filename differs from what we downloaded as
        if detected_filename and detected_filename != downloaded_path.name:
            new_path = self.archives_dir / detected_filename
            if new_path != downloaded_path:
                if new_path.exists():
                    logger.warning(
                        f"Target file already exists: {detected_filename}, "
                        "comparing with downloaded file"
                    )

                    # Compare the two files
                    new_sha256 = self.calculate_sha256(new_path)
                    downloaded_sha256 = actual_sha256  # Already calculated above

                    if new_sha256 == downloaded_sha256:
                        # Files are identical, remove downloaded duplicate
                        logger.info(
                            f"Downloaded file is identical to existing {detected_filename}, "
                            "removing duplicate"
                        )
                        downloaded_path.unlink()
                    else:
                        # Files differ, replace old with new
                        logger.info(
                            f"Replacing existing {detected_filename} with newer version"
                        )
                        new_path.unlink()
                        downloaded_path.rename(new_path)

                        # Recalculate hash for the final file
                        actual_sha256 = self.calculate_sha256(new_path)
                        actual_size = self.get_file_size(new_path)
                else:
                    logger.info(f"Renaming {downloaded_path.name} → {detected_filename}")
                    downloaded_path.rename(new_path)

        logger.info(f"Processed archive: {final_filename} ({actual_size} bytes)")

        return {
            "filename": final_filename,
            "size": actual_size,
            "sha256": actual_sha256,
        }

    def process_existing(self, filename: str) -> dict | None:
        """Process existing archive file (no download).

        Args:
            filename: Archive filename

        Returns:
            Dictionary with file info or None if not found
        """
        archive_path = self.find_archive(filename)
        if not archive_path:
            logger.warning(f"Archive not found: {filename}")
            return None

        sha256 = self.calculate_sha256(archive_path)
        size = self.get_file_size(archive_path)

        if not sha256:
            return None

        return {"filename": filename, "size": size, "sha256": sha256}
