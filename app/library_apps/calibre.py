"""
Calibre Library App — adapter for Calibre ebook management.
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

    async def sync(self, library: dict) -> dict:
        """Sync Calibre metadata.db into Seshat's discovery database."""
        from app.discovery.calibre_sync import sync_calibre
        return await sync_calibre(
            calibre_db_path=library["source_db_path"],
            calibre_library_path=library["library_path"],
        )

    def get_cover_path(self, book_path: str, library_path: str) -> Optional[str]:
        if not book_path:
            return None
        candidate = os.path.join(library_path, book_path, "cover.jpg")
        return candidate if os.path.exists(candidate) else None
