# Changelog

All notable changes to Seshat are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.3.0] — 2026-04-15

Closes the v1.2 backlog. One new feature + polish across the board.

### Added

- **By-author download folder structure.** The existing
  `download_folder_structure` setting (previously only supported
  "monthly", "yearly", and "flat") now accepts `"author"`. When
  set, completed downloads land in a normalized author-name
  subfolder inside the qBit download path (e.g.,
  `/downloads/[mam-complete]/William D Arand/`). Author name comes
  from the grab's `author_blob` (the IRC announce). Dots are
  collapsed to spaces so "William D. Arand" and "William D Arand"
  share a folder. Empty/missing author names fall back to
  `_Unknown/`. The setting is exposed as a dropdown in
  Settings → Download Client (replacing the old description that
  only mentioned Monthly/Flat). Both the dispatcher submit path
  and the budget-watcher queue-resubmit path honor the new mode.

### Changed

- **Log levels recalibrated.** 15 adjustments across
  `budget_watcher.py`, `dispatch.py`, `pipeline.py`, and
  `enricher.py`. Pattern: per-book operational detail (staging,
  epub patching, enricher per-source results, queue pop details)
  demoted from INFO → DEBUG to reduce default-level noise.
  Folder pre-creation failure promoted from WARNING → ERROR
  (genuine operational failure). Client-unreachable queueing and
  enricher budget-exceeded demoted from WARNING → INFO (expected
  resilience, not exceptional). Policy/user_status lookup
  fallbacks demoted from WARNING → DEBUG (implementation detail).

### Fixed

- **`test_scoring.py::test_partial_overlap` assertion stale.**
  The upper bound expected `< 0.6` but the scoring function
  (changed in `a09d063`) now correctly weights substring
  containment higher, producing 0.714 for "Foundation" vs
  "Foundation and Empire". Updated to `0.6 < score < 0.8`.

- **`test_pipeline.py::test_no_book_files_fails` assertion
  didn't match v1.2.3 error message.** The v1.2.3 file-list
  feature changed the error wording from "no book files" to
  "no file matching '...'". Updated to accept either form.

### Removed

- **Orphaned `CredentialsPage.tsx` deleted.** Dead code since
  v1.1.2 when credential editing moved inline to SettingsPage
  via `CredField`. Never imported in `App.tsx`.

## [1.2.4] — 2026-04-15

### Fixed

- **ibdb enricher picked up stale API field names.** The source
  was written for a pre-2026 `ibdb.dev` response shape and
  expected snake_case keys (`isbn_13`, `publication_date`,
  `pages`) plus a bare URL string for `cover`/`image`/`thumbnail`.
  Verified live during the AthenaScout v1.1.9 cross-port review:
  the API now returns camelCase (`isbn13`, `synopsis`,
  `publicationDate`, `pageCount`) and `image` is a DICT
  `{id, url, width, height}` — so the old code was shoving a
  dict into `MetaRecord.cover_url`. AthenaScout's identical bug
  crashed sqlite3 parameter binding; Seshat's record path
  doesn't bind cover_url to SQL directly, but a dict-shaped URL
  would still have blown up once it reached the downloader or
  OPF serializer.

  Fix: prefer camelCase keys with snake_case as fallback,
  extract `image.url` when image is a dict (tolerate either
  shape with `isinstance()`), and type-guard `description` /
  `language` against non-string values landing in scalar slots.

## [1.2.3] — 2026-04-14

### Fixed

- **Staging picked the wrong book file when the torrent's on-disk
  name didn't match its announce name.** `_prepare_book` built
  `source = save_path / torrent_name`, and when that path didn't
  exist (single-file torrent with a different filename, multi-file
  torrent that drops loose files into the save_path, etc.) it tried
  `_find_torrent_file` with exact / prefix / substring matches
  against `torrent_name`. When *that* also missed — common for
  MAM torrents where qBit writes names like
  `Infinite_Warship_-_Scott_Bartlett.epub` for an announce titled
  "Infinite Warship", or where the save_path is shared across many
  torrents — the code fell through to scanning the whole save_path
  and picking the alphabetically/size-first book file as "primary".

  Blast radius: every grab that couldn't resolve its own file
  ended up staging whoever else happened to be in the save_path.
  In the user's case, a Tsukimichi pack of 37 loose epubs in the
  month folder meant every mis-resolving grab since got Tsukimichi
  Volume 14 staged — metadata enricher then ran its fuzzy search
  against "Tsukimichi Moonlit Fantasy Volume 14" instead of the
  actual book, and the review queue card showed the right cover
  (from the MAM exact-ID lookup on the intended torrent) next to
  a completely unrelated staged file.

  Fix: ask qBit for the actual file list of the completed torrent
  via a new `TorrentClient.list_torrent_files(hash)` method
  (`GET /api/v2/torrents/files`). The budget_watcher threads the
  result into `process_completion` → `_prepare_book` →
  `copy_to_staging`. File paths come from qBit's own view of what
  got written to disk, so there's no string-match step that can
  go wrong. The legacy heuristic path still runs when the client
  can't introspect (other clients stub `list_torrent_files` to
  `[]`), but now FAILS loudly instead of silently scanning the
  save_path — an unresolved file is a real error, not a reason
  to stage a random other book.

