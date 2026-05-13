"""
Tests for the Phase 7 unified metadata source configuration.

Exercises `migrate_legacy_settings`, `derive_enrich_priority`,
`derive_scan_priority`, and `sync_legacy_keys` against a range of
settings shapes: fresh install, legacy settings, partial legacy,
and already-migrated (idempotent).
"""
from app.metadata.source_config import (
    KNOWN_SOURCES,
    derive_enrich_priority,
    derive_scan_priority,
    get_source_rate_limit,
    is_source_mandatory,
    migrate_legacy_settings,
    sync_legacy_keys,
)


class TestMigration:
    def test_fresh_install_seeds_defaults(self):
        """No legacy keys → ship-with defaults, every known source
        present, MAM pinned first in both priority lists."""
        settings: dict = {}
        assert migrate_legacy_settings(settings) is True

        sources = settings["metadata_sources"]
        priority = settings["metadata_priority"]

        # Every known source has an entry.
        assert set(sources.keys()) == set(KNOWN_SOURCES.keys())

        # MAM pinned first in both content types.
        assert priority["ebook"][0] == "mam"
        assert priority["audiobook"][0] == "mam"

        # Shipped defaults: Audible on for audiobook, off for ebook.
        assert sources["audible"]["audiobook_enrich"] is True
        assert sources["audible"]["ebook_enrich"] is False

        # Google Books off for audiobook enrich (rate-limited, no
        # audiobook fields).
        assert sources["google_books"]["audiobook_enrich"] is False
        assert sources["google_books"]["ebook_enrich"] is True

    def test_legacy_priority_preserved(self):
        """When the legacy priority list is populated, migration
        preserves its order — MAM gets prepended if absent.
        Retired source names (audnexus) are scrubbed. Per v2.10.8,
        any KNOWN_SOURCES not represented in the legacy list get
        appended at the END (so the user-curated head order stays
        intact)."""
        settings = {
            "metadata_provider_priority": ["goodreads", "hardcover", "amazon"],
            # Legacy list that includes the retired audnexus entry —
            # scrub should drop it but preserve the rest.
            "metadata_audiobook_priority": ["audible", "audnexus", "hardcover"],
        }
        migrate_legacy_settings(settings)
        # User's head order is preserved.
        assert settings["metadata_priority"]["ebook"][:4] == [
            "mam", "goodreads", "hardcover", "amazon",
        ]
        assert settings["metadata_priority"]["audiobook"][:3] == [
            "mam", "audible", "hardcover",
        ]
        # Backfill appended the rest of KNOWN_SOURCES at the tail.
        # Order doesn't matter; presence does.
        ebook_tail = set(settings["metadata_priority"]["ebook"][4:])
        assert "kobo" in ebook_tail
        assert "openlibrary" in ebook_tail
        # Retired entries never come back.
        assert "audnexus" not in settings["metadata_priority"]["ebook"]
        assert "audnexus" not in settings["metadata_priority"]["audiobook"]

    def test_legacy_enabled_flags_respected(self):
        """`<name>_enabled` → per-source scan toggles honour availability."""
        settings = {
            "goodreads_enabled": True,
            "kobo_enabled": False,
            "audible_enabled": True,  # ebook-unavailable — should not flip ebook_scan
        }
        migrate_legacy_settings(settings)
        sources = settings["metadata_sources"]

        # Goodreads available for both, scan flipped on both surfaces.
        assert sources["goodreads"]["ebook_scan"] is True
        assert sources["goodreads"]["audiobook_scan"] is True

        # Kobo ebook-only, scan explicitly off.
        assert sources["kobo"]["ebook_scan"] is False
        assert sources["kobo"]["audiobook_scan"] is False

        # Audible audiobook-only — legacy bool was True, but
        # availability restricts ebook_scan to False regardless.
        assert sources["audible"]["ebook_scan"] is False
        assert sources["audible"]["audiobook_scan"] is True

    def test_source_without_legacy_key_keeps_shipped_defaults(self):
        """Sources that never had a legacy `*_enabled` key should
        inherit the per-surface ship-with default, not collapse to
        False via a single-bool fallback.

        Originally written against audnexus (now retired); still
        valuable as a regression guard using audible instead, since
        it exhibits the same "new audiobook-surface source with no
        legacy bool" shape.
        """
        settings = {
            # Plausible legacy state: goodreads & hardcover mentioned,
            # audible completely absent.
            "goodreads_enabled": True,
            "hardcover_enabled": True,
        }
        migrate_legacy_settings(settings)
        audible = settings["metadata_sources"]["audible"]
        # Ship-with default has audiobook_scan=True; preserved for
        # sources the user hadn't explicitly touched.
        assert audible["audiobook_scan"] is True
        # audible's ebook_scan default is False (audiobook-only
        # `available_for`) — stays at the ship-with default.
        assert audible["ebook_scan"] is False

    def test_legacy_rate_limits_carried_over(self):
        settings = {
            "rate_goodreads": 1.5,
            "rate_hardcover": 0.5,
            "rate_audible": 0.25,
        }
        migrate_legacy_settings(settings)
        assert settings["metadata_sources"]["goodreads"]["rate_limit"] == 1.5
        assert settings["metadata_sources"]["hardcover"]["rate_limit"] == 0.5
        assert settings["metadata_sources"]["audible"]["rate_limit"] == 0.25
        # Unspecified sources fall back to KNOWN_SOURCES defaults.
        assert settings["metadata_sources"]["kobo"]["rate_limit"] == 3.0

    def test_idempotent_when_already_migrated(self):
        """Running twice is a no-op (legacy migration AND backfill
        both stay quiet on the second pass)."""
        settings: dict = {}
        assert migrate_legacy_settings(settings) is True
        snapshot_sources = dict(settings["metadata_sources"])
        assert migrate_legacy_settings(settings) is False
        assert settings["metadata_sources"] == snapshot_sources

    def test_backfill_adds_new_known_source_to_existing_install(self):
        """v2.10.8 — when a new source is added to KNOWN_SOURCES (e.g.
        openlibrary in v2.10.6), upgraded installs whose persisted
        settings predate that addition should pick it up automatically
        on the next settings load. Pre-v2.10.8 the migration's
        idempotent-skip path returned False without backfilling, so
        new sources stayed dead-weight on every existing install."""
        # Reproduce a v2.10.5-era settings dict — fully migrated, but
        # missing `openlibrary` (which didn't exist yet at the time).
        settings = {
            "metadata_sources": {
                name: {
                    "ebook_enrich": True, "ebook_scan": True,
                    "audiobook_enrich": False, "audiobook_scan": False,
                    "mandatory": False, "rate_limit": 1.0,
                }
                for name in KNOWN_SOURCES
                if name != "openlibrary"
            },
            "metadata_priority": {
                "ebook": [n for n in KNOWN_SOURCES if n != "openlibrary"],
                "audiobook": ["mam", "audible", "hardcover"],
            },
        }
        assert "openlibrary" not in settings["metadata_sources"]

        ran = migrate_legacy_settings(settings)
        assert ran is True, "backfill must signal that something changed"

        # New source is in metadata_sources with sane defaults.
        assert "openlibrary" in settings["metadata_sources"]
        ol = settings["metadata_sources"]["openlibrary"]
        assert ol["rate_limit"] > 0  # picked up KNOWN_SOURCES default_rate
        assert "ebook_enrich" in ol  # filled from _DEFAULT_NEW_INSTALL_STATE

        # And appended to both priority lists (since it's available
        # for both content types per KNOWN_SOURCES["openlibrary"]).
        assert "openlibrary" in settings["metadata_priority"]["ebook"]
        assert "openlibrary" in settings["metadata_priority"]["audiobook"]

    def test_backfill_idempotent(self):
        """Once the backfill has run, re-running migration should
        return False (no second-write side effects)."""
        settings = {
            "metadata_sources": {
                name: {
                    "ebook_enrich": True, "ebook_scan": True,
                    "audiobook_enrich": False, "audiobook_scan": False,
                    "mandatory": False, "rate_limit": 1.0,
                }
                for name in KNOWN_SOURCES
                if name != "openlibrary"
            },
            "metadata_priority": {
                "ebook": [n for n in KNOWN_SOURCES if n != "openlibrary"],
                "audiobook": ["mam"],
            },
        }
        # First call backfills.
        assert migrate_legacy_settings(settings) is True
        snapshot = {k: dict(v) for k, v in settings["metadata_sources"].items()}
        # Second call should be a no-op.
        assert migrate_legacy_settings(settings) is False
        assert settings["metadata_sources"] == snapshot

    def test_backfill_preserves_user_curated_priority_order(self):
        """Backfill appends new sources at the END of priority lists —
        never reshuffles user's existing curation."""
        custom_order = ["hardcover", "mam", "amazon"]  # user moved Hardcover above MAM
        settings = {
            "metadata_sources": {
                name: {
                    "ebook_enrich": True, "ebook_scan": True,
                    "audiobook_enrich": False, "audiobook_scan": False,
                    "mandatory": False, "rate_limit": 1.0,
                }
                for name in custom_order
            },
            "metadata_priority": {
                "ebook": custom_order.copy(),
                "audiobook": ["audible"],
            },
        }
        migrate_legacy_settings(settings)
        # User's first 3 entries kept in order at the front.
        assert settings["metadata_priority"]["ebook"][:3] == custom_order
        # New sources appended to the tail, not interleaved.
        assert "openlibrary" in settings["metadata_priority"]["ebook"]
        assert settings["metadata_priority"]["ebook"].index("openlibrary") >= 3

    def test_backfill_does_not_add_audiobook_only_source_to_ebook_priority(self):
        """Audible is audiobook-only per KNOWN_SOURCES.available_for —
        backfill shouldn't append it to the ebook priority list when
        an existing install is missing it from there."""
        settings = {
            "metadata_sources": {
                "mam": {
                    "ebook_enrich": True, "ebook_scan": True,
                    "audiobook_enrich": True, "audiobook_scan": True,
                    "mandatory": False, "rate_limit": 2.0,
                },
            },
            "metadata_priority": {
                "ebook": ["mam"],
                "audiobook": ["mam"],
            },
        }
        migrate_legacy_settings(settings)
        assert "audible" not in settings["metadata_priority"]["ebook"]
        assert "audible" in settings["metadata_priority"]["audiobook"]

    def test_retired_sources_scrubbed_from_existing_settings(self):
        """Settings.json from an older build that still carries
        `audnexus` in both the unified shape and the legacy priority
        lists should come out clean on the next migration pass."""
        settings = {
            "metadata_sources": {
                "audnexus": {"rate_limit": 1.0, "audiobook_enrich": True},
                "audible": {"rate_limit": 0.5, "audiobook_enrich": True},
            },
            "metadata_priority": {
                "ebook": ["mam", "audnexus", "goodreads"],
                "audiobook": ["mam", "audible", "audnexus"],
            },
            # A user whose legacy list still named audnexus, forcing
            # the seeder to scrub there too.
            "metadata_audiobook_priority": ["audible", "audnexus"],
        }
        migrate_legacy_settings(settings)
        sources = settings["metadata_sources"]
        priority = settings["metadata_priority"]
        assert "audnexus" not in sources
        assert "audnexus" not in priority["ebook"]
        assert "audnexus" not in priority["audiobook"]
        # Non-retired entries preserved.
        assert "audible" in sources
        assert "audible" in priority["audiobook"]

    def test_migration_enabled_means_in_priority_list(self):
        """A source present in the legacy priority list gets
        `ebook_enrich=True` automatically."""
        settings = {
            "metadata_provider_priority": ["goodreads", "amazon"],
            "metadata_audiobook_priority": ["audible"],
        }
        migrate_legacy_settings(settings)
        sources = settings["metadata_sources"]
        # Goodreads was in the ebook priority list → enrich on.
        assert sources["goodreads"]["ebook_enrich"] is True
        # Amazon was in the ebook priority list → enrich on.
        assert sources["amazon"]["ebook_enrich"] is True
        # Hardcover was NOT in either list → enrich off.
        assert sources["hardcover"]["ebook_enrich"] is False
        assert sources["hardcover"]["audiobook_enrich"] is False
        # Audible in audiobook priority → audiobook_enrich on, ebook off.
        assert sources["audible"]["ebook_enrich"] is False
        assert sources["audible"]["audiobook_enrich"] is True


