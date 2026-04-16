"""
Calibre Library App — adapter for Calibre ebook management.

Note: The actual sync logic (calibre_sync.py) will be ported in Phase 2
when the full discovery domain is brought over. For now, this provides
the discovery interface that config.discover_libraries() needs.
"""
import os
import logging
from typing import Optional
from app.library_apps.base import LibraryApp

logger = logging.getLogger("seshat.library_apps.calibre")


class CalibreApp(LibraryApp):
    """Calibre ebook library source."""

    app_type = "calibre"
    content_type = "ebook"
    display_name = "Calibre"
    db_filename = "metadata.db"
    env_root_var = "CALIBRE_PATH"
    env_extra_var = "CALIBRE_EXTRA_PATHS"

    async def sync(self, source_db_path: str, library_path: str) -> dict:
        """Sync Calibre metadata.db into Seshat's discovery database."""
        from app.discovery.calibre_sync import sync_calibre
        return await sync_calibre(
            calibre_db_path=source_db_path,
            calibre_library_path=library_path,
        )

    def get_cover_path(self, book_path: str, library_path: str) -> Optional[str]:
        if not book_path:
            return None
        candidate = os.path.join(library_path, book_path, "cover.jpg")
        return candidate if os.path.exists(candidate) else None
