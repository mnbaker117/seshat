# v2.12.1 — UAT findings (running list)

Captured during v2.12.0 UAT 2026-05-14. Fix as a single v2.12.1
hotfix release after UAT completes.

---

## #1 — Cross-library bulk-delete sends IDs to wrong library

**Severity**: Dangerous. Could delete unrelated books on id-collision when the colliding row isn't Calibre/ABS-protected.

**Found during**: UAT item 2.2 (delete confirmation copy).

**Reproduction**:
1. Authors page → "All" filter (cross-library merged view)
2. Search Corey, click into his detail page
3. Stay on Combined tab (or switch tabs — doesn't change behavior)
4. Click "Select" → pick "The Far Reaches" from the Audiobook block
5. Click bulk Delete → confirm

**Observed**:
- Toast: `"Deleted 0 book(s), skipped 1 Calibre-synced"`
- "The Far Reaches" still present in audiobook list
- The actual book being protected was an unrelated ebook in the calibre library (id=288 = "God Hammer") because of id collision

**Root cause**:
- Frontend `bulkAct` sends `book_ids=[<ids>]` to a single endpoint URL with `?slug=${a.active_library_slug}`
- `a.active_library_slug` is fixed by the URL slug used to navigate (the active library at navigation time)
- Selecting books from a different library tab in Combined view → IDs are scoped to that other library, but the request goes to the active library's DB
- Backend matches IDs against the wrong DB → arbitrary unrelated books get acted on (or protected as Calibre-synced if collision lands on a Calibre row)

**Files involved**:
- `frontend/src/pages/DiscAuthorDetailPage.tsx` `bulkAct()` ~line 773
- `frontend/src/pages/MobileAuthorDetailPage.tsx` parallel mobile path
- `app/discovery/routers/books.py` `bulk_delete` (and `bulk_hide` / `bulk_dismiss` / `bulk_skip_mam` — likely same pattern)

**Fix (Option B per Mark)**:
- Refactor selection state from `Set<bookId>` to `Set<"libSlug:bookId">` (or equivalent)
- On bulk action, partition selection by libSlug
- Fire one bulk-{op} request per library, each with that library's `slug=` + scoped book_ids
- Aggregate the per-library responses into a single user-facing toast
- **All four bulk endpoints fixed in one sweep** (per Mark 2026-05-14): `bulk-delete`, **`bulk-hide`** (first-class target, Mark explicit), `bulk-dismiss`, `bulk-skip-mam`. Same structural bug, same fix shape — handled automatically because the frontend `bulkAct(kind)` function is the single source of truth for all four.

**Side effect of fix**: the `syncedLabel` copy that picks Calibre-synced/Audiobookshelf-synced from `a.active_content_type` becomes less relevant — each per-library response has its own skipped count + label. Cleaner UX: count Calibre-skipped + ABS-skipped separately in the aggregate toast.

**Related**: cross-library scan-sources + clear-sources already handle this via `author_names` cross-library resolution. Bulk-delete is the same class of bug but operates on book_ids which can't be name-resolved — needs the per-library partition approach.

**Also blocks UAT 2.8 verification** (Bulk-delete ABS protection from Phase 2.2). UAT attempt 2026-05-14: Mark selected an owned audiobook, clicked Delete, got "skipped 1 Calibre-synced" toast. The toast appeared to confirm protection — but the log shows the request went to `?slug=calibre-library` and the "protected" book was actually an unrelated Calibre ebook with the same numeric id (id-collision). The Phase 2.2 ABS-protection backend code is correct on paper but un-testable in production until this finding is fixed. Post-fix UAT (or v2.12.2 UAT) needs to re-verify 2.8 explicitly.

---

## #2 — Cross-library Scan Audiobooks/Ebooks no-ops when author missing from target lib

**Severity**: Medium-low. Functional toast appears but feature under-delivers vs the original intent.

**Found during**: UAT item 2.1 / 2.3 — Mark noted he has no Sanderson audiobooks owned, asked whether the cross-library Scan Audiobooks would still try to discover.

**v2.12.0 behavior** (implemented as conservative Option A): per-author cross-library scan iterates target-type libraries, runs `SELECT id, name FROM authors WHERE name = ?` in each, and bails with `{total: 0, message: "no audiobook-library match"}` if no matching row found in any.

**Original spec intent (Q1)**: *"search all audiobook libraries of this author and then go find more audiobooks"* — implies the scan SHOULD fire even when no author row exists yet, to discover new content.

**Mark's design (decided 2026-05-14 mid-UAT)**: **Dual author-row pattern.**

As soon as an author is created in EITHER library type (ebook OR audiobook), a stub row is automatically created in the OTHER type. The stub has zero books — empty author pages just display "no books yet" when the user filters to that type. All/Combined views work naturally (the empty side just contributes nothing).

This makes the cross-library Scan Audiobooks/Ebooks feature work cleanly:
- Author always has a row in both lib types
- Scan picks up the stub row, runs full discovery against it
- Newly-discovered books land in the target lib via the normal merge layer

**Implementation surfaces**:
  - Calibre sync (`calibre_sync.py`): when inserting a new author row, mirror the insert into every audiobook library DB (stub author with no books).
  - ABS sync (`audiobookshelf_sync.py`): when inserting a new author row, mirror into every ebook library DB.
  - Manual add-author flow (if exists): same mirroring.
  - Backfill migration: one-shot pass on upgrade to v2.12.1 — for every author in any library, create stubs in the other-type libraries where missing.
  - Author dedup: cross-library author identity needs to be stable (same canonical name → same conceptual author across both lib types). The existing `_normalize_author` machinery probably covers this; verify before relying on it.

**Frontend impact**:
  - Author Detail empty-tab UX: when filtered to audiobook and the audiobook stub has zero books, show "No audiobooks yet for this author. Click Scan Audiobooks to discover."
  - List pages: filter to audiobook only → stub-only authors don't appear (filter on "has books in this type").

**Scope estimate**: medium — 2-3 sync files + 1 migration + frontend empty-state copy. Lower-risk than (B)/(C) options because the model is cleaner (always have an author row, just sometimes empty).

**Resolves finding #2's no-op behavior naturally**: once dual-row is in place, the Phase 3 `pre_resolved` SQL will always find a row, so total>0 and the scan runs.

---

## #3 — Toast copy refinements (v2.12.1 polish)

**Severity**: Cosmetic.

**Found during**: UAT items 2.10 (cross-library scan) — toast text is technically correct but not user-friendly.

**Examples**:
  - **Sanderson Scan Audiobooks**: `"No matching authors in target libraries."` → better: `"No audiobook-library match for 'Brandon Sanderson' — add him to an audiobook library first, or click Scan Ebooks instead."` (or similar — name the author + suggest a remedy)
  - **Sanderson Scan Ebooks**: `"Ebook scan started — 1 library iteration(s)."` → better: `"Scanning ebook sources for 'Brandon Sanderson' across 1 library."` (drop the technical "iteration" phrasing, prefer present-tense action)

**Files**:
  - `frontend/src/pages/DiscAuthorDetailPage.tsx` `_crossLibraryAuthorScan` toast strings
  - `frontend/src/pages/DiscAuthorsPage.tsx` `scanSources` toast strings (parallel)
  - `frontend/src/pages/DiscBooksPage.tsx` `scanSources` toast strings (parallel)
  - Backend: `app/discovery/routers/authors.py` cross-library `message` fields (e.g. `"No matching authors in target libraries."` → name the author)

**Approach**: pass the author name back through to the frontend toast template; rewrite messages in plain English; keep total/library counts but in a less mechanical phrasing.

---

---
