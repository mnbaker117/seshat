"""
Unified metadata source configuration.

Seshat's metadata / discovery sources were configured across many
scattered settings: `goodreads_enabled`, `rate_goodreads`,
`metadata_provider_priority`, `metadata_audiobook_priority`, and
friends. Phase 7 consolidates them into two keys:

    settings["metadata_sources"] = {
        "goodreads":   {"rate_limit": 2.0,
                        "ebook_enrich": True,  "ebook_scan": True,
                        "audiobook_enrich": False, "audiobook_scan": False},
        "audible":     {"rate_limit": 0.5,
                        "ebook_enrich": False, "ebook_scan": False,
                        "audiobook_enrich": True,  "audiobook_scan": True},
        ...
    }

    settings["metadata_priority"] = {
        "ebook":     ["mam", "goodreads", "hardcover", ...],
        "audiobook": ["mam", "audible", "hardcover", ...],
    }

The ORDER in `metadata_priority[content_type]` defines rank. The
toggles in `metadata_sources[name]` decide which sources run for
which surface (enrich vs scan). Enrich and scan share the same
priority order per content type, but can opt in/out independently.

This module exposes:

* `migrate_legacy_settings(settings)` — one-shot pure function that
  returns a NEW settings dict populated from the legacy keys. Safe
  to call repeatedly; returns early when both new keys already have
  content (idempotent).
* `derive_enrich_priority(settings, audiobook)` — live derivation
  consumed by `_build_metadata_enricher` in main.py.
* `derive_scan_priority(settings, content_type)` — same shape for
  the discovery-side author / library scanners.
* `sync_legacy_keys(settings)` — dual-write helper used by the
  /v1/metadata-sources PATCH path so old code still reading
  `goodreads_enabled` / `rate_goodreads` / `metadata_provider_priority`
  sees consistent values.

Every function is a pure transform over a settings dict — no I/O,
no globals — so unit tests can exercise the edge cases without
touching the real config file.
"""
from __future__ import annotations

import logging
from typing import Any

_log = logging.getLogger("seshat.metadata.source_config")


# All sources the app knows about. The migration seeds an entry for
# each of these (even if the legacy config didn't list them) so the
# new UI always shows every supported source. `available_for`
# describes where a source can run — e.g. Audnexus only makes sense
# for audiobook content, so its ebook toggles stay hidden/disabled
# in the UI.
KNOWN_SOURCES: dict[str, dict[str, Any]] = {
    "mam":         {"display": "MyAnonamouse",  "available_for": ("ebook", "audiobook"), "default_rate": 2.0, "mam_only": True},
    "goodreads":   {"display": "Goodreads",     "available_for": ("ebook", "audiobook"), "default_rate": 2.0},
    "amazon":      {"display": "Amazon",        "available_for": ("ebook",),             "default_rate": 2.0},
    "hardcover":   {"display": "Hardcover",     "available_for": ("ebook", "audiobook"), "default_rate": 1.0},
    "kobo":        {"display": "Kobo",          "available_for": ("ebook",),             "default_rate": 3.0},
    "ibdb":        {"display": "IBDB",          "available_for": ("ebook",),             "default_rate": 1.0},
    "google_books": {"display": "Google Books", "available_for": ("ebook", "audiobook"), "default_rate": 1.5},
    "audible":     {"display": "Audible",       "available_for": ("audiobook",),         "default_rate": 0.5},
    "audnexus":    {"display": "Audnexus",      "available_for": ("audiobook",),         "default_rate": 1.0},
}


# Ship-with defaults derived from live-observed behaviour. Applied
# on fresh-install (no legacy settings present). Users can toggle
# anything after the fact via the Metadata Sources panel.
_DEFAULT_NEW_INSTALL_STATE: dict[str, dict[str, bool]] = {
    "mam":         {"ebook_enrich": True,  "ebook_scan": True,  "audiobook_enrich": True,  "audiobook_scan": True},
    "goodreads":   {"ebook_enrich": True,  "ebook_scan": True,  "audiobook_enrich": True,  "audiobook_scan": True},
    "amazon":      {"ebook_enrich": True,  "ebook_scan": True,  "audiobook_enrich": False, "audiobook_scan": False},
    "hardcover":   {"ebook_enrich": True,  "ebook_scan": True,  "audiobook_enrich": True,  "audiobook_scan": True},
    "kobo":        {"ebook_enrich": True,  "ebook_scan": True,  "audiobook_enrich": False, "audiobook_scan": False},
    "ibdb":        {"ebook_enrich": False, "ebook_scan": False, "audiobook_enrich": False, "audiobook_scan": False},
    # Google Books defaults off for audiobook enrich — rate-limited
    # and carries no audiobook-specific fields (narrator, duration,
    # ASIN). Keeps firing for ebook grabs where it's useful.
    "google_books": {"ebook_enrich": True, "ebook_scan": True,  "audiobook_enrich": False, "audiobook_scan": False},
    "audible":     {"ebook_enrich": False, "ebook_scan": False, "audiobook_enrich": True,  "audiobook_scan": True},
    # Audnexus defaults off for audiobook enrich — 0 matches across
    # live test corpus (Halo: Empty Throne / Outcasts / Legacy of
    # Onyx). Kept on for scan where catalog breadth matters less.
    "audnexus":    {"ebook_enrich": False, "ebook_scan": False, "audiobook_enrich": False, "audiobook_scan": True},
}


