"""
Download folder management.

Computes the subfolder path based on the user's chosen structure
(monthly, yearly, author, or flat) and ensures it exists. The qBit
`save_path` parameter is set to this folder when submitting a
torrent, so downloads land directly in the organized structure
without needing a post-download move/copy step.

Supported modes (via settings `download_folder_structure`):
    "monthly" = [YYYY-MM]/ subfolders           (default)
    "yearly"  = [YYYY]/ subfolders
    "author"  = Author Name/ subfolders
    "flat"    = no subfolder, everything in root
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_log = logging.getLogger("seshat.orchestrator.download_folders")


def current_month_folder(
    base_path: str,
    *,
    now: Optional[datetime] = None,
) -> str:
    """Compute the current month's download folder path.

    Args:
        base_path: The qBit base download directory
                   (e.g. "/downloads/[mam-complete]" or
                   "/mnt/user/downloads/[mam-complete]").
        now: Override for testing. Defaults to UTC now.

    Returns the full path including the month subfolder,
    e.g. "/downloads/[mam-complete]/[2026-04]".
    Returns base_path unchanged if it's empty.
    """
    if not base_path:
        return ""

    dt = now or datetime.now(timezone.utc)
    folder_name = f"[{dt.strftime('%Y-%m')}]"
    return str(Path(base_path) / folder_name)


def _normalize_author_folder(author_name: str) -> str:
    """Normalize an author name into a filesystem-safe folder name.

    Strips leading/trailing whitespace, replaces filesystem-unsafe
    characters, and collapses multiple spaces. The goal is to have
    "William D. Arand" and "William D Arand" land in the same folder.
    Empty or whitespace-only names get a fallback "_Unknown" so we
    never pass an empty string to Path().
    """
    import re
    name = (author_name or "").strip()
    if not name:
        return "_Unknown"
    # Remove characters that are illegal or annoying in paths
    name = re.sub(r'[<>:"/\\|?*]', '', name)
    # Collapse dots and multiple spaces (William D. Arand → William D Arand)
    name = name.replace('.', ' ')
    name = re.sub(r'\s+', ' ', name).strip()
    return name or "_Unknown"


def compute_download_folder(
    base_path: str,
    structure: str,
    *,
    author_name: str = "",
    now: Optional[datetime] = None,
) -> Optional[str]:
    """Compute the download subfolder path for the given structure mode.

    Args:
        base_path: qBit base download directory.
        structure: One of "monthly", "yearly", "author", "flat".
        author_name: Author blob from the grab row (only used in
                     "author" mode). Falls back to "_Unknown" if
                     empty/missing.
        now: Override for testing. Defaults to UTC now.

    Returns the full subfolder path, or None when no subfolder is
    needed ("flat") or base_path is empty.
    """
    if not base_path:
        return None

    if structure == "flat":
        return None  # caller passes None → qBit uses its default save_path

    if structure == "yearly":
        dt = now or datetime.now(timezone.utc)
        return str(Path(base_path) / f"[{dt.strftime('%Y')}]")

    if structure == "author":
        folder = _normalize_author_folder(author_name)
        return str(Path(base_path) / folder)

    # Default: monthly (also covers unknown/typo values)
    return current_month_folder(base_path, now=now)


def translate_path(
    path: str,
    from_prefix: str,
    to_prefix: str,
) -> str:
    """Translate a path between container mount namespaces.

    E.g. translate_path("/data/[mam-complete]/book", "/data", "/downloads")
         → "/downloads/[mam-complete]/book"

    Returns the path unchanged if it doesn't start with from_prefix.
    """
    if not path or not from_prefix:
        return path
    from_prefix = from_prefix.rstrip("/")
    to_prefix = to_prefix.rstrip("/")
    if path.startswith(from_prefix + "/") or path == from_prefix:
        return to_prefix + path[len(from_prefix):]
    return path


def ensure_folder_exists(path: str) -> bool:
    """Create the folder if it doesn't exist, with world-writable perms.

    Returns True if the folder exists (or was created), False on error.
    This is called before submitting to qBit so the save_path is valid.
    In Docker, the container needs write access to the mounted volume.

    Permissions are set to 0o777 (world-writable) because the download
    client may run as a different user/group than Seshat. Without
    world-writable, qBit v5's setSavePath/setLocation returns
    "403 Cannot write to directory".
    """
    if not path:
        return False
    try:
        p = Path(path)
        p.mkdir(parents=True, exist_ok=True)
        try:
            import os
            os.chmod(str(p), 0o777)
        except (OSError, PermissionError):
            pass
        return True
    except Exception:
        _log.exception("failed to create download folder: %s", path)
        return False
