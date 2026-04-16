"""
Torrent client implementations.

The `base.TorrentClient` Protocol defines the contract every concrete
client implements: login, add a .torrent file, list torrents, get one
by hash, pause/resume/move/recheck for migration support.

Supported clients:
  - `qbittorrent.QbitClient` — qBittorrent v4/v5 WebUI API
  - `transmission.TransmissionClient` — Transmission RPC
  - `deluge.DelugeClient` — Deluge Web UI JSON-RPC
  - `rtorrent.RtorrentClient` — rTorrent XML-RPC

The `download_client_type` setting in settings.json selects which
client class is instantiated at startup. The rest of Seshat only
talks through the `TorrentClient` Protocol — it never knows which
client it's using.
"""
