#!/usr/bin/env python3
"""JSON schema validator for mod files.

Validates JSON files against a JSON schema, supporting both single files
and directories containing multiple JSON files.
"""

import argparse
import json
import logging
from pathlib import Path
import sys
from typing import Dict, Optional

from jsonschema import Draft7Validator

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


class ValidationResult:
    """Result of a JSON validation operation.

    Attributes:
        file_path: Path to validated file
        errors: List of validation error messages
        is_valid: Whether validation passed
    """

    def __init__(self, file_path: Path, errors: Optional[list[str]] = None) -> None:
        """Initialize validation result.

        Args:
            file_path: Path to validated file
            errors: List of validation errors (empty if valid)
        """
        self.file_path = file_path
        self.errors = errors or []
        self.is_valid = len(self.errors) == 0

    def __repr__(self) -> str:
        """String representation for debugging."""
        status = "✅" if self.is_valid else "❌"
        return f"<ValidationResult {status} {self.file_path} errors={len(self.errors)}>"


class JSONValidator:
    """JSON schema validator with support for batch validation."""

    def __init__(self, schema_path: Path) -> None:
        """Initialize validator with a schema.

        Args:
            schema_path: Path to JSON schema file

        Raises:
            FileNotFoundError: If schema file doesn't exist
            json.JSONDecodeError: If schema is invalid JSON
        """
        self.schema_path = schema_path
        self.schema = self._load_schema()
        self.validator = Draft7Validator(self.schema)

        logger.debug(f"Schema loaded: {schema_path}")

    def _load_schema(self) -> Dict:
        """Load and parse JSON schema file.

        Returns:
            Parsed schema dictionary

        Raises:
            FileNotFoundError: If schema file doesn't exist
            json.JSONDecodeError: If schema is invalid JSON
        """
        if not self.schema_path.exists():
            raise FileNotFoundError(f"Schema not found: {self.schema_path}")

        try:
            with self.schema_path.open(encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            logger.error(f"Invalid schema JSON: {e}")
            raise
        except Exception as e:
            logger.error(f"Cannot read schema: {e}")
            raise

    def validate_file(self, file_path: Path) -> ValidationResult:
        """Validate a single JSON file against the schema.

        Args:
            file_path: Path to JSON file to validate

        Returns:
            ValidationResult with errors (if any)
        """
        errors = []

        # Load and parse JSON
        try:
            with file_path.open(encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            errors.append(f"Invalid JSON syntax: {e}")
            return ValidationResult(file_path, errors)
        except Exception as e:
            errors.append(f"Cannot read file: {e}")
            return ValidationResult(file_path, errors)

        # Validate against schema
        for error in sorted(self.validator.iter_errors(data), key=lambda e: e.path):
            path = ".".join(str(p) for p in error.path) or "root"
            errors.append(f"{path}: {error.message}")

        return ValidationResult(file_path, errors)

    def validate_directory(
        self, directory: Path, pattern: str = "*.json"
    ) -> list[ValidationResult]:
        """Validate all JSON files in a directory.

        Args:
            directory: Directory containing JSON files
            pattern: Glob pattern for file matching

        Returns:
            List of validation results
        """
        if not directory.is_dir():
            logger.error(f"Not a directory: {directory}")
            return []

        files = sorted(directory.glob(pattern))

        if not files:
            logger.warning(f"No JSON files found in {directory}")
            return []

        logger.info(f"Validating {len(files)} files in {directory}")

        results = []
        for file_path in files:
            result = self.validate_file(file_path)
            results.append(result)

        return results

    def validate_target(self, target: Path) -> list[ValidationResult]:
        """Validate a file or directory.

        Args:
            target: Path to file or directory

        Returns:
            List of validation results
        """
        if target.is_file():
            return [self.validate_file(target)]
        elif target.is_dir():
            return self.validate_directory(target)
        else:
            logger.error(f"Target not found: {target}")
            return []


def print_results(results: list[ValidationResult], errors_only: bool = False) -> int:
    """Print validation results to console.

    Args:
        results: List of validation results
        errors_only: If True, only show failed validations

    Returns:
        Number of failed validations
    """
    failed_count = 0

    for result in results:
        if result.is_valid:
            if not errors_only:
                print(f"✅ {result.file_path}")
        else:
            failed_count += 1
            print(f"❌ {result.file_path}", file=sys.stderr)
            for error in result.errors:
                print(f"  → {error}", file=sys.stderr)
            print(file=sys.stderr)

    # Summary
    total = len(results)
    passed = total - failed_count

    if total > 1:
        print(f"\n{'=' * 60}")
        print(f"Summary: {passed}/{total} files passed validation")
        if failed_count > 0:
            print(f"Failed: {failed_count}")

    return failed_count


def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments.

    Returns:
        Parsed arguments namespace
    """
    parser = argparse.ArgumentParser(
        description="Validate JSON files against a JSON schema.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s data/mods/mod1.json schema.json
  %(prog)s data/mods/ schema.json
  %(prog)s data/mods/ schema.json --errors-only
  %(prog)s data/mods/ schema.json -e --quiet
        """,
    )

    # Positional arguments
    parser.add_argument(
        "target",
        type=Path,
        help="path to JSON file or directory containing JSON files",
    )

    parser.add_argument(
        "schema",
        type=Path,
        help="path to JSON schema file",
    )

    # Optional arguments
    parser.add_argument(
        "-e",
        "--errors-only",
        action="store_true",
        help="only display files with validation errors (hide successful validations)",
    )

    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="suppress info messages (only show errors and summary)",
    )

    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="enable verbose output (debug information)",
    )

    return parser.parse_args()


def main() -> int:
    """Main entry point for the validator.

    Returns:
        Exit code (0 for success, 1 for validation failures)
    """
    # Parse arguments
    args = parse_arguments()

    # Configure logging level based on flags
    if args.verbose:
        logger.setLevel(logging.DEBUG)
    elif args.quiet:
        logger.setLevel(logging.ERROR)

    # Validate inputs
    if not args.target.exists():
        logger.error(f"Target not found: {args.target}")
        return 1

    if not args.schema.exists():
        logger.error(f"Schema not found: {args.schema}")
        return 1

    # Create validator and validate
    try:
        validator = JSONValidator(args.schema)
    except Exception as e:
        logger.error(f"Failed to load schema: {e}")
        return 1

    results = validator.validate_target(args.target)

    if not results:
        logger.warning("No files validated")
        return 0

    # Print results and return exit code
    failed_count = print_results(results, errors_only=args.errors_only)
    return 1 if failed_count > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
