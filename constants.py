from pathlib import Path
import sys

from PySide6.QtCore import Qt

# ============================================================================
# APPLICATION METADATA
# ============================================================================

APP_NAME = "Big World Setup - Next Generation"
APP_VERSION = "0.9.11-beta"
# Pre-release suffix ranks in version comparison; unknown suffixes rank 0
VERSION_SUFFIX_PRIORITY = {"alpha": 1, "beta": 2, "rc": 3}
VERSION_RELEASE_PRIORITY = 4
APP_ORG = "Selphira"

DATA_SCHEMA_VERSION = "1.0"

# ============================================================================
# PATHS
# ============================================================================

# Portable storage is anchored to the application, never to the launch directory.
PROJECT_ROOT: Path = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
RESOURCE_ROOT: Path = PROJECT_ROOT
USER_DATA_ROOT: Path = PROJECT_ROOT
LOCALES_DIR: Path = RESOURCE_ROOT / "i18n"
SCHEMAS_DIR: Path = RESOURCE_ROOT / "validation" / "schemas"
STATE_FILE: Path = USER_DATA_ROOT / "state.json"
DATA_DIR = USER_DATA_ROOT / "data"
MODS_DIR = DATA_DIR / "mods"
GAMES_DIR = DATA_DIR / "games"
RULES_DIR = DATA_DIR / "rules"
CUSTOM_DIR = DATA_DIR / "custom"
CUSTOM_MODS_DIR = CUSTOM_DIR / "mods"
TOOLS_DIR = RESOURCE_ROOT / "tools"
CACHE_DIR = USER_DATA_ROOT / ".cache"
LOG_DIR = USER_DATA_ROOT / "logs"

# Resource directories
RESOURCES_DIR = RESOURCE_ROOT / "resources"
FLAGS_DIR = RESOURCES_DIR / "flags"
ICONS_DIR = RESOURCES_DIR / "icons"
THEMES_DIR = RESOURCES_DIR / "themes"

# Cache subdirectories
LCC_CACHE_DIR = CACHE_DIR / "lcc"

SEVEN_Z_PATH = TOOLS_DIR / "7z.exe"
EXTRACT_DIR = "bws-ng-extract"
INSTALLATION_JOURNAL_NAME = "installation-run.json"
INSTALLATION_JOURNAL_FORMAT = 3
ARCHIVE_REGISTRY_FILE: Path = USER_DATA_ROOT / "archives.json"
ARCHIVE_REGISTRY_FORMAT = 1
# Unreadable persisted files are renamed "<name><suffix><timestamp>" for diagnosis
UNREADABLE_FILE_SUFFIX = ".unreadable-"
UNREADABLE_FILE_STAMP_FORMAT = "%Y%m%d-%H%M%S"

# Extraction: proof of preparation, kept under EXTRACT_DIR of each game folder
EXTRACT_MANIFEST_DIR = ".manifests"
EXTRACT_MANIFEST_FORMAT = 1
# Extraction safety limits, checked against the archive listing before writing
EXTRACT_MAX_UNPACKED_BYTES = 16 * 1024**3
EXTRACT_MAX_ENTRIES = 250_000
EXTRACT_DISK_MARGIN_BYTES = 512 * 1024**2
EXTRACT_TOOL_TIMEOUT_S = 900
# Placeholder replaced by the staging directory in a mod's extraction recipe
EXTRACT_RECIPE_DESTINATION = "{destination}"

# ============================================================================
# TIMING & DELAYS (milliseconds)
# ============================================================================

# Search debounce delay
SEARCH_DEBOUNCE_DELAY = 300

# Installation and shutdown
PROCESS_STOP_GRACE_MS = 3000
PROCESS_POLL_MS = 50
WORKER_POLL_MS = 100
SHUTDOWN_POLL_MS = 100

# Downloads: inactivity timeout and bounded retries with exponential delay
DOWNLOAD_TRANSFER_TIMEOUT_MS = 60_000
DOWNLOAD_MAX_ATTEMPTS = 3
DOWNLOAD_RETRY_BASE_DELAY_MS = 2_000

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
RESOLUTION_PREVIEW_WIDTH = 720
RESOLUTION_PREVIEW_MAX_VISIBLE_ROWS = 12

# Installation decision dialogs listing WeiDU messages
DECISION_DIALOG_MIN_WIDTH = 700
DECISION_DETAILS_MIN_HEIGHT = 250
# Progress bar text while no component is being installed
PROGRESS_IDLE_FORMAT = "%p%"

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
PROCESS_READ_CHUNK_SIZE = 4096

# ============================================================================
# WEIDU
# ============================================================================

# Exit code of a WeiDU run whose components installed with warnings
WEIDU_EXIT_WARNINGS = 3
# Distinct diagnostic lines kept for one component of a DEBUG file
DEBUG_MESSAGES_LIMIT = 200
WEIDU_SETUP_PREFIX = "setup-"
# Generated installer registering download-only components in WeiDU.log
DWN_TP2_PREFIX = "bws-dwn-"
# The generated installer declares no LANGUAGE, so WeiDU uses index 0
DWN_TP2_LANGUAGE = "0"

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
GITHUB_REPO_NAME = "BigWorldSetup-Next-Generation"
GITHUB_API_BASE = "https://api.github.com"
GITHUB_PAGES_BASE = "https://selphira.github.io/BigWorldSetup-Next-Generation"

# Data Update Configuration
DATA_VERSION_FILE = DATA_DIR / "data_version.json"
DATA_VERSION_URL = f"{GITHUB_PAGES_BASE}/data_version.json"
DATA_ZIP_URL = f"{GITHUB_PAGES_BASE}/data.zip"
MAX_RETRIES = 3

# App Update Configuration
APP_UPDATE_CHECK_FILE = CACHE_DIR / "last_update_check.json"

# ============================================================================
# BACKUP AND RESTORATION
# ============================================================================

BACKUP_MANIFEST_VERSION = 1
BACKUP_SNAPSHOT_CONTRACT = "full_snapshot_preserve_user_directories"
BACKUP_USER_DIRECTORIES = frozenset({"save", "mpsave", "portraits"})
BACKUP_SPACE_MARGIN = 1.5
BACKUP_MANIFEST_FILE = "manifest.json"
RESTORE_JOURNAL_FILE = "restore.json"
