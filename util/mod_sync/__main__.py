"""Main entry point for mod synchronization tool.

Can be run in two modes:
1. GUI mode (default): python -m util.mod_sync
2. CLI mode: python -m util.mod_sync --cli [options]
"""

import argparse
import logging
from pathlib import Path
import sys

from core.enums.GameEnum import GameEnum


def setup_logging(verbose: bool = False) -> None:
    """Setup logging configuration.

    Args:
        verbose: Enable debug logging
    """
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def run_gui() -> None:
    """Run GUI mode."""
    from PySide6.QtWidgets import QApplication

    from util.mod_sync.gui.sync_dialog import ModSyncDialog

    app = QApplication(sys.argv)
    app.setApplicationName("BWS-EE Mod Sync")
    app.setOrganizationName("BWS-EE")

    dialog = ModSyncDialog()
    dialog.show()

    sys.exit(app.exec())


def run_cli(args: argparse.Namespace) -> None:
    """Run CLI mode.

    Args:
        args: Parsed command line arguments
    """
    from util.mod_sync.models.sync_result import ModFilter, SyncConfig
    from util.mod_sync.orchestrator import ModSyncOrchestrator

    config = SyncConfig(
        definitions_dir=Path(args.definitions_dir),
        archives_dir=Path(args.archives_dir) if args.archives_dir else None,
        mods_dir=Path(args.mods_dir) if args.mods_dir else None,
        force_lcc_refresh=args.force_refresh,
        github_token=args.github_token,
        create_new=not args.no_create,
        update_existing=not args.no_update,
        extract_tp2=not args.no_tp2,
        extract_ini=not args.no_ini,
        extract_archives=args.extract_archives,
        calculate_hashes=args.calculate_hashes,
        check_github_versions=args.check_github,
        mod_filter=ModFilter(tp2_names=frozenset(args.only), games=frozenset(args.game)),
    )

    def progress_callback(current: int, total: int, mod_name: str) -> None:
        print(f"[{current}/{total}] Processing: {mod_name}")

    try:
        orchestrator = ModSyncOrchestrator(config)
        stats = orchestrator.run(progress_callback)

        # Display summary
        print(stats.get_summary())

        # Display GitHub report
        if github_report := stats.get_github_report():
            print(github_report)

        # Display errors if any
        if stats.errors > 0:
            print("\nErrors:")
            for result in stats.results:
                if result.status.value == "error":
                    print(f"  ✗ {result.tp2_name}: {result.message}")

        # Exit with error code if there were errors
        sys.exit(1 if stats.errors > 0 else 0)

    except Exception as e:
        print(f"Fatal error: {e}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Synchronize mod definitions from multiple sources (LCC, TP2, INI)"
    )

    # Mode selection
    parser.add_argument(
        "--cli",
        action="store_true",
        help="Run in CLI mode instead of GUI",
    )

    # CLI-specific arguments
    parser.add_argument(
        "--definitions-dir",
        type=str,
        default="data/mods",
        help="Path to definitions directory (default: data/mods)",
    )
    parser.add_argument(
        "--archives-dir",
        type=str,
        help="Path to archives directory",
    )
    parser.add_argument(
        "--mods-dir",
        type=str,
        help="Path to extracted mods directory",
    )
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Force LCC cache refresh",
    )
    parser.add_argument(
        "--github-token",
        type=str,
        help="GitHub API token for higher rate limits",
    )
    parser.add_argument(
        "--no-create",
        action="store_true",
        help="Don't create new definitions",
    )
    parser.add_argument(
        "--no-update",
        action="store_true",
        help="Don't update existing definitions",
    )
    parser.add_argument(
        "--no-tp2",
        action="store_true",
        help="Don't extract from TP2 files",
    )
    parser.add_argument(
        "--no-ini",
        action="store_true",
        help="Don't extract from INI files",
    )
    parser.add_argument(
        "--extract-archives",
        action="store_true",
        help="Extract archives for TP2/INI parsing (only .tp2/.tra files)",
    )
    parser.add_argument(
        "--seven-z-path",
        type=str,
        default="7z",
        help="Path to 7z executable (default: 7z)",
    )
    parser.add_argument(
        "--calculate-hashes",
        action="store_true",
        help="Calculate archive hashes",
    )
    parser.add_argument(
        "--check-github",
        action="store_true",
        help="Check GitHub versions",
    )
    parser.add_argument(
        "--only",
        nargs="+",
        action="extend",
        default=[],
        metavar="TP2",
        help="Only synchronize these mods (tp2 names, case-insensitive)",
    )
    parser.add_argument(
        "--game",
        nargs="+",
        action="extend",
        default=[],
        choices=[game.value for game in GameEnum],
        help="Only synchronize mods supporting at least one of these games",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    # Setup logging
    setup_logging(args.verbose)

    # Run appropriate mode
    if args.cli:
        run_cli(args)
    else:
        run_gui()


if __name__ == "__main__":
    main()
