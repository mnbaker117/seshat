"""
Library app base class — the interface every library backend implements.

Each library app represents a different ebook or audiobook management
application that Seshat can sync from for its discovery domain.
"""
import os
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

logger = logging.getLogger("seshat.library_apps")


class LibraryApp(ABC):
    """Abstract base class for library source applications."""

    app_type: str = ""
    content_type: str = "ebook"
    display_name: str = ""
    db_filename: str = ""
    env_root_var: str = ""
    env_extra_var: str = ""

    def get_root_path(self) -> str:
        return os.getenv(self.env_root_var, "")

    def get_extra_paths(self) -> list:
        raw = os.getenv(self.env_extra_var, "")
        if not raw:
            return []
        paths = [p.strip() for p in raw.split(",") if p.strip()]
        valid = []
        for p in paths:
            try:
                exists = Path(p).exists()
            except (PermissionError, OSError) as e:
                logger.warning(f"{self.display_name}: extra path unreadable ({e}): {p}")
                exists = False
            if exists:
                valid.append(p)
            else:
                logger.warning(f"{self.display_name}: extra path does not exist: {p}")
        return valid

    def discover(self, root_path: str) -> list:
        """Discover libraries under a root path.

        Scans one level deep for directories containing self.db_filename.
        """
        from app.config import slugify
        libraries = []
        seen_slugs = set()
        root = Path(root_path)

        try:
            if not root.exists():
                logger.warning(f"{self.display_name}: root path does not exist: {root_path}")
                return []
        except PermissionError:
            logger.warning(f"{self.display_name}: cannot stat root path: {root_path}")
            return []

        def _add(mdb_path):
            parent = mdb_path.parent
            name = parent.name
            slug = slugify(name)
            base_slug = slug
            counter = 2
            while slug in seen_slugs:
                slug = f"{base_slug}-{counter}"
                counter += 1
            seen_slugs.add(slug)
            libraries.append({
                "name": name,
                "slug": slug,
                "app_type": self.app_type,
                "content_type": self.content_type,
                "display_name": self.display_name,
                "source_db_path": str(mdb_path),
                "library_path": str(parent),
            })

        def _safe_db_exists(db_file: Path) -> bool:
            try:
                return db_file.exists()
            except (PermissionError, OSError):
                return False

        try:
            children = sorted(root.iterdir())
        except (PermissionError, OSError) as e:
            logger.warning(f"{self.display_name}: cannot list {root_path} ({e})")
            return []

        for child in children:
            if child.name.startswith("."):
                continue
            try:
                is_dir = child.is_dir()
            except (PermissionError, OSError):
                continue
            if is_dir:
                db_file = child / self.db_filename
                if _safe_db_exists(db_file):
                    _add(db_file)

        root_db = root / self.db_filename
        if _safe_db_exists(root_db):
            _add(root_db)

        return libraries

    @abstractmethod
    async def sync(self, source_db_path: str, library_path: str) -> dict:
        """Sync from the source database into Seshat's discovery database."""
        pass

    @abstractmethod
    def get_cover_path(self, book_path: str, library_path: str) -> Optional[str]:
        """Get the filesystem path to a book's cover image."""
        pass

    def get_mtime(self, source_db_path: str) -> float:
        try:
            return os.path.getmtime(source_db_path)
        except OSError:
            return 0.0