class TestDerivation:
    def _seeded_settings(self) -> dict:
        settings: dict = {}
        migrate_legacy_settings(settings)
        return settings

    def test_enrich_priority_filters_by_toggle(self):
        """Sources with `*_enrich=False` drop out of the live list."""
        settings = self._seeded_settings()
        settings["metadata_sources"]["goodreads"]["ebook_enrich"] = False
        result = derive_enrich_priority(settings, audiobook=False)
        assert "goodreads" not in result
        # mam still there (enrich default true).
        assert result[0] == "mam"

    def test_enrich_priority_preserves_order(self):
        """List order mirrors `metadata_priority` ranking."""
        settings = self._seeded_settings()
        settings["metadata_priority"]["ebook"] = [
            "hardcover", "mam", "goodreads",
        ]
        # Every row enabled — result matches the new order.
        for name in ("hardcover", "mam", "goodreads"):
            settings["metadata_sources"][name]["ebook_enrich"] = True
        result = derive_enrich_priority(settings, audiobook=False)
        assert result[:3] == ["hardcover", "mam", "goodreads"]

    def test_audiobook_vs_ebook_lists_independent(self):
        settings = self._seeded_settings()
        # Audible off for ebook enrich (already default), on for audiobook.
        assert "audible" not in derive_enrich_priority(
            settings, audiobook=False,
        )
        assert "audible" in derive_enrich_priority(
            settings, audiobook=True,
        )

    def test_scan_priority_uses_scan_flags(self):
        """Scan and enrich share the priority order but filter
        on different per-source flags."""
        settings = self._seeded_settings()
        # Turn goodreads scan off but leave enrich on.
        settings["metadata_sources"]["goodreads"]["ebook_scan"] = False
        assert "goodreads" in derive_enrich_priority(
            settings, audiobook=False,
        )
        assert "goodreads" not in derive_scan_priority(
            settings, audiobook=False,
        )

    def test_missing_source_entry_excluded(self):
        """A name in the priority list without a matching source
        entry is silently dropped from the derived list."""
        settings = self._seeded_settings()
        settings["metadata_priority"]["ebook"] = [
            "mam", "goodreads", "bogus_source",
        ]
        result = derive_enrich_priority(settings, audiobook=False)
        assert "bogus_source" not in result

    def test_rate_limit_lookup(self):
        settings = self._seeded_settings()
        settings["metadata_sources"]["goodreads"]["rate_limit"] = 7.5
        assert get_source_rate_limit(settings, "goodreads") == 7.5
        # Unknown name falls back to 1.0.
        assert get_source_rate_limit(settings, "unknown") == 1.0


