import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from core.ComponentReference import ComponentReference
from core.models.PauseEntry import PauseEntry

if TYPE_CHECKING:
    from core.WeiDULogParser import WeiDULogParser

logger = logging.getLogger(__name__)


# ============================================================================
# Exceptions
# ============================================================================


class OrderImportError(Exception):
    """Base exception for order import errors."""


class InvalidFormatError(OrderImportError):
    """Raised when file format is invalid."""


class FileReadError(OrderImportError):
    """Raised when file cannot be read."""


# ============================================================================
# JSON Order Parser
# ============================================================================


class OrderFileParser:
    """Parser for installation order JSON files."""

    @staticmethod
    def parse(file_path: str | Path) -> dict[int, list[ComponentReference]]:
        """Parse order from JSON file.

        Args:
            file_path: Path to JSON file

        Returns:
            Dict mapping sequence index to list of component references

        Raises:
            FileReadError: If file cannot be read
            InvalidFormatError: If file format is invalid
        """
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError as e:
            raise FileReadError(f"File not found: {file_path}") from e
        except PermissionError as e:
            raise FileReadError(f"Permission denied: {file_path}") from e
        except json.JSONDecodeError as e:
            raise InvalidFormatError(f"Invalid JSON: {e}") from e
        except Exception as e:
            raise FileReadError(f"Error reading file: {e}") from e

        if not isinstance(data, dict):
            raise InvalidFormatError("Root must be an object")

        result: dict[int, list[ComponentReference]] = {}

        for key, value in data.items():
            try:
                seq_idx = int(key)
            except ValueError as e:
                raise InvalidFormatError(
                    f"Invalid sequence key '{key}': must be an integer"
                ) from e

            if not isinstance(value, list):
                raise InvalidFormatError(f"Sequence {seq_idx} must be a list")

            try:
                references = [ComponentReference.from_string(entry) for entry in value]
                result[seq_idx] = references
            except Exception as e:
                raise InvalidFormatError(
                    f"Invalid component reference in sequence {seq_idx}: {e}"
                ) from e

        return result

    @staticmethod
    def serialize(order: dict[int, list[ComponentReference]], file_path: str | Path) -> None:
        """Serialize order to JSON file.

        Args:
            order: Dict mapping sequence index to list of component references
            file_path: Path to output JSON file

        Raises:
            FileReadError: If file cannot be written
        """
        # Convert to serializable format
        data = {
            str(seq_idx): ComponentReference.to_string_list(references)
            for seq_idx, references in order.items()
        }

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except PermissionError as e:
            raise FileReadError(f"Permission denied: {file_path}") from e
        except Exception as e:
            raise FileReadError(f"Error writing file: {e}") from e


# ============================================================================
# Import/Export Manager
# ============================================================================


class OrderImportExportManager:
    def __init__(self, weidu_parser: "WeiDULogParser"):
        """Initialize manager.

        Args:
            weidu_parser: WeiDU log parser instance
        """
        self._weidu_parser = weidu_parser
        self._file_parser = OrderFileParser()

    # ========================================
    # Import Operations
    # ========================================

    def import_from_json(self, file_path: str | Path) -> dict[int, list[ComponentReference]]:
        """Import order from JSON file.

        Args:
            file_path: Path to JSON file

        Returns:
            Dict mapping sequence index to list of component references

        Raises:
            OrderImportError: If import fails
        """
        logger.info(f"Importing order from JSON: {file_path}")

        try:
            result = self._file_parser.parse(file_path)

            total_components = sum(len(refs) for refs in result.values())
            logger.info(
                f"Successfully imported {total_components} components "
                f"from {len(result)} sequence(s)"
            )

            return result
        except OrderImportError:
            raise
        except Exception as e:
            logger.error(f"Unexpected error importing JSON: {e}", exc_info=True)
            raise OrderImportError(f"Failed to import order: {e}") from e

    def import_from_weidu_log(self, file_path: str | Path) -> list[ComponentReference]:
        """Import order from WeiDU.log file.

        Args:
            file_path: Path to WeiDU.log file

        Returns:
            list with single sequence containing parsed components

        Raises:
            OrderImportError: If import fails
        """
        logger.info(f"Importing order from WeiDU.log: {file_path}")

        try:
            log_entries = self._weidu_parser.parse_file(file_path)
            component_ids = log_entries.get_component_ids()
            references = ComponentReference.from_string_list(component_ids)

            logger.info(f"Successfully imported {len(references)} components from WeiDU.log")

            return references
        except FileNotFoundError as e:
            raise FileReadError(f"WeiDU.log not found: {file_path}") from e
        except Exception as e:
            logger.error(f"Error parsing WeiDU.log: {e}", exc_info=True)
            raise OrderImportError(f"Failed to parse WeiDU.log: {e}") from e

    # ========================================
    # Export Operations
    # ========================================

    def export_to_json(
        self, order: dict[int, list[ComponentReference]], file_path: str | Path
    ) -> None:
        """Export order to JSON file.

        Args:
            order: Dict mapping sequence index to list of component references
            file_path: Path to output JSON file

        Raises:
            OrderImportError: If export fails
        """
        logger.info(f"Exporting order to JSON: {file_path}")

        try:
            self._file_parser.serialize(order, file_path)

            total_components = sum(len(refs) for refs in order.values())
            logger.info(f"Successfully exported {total_components} components to {file_path}")
        except OrderImportError:
            raise
        except Exception as e:
            logger.error(f"Error exporting order: {e}", exc_info=True)
            raise OrderImportError(f"Failed to export order: {e}") from e

    # ========================================
    # Utilities
    # ========================================

    @staticmethod
    def get_order_statistics(order: dict[int, list[ComponentReference]]) -> dict[str, int]:
        """Get statistics about an order.

        Args:
            order: Order to analyze

        Returns:
            Dict with statistics
        """
        total_components = sum(len(references) for references in order.values())
        pauses = sum(
            1
            for references in order.values()
            for reference in references
            if PauseEntry.is_pause(reference.comp_key)
        )

        return {
            "sequence_count": len(order),
            "total_components": total_components,
            "pause_count": pauses,
            "component_count": total_components - pauses,
        }
