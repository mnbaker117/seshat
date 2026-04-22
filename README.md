<div align="center">

<img src="icon.png" width="96" alt="Seshat" />

# Seshat

**Self-hosted book discovery and acquisition platform.**

Scans your Calibre library against multiple metadata sources, searches
a private tracker for missing titles, and automates the full pipeline
from IRC announce monitoring through torrent management, metadata
enrichment, and Calibre delivery — all from a single unified interface.

*Named after the Egyptian goddess of writing, libraries, and record-keeping.*

[![Docker Image](https://img.shields.io/badge/ghcr.io-seshat-blue?style=flat-square&logo=docker)](https://github.com/mnbaker117/seshat/pkgs/container/seshat)
[![Python](https://img.shields.io/badge/python-3.12-blue?style=flat-square&logo=python)](https://www.python.org/)
[![React](https://img.shields.io/badge/react-18-61DAFB?style=flat-square&logo=react)](https://react.dev/)
[![Tests](https://img.shields.io/badge/tests-625_passing-brightgreen?style=flat-square)](tests/)
[![License](https://img.shields.io/github/license/mnbaker117/seshat?style=flat-square)](LICENSE)

</div>

---

## Two domains, one app

### Discovery

Sync your Calibre library and find every book you're missing across
7 metadata sources (Goodreads, Hardcover, Kobo, Amazon, IBDB, Google
Books, MAM). Manage authors, series, and pen-name aliases. Search MAM
for matches and see which titles are available.

### Pipeline

Monitor MAM's IRC announce channel in real time. Filter against your
author lists, evaluate economic policy (VIP, freeleech, wedge, ratio),
manage a snatch budget, download through your torrent client, enrich
metadata from 7 sources, queue everything for manual review with cover
images, and deliver approved books to Calibre/CWA.

### Audiobook support

Audiobookshelf is a first-class library backend. Seshat discovers your
ABS library alongside Calibre, pulls metadata from Audible + Audnexus,
routes audiobook MAM grabs through a dedicated sink, and triggers a
scan on the ABS server so new books show up without a manual refresh.

Cross-library *works* link the same book across ebook + audiobook
libraries so your Discovery views can show "Foundation" as one entity
with both formats. Per-author tracking preferences let you pin an
author to ebook-only, audiobook-only, or both — missing detection and
MAM scans respect the preference automatically.

### Unified Dashboard

Discovery stats, pipeline stats, and per-library sync rows (Calibre +
ABS side-by-side) all live on a single three-column dashboard. Stats
rail on the right, Quick Actions across the bottom.

---

## Screenshots

<div align="center">

<img src="docs/images/dashboard.png" alt="Seshat Dashboard" width="900" />

</div>

---

## Quick start (Docker)

```yaml
services:
  seshat:
    image: ghcr.io/mnbaker117/seshat:latest
    container_name: seshat
    ports:
      - "8789:8789"
    volumes:
      - ./data:/app/data
      - /path/to/calibre/books:/calibre:ro
      - /path/to/audiobookshelf/library:/audiobooks   # optional
      - /path/to/downloads:/downloads
      - ./staging:/staging
      - ./review-staging:/review-staging
    environment:
      CALIBRE_PATH: "/calibre"
    restart: unless-stopped
```

Then open `http://your-server:8789` and follow the first-run wizard.
If you're adding Audiobookshelf, point it at the same `/audiobooks`
mount so Seshat can drop new audiobook files straight into ABS's
library path — Seshat will trigger a scan on the ABS server as soon
as the file lands.

---

## Architecture

- **Backend:** Python 3.12 + FastAPI + SQLite (WAL mode) + aiosqlite
- **Frontend:** Vite + React 18 + TypeScript
- **Databases:** Separate SQLite files — per-library discovery DBs + pipeline DB + auth DB
- **Background jobs:** supervised asyncio tasks + APScheduler
- **Auth:** bcrypt + itsdangerous signed cookies + Fernet-encrypted secrets
- **Theme:** Egyptian goddess palette (gold, deep indigo, jade green)
- **Docker:** two-stage build (node:22-alpine + python:3.12-slim)
- **API routes:** 138 total (74 discovery + 53 pipeline + 11 shared)
- **Library backends:** Calibre (file-based) + Audiobookshelf (API-based),
  composable — users can run multiple of either. Cross-library `works`
  linked via the pipeline DB.

---

## Requirements

- **Docker** (recommended) or Python 3.12+ for development
- A **Calibre library** (mounted read-only for discovery sync)
- A **MAM** account with IRC credentials + session cookie
- A **torrent client** (qBittorrent, Transmission, Deluge, or rTorrent)
- *Optional:* **Audiobookshelf** (URL + API key) for audiobook support,
  Hardcover API key, ntfy server for notifications

---

## License

[MIT](LICENSE)

---

<div align="center">

*Seshat finds the books. Seshat gets the books.*

</div>