class TestSyncLegacy:
    """Post-Phase-7 sync_legacy_keys is narrowed to one responsibility:
    mirror the MAM rate_limit from the unified `metadata_sources`
    shape onto the standalone `rate_mam` key, since `rate_mam` still
    has ~7 non-metadata-source call sites that weren't migrated.
    Every other legacy mirror (goodreads_enabled, rate_goodreads,
    metadata_provider_priority, etc.) was retired alongside the
    DEFAULT_SETTINGS cleanup."""

    def test_mirrors_rate_mam_from_panel(self):
        settings: dict = {}
        migrate_legacy_settings(settings)
        settings["metadata_sources"]["mam"]["rate_limit"] = 3.5
        sync_legacy_keys(settings)
        assert settings["rate_mam"] == 3.5

    def test_no_op_when_mam_rate_missing(self):
        """Missing MAM rate_limit shouldn't stamp a default onto
        settings — leave rate_mam to its own default elsewhere."""
        settings: dict = {}
        migrate_legacy_settings(settings)
        # Explicitly delete rate_limit to confirm we don't write.
        settings["metadata_sources"]["mam"].pop("rate_limit", None)
        settings.pop("rate_mam", None)
        sync_legacy_keys(settings)
        assert "rate_mam" not in settings

    def test_mam_enabled_is_not_clobbered(self):
        """`mam_enabled` is a wider-scope toggle (IRC listener gate),
        not just a source toggle. sync_legacy_keys must NOT touch it."""
        settings = {"mam_enabled": False}
        migrate_legacy_settings(settings)
        settings["metadata_sources"]["mam"]["ebook_scan"] = True
        sync_legacy_keys(settings)
        # The wider IRC gate stays False.
        assert settings["mam_enabled"] is False