# Default priority order used on fresh installs. MAM always first —
# it's free and authoritative. The rest follows a "most-coverage-
# first, specialized-last" ordering per content type.
_DEFAULT_EBOOK_PRIORITY: list[str] = [
    "mam", "goodreads", "amazon", "hardcover", "kobo", "ibdb",
    "google_books", "audible", "audnexus",
]
_DEFAULT_AUDIOBOOK_PRIORITY: list[str] = [
    "mam", "audible", "audnexus", "goodreads", "hardcover",
    "google_books", "amazon", "kobo", "ibdb",
]


# ─── Migration ────────────────────────────────────────────────


def migrate_legacy_settings(settings: dict) -> bool:
    """Populate `metadata_sources` + `metadata_priority` from legacy keys.

    Mutates `settings` in-place. Returns True when a migration ran,
    False when the new shape was already populated (idempotent no-op).

    Migration sources:
      * `metadata_provider_priority`  — priority order for ebook
      * `metadata_audiobook_priority` — priority order for audiobook
      * `goodreads_enabled` / `hardcover_enabled` / `kobo_enabled` /
        `amazon_enabled` / `ibdb_enabled` / `google_books_enabled` /
        `audible_enabled`  — discovery-side scan toggles
      * `rate_goodreads` / `rate_hardcover` / `rate_kobo` /
        `rate_amazon` / `rate_ibdb` / `rate_google_books` /
        `rate_audible` / `rate_mam`  — per-source rate limits

    On fresh install (all legacy keys empty/missing), seeds sensible
    ship-with defaults from `_DEFAULT_NEW_INSTALL_STATE`.
    """
    existing_sources = settings.get("metadata_sources") or {}
    existing_priority = settings.get("metadata_priority") or {}

    # Already migrated — new shape has entries AND at least one
    # priority list is populated. Nothing to do.
    has_sources = bool(existing_sources)
    has_priority = bool(
        (existing_priority.get("ebook") or [])
        or (existing_priority.get("audiobook") or [])
    )
    if has_sources and has_priority:
        return False

    ebook_priority = _derive_priority_list(
        settings, "metadata_provider_priority", _DEFAULT_EBOOK_PRIORITY,
    )
    audiobook_priority = _derive_priority_list(
        settings, "metadata_audiobook_priority", _DEFAULT_AUDIOBOOK_PRIORITY,
    )

    sources: dict[str, dict[str, Any]] = {}
    for name, meta in KNOWN_SOURCES.items():
        sources[name] = _build_source_entry(name, meta, settings)

    settings["metadata_sources"] = sources
    settings["metadata_priority"] = {
        "ebook": ebook_priority,
        "audiobook": audiobook_priority,
    }
    _log.info(
        "metadata sources migrated: %d sources, ebook priority=%d, "
        "audiobook priority=%d",
        len(sources), len(ebook_priority), len(audiobook_priority),
    )
    return True


def _derive_priority_list(
    settings: dict, legacy_key: str, fallback: list[str],
) -> list[str]:
    """Build a priority list: legacy value if non-empty, else fallback."""
    raw = settings.get(legacy_key) or []
    if not isinstance(raw, list):
        return list(fallback)
    cleaned = [str(n).strip() for n in raw if isinstance(n, str) and n.strip()]
    if cleaned:
        # Ensure MAM is pinned at position 0 even if the legacy
        # priority list didn't include it — discovery-side lists
        # historically omitted MAM (which is ebook-only there).
        if "mam" not in cleaned:
            cleaned = ["mam"] + cleaned
        return cleaned
    return list(fallback)


def _build_source_entry(
    name: str, meta: dict, settings: dict,
) -> dict[str, Any]:
    """Construct one `metadata_sources[name]` row from legacy keys."""
    defaults = _DEFAULT_NEW_INSTALL_STATE.get(name, {
        "ebook_enrich": False, "ebook_scan": False,
        "audiobook_enrich": False, "audiobook_scan": False,
    })

    # Read legacy scan toggle: `<name>_enabled`. When absent fall
    # back to the ship-with default. MAM is special-cased because
    # `mam_enabled` guards the whole IRC listener, not just source
    # scanning — don't inherit that here.
    legacy_scan_key = f"{name}_enabled"
    if name == "mam":
        ebook_scan = defaults["ebook_scan"]
        audiobook_scan = defaults["audiobook_scan"]
    else:
        scan_enabled = bool(
            settings.get(legacy_scan_key, defaults.get("ebook_scan", False))
        )
        # One legacy bool, two new surfaces. Preserve availability:
        # a source like Kobo (ebook-only) never gets its audiobook
        # toggle turned on regardless of the legacy bool.
        avail = meta.get("available_for", ())
        ebook_scan = scan_enabled and "ebook" in avail
        audiobook_scan = scan_enabled and "audiobook" in avail

    ebook_priority_list = settings.get("metadata_provider_priority") or []
    audiobook_priority_list = settings.get("metadata_audiobook_priority") or []
    ebook_enrich = (
        name in ebook_priority_list
        if ebook_priority_list else defaults["ebook_enrich"]
    )
    audiobook_enrich = (
        name in audiobook_priority_list
        if audiobook_priority_list else defaults["audiobook_enrich"]
    )

    rate_limit = _legacy_rate_for(name, settings, meta["default_rate"])

    return {
        "rate_limit": float(rate_limit),
        "ebook_enrich": bool(ebook_enrich),
        "ebook_scan": bool(ebook_scan),
        "audiobook_enrich": bool(audiobook_enrich),
        "audiobook_scan": bool(audiobook_scan),
    }


