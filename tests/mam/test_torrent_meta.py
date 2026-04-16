"""
Unit tests for the bencode info-hash extractor.

The function has one job — given .torrent bytes, return the same
40-character lowercase hex string qBittorrent would report. The
hash MUST be computed over the original byte slice, not a
re-encoded version, because bencode allows multiple encodings of
the same logical data and qBit hashes the file as-is.

Test strategy:
  - Hand-craft a small bencoded torrent with a known SHA1 expected
    value (computed independently with `hashlib.sha1` on the info
    slice). This gives us a regression-proof anchor.
  - Verify the function isolates the info dict cleanly even when
    other top-level keys come before or after it.
  - Verify error handling on malformed input — the dispatcher needs
    a clear `BencodeError` to fall back to "unknown hash" mode.
"""
import hashlib

import pytest

from app.mam.torrent_meta import BencodeError, info_hash


# ─── Hand-built fixtures ─────────────────────────────────────


def _build_torrent(*, info_payload: bytes, extra_keys: dict = None) -> bytes:
    """Construct a bencoded torrent file from a raw info dict body.

    `info_payload` is the bytes BETWEEN the info dict's `d` and `e`
    markers — e.g. `4:name8:test.txt12:piece lengthi16384e`. The
    helper wraps it with `d...e` and inserts it into a top-level
    dict alongside the standard `announce` key (and any extras).
    """
    info_slice = b"d" + info_payload + b"e"
    parts = [b"d"]
    parts.append(b"8:announce31:http://tracker.example/announce")
    if extra_keys:
        for k, v in sorted(extra_keys.items()):
            parts.append(f"{len(k)}:".encode() + k + v)
    parts.append(b"4:info" + info_slice)
    parts.append(b"e")
    return b"".join(parts)


def _expected_hash(info_payload: bytes) -> str:
    return hashlib.sha1(b"d" + info_payload + b"e").hexdigest()


# ─── Happy path ──────────────────────────────────────────────


class TestHashHappyPath:
    def test_minimal_valid_torrent(self):
        info = (
            b"4:name8:test.txt12:piece lengthi16384e6:pieces20:"
            + b"\x00" * 20
        )
        torrent = _build_torrent(info_payload=info)
        result = info_hash(torrent)
        assert result == _expected_hash(info)
        assert len(result) == 40
        assert all(c in "0123456789abcdef" for c in result)

    def test_extra_keys_before_info_ignored(self):
        info = b"4:name3:foo12:piece lengthi16384e6:pieces20:" + b"\x00" * 20
        # Add a `comment` key (sorted before `info` alphabetically).
        torrent = _build_torrent(
            info_payload=info,
            extra_keys={b"comment": b"7:hello!!"},
        )
        assert info_hash(torrent) == _expected_hash(info)

    def test_nested_info_with_files_list(self):
        # Multi-file torrent shape: info dict contains a `files` list.
        info = (
            b"5:filesld6:lengthi100e4:pathl5:a.txteed6:lengthi200e4:pathl5:b.txteee"
            b"4:name3:foo12:piece lengthi16384e6:pieces40:" + b"\x00" * 40
        )
        torrent = _build_torrent(info_payload=info)
        assert info_hash(torrent) == _expected_hash(info)


# ─── Failure cases ───────────────────────────────────────────


class TestHashFailures:
    def test_empty_input(self):
        with pytest.raises(BencodeError, match="empty"):
            info_hash(b"")

    def test_not_starting_with_dict(self):
        with pytest.raises(BencodeError, match="top-level dict"):
            info_hash(b"i42e")

    def test_no_info_key(self):
        # Top-level dict with announce but no info.
        torrent = b"d8:announce30:http://tracker.example/announcee"
        with pytest.raises(BencodeError, match="no `info` key"):
            info_hash(torrent)

    def test_truncated_torrent(self):
        # Valid prefix, abruptly cut off mid-info-dict.
        torrent = b"d8:announce5:short4:infod4:name8:cutoff!!"
        with pytest.raises(BencodeError):
            info_hash(torrent)

    def test_malformed_string_length(self):
        torrent = b"dXY:fooe"
        with pytest.raises(BencodeError):
            info_hash(torrent)
