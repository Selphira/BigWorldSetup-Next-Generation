from pathlib import Path

MODS_DIR = Path('data/mods')
CACHE_DIR = Path('.cache')
LCC_CACHE_DIR = CACHE_DIR / "lcc"

SEARCH_DEBOUNCE_DELAY = 300

# ========================================
# COLORS
# ========================================

COLOR_PANEL_BG = "#1e1e1e"
COLOR_SEPARATOR = "#404040"

COLOR_SELECTED_BG = "#655949"
COLOR_UNSELECTED_BG = "#2a2a2a"
COLOR_HOVER_BG = "#333333"
COLOR_SELECTED_TEXT = "#ffffff"
COLOR_UNSELECTED_TEXT = "#cccccc"

COLOR_STATUS_NONE = "#888888"
COLOR_STATUS_COMPLETE = "#00cc00"
COLOR_STATUS_PARTIAL = "#ff9900"