class TestMandatoryFlag:
    """v2.3.2 per-source `mandatory` flag: governs the per-source
    `existing_titles` gating in the source-scan loop. Default true
    on the primary tier (Goodreads/Hardcover for ebook;
    Audible/Hardcover for audiobook), false elsewhere."""

    def test_fresh_install_seeds_mandatory_field(self):
        settings: dict = {}
        migrate_legacy_settings(settings)
        sources = settings["metadata_sources"]
        # Every known source has the field after migration.
        for name in sources:
            assert "mandatory" in sources[name], \
                f"source {name!r} missing mandatory key"

    def test_default_mandatory_primary_tier(self):
        settings: dict = {}
        migrate_legacy_settings(settings)
        # Primary tier defaults true.
        assert is_source_mandatory(settings, "goodreads") is True
        assert is_source_mandatory(settings, "hardcover") is True
        assert is_source_mandatory(settings, "audible") is True
        # Secondary / supplementary defaults false.
        assert is_source_mandatory(settings, "kobo") is False
        assert is_source_mandatory(settings, "amazon") is False
        assert is_source_mandatory(settings, "ibdb") is False
        assert is_source_mandatory(settings, "google_books") is False
        # MAM is not part of the source-scan registry; mandatory
        # default is false.
        assert is_source_mandatory(settings, "mam") is False

    def test_user_override_persists(self):
        settings: dict = {}
        migrate_legacy_settings(settings)
        # User clears Goodreads's mandatory bit.
        settings["metadata_sources"]["goodreads"]["mandatory"] = False
        # User flips Kobo on.
        settings["metadata_sources"]["kobo"]["mandatory"] = True
        assert is_source_mandatory(settings, "goodreads") is False
        assert is_source_mandatory(settings, "kobo") is True

    def test_missing_field_falls_back_to_ship_default(self):
        """An upgraded settings.json from before v2.3.2 won't have
        the `mandatory` key on existing entries. The accessor must
        fall back to the ship-with default rather than False, so
        users get the intended behavior without an explicit migration
        write."""
        settings = {
            "metadata_sources": {
                "goodreads": {
                    "rate_limit": 2.0,
                    "ebook_enrich": True, "ebook_scan": True,
                    "audiobook_enrich": True, "audiobook_scan": True,
                    # `mandatory` deliberately absent.
                },
                "kobo": {
                    "rate_limit": 3.0,
                    "ebook_enrich": True, "ebook_scan": True,
                    "audiobook_enrich": False, "audiobook_scan": False,
                },
            },
        }
        # Goodreads ship-default: True. Kobo ship-default: False.
        assert is_source_mandatory(settings, "goodreads") is True
        assert is_source_mandatory(settings, "kobo") is False

    def test_unknown_source_returns_false(self):
        """A source the app doesn't know about — defensive fallback
        rather than crash. Unknown sources can't be mandatory because
        the scan loop won't run them anyway."""
        settings: dict = {}
        migrate_legacy_settings(settings)
        assert is_source_mandatory(settings, "nonexistent") is False