### Added

- **`TorrentClient.list_torrent_files(hash)` protocol method.**
  qBittorrent implements it against `/api/v2/torrents/files` and
  returns the relative file paths. Transmission / Deluge /
  rtorrent keep the default stub (empty list) until someone needs
  it there. The pipeline treats an empty return as "couldn't
  introspect" and falls through to the legacy name-match.

## [1.2.2] — 2026-04-14

### Fixed

- **Review-queue edits looked ignored because the UI preferred the
  enricher value.** The resolved display values in `ReviewPage`
  used `enriched.X || topLevel.X`, so when a user corrected the
  author through Edit → Save edits, the save landed correctly on
  the top-level metadata (and on `grabs.torrent_name` for titles)
  but the card still rendered the enricher's pre-edit value. Flip
  to `topLevel.X || enriched.X` — edits are authoritative; the
  enricher dict is reference/provenance.
- **Enriched descriptions + languages weren't patched into the
  staged epub.** The initial staging patch in `_stage_for_review`
  was passing `title / authors / series / series_index` only. The
  enricher-returned description and language stayed in the
  review-queue row + on the UI, but the on-disk epub handed to
  CWA / Calibre had a blank `<dc:description>`, so imported books
  showed up missing synopses even though Seshat had said the
  scrapers "found" them. Now passes `description` and `language`
  to `patch_epub_metadata` at staging time — matches the re-patch
  step added by v1.2.1.

### Added

- **AthenaScout `GrabItem.category`.** Seshat now accepts an
  optional `category` on each item in the `/from-athenascout`
  payload (pairs with AthenaScout v1.1.5, which captures MAM's
  category — e.g. "Ebooks - Fantasy" — during its MAM scan and
  forwards it). `inject_grab` receives the value so the grab row
  starts with a proper category instead of an empty string. No
  schema change needed — the column has always existed; pre-v1.1.5
  AS clients still work (empty string fallback).

## [1.2.1] — 2026-04-14

Follow-up to the v1.2.0 review-edit workflow. Two issues the user
hit after the first release:

### Fixed

- **Review-queue metadata edits never reached Calibre.** The epub
  file is patched at staging time (see `_stage_for_review`) with
  the pre-edit metadata — by the time the user corrects the title
  / author / description / etc. through the Review page, the
  staged file on disk is already baked. `deliver_reviewed` then
  handed that stale file straight to the sink, so CWA / Calibre
  imported a book with the old (wrong) metadata even though the
  review-queue row + dashboard reflected the edit. Fix:
  `deliver_reviewed` now re-patches a temp copy of the staged
  epub with the current review-queue metadata before delivery,
  same shape as `_stage_for_review`'s patch step. On patch
  failure the sink still gets the unpatched file rather than
  refusing delivery.

### Added

- **Description + Language editable in the Review page.** The
  v1.2.0 edit form only exposed title / author / series /
  series_index / isbn / publisher. Now also editable:
  - **Description** — inline textarea with resize handle
  - **Language** — small input (defaults to `en`)
  `patch_epub_metadata` gained a `description` parameter so the
  `<dc:description>` element in the OPF gets the edit too.

## [1.2.0] — 2026-04-14

Operator-tooling release. Two interlocking editors — one in the
Review queue, one in the DB browser — so the user can fix bad
metadata or surgically repair a bad row without SSHing into the
container.

### Added

- **Review-queue edit workflow.** The Review page's existing Edit
  button now also offers **Save edits** (persist the metadata
  changes without approving yet) and **Re-enrich** (apply pending
  edits, then rerun the metadata scraper chain against the new
  title + author and replace `metadata.enriched` with the fresh
  result). Title edits propagate into `grabs.torrent_name` so the
  Snatch Budget widget, Recent Activity feed, and review queue
  label all reflect the correction. Direct response to the stuck
  `manual_inject_1024455` row — edit → re-enrich → approve
  replaces the "reject + resend" workaround.
  - `POST /api/v1/review/{id}/save` — metadata-only edit
  - `POST /api/v1/review/{id}/re-enrich` — save + rerun enricher
  - `grabs.set_torrent_name()` storage helper
