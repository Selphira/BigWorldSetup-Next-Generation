"""Example configuration for mod synchronization.

This file shows how to programmatically use the ModSyncOrchestrator
for custom workflows or integration into larger systems.
"""

from pathlib import Path

from util.mod_sync import ModFilter, ModSyncOrchestrator, SyncConfig


def example_basic_sync():
    """Basic synchronization: LCC only, no extras."""
    config = SyncConfig(
        definitions_dir=Path("data/mods"),
        force_lcc_refresh=False,
        create_new=True,
        update_existing=True,
        extract_tp2=False,
        extract_ini=False,
        calculate_hashes=False,
        check_github_versions=False,
    )

    orchestrator = ModSyncOrchestrator(config)
    stats = orchestrator.run()

    print(stats.get_summary())
    return stats


def example_full_sync_with_extras():
    """Full synchronization with TP2, INI, and hashes."""
    config = SyncConfig(
        definitions_dir=Path("data/mods"),
        archives_dir=Path("downloads/mods"),
        mods_dir=Path("extracted/mods"),
        force_lcc_refresh=False,
        github_token="ghp_YOUR_TOKEN_HERE",  # Optional but recommended
        create_new=True,
        update_existing=True,
        extract_tp2=True,
        extract_ini=True,
        calculate_hashes=True,
        check_github_versions=False,
    )

    def progress_callback(current, total, mod_name):
        """Custom progress tracking."""
        percentage = (current / total) * 100
        print(f"[{percentage:5.1f}%] {current}/{total}: {mod_name}")

    orchestrator = ModSyncOrchestrator(config)
    stats = orchestrator.run(progress_callback)

    print(stats.get_summary())

    # Handle outdated mods
    if stats.github_outdated:
        print("\n⚠️  Outdated mods found:")
        for tp2, current, latest in stats.github_outdated:
            print(f"  {tp2}: {current} → {latest}")

    # Handle errors
    if stats.errors > 0:
        print(f"\n❌ {stats.errors} errors occurred:")
        for result in stats.results:
            if result.status.value == "error":
                print(f"  {result.tp2_name}: {result.message}")

    return stats


def example_update_only():
    """Update existing definitions only, don't create new ones."""
    config = SyncConfig(
        definitions_dir=Path("data/mods"),
        mods_dir=Path("extracted/mods"),
        create_new=False,  # Don't create new
        update_existing=True,
        extract_tp2=True,
        extract_ini=True,
    )

    orchestrator = ModSyncOrchestrator(config)
    stats = orchestrator.run()

    print(f"Updated {stats.updated} existing definitions")
    return stats


def example_github_version_check_only():
    """Only check GitHub versions, don't modify definitions."""
    config = SyncConfig(
        definitions_dir=Path("data/mods"),
        github_token="ghp_YOUR_TOKEN_HERE",
        create_new=False,
        update_existing=False,
        extract_tp2=False,
        extract_ini=False,
        calculate_hashes=False,
        check_github_versions=True,
    )

    orchestrator = ModSyncOrchestrator(config)
    stats = orchestrator.run()

    if stats.github_outdated:
        print("Outdated mods:")
        for tp2, current, latest in stats.github_outdated:
            print(f"  - {tp2}: {current} → {latest}")
    else:
        print("All mods are up to date!")

    return stats


def example_custom_filtering():
    """Sync only specific mods supporting specific games."""
    config = SyncConfig(
        definitions_dir=Path("data/mods"),
        mod_filter=ModFilter(
            tp2_names=frozenset({"ascension", "tweaks", "bg2fixpack"}),
            games=frozenset({"bg2ee", "eet"}),
        ),
    )

    orchestrator = ModSyncOrchestrator(config)
    stats = orchestrator.run()

    # Mods left out by the filter are reported as skipped, never as errors
    print(stats.get_summary())
    return stats


def example_batch_processing():
    """Process mods in batches for better control."""
    from util.mod_sync.sources.lcc_source import LCCSource

    lcc = LCCSource()
    lcc.fetch_all()

    all_mods = list(lcc.get_all_mods().keys())
    batch_size = 100

    for i in range(0, len(all_mods), batch_size):
        batch = all_mods[i : i + batch_size]
        print(f"\nProcessing batch {i // batch_size + 1}: {len(batch)} mods")

        # Process batch...
        # (Would require custom orchestrator logic)


def example_with_error_handling():
    """Robust synchronization with comprehensive error handling."""
    import logging

    # Setup detailed logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler("sync.log"),
            logging.StreamHandler(),
        ],
    )

    config = SyncConfig(
        definitions_dir=Path("data/mods"),
        mods_dir=Path("extracted/mods"),
        create_new=True,
        update_existing=True,
        extract_tp2=True,
        extract_ini=True,
    )

    try:
        orchestrator = ModSyncOrchestrator(config)
        stats = orchestrator.run()

        # Success summary
        print("\n" + "=" * 60)
        print(stats.get_summary())

        # Save results to file
        with open("sync_results.txt", "w") as f:
            f.write(stats.get_summary())
            for result in stats.results:
                f.write(f"{result}\n")

        return stats

    except Exception as e:
        logging.error(f"Fatal error during synchronization: {e}", exc_info=True)
        print(f"\n❌ Synchronization failed: {e}")
        return None


def example_programmatic_access():
    """Access individual sources programmatically."""
    from util.mod_sync.sources import IniSource, LCCSource, TP2Source

    # LCC data
    lcc = LCCSource()
    lcc.fetch_all()
    ascension_data = lcc.find_by_tp2("ascension")
    print(f"Ascension from LCC: {ascension_data['name']}")

    # TP2 data
    tp2 = TP2Source(Path("extracted/mods"))
    tp2_data = tp2.extract("ascension")
    if tp2_data:
        print(f"Ascension version from TP2: {tp2_data.get('version')}")

    # INI data
    ini = IniSource(Path("extracted/mods"))
    ini_data = ini.extract("ascension")
    if ini_data:
        print(f"Ascension homepage from INI: {ini_data.get('links', {}).get('homepage')}")


if __name__ == "__main__":
    # Run the basic example
    print("Running basic sync example...\n")
    example_basic_sync()

    # Or uncomment to run other examples:
    # example_full_sync_with_extras()
    # example_update_only()
    # example_github_version_check_only()
    # example_with_error_handling()
