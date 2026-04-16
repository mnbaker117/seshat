"""
MAM IRC announce parser.

Turns one raw announce line from MouseBot in `#announce` on
`irc.myanonamouse.net` into a fully populated `Announce` object that the
filter can evaluate.

The regex is the exact one Autobrr uses in its `myanonamouse.yaml`
indexer definition. Lifted verbatim with no modifications because it's
been battle-tested against years of real MAM IRC traffic — divergence
would be a footgun. The capture groups are:

    1. title              "The Demon King"
    2. author_blob        "Peter V Brett"
    3. category           "Audiobooks - Fantasy"
    4. size               "921.91 MiB"
    5. filetype           "m4b"
    6. language           "English"
    7. base_url           "https://www.myanonamouse.net/"
    8. torrent_id         "1233592"
    9. vip                "VIP" or None

Real format observed in production (from autobrr.log fixtures):

    New Torrent: The Demon King By: Peter V Brett Category: ( Audiobooks - Fantasy ) Size: ( 921.91 MiB ) Filetype: ( m4b ) Language: ( English ) Link: ( https://www.myanonamouse.net/t/1233592 ) VIP

This module is intentionally pure — no I/O, no logging, no state.
Database persistence and side effects are the caller's job.
"""
from __future__ import annotations

import re
from typing import Optional

from app.filter.gate import Announce


# The Autobrr MAM regex, ported verbatim. See module docstring for the
# capture-group breakdown. The leading `New Torrent: ` literal is what
# distinguishes a real announce from any other PRIVMSG MouseBot might
# emit (status messages, errors, etc.) — anything that doesn't start
# with that prefix is silently ignored.
_ANNOUNCE_RX = re.compile(
    r"New Torrent: (.*) By: (.*) Category: \( (.*) \) "
    r"Size: \( (.*) \) Filetype: \( (.*) \) Language: \( (.*) \) "
    r"Link: \( (https?://[^/]+/).*?(\d+)\s*\)\s*(VIP)?"
)

# mIRC formatting codes that real MAM IRC traffic includes inline.
# Without stripping these, the regex above silently fails to match
# every real announce — `04New Torrent:14` (color 4 / 14 wrapping
# the literal text) is not the same as `New Torrent:` to a regex.
# Caught the hard way during the first production smoke test: the
# unit-test fixtures we had were the DECOLORED form Autobrr serves
# in its logs, but raw IRC traffic carries the color bytes.
#
#   \x02  bold
#   \x03  color (followed by NN[,MM] digits)
#   \x0f  reset
#   \x16  reverse
#   \x1d  italic
#   \x1e  strikethrough
#   \x1f  underline
#
# The color sequence `\x03NN` or `\x03NN,MM` is the special case
# because it has a numeric payload following the marker byte. The
# others are single-byte tokens that we can drop directly.
_COLOR_CODE_RX = re.compile(r"\x03(?:\d{1,2}(?:,\d{1,2})?)?")
_FORMATTING_CODES = str.maketrans(
    "", "", "\x02\x0f\x16\x1d\x1e\x1f"
)


def _strip_irc_formatting(line: str) -> str:
    """Strip mIRC color/formatting codes from a raw IRC PRIVMSG body.

    Order matters: handle the variable-length color sequences with a
    regex first, THEN drop the single-byte formatting tokens with a
    translate. Doing it in the other order would leave orphan digit
    bytes from a color marker that's been partially consumed.
    """
    if not line:
        return line
    cleaned = _COLOR_CODE_RX.sub("", line)
    cleaned = cleaned.translate(_FORMATTING_CODES)
    return cleaned

# MAM truncates the author list when there are too many co-authors,
# appending "and N more" (or just ", N more"). Without stripping this
# the splitter would happily produce a phantom author named "1 more"
# and that author would never match anything in the allow/ignore lists.
# Real example from autobrr.log:
#   "Stephen Marlowe, John Roeburt, Ed Lacy, and 1 more"
# Strips trailing ", and N more" / ", N more" / "and N more".
_AND_N_MORE_RX = re.compile(
    r"\s*,?\s*(?:and\s+)?\d+\s+more\s*$",
    re.IGNORECASE,
)


def _strip_and_n_more(blob: str) -> str:
    """Remove a trailing 'and N more' truncation marker from an author blob."""
    cleaned = _AND_N_MORE_RX.sub("", blob).rstrip().rstrip(",").rstrip()
    return cleaned


def parse_announce(line: str) -> Optional[Announce]:
    """Parse one IRC announce line into an `Announce`, or None if it doesn't match.

    The MAM IRC channel emits a steady stream of `New Torrent:` PRIVMSGs
    plus the occasional unrelated bot message. Anything that doesn't
    match the announce regex returns None — the caller treats None as
    "ignore this line, not for us."

    No exceptions are raised for malformed input. The contract is
    Optional[Announce], not "raises on bad input" — the IRC listener
    runs in a tight loop and exception handling per line would just be
    silently absorbed `try/except` boilerplate at the call site.
    """
    if not line:
        return None

    # Strip mIRC color and formatting codes BEFORE running the regex.
    # MAM's MouseBot wraps fields in color codes (`\x0304New
    # Torrent:\x0314 ...`) that the unit-test fixtures didn't have
    # because they came from Autobrr's already-decolored log dump.
    cleaned = _strip_irc_formatting(line)

    m = _ANNOUNCE_RX.search(cleaned)
    if not m:
        return None

    title = m.group(1).strip()
    raw_author = m.group(2).strip()
    category = m.group(3).strip()
    size = m.group(4).strip()
    filetype = m.group(5).strip()
    language = m.group(6).strip()
    base_url = m.group(7)
    torrent_id = m.group(8)
    vip = bool(m.group(9))

    author_blob = _strip_and_n_more(raw_author)

    # Reconstruct the canonical info URL from the captured base + ID.
    # MAM's torrent landing page URL is `<base>t/<id>`. Building it from
    # captures rather than copying the raw match guarantees the URL we
    # store is well-formed even if MAM ever changes the path slightly.
    info_url = f"{base_url}t/{torrent_id}"

    return Announce(
        torrent_id=torrent_id,
        torrent_name=title,
        category=category,
        author_blob=author_blob,
        title=title,
        info_url=info_url,
        size=size,
        filetype=filetype,
        language=language,
        vip=vip,
    )


def build_download_url(torrent_id: str, *, use_fl_wedge: bool = False) -> str:
    """Construct the .torrent file download URL for a given MAM torrent ID.

    Used by the grab path. Kept here next to the parser because the
    URL shape is part of the same MAM-specific API surface, and the
    caller already has the parsed `torrent_id` field handy.

    The URL is the same one Autobrr uses (confirmed from its
    `myanonamouse.yaml`) — `/tor/download.php?tid=<id>`. Authentication
    is via the `mam_id` cookie attached as an HTTP header at fetch time;
    the URL itself carries no token.

    When `use_fl_wedge=True`, appends `&fl=1` to spend a freeleech
    wedge on this torrent, making the download free. The policy engine
    decides whether to set this flag.
    """
    url = f"https://www.myanonamouse.net/tor/download.php?tid={torrent_id}"
    if use_fl_wedge:
        url += "&fl=1"
    return url