- **Database browser writes (plan item 4.3 completion).** The
  read-only MVP from v1.1 gains click-to-edit cells, a sticky
  "N pending changes · Commit / Revert" tray, and a per-row
  delete action.
  - `POST /api/v1/db/table/{name}/update` — batch cell updates,
    validated against `PRAGMA table_info` before any write commits
  - `POST /api/v1/db/table/{name}/add` — insert new row
  - `DELETE /api/v1/db/table/{name}/row/{id}` — delete by PK
  - Writes inherit the same `_TABLES` whitelist as the read
    endpoints, so the editor can never reach `sqlite_master` or
    anything outside the expected operational tables. FK-constraint
    violations on delete surface as a readable 409 ("delete or
    reassign the dependent rows first") instead of the raw
    sqlite3 error.

## [1.1.4] — 2026-04-14

### Fixed

- **AthenaScout sends landed with `manual_inject_<id>` as the title.**
  `/api/v1/grabs/from-athenascout` was calling `inject_grab` without
  a `torrent_name`, so the default `f"manual_inject_{torrent_id}"`
  placeholder got written to `grabs.torrent_name` and leaked into
  the Snatch Budget widget, Recent Activity, the review queue, and
  (worst of all) the metadata enricher's fuzzy search — which
  returned garbage against the placeholder title. The `GrabItem`
  schema now accepts an optional `title` field; AthenaScout v1.1.4
  populates it from its own `books.title` row. Absent-title payloads
  from pre-v1.1.4 AthenaScout clients still work — they just keep
  the old placeholder behavior.

## [1.1.3] — 2026-04-14

Urgent hotfix for two v1.1.1 regressions plus a latent enricher
bug that's been silent since v1.0.

### Fixed

- **Dispatcher broken after any credential or settings save.**
  `_build_dispatcher` became async in v1.1.1 (so the filter's
  allow/ignore author sets can be loaded from the DB). The two
  rebuild call sites — `routers/credentials.py::_apply_credential`
  and `routers/settings.py::update_settings` — kept their
  synchronous `state.dispatcher = _build_dispatcher(settings)` form,
  which silently produces a bare coroutine object instead of a
  `DispatcherDeps`. Every attribute access afterward raises
  `AttributeError: 'coroutine' object has no attribute …`.
  User-visible symptoms: Send-to-Seshat inject silently fails,
  `/api/v1/grabs/budget` returns 500, budget widget goes dark,
  and the IRC pipeline effectively stops processing announces as
  soon as the user touches any setting or credential. Fix: both
  sites now `await _build_dispatcher(settings, resolved_secrets)`,
  and both pull fresh secrets from the encrypted store via a new
  `_resolve_secrets()` helper rather than injecting only the one
  being updated.
- **Hardcover metadata enricher silently unauthenticated since
  Sprint 6.** `_build_default_sources` read `hardcover_api_key`
  via a fallback to `load_settings()` whenever the event loop is
  running (always true in the dispatcher build path). The Sprint 6
  encrypted-store migration blanked that field in `settings.json`,
  so Hardcover was being instantiated with `api_key=""` on every
  enrichment run and returning None for every lookup. Fix: the
  key is now plumbed through `_build_dispatcher` →
  `_build_metadata_enricher` → `MetadataEnricher.__init__` →
  `_build_default_sources` from `resolved_secrets`, same shape as
  qbit/mam credentials. Matches AthenaScout's v1.1.1 hotfix for
  the same storage-accessor-audit miss.
- **No-match sources were invisible in the log stream.** The
  enricher only emitted an INFO line on successful matches, so
  a book that hit Goodreads/Hardcover/IBDB with no result looked
  like those sources were never queried. Added a matching INFO
  line on the no-match path so the full provider chain is
  observable. (Timeouts and exceptions were already WARNING /
  ERROR; this only changes the silent-None case.)

## [1.1.2] — 2026-04-14

### Fixed

- **AthenaScout shared API key wasn't surfaced in the UI.** v1.1.1
  added the `athenascout_api_key` credential to the backend
  `SECRET_KEYS` but put the Generate button in `CredentialsPage.tsx`
  — which turns out to be orphaned dead code (never imported into
  `App.tsx`, no route, no nav entry). Credential editing actually
  happens inline inside `SettingsPage` via the `CredField` component
  filtered into mam / qbit / api buckets. Fix: extended `CredField`
  with a `canGenerate` prop (text-input + Generate button + copyable
  value pre-save) and surfaced the new key inside Settings → API
  Keys & Sink. The orphaned `CredentialsPage.tsx` is left as-is —
  harmless dead code, flagged for cleanup in v1.2.

## [1.1.1] — 2026-04-14

Post-release polish and a v1.0 latent bug fix. Bundles the three
patches that landed on `main` after the v1.1.0 tag plus a new
shared-API-key mechanism for AthenaScout's "Send to Seshat"
integration.

### Added

- **AthenaScout shared API key.** New `athenascout_api_key` entry
  in the Credentials page. Generates a 64-char hex token (browser
  `crypto.getRandomValues`) that the user copies into AthenaScout's
  Settings. The auth middleware now accepts this token via the
  `X-API-Key` header as an alternative to the session cookie, so
  AthenaScout → Seshat service-to-service calls don't need a
  login session. Constant-time compare to blunt timing oracles.
  Value is cached in `state.athenascout_api_key` and refreshed
  whenever the credential is set or deleted — no DB hit per request.

### Changed

- **Settings page column balance.** Grab Policy moved from the
  right column to the left (semantically pairs with Pipeline and
  Review — all "what gets grabbed / what gets approved" decisions).
  Snatch Budget moved from left to right (MAM-imposed infrastructure,
  pairs with MyAnonamouse and Download Client). Evens out column
  heights and tightens the mental grouping.

### Fixed

- **Author allow/ignore lists were never loaded into the filter at
  runtime.** Latent bug from v1.0: `_build_filter_config` hardcoded
  `allowed_authors=frozenset()` and `ignored_authors=frozenset()`,
  so every IRC announce was evaluated against empty sets regardless
  of what was in the DB. The symptom the user reported: James S A
  Corey was in the allowed list but an announce for his book went
  to tentative review. Fix: `_build_filter_config` now async, reads
  both sets from `load_normalized_sets()` at startup; every mutation
  site (authors router, auto-train, tentative-promotion cron, digest
  auto-promote) calls new `state.refresh_filter_authors()` so the
  live dispatcher sees changes immediately.
- **Epub metadata patch crashed on float `series_index`.** Python's
  XML writer raises `TypeError: argument of type 'float' is not
  iterable` in `_escape_attrib` when a non-string attribute value
  is passed. The enricher and the AthenaScout handoff both produce
  floats for `series_index`. Fix: `_set_meta` now coerces `content`
  to `str` up front and guards against `None`; handles both the
  "existing meta" update path and the "create new meta" path.
- **Log viewer missed overnight history.** Ring buffer was 5000
  records (~4–6 hours during active IRC periods) and the frontend
  requested 500 lines by default. Bumped to 20000 records / 2000
  lines so a user checking the log in the morning sees the full
  overnight activity window.
- **UI polish.** Dashboard navbar widened from `NARROW_WIDTH` to
  `WIDE_WIDTH` so it matches the content pages. Stat-tile grid
  tightened from `minmax(170px, 1fr)` to `minmax(150px, 1fr)` to
  reduce asymmetry when the tile count doesn't evenly divide the
  row width.

## [1.1.0] — 2026-04-14

Quality-of-life release. Thirteen items split across two sprints —
most are direct cross-ports from [AthenaScout](https://github.com/mnbaker117/AthenaScout)
v1.1.0's playbook plus Seshat-specific integration + operator
tooling.

### Added

#### AthenaScout integration

- **Metadata handoff (plan item 1.2)** — `/from-athenascout` grab
  submissions now accept an optional `metadata` dict carrying
  AthenaScout's already-scanned book metadata (title, author,
  series, ISBN, cover URL, description, etc.). The blob is
  persisted on the grab row (new `grabs.source_metadata` column)
  and the pipeline's `_prepare_book` uses it to skip Seshat's
  own enricher chain — saves ~6 outbound scraper requests per
  book and guarantees metadata consistency with AS.

#### Operator tooling

- **Database browser (plan item 4.3)** — new **🗄️ Database** page
  for inspecting Seshat's SQLite without SSH-ing into the
  container. Left pane: whitelisted table list with row counts.
  Right pane: paginated row grid (50/page) with case-insensitive
  text-column search. **Read-only for v1.1**; cell editing, inserts,
  and deletes arrive in v1.2.
- **Build SHA display (plan item 4.2)** — Settings page footer now
  shows the git commit the running container was built from.
  Dockerfile bakes `GIT_SHA` via build-arg; `/api/version` exposes
  it; the Settings page reads it at mount time. Makes "which
  version am I actually running?" unambiguous after a pull.
- **Log viewer tabs (plan item 2.3)** — the Logs page gains two
  new tabs: **Application** (everything NOT under `seshat.mam.irc`)
  and **IRC** (only `seshat.mam.irc.*`). Drives a new `category`
  query param on `/api/v1/logs` that filters by logger-name prefix.
- **Log viewer filter input (plan item 2.2)** — case-insensitive
  substring filter narrows the visible log rows in real time.
  Matches against both logger name and message body.

#### Notifications

- **Per-event ntfy gating (plan item 4.5)** — three ntfy call
  sites in the pipeline were firing regardless of the
  `per_event_notifications` setting. Now all success-path
  notifications (`notify_pipeline_complete`, `notify_download_complete`)
  honor the setting uniformly; `notify_error` stays always-on
  because errors aren't in the daily digest and shouldn't be
  suppressed.
- **Weekly Calibre audit (plan item 5.1)** — new
  `weekly_calibre_audit` APScheduler job fires Sundays at 22:30
  local (one hour before the weekly digest). Shells out to
  `calibredb list --for-machine`, compares to
  `calibre_additions` over the 7-day window, and flags any book
  that entered Calibre outside Seshat's knowledge (manual
  add, other tool, etc.). Silent when nothing's off.

