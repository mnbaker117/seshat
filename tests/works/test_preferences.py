"""
Per-author format preference tests.
"""
from __future__ import annotations

import pytest

from app.works import preferences


async def test_set_then_get(temp_db):
    await preferences.set_preference("Brandon Sanderson", "audiobook")
    pref = await preferences.get_preference("Brandon Sanderson")
    assert pref is not None
    assert pref.tracking_mode == "audiobook"
    assert pref.display_name == "Brandon Sanderson"


async def test_invalid_mode_raises(temp_db):
    with pytest.raises(ValueError, match="tracking_mode"):
        await preferences.set_preference("Someone", "invalid-mode")


async def test_empty_author_raises(temp_db):
    with pytest.raises(ValueError, match="non-empty"):
        await preferences.set_preference("   ", "both")


async def test_unset_returns_none(temp_db):
    assert await preferences.get_preference("Nobody Famous") is None


async def test_set_upserts(temp_db):
    await preferences.set_preference("Author", "ebook")
    await preferences.set_preference("Author", "audiobook")
    pref = await preferences.get_preference("Author")
    assert pref.tracking_mode == "audiobook"


async def test_clear_removes_row(temp_db):
    await preferences.set_preference("Author", "both")
    assert await preferences.clear_preference("Author") is True
    assert await preferences.get_preference("Author") is None
    assert await preferences.clear_preference("Author") is False


async def test_normalized_lookup(temp_db):
    """Case / whitespace differences share the same preference row."""
    await preferences.set_preference("Brandon Sanderson", "audiobook")
    a = await preferences.get_preference("BRANDON SANDERSON")
    b = await preferences.get_preference("brandon   sanderson")
    assert a is not None and a.tracking_mode == "audiobook"
    assert b is not None and b.tracking_mode == "audiobook"


async def test_list_preferences_ordered(temp_db):
    await preferences.set_preference("Zora Neale Hurston", "both")
    await preferences.set_preference("Alan Moore", "ebook")
    rows = await preferences.list_preferences()
    assert [r.display_name for r in rows] == ["Alan Moore", "Zora Neale Hurston"]


async def test_effective_mode_uses_global_default(temp_db, monkeypatch):
    """No per-author pref → fall through to settings.audiobook_tracking_mode."""
    from app import config
    monkeypatch.setattr(
        config, "load_settings",
        lambda: {"audiobook_tracking_mode": "ebook"},
    )
    # Also need preferences._global_default's late import:
    mode = await preferences.effective_tracking_mode("No Author Set")
    assert mode == "ebook"


async def test_effective_mode_override_wins(temp_db, monkeypatch):
    from app import config
    monkeypatch.setattr(
        config, "load_settings",
        lambda: {"audiobook_tracking_mode": "ebook"},
    )
    await preferences.set_preference("Override Author", "audiobook")
    assert await preferences.effective_tracking_mode("Override Author") == "audiobook"


async def test_effective_mode_rejects_garbage_global(temp_db, monkeypatch):
    """If settings.audiobook_tracking_mode is garbage, fall back to 'both'."""
    from app import config
    monkeypatch.setattr(
        config, "load_settings",
        lambda: {"audiobook_tracking_mode": "chicken"},
    )
    assert await preferences.effective_tracking_mode("Random") == "both"
