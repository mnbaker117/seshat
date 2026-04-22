# Contributing to Seshat

Thanks for the interest. Seshat is a single-maintainer hobby project, so a
few words on scope and workflow before you invest time.

## What's welcome

- **Bug reports.** Include the Seshat version, deployment method
  (Docker / Unraid / bare metal), relevant log output, and repro steps.
- **Small, focused PRs** for bugs, typos, docs, dependency bumps, or
  obvious quality-of-life fixes. These can go straight in without
  prior discussion.
- **New metadata sources** behind the existing `MetadataSource`
  interface — the architecture is designed for this.
- **New torrent clients** behind the `TorrentClient` interface.
- **New delivery sinks** (alongside Calibre / CWA / ABS / folder).
- **Bug-fix PRs for audiobook or Audiobookshelf integration** — ABS
  coverage is newer than the Calibre side and more likely to have
  rough edges.

## What to discuss first

Open an issue before starting work on any of these:

- Large features, refactors, or new UI surfaces
- Changes to the database schema or migration layer
- New top-level settings or breaking changes to existing ones
- Support for additional private trackers (the IRC / policy / snatch
  logic is tightly coupled to MAM's conventions; generalizing it is a
  deliberate design decision, not a drop-in)

## What's out of scope

- Multi-user / role-based auth. Seshat is single-admin by design.
- Features that require running Seshat as a public-facing service.
- Bundling a torrent client, VPN, or proxy inside the container.

## Dev setup

**Backend** (Python 3.12+):

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
uvicorn app.main:app --reload --port 8789
```

**Frontend** (Node 22+):

```bash
cd frontend
npm install
npm run dev
```

The Vite dev server proxies `/api` to `http://localhost:8789`, so run
both together. Point `CALIBRE_PATH` at a Calibre library (a small test
library works fine) before starting the backend.

## Tests

```bash
# Backend — 625+ tests, should finish in under a minute
pytest

# Frontend typecheck
cd frontend && npm run typecheck
```

All PRs need to keep `pytest` green. If you add behavior, add a test
for it — the test suite is the safety net that lets this project ship
quickly.

## Code style

- **Python:** standard library first, then third-party, then local.
  Prefer explicit types on public functions. `async def` for anything
  that touches I/O. No new `print()` calls — use the `logging` module.
- **TypeScript:** strict mode is on; don't disable it per-file. No new
  `@ts-nocheck` or `@ts-ignore` without a comment explaining why.
- **SQL:** migrations live in `app/db/migrations/`. Every schema change
  needs a migration; never edit an existing migration after it's
  shipped.
- **Comments:** only when the *why* is non-obvious. The *what* should
  be clear from the code.

## Commit messages

One short imperative-mood line summarizing the change, optionally
followed by a blank line and a paragraph of context. No emoji, no
`feat:` / `fix:` prefixes.

## Pull requests

- Keep PRs scoped to one thing. Two unrelated fixes = two PRs.
- Include a short summary of what changed and why.
- If the change affects user-visible behavior, add a line to
  `CHANGELOG.md` under the Unreleased section.
- Expect review turnaround in days, not hours.

## Security issues

Don't file these as public issues. See [SECURITY.md](SECURITY.md).