#### Resilience

- **Global per-book enricher budget (plan item 6.1)** — on top of
  the existing per-source `asyncio.wait_for` timeouts, the
  enricher now enforces a wall-clock budget across an entire
  `enrich()` call (default 60s). Per-source timeouts are clamped
  to the remaining budget so a slow late-stage source can't
  single-handedly blow the cap. Source-log entries get
  `status: "budget_exceeded"` for anything skipped.

### Changed

- **Log noise reduction (plan item 2.1)** — new `_QuietAccessFilter`
  installed on `uvicorn.access` suppresses `/api/health` records
  (~2× per minute from the Docker healthcheck). Extensible list
  for future high-frequency paths.
- **Page Visibility API for polling (plan item 4.1)** — six pages
  (Dashboard, Review, Tentative, Migration, Logs, Mam) previously
  ran their `setInterval` pollers at full speed even when their
  tab was backgrounded. Extracted a reusable
  `useVisibleInterval()` hook that pauses the interval on
  `document.hidden` and fires a catch-up tick on visibility
  return. Saves idle network traffic + battery on parked tabs.
- **Per-page content widths (plan item 4.4)** — the hardcoded
  1400px main-content cap is now a per-page lookup: data-heavy
  pages get 1400px, form/config pages get 1120px, the navbar
  stays 1120px regardless. Mirrors AthenaScout's Sprint 7.3
  convention.
