from pathlib import Path

from PySide6.QtCore import Qt

# ============================================================================
# APPLICATION METADATA
# ============================================================================

APP_NAME = "Big World Setup - Enhanced Edition"
APP_VERSION = "0.9.7-beta"
APP_ORG = "Selphira"

# ============================================================================
# PATHS
# ============================================================================

# Data directories
DATA_DIR = Path("data")
MODS_DIR = DATA_DIR / "mods"
GAMES_DIR = DATA_DIR / "games"
RULES_DIR = DATA_DIR / "rules"
CUSTOM_DIR = DATA_DIR / "custom"
CUSTOM_MODS_DIR = CUSTOM_DIR / "mods"
TOOLS_DIR = Path("tools")
CACHE_DIR = Path(".cache")
LOG_DIR = Path("logs")

# Resource directories
RESOURCES_DIR = Path("resources")
FLAGS_DIR = RESOURCES_DIR / "flags"
ICONS_DIR = RESOURCES_DIR / "icons"
THEMES_DIR = RESOURCES_DIR / "themes"

# Cache subdirectories
LCC_CACHE_DIR = CACHE_DIR / "lcc"

SEVEN_Z_PATH = TOOLS_DIR / "7z.exe"
EXTRACT_DIR = "bws-ee-extract"

# ============================================================================
# TIMING & DELAYS (milliseconds)
# ============================================================================

# Search debounce delay
SEARCH_DEBOUNCE_DELAY = 300

# ============================================================================
# COLORS
# ============================================================================

COLOR_BACKGROUND_PRIMARY = "#1e1e1e"
COLOR_BACKGROUND_SECONDARY = "#2a2a2a"
COLOR_BACKGROUND_ACCENTED = "#333333"
COLOR_BACKGROUND_HIGHLIGHT = "#ffff00"
COLOR_BACKGROUND_WARNING = "#7a5f0c"
COLOR_BACKGROUND_ERROR = "#5f2120"

COLOR_ACCENT = "#655949"
COLOR_ACCENT_FOCUS = "Goldenrod"

# Text colors
COLOR_TEXT = "#ffffff"
COLOR_TEXT_UNSELECTED = "#cccccc"
COLOR_TEXT_DISABLED = "#666666"
COLOR_TEXT_HIGHLIGHT = "#000000"

# Status colors
COLOR_STATUS_NONE = "#888888"
COLOR_STATUS_PARTIAL = "#ff9900"
COLOR_STATUS_COMPLETE = "#00cc00"

# Validation colors
COLOR_SUCCESS = "#00cc00"
COLOR_ERROR = "#ff6666"
COLOR_WARNING = "#ffaa00"
COLOR_INFO = "#4aa3ff"

# ============================================================================
# ICONS & EMOJIS
# ============================================================================

# Navigation arrows
ICON_ARROW_LEFT = "←"
ICON_ARROW_RIGHT = "→"
ICON_ARROW_UP = "↑"
ICON_ARROW_DOWN = "↓"

# Status indicators
ICON_SUCCESS = "✓"
ICON_ERROR = "✗"
ICON_WARNING = "⚠"
ICON_INFO = "ℹ"
ICON_SKIPPED = "⊘"
ICON_INSTALLED = "↷"

# UI elements
ICON_SEARCH = "🔍"
ICON_FILTER = "🔽"
ICON_CLEAR = "✕"
ICON_MENU = "☰"

# Default fallback icons
ICON_GAME_DEFAULT = "🎮"
ICON_LANGUAGE_DEFAULT = "🌐"
ICON_MOD_DEFAULT = "📦"

# ============================================================================
# SIZES & SPACING
# ============================================================================

# Window dimensions
WINDOW_MIN_WIDTH = 1200
WINDOW_MIN_HEIGHT = 800

# Header & Footer heights
HEADER_HEIGHT = 80
FOOTER_HEIGHT = 70

