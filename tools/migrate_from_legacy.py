#!/usr/bin/env python3
"""
One-time migration from AthenaScout + Hermeece to Seshat.

Run this INSIDE the Seshat container (or with DATA_DIR pointing at
the Seshat data directory) BEFORE the first Seshat boot. It copies
database files and merges settings from both legacy apps.

Usage:
    python tools/migrate_from_legacy.py \
        --as-data /path/to/athenascout/data \
        --hm-data /path/to/hermeece/data \
        --seshat-data /path/to/seshat/data

What it does:
    1. Copies athenascout_*.db files as seshat_*.db (discovery DBs)
    2. Copies hermeece.db as seshat.db (pipeline DB)
    3. Copies one auth DB as seshat_auth.db (merges secrets)
    4. Copies auth_secret file
    5. Merges both settings.json files into one

Safe to re-run — skips files that already exist.
"""
import argparse
import json
import shutil
import sys
from pathlib import Path


def migrate(as_data: Path, hm_data: Path, seshat_data: Path) -> None:
    seshat_data.mkdir(parents=True, exist_ok=True)
    actions = []

    # ── 1. Discovery databases (athenascout_*.db → seshat_*.db) ──
    for db_file in sorted(as_data.glob("athenascout_*.db")):
        # Skip WAL/SHM sidecar files
        if db_file.suffix in (".db-wal", ".db-shm"):
            continue
        new_name = db_file.name.replace("athenascout_", "seshat_")
        dest = seshat_data / new_name
        if dest.exists():
            print(f"  SKIP {new_name} (already exists)")
        else:
            shutil.copy2(db_file, dest)
            # Also copy WAL/SHM if present
            for ext in ("-wal", "-shm"):
                sidecar = db_file.parent / (db_file.name + ext)
                if sidecar.exists():
                    shutil.copy2(sidecar, seshat_data / (new_name + ext))
            actions.append(f"Copied {db_file.name} → {new_name}")
            print(f"  COPY {db_file.name} → {new_name}")

    # Also handle bare athenascout.db (legacy single-library)
    legacy_as = as_data / "athenascout.db"
    if legacy_as.exists() and not (seshat_data / "athenascout.db").exists():
        # Copy as-is — the Seshat lifespan's migrate_legacy_db() will
        # rename it to the correct per-library filename at first boot.
        shutil.copy2(legacy_as, seshat_data / "athenascout.db")
        actions.append("Copied athenascout.db (lifespan will rename at first boot)")
        print("  COPY athenascout.db (lifespan will auto-rename)")

    # ── 2. Pipeline database (hermeece.db → seshat.db) ───────────
    hm_db = hm_data / "hermeece.db"
    seshat_db = seshat_data / "seshat.db"
    if hm_db.exists() and not seshat_db.exists():
        shutil.copy2(hm_db, seshat_db)
        for ext in ("-wal", "-shm"):
            sidecar = hm_data / f"hermeece.db{ext}"
            if sidecar.exists():
                shutil.copy2(sidecar, seshat_data / f"seshat.db{ext}")
        actions.append("Copied hermeece.db → seshat.db")
        print("  COPY hermeece.db → seshat.db")
    elif seshat_db.exists():
        print("  SKIP seshat.db (already exists)")
    else:
        print("  WARN hermeece.db not found — pipeline DB will start empty")

    # ── 3. Auth database ─────────────────────────────────────────
    seshat_auth = seshat_data / "seshat_auth.db"
    if not seshat_auth.exists():
        # Prefer Hermeece's auth DB (has more secrets: IRC, qBit)
        for candidate_name, candidate_dir in [
            ("hermeece_auth.db", hm_data),
            ("athenascout_auth.db", as_data),
        ]:
            candidate = candidate_dir / candidate_name
            if candidate.exists():
                shutil.copy2(candidate, seshat_auth)
                actions.append(f"Copied {candidate_name} → seshat_auth.db")
                print(f"  COPY {candidate_name} → seshat_auth.db")
                break
        else:
            print("  WARN no auth DB found — Seshat will create a fresh one")
    else:
        print("  SKIP seshat_auth.db (already exists)")

    # ── 4. Auth secret file ──────────────────────────────────────
    seshat_secret = seshat_data / "auth_secret"
    if not seshat_secret.exists():
        for candidate_dir in [hm_data, as_data]:
            candidate = candidate_dir / "auth_secret"
            if candidate.exists():
                shutil.copy2(candidate, seshat_secret)
                actions.append(f"Copied auth_secret from {candidate_dir.name}")
                print(f"  COPY auth_secret from {candidate_dir.name}/")
                break
        else:
            print("  WARN no auth_secret found — Seshat will generate a new one")
    else:
        print("  SKIP auth_secret (already exists)")

    # ── 5. Merge settings.json ───────────────────────────────────
    seshat_settings = seshat_data / "settings.json"
    if not seshat_settings.exists():
        merged = {}
        # Load Hermeece settings first (pipeline settings)
        hm_settings_file = hm_data / "settings.json"
        if hm_settings_file.exists():
            try:
                with open(hm_settings_file) as f:
                    merged.update(json.load(f))
                print("  LOAD hermeece settings.json")
            except Exception as e:
                print(f"  WARN failed to read hermeece settings.json: {e}")

        # Layer AthenaScout settings on top (discovery settings)
        as_settings_file = as_data / "settings.json"
        if as_settings_file.exists():
            try:
                with open(as_settings_file) as f:
                    as_settings = json.load(f)
                # Only merge discovery-specific keys, don't overwrite
                # pipeline settings with AS defaults
                discovery_keys = [
                    "goodreads_enabled", "hardcover_enabled", "kobo_enabled",
                    "amazon_enabled", "ibdb_enabled", "google_books_enabled",
                    "google_books_auto_disabled_at", "theme", "languages",
                    "lookup_interval_days", "library_sync_interval_minutes",
                    "rate_goodreads", "rate_hardcover", "rate_kobo",
                    "rate_amazon", "rate_ibdb", "rate_google_books",
                    "author_scanning_enabled", "author_scan_owned_only",
                    "exclude_audiobooks", "mam_enabled", "mam_scanning_enabled",
                    "mam_scan_interval_minutes", "mam_format_priority",
                    "rate_mam", "active_library", "library_mtimes",
                    "library_sources",
                ]
                for k in discovery_keys:
                    if k in as_settings:
                        merged[k] = as_settings[k]
                print("  LOAD athenascout settings.json (discovery keys)")
            except Exception as e:
                print(f"  WARN failed to read athenascout settings.json: {e}")

        # Drop cross-app integration keys (no longer needed)
        for old_key in ["hermeece_url", "hermeece_api_key",
                        "athenascout_api_key"]:
            merged.pop(old_key, None)

        if merged:
            with open(seshat_settings, "w") as f:
                json.dump(merged, f, indent=2)
            actions.append("Merged settings.json from both apps")
            print("  WRITE merged settings.json")
        else:
            print("  WARN no settings to merge — Seshat will use defaults")
    else:
        print("  SKIP settings.json (already exists)")

    # ── Summary ──────────────────────────────────────────────────
    print()
    if actions:
        print(f"Migration complete — {len(actions)} action(s):")
        for a in actions:
            print(f"  - {a}")
    else:
        print("Nothing to migrate (all files already exist).")
    print()
    print("Next: start the Seshat container and verify in the web UI.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate from AthenaScout + Hermeece to Seshat")
    parser.add_argument("--as-data", required=True, help="Path to AthenaScout's data directory")
    parser.add_argument("--hm-data", required=True, help="Path to Hermeece's data directory")
    parser.add_argument("--seshat-data", required=True, help="Path to Seshat's data directory")
    args = parser.parse_args()

    print("Seshat migration: AthenaScout + Hermeece → Seshat")
    print(f"  AS data:     {args.as_data}")
    print(f"  HM data:     {args.hm_data}")
    print(f"  Seshat data: {args.seshat_data}")
    print()

    migrate(
        as_data=Path(args.as_data),
        hm_data=Path(args.hm_data),
        seshat_data=Path(args.seshat_data),
    )