- **Amazon scraper hardening (plan item 1.3)** — backported from
  AthenaScout commit `423450b`:
  - Junk-listing pre-filter (regex for third-party seller
    titles, bracketed format suffixes, "By AUTHOR - Title" sham
    listings) runs at the search-result scoring step so garbage
    never reaches the detail-page fetch.
  - Audiobook detection in `_parse_detail_page` — checks RPI
    card text + `#productSubtitle` against keywords
    (`audible`, `audiobook`, `audio cd`, `listening length`).
    Audiobook hits return None so the enricher loop falls
    through to the next source.

### Fixed

- **MAM cookie rotation log leaked token prefix** — the INFO log
  on rotation included `{old[:8]}... → {new[:8]}...`. An 8-char
  prefix has enough entropy to correlate sessions across log
  aggregators, and `docker logs` readership is wider than
  "people authorized to see the MAM session." Dropped the
  snippets entirely; fact-of-rotation is the only diagnostic
  that matters. (Same lesson as AthenaScout v1.1.1 commit
  `23e01fd`.)

### Internals (no user-visible change)

- New `frontend/src/hooks/useVisibleInterval.ts` — reusable
  visibility-aware interval hook.
- New `app/routers/db_editor.py` — read-only SQLite browser.
- New `grabs.source_metadata` column (MIGRATIONS list's first
  entry).
- `EnrichmentConfig` gains `per_book_budget: float = 60.0`.
- `DigestContext` gains `calibre_library_path: str = ""` for the
  weekly audit.
- `app/storage/grabs.py` adds a thin `get_source_metadata(db,
  grab_id)` helper so the one consumer (pipeline._prepare_book)
  doesn't drag the column through the GrabRow dataclass.

---

## [1.0.0] — 2026-04-09

Initial public release. See release notes at
<https://github.com/mnbaker117/seshat/releases/tag/v1.0.0>.

[1.1.0]: https://github.com/mnbaker117/seshat/releases/tag/v1.1.0
[1.0.0]: https://github.com/mnbaker117/seshat/releases/tag/v1.0.0