def _legacy_rate_for(
    name: str, settings: dict, default: float,
) -> float:
    """Read `rate_<name>` from legacy settings, with fallback."""
    key = f"rate_{name}"
    raw = settings.get(key)
    if raw is None:
        return float(default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(default)


# ─── Derivation (live reads) ──────────────────────────────────


def derive_enrich_priority(
    settings: dict, *, audiobook: bool,
) -> list[str]:
    """Ordered list of sources to run for enrichment.

    Reads `metadata_priority[content_type]` and filters to sources
    whose `*_enrich` toggle is True in `metadata_sources`. This is
    what `_build_metadata_enricher` consumes to construct the
    per-surface source list.
    """
    content_type = "audiobook" if audiobook else "ebook"
    surface = "audiobook_enrich" if audiobook else "ebook_enrich"
    return _filter_priority(settings, content_type, surface)


def derive_scan_priority(
    settings: dict, *, audiobook: bool,
) -> list[str]:
    """Ordered list of sources to run for discovery-side scanning."""
    content_type = "audiobook" if audiobook else "ebook"
    surface = "audiobook_scan" if audiobook else "ebook_scan"
    return _filter_priority(settings, content_type, surface)


def _filter_priority(
    settings: dict, content_type: str, surface: str,
) -> list[str]:
    priority = (settings.get("metadata_priority") or {}).get(content_type) or []
    sources = settings.get("metadata_sources") or {}
    out: list[str] = []
    for name in priority:
        entry = sources.get(name)
        if entry and entry.get(surface):
            out.append(name)
    return out


def get_source_rate_limit(settings: dict, name: str) -> float:
    """Rate limit (queries/sec) for a single source, with fallback."""
    entry = (settings.get("metadata_sources") or {}).get(name) or {}
    raw = entry.get("rate_limit")
    if raw is None:
        meta = KNOWN_SOURCES.get(name, {})
        return float(meta.get("default_rate", 1.0))
    try:
        return float(raw)
    except (TypeError, ValueError):
        meta = KNOWN_SOURCES.get(name, {})
        return float(meta.get("default_rate", 1.0))


# ─── Dual-write (keep legacy keys in sync) ────────────────────


def sync_legacy_keys(settings: dict) -> None:
    """Mirror the new shape back onto legacy keys.

    Called by the `/v1/metadata-sources` PATCH handler after a user
    edits the panel. Keeps `goodreads_enabled` / `rate_goodreads` /
    `metadata_provider_priority` / `metadata_audiobook_priority`
    consistent so any part of the codebase still reading the old
    names during the Phase 7 transition sees the same truth.

    After every consumer is ported to the derivation helpers, this
    function and the legacy keys can be retired together.
    """
    sources = settings.get("metadata_sources") or {}
    priority = settings.get("metadata_priority") or {}

    # Per-source legacy bools. The old `*_enabled` flag gated
    # discovery-side scanning, so mirror from the scan surface.
    # Take ebook_scan OR audiobook_scan — either surface enabled
    # means the source is "on" in the legacy single-bool sense.
    for name, meta in KNOWN_SOURCES.items():
        if name == "mam":
            continue  # mam_enabled is a separate, wider-scope toggle
        entry = sources.get(name) or {}
        legacy_on = bool(
            entry.get("ebook_scan") or entry.get("audiobook_scan")
        )
        settings[f"{name}_enabled"] = legacy_on
        rate_key = f"rate_{name}"
        if "rate_limit" in entry:
            settings[rate_key] = float(entry["rate_limit"])

    # Rate limit for MAM sits separately under `rate_mam`.
    mam_entry = sources.get("mam") or {}
    if "rate_limit" in mam_entry:
        settings["rate_mam"] = float(mam_entry["rate_limit"])

    # Priority lists: legacy shape is just the filtered enrich list.
    settings["metadata_provider_priority"] = derive_enrich_priority(
        settings, audiobook=False,
    )
    settings["metadata_audiobook_priority"] = derive_enrich_priority(
        settings, audiobook=True,
    )
