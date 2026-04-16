<div align="center">

# Seshat

**Self-hosted book discovery and acquisition platform.**

Scans your Calibre library against multiple metadata sources, searches
MyAnonamouse for missing titles, and automates the full pipeline from
IRC announce monitoring through torrent management, metadata enrichment,
and Calibre delivery — all from a single unified interface.

*Named after the Egyptian goddess of writing, libraries, and record-keeping.*

</div>

---

## What it does

Seshat combines two complementary workflows into one app:

### Discovery

Sync your Calibre library and find every book you're missing across
7 metadata sources (Goodreads, Hardcover, Kobo, Amazon, IBDB, Google
Books, MyAnonamouse). Manage authors, series, and pen-name aliases.
Search MAM for matches and see which titles are available.

### Acquisition

Monitor MAM's IRC announce channel in real time. Filter against your
author lists, evaluate economic policy (VIP, freeleech, wedge, ratio),
manage a snatch budget, download through your torrent client, enrich
metadata from 7 sources, queue everything for manual review with cover
images, and deliver approved books to Calibre/CWA.

---

## Architecture

- **Backend:** Python 3.12 + FastAPI + SQLite (WAL mode) + aiosqlite
- **Frontend:** Vite + React 18 + TypeScript
- **Background jobs:** supervised asyncio tasks + APScheduler
- **Auth:** bcrypt + itsdangerous signed cookies + Fernet-encrypted secrets
- **Docker:** two-stage build (node:22-alpine + python:3.12-slim)

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
      - /path/to/calibre/books:/calibre
      - /path/to/downloads:/downloads
      - ./staging:/staging
      - ./review-staging:/review-staging
    restart: unless-stopped
```

Then open `http://your-server:8789` and follow the first-run wizard.

---

## Requirements

- **Docker** (recommended) or Python 3.12+ for development
- A **Calibre library** (mounted read-only for discovery sync)
- A **MyAnonamouse** account with IRC credentials + session cookie
- A **torrent client** (qBittorrent, Transmission, Deluge, or rTorrent)
- *Optional:* Hardcover API key, ntfy server for notifications

---

## License

[MIT](LICENSE)
</div>
