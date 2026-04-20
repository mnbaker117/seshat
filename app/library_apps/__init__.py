"""
Library app registry — central registry of supported library backends.

Each registered app becomes a candidate backend that the user can point
at a library directory (or API endpoint) for discovery and sync.
"""
from app.library_apps.audiobookshelf import AudiobookshelfApp
from app.library_apps.calibre import CalibreApp

LIBRARY_APPS = {
    "calibre": CalibreApp(),
    "audiobookshelf": AudiobookshelfApp(),
}


def get_app(app_type):
    """Get a registered library app by type string."""
    return LIBRARY_APPS.get(app_type)


def get_all_apps():
    """Get all registered library apps."""
    return LIBRARY_APPS
