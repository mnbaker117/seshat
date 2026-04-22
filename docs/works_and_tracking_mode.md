# Works and tracking mode

Audiobook support introduced two concepts that don't exist in a
Calibre-only deployment: **works** (cross-library book identity) and
**tracking mode** (per-author format preference). This doc is the
canonical explanation of what they mean, how they're stored, and how
they interact with the rest of the system.

## Works: cross-library book identity

A *work* is the abstract book that can exist in more than one library.
`Foundation` by Isaac Asimov might be:

- row 1234 in the Calibre library (`seshat_calibre.db`, `content_type=ebook`)
- row 98 in the Audiobookshelf library (`seshat_abs.db`, `content_type=audiobook`)

Both rows describe the same underlying work. The Works table
(`work_links` in the pipeline DB) stores the mapping:

    CREATE TABLE work_links (
        id            INTEGER PRIMARY KEY,
        work_id       TEXT    NOT NULL,          -- random hex UUID
        library_slug  TEXT    NOT NULL,
        book_id       INTEGER NOT NULL,
        content_type  TEXT    NOT NULL,          -- 'ebook' | 'audiobook'
        link_source   TEXT    NOT NULL DEFAULT 'auto',  -- 'auto' | 'manual'
        created_at    REAL    NOT NULL DEFAULT (strftime('%s','now')),
        UNIQUE(library_slug, book_id)
    );

- `work_id` is a random UUID4 hex (no dashes). We deliberately *don't*
  derive it from an (author, title) hash — a cosmetic metadata cleanup
  that changes the normalized title shouldn't accidentally unify two
  works a user deliberately kept separate.
- `UNIQUE(library_slug, book_id)` means a book belongs to exactly one
  work at a time. Re-homing uses `UPDATE`, not `INSERT`.
- `link_source` discriminates between the auto-matcher's output and
  user-initiated merges. The auto-matcher preserves `manual` rows
  across rebuilds.

### The matcher

`app/works/matcher.py` runs during library sync + on demand via
`POST /api/v1/works/rebuild`. Algorithm:

1. Enumerate every book in every discovered library.
2. Generate one or more `match_keys` for each book (see
   `app/works/normalize.py`). The primary key is the strict normalized
   `(author, title)` tuple; loose variants strip a trailing
   `" - Subtitle"` clause for subtitle-variant tolerance.
3. Bucket books by overlapping keys (connected-components walk — two
   books share a bucket if *any* key pair overlaps).
4. For each bucket, either reuse an existing `work_id` (if a member
   has one) or mint a new one. Insert missing `auto` rows.
5. Remove stale `auto` rows: any row whose key no longer agrees with
   the work's prevailing key (e.g. ABS re-named a file and the key
   shifted out from under the link).
6. Reconcile orphans: link rows whose `book_id` no longer exists in
   its source library get dropped.

Manual links (`link_source='manual'`) are *never* touched by the
matcher except to migrate them when the user re-homes.

### What the matcher does *not* do

Earlier iterations stripped trailing volume markers (`Vol 1`, `Book 2`,
`Part I`) to unify a series across libraries that disagreed on
volume notation. This caused false-merges (the Spice & Wolf vs
Hero-Killing Bride incident) and was removed in `4880bac`. The
current rule is exact match + " - Subtitle" loose variant. Volume
differences need external metadata (ASIN, ISBN, series-index) to
link safely, which we don't have end-to-end yet, so we err
conservative and let the user merge manually.

### Singleton rows

The auto-matcher can create work rows with exactly one member — an
intermediate state during drift (the sibling got pruned but the
reconcile pass hasn't run yet) or an author with only one format in
the user's libraries. The Works UI filters `members.length >= 2` so
these don't clutter the index, but the rows exist and are valid.
They become multi-member automatically as soon as a sibling appears.

## Tracking mode: per-author format preference

An author's *tracking mode* answers "what formats do I want to hear
about for this author's books?" Three values:

- `ebook` — only care about ebook versions
- `audiobook` — only care about audiobook versions
- `both` — care about either

### Storage

- **Global default:** `settings.audiobook_tracking_mode` (one of the
  three modes; defaults to `both`).
- **Per-author override:** `author_format_preferences` (pipeline DB),
  keyed by **normalized** author name so Calibre's "Brandon Sanderson"
  and ABS's "Brandon Sanderson" share one preference row. Keying by
  the per-library author ID would require duplicating the preference
  every time you added a library, which doesn't match user intent.

### Resolution

`effective_tracking_mode(author_name)`:

1. Look up the per-author override. If set, return it.
2. Otherwise return the global default.

### Where it applies

- **Missing detection.** `/api/v1/discovery/missing?content_type=...`
  runs `_apply_tracking_mode_filter` on the cross-library aggregated
  result. An author with `tracking_mode='audiobook'` has their ebook
  rows dropped from the missing list; symmetric for the other
  direction. `mode='both'` keeps everything.
- **Future: MAM scan filters.** When a MAM scan encounters a book
  from an author pinned to the opposite format, the scan can skip
  the network round-trip. Not currently wired in — the filter is
  applied client-side via the missing list only.

### What it does *not* do

Tracking mode does **not**:

- hide the author from library views (the underlying books still
  exist; only the *missing* detection is filtered)
- affect the matcher (work linking is format-agnostic)
- block manual grabs (a user can still grab an audiobook for an
  "ebook only" author — the preference is for automatic surfacing,
  not a hard lock)

### Interaction with works

Tracking mode and works are **orthogonal**:

- Works are about identity ("these two rows describe the same book").
- Tracking mode is about preference ("don't surface audiobook rows
  for this author").

A user with `mode='ebook'` for Sanderson and a Calibre + ABS library
that both have Mistborn will see:

- one work row in the Works index (the two rows are linked)
- one row in `/missing` (the ebook one) if either is missing —
  the audiobook row is filtered out before pagination

## API surface

    GET    /api/v1/works
    GET    /api/v1/works/{work_id}
    POST   /api/v1/works/rebuild
    POST   /api/v1/works/link
    DELETE /api/v1/works/link/{library_slug}/{book_id}

    GET    /api/v1/works/author-preferences
    GET    /api/v1/works/author-preferences/{name}
    PUT    /api/v1/works/author-preferences/{name}
    DELETE /api/v1/works/author-preferences/{name}

The `/{work_id}` route must be declared *after* every static-prefix
route (like `/author-preferences`) or FastAPI's first-match semantics
silently swallow them as `work_id` values — same class of bug as the
bulk/tentative routing fix. See `app/routers/works.py` for the ordering.

## See also

- `app/works/normalize.py` — key generation
- `app/works/matcher.py` — the matcher loop
- `app/works/storage.py` — CRUD helpers
- `app/works/preferences.py` — tracking_mode storage
- `app/discovery/routers/books.py::_apply_tracking_mode_filter` —
  the missing-list filter
- `tests/works/` + `tests/routers/test_tracking_mode_filter.py` +
  `tests/routers/test_works.py` — coverage