# Button dimensions
BUTTON_WIDTH_STANDARD = 150
BUTTON_WIDTH_SMALL = 100
BUTTON_HEIGHT_STANDARD = 32

# Icon sizes
ICON_SIZE_SMALL = 16
ICON_SIZE_MEDIUM = 24
ICON_SIZE_LARGE = 32
ICON_SIZE_XLARGE = 64

# Game button
GAME_BUTTON_HEIGHT = 120
GAME_BUTTON_ICON_SIZE = 64

# Spacing constants
SPACING_TINY = 2
SPACING_SMALL = 5
SPACING_MEDIUM = 10
SPACING_LARGE = 15
SPACING_XLARGE = 20

# Margin constants
MARGIN_TINY = 5
MARGIN_SMALL = 10
MARGIN_STANDARD = 15
MARGIN_LARGE = 20

# Badge constants
BADGE_MIN_WIDTH = 35
BADGE_HEIGHT = 18

# ============================================================================
# FONT SETTINGS
# ============================================================================

FONT_SIZE_SMALL = 9
FONT_SIZE_NORMAL = 10
FONT_SIZE_MEDIUM = 11
FONT_SIZE_LARGE = 14
FONT_SIZE_TITLE = 20

# ============================================================================
# FILE SIZE LIMITS
# ============================================================================

MAX_LUA_FILE_SIZE = 10 * 1024 * 1024  # 10 MB

# ============================================================================
# INPUT LIMITS
# ============================================================================

MAX_PATH_LENGTH = 260  # Windows MAX_PATH
MIN_SEARCH_LENGTH = 3
MAX_SEARCH_LENGTH = 100

# ============================================================================
# LOGGING CONFIGURATION
# ============================================================================

LOG_FILE_NAME = "bws_ng.log"
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
LOG_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
LOG_BACKUP_COUNT = 5

# ============================================================================
# QT DATA ROLES (Custom ItemDataRole extensions)
# ============================================================================

# Mod/Component data roles
ROLE_MOD = Qt.ItemDataRole.UserRole + 1
ROLE_COMPONENT = Qt.ItemDataRole.UserRole + 2
ROLE_OPTION_KEY = Qt.ItemDataRole.UserRole + 3
ROLE_PROMPT_KEY = Qt.ItemDataRole.UserRole + 4
ROLE_IS_DEFAULT = Qt.ItemDataRole.UserRole + 5

# Filtering roles
ROLE_GAMES = Qt.ItemDataRole.UserRole + 10
ROLE_CATEGORY = Qt.ItemDataRole.UserRole + 11
ROLE_AUTHOR = Qt.ItemDataRole.UserRole + 12
ROLE_LANGUAGES = Qt.ItemDataRole.UserRole + 13

ROLE_RADIO = Qt.ItemDataRole.UserRole + 100
ROLE_BACKGROUND = Qt.ItemDataRole.UserRole + 101

# ============================================================================
# NETWORK CONFIGURATION
# ============================================================================

DOWNLOAD_TIMEOUT = 30  # seconds
DOWNLOAD_CHUNK_SIZE = 8192  # bytes
MAX_CONCURRENT_DOWNLOADS = 3
USER_AGENT = f"{APP_NAME}/{APP_VERSION}"

# GitHub Configuration
GITHUB_REPO_OWNER = "selphira"
GITHUB_REPO_NAME = "BigWorldSetup-Enhanced-Edition"
GITHUB_API_BASE = "https://api.github.com"
GITHUB_PAGES_BASE = "https://selphira.github.io/BigWorldSetup-Enhanced-Edition"

# Data Update Configuration
DATA_VERSION_FILE = CACHE_DIR / "data_version.json"
DATA_VERSION_URL = f"{GITHUB_PAGES_BASE}/data_version.json"
DATA_ZIP_URL = f"{GITHUB_PAGES_BASE}/data.zip"
MAX_RETRIES = 3

# App Update Configuration
APP_UPDATE_CHECK_FILE = CACHE_DIR / "last_update_check.json"
