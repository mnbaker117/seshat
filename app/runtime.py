"""
Runtime environment detection — Docker vs standalone, OS type, default
data directory paths.

Kept free of any in-app imports so it can be imported early by
`app.config` without circular dependency risk.
"""
import os
import platform as _platform
from pathlib import Path


def _detect_runtime_mode() -> str:
    """Detect Docker vs standalone.

    Priority:
      1. SESHAT_MODE env var (explicit override: 'docker' or 'standalone')
      2. Presence of /.dockerenv (Docker's marker file)
      3. /proc/1/cgroup contains 'docker' or 'containerd'
      4. Default: standalone
    """
    override = os.getenv("SESHAT_MODE", "").lower().strip()
    if override in ("docker", "standalone"):
        return override

    if Path("/.dockerenv").exists():
        return "docker"

    try:
        cgroup = Path("/proc/1/cgroup")
        if cgroup.exists():
            text = cgroup.read_text()
            if "docker" in text or "containerd" in text:
                return "docker"
    except (PermissionError, OSError):
        pass

    return "standalone"


def _get_os_type() -> str:
    """Normalized OS type: 'linux', 'macos', or 'windows'."""
    system = _platform.system().lower()
    if system == "darwin":
        return "macos"
    return system


# Computed once at import time.
RUNTIME_MODE = _detect_runtime_mode()
OS_TYPE = _get_os_type()
IS_DOCKER = RUNTIME_MODE == "docker"
IS_STANDALONE = RUNTIME_MODE == "standalone"


def get_data_dir() -> Path:
    """Where Seshat stores its database, settings, and auth secret.

    Docker: /app/data (set by Dockerfile via DATA_DIR env var)
    Linux standalone: $XDG_DATA_HOME/seshat or ~/.local/share/seshat
    macOS standalone: ~/Library/Application Support/Seshat
    Windows standalone: %LOCALAPPDATA%/Seshat
    """
    if IS_DOCKER:
        return Path("/app/data")

    if OS_TYPE == "windows":
        base = os.environ.get("LOCALAPPDATA", "")
        if base:
            return Path(base) / "Seshat"
        return Path.home() / "AppData" / "Local" / "Seshat"

    if OS_TYPE == "macos":
        return Path.home() / "Library" / "Application Support" / "Seshat"

    xdg = os.environ.get("XDG_DATA_HOME", "")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "seshat"


# ─── Default Library Paths ───────────────────────────────────

def get_default_library_paths() -> list[dict]:
    """Get OS-appropriate default library path suggestions.

    Returns a list of dicts with 'path', 'app_type', and 'description'.
    These are suggestions for the standalone setup wizard — the paths may
    or may not exist on the user's system.

    In Docker mode, returns an empty list. Docker users always configure
    paths via the CALIBRE_PATH / CALIBRE_EXTRA_PATHS environment variables
    pointing at mounted volumes, so OS-default paths inside the container
    are never useful and would only confuse the setup wizard UI.
    """
    # Docker has no concept of "default install location" — paths come
    # from env vars pointing at user-mounted volumes.
    if IS_DOCKER:
        return []

    home = str(Path.home())

    if OS_TYPE == "windows":
        appdata = os.environ.get("APPDATA", "")
        return [
            {
                "path": os.path.join(home, "Calibre Library"),
                "app_type": "calibre",
                "description": "Default Calibre library location",
            },
            {
                "path": os.path.join(appdata, "calibre") if appdata else "",
                "app_type": "calibre",
                "description": "Calibre configuration directory",
            },
        ]

    if OS_TYPE == "macos":
        return [
            {
                "path": os.path.join(home, "Calibre Library"),
                "app_type": "calibre",
                "description": "Default Calibre library location",
            },
        ]

    # Linux
    return [
        {
            "path": os.path.join(home, "Calibre Library"),
            "app_type": "calibre",
            "description": "Default Calibre library location",
        },
        {
            "path": os.path.join(home, "calibre"),
            "app_type": "calibre",
            "description": "Alternative Calibre library location",
        },
    ]


# ─── Platform Info ────────────────────────────────────────────

def get_platform_info() -> dict:
    """Aggregate all platform info into a single dict for the API.

    Used by GET /api/discovery/platform — the setup wizard reads this
    to branch between Docker/standalone flows and to suggest Calibre
    library locations. `/platform` was calling this function before
    it existed (bug dating back to the Phase 2 discovery-port commit,
    dd22c43): the import raised `ImportError: cannot import name
    'get_platform_info'` and the platform endpoint 500'd on every
    first-run wizard load.
    """
    return {
        "runtime_mode": RUNTIME_MODE,
        "os_type": OS_TYPE,
        "is_docker": IS_DOCKER,
        "is_standalone": IS_STANDALONE,
        "data_dir": str(get_data_dir()),
        "default_library_paths": get_default_library_paths(),
        "python_version": _platform.python_version(),
        "platform_detail": _platform.platform(),
    }
