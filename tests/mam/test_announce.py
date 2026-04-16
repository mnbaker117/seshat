"""
Unit tests for the MAM announce parser.

Two layers of testing:

  1. **Hand-written cases** — pin down specific behaviors (the
     "and N more" stripping, the typographic apostrophe, optional VIP
     suffix, malformed input rejection). These are the regression tests
     that catch deliberate behavior changes.

  2. **Real fixture sweep** — every line in
     `tests/fixtures/real_announces.txt` (18 captures from the user's
     production Autobrr log) MUST parse cleanly. This is the safety net
     that catches divergence between Autobrr's regex (which we cribbed
     verbatim) and what MAM actually emits today. If MAM changes the
     announce format, this sweep is the alarm.
"""
from pathlib import Path

from app.filter.gate import Announce
from app.mam.announce import (
    _strip_and_n_more,
    _strip_irc_formatting,
    build_download_url,
    parse_announce,
)


_FIXTURES_PATH = Path(__file__).parent.parent / "fixtures" / "real_announces.txt"


def _load_real_announces() -> list[str]:
    return [
        line.rstrip("\n")
        for line in _FIXTURES_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# ─── Real-fixture sweep ──────────────────────────────────────


class TestRealFixturesParse:
    """Every captured production announce must parse cleanly."""

    def test_all_fixtures_parse(self):
        announces = _load_real_announces()
        assert len(announces) >= 18, "Fixture file shrunk unexpectedly"
        for line in announces:
            result = parse_announce(line)
            assert result is not None, f"Failed to parse: {line!r}"
            assert isinstance(result, Announce)
            assert result.torrent_id, f"Missing torrent_id in: {line!r}"
            assert result.torrent_name, f"Missing torrent_name in: {line!r}"
            assert result.category, f"Missing category in: {line!r}"
            assert result.author_blob, f"Missing author_blob in: {line!r}"

    def test_fixture_torrent_ids_are_unique_and_numeric(self):
        announces = _load_real_announces()
        ids = []
        for line in announces:
            result = parse_announce(line)
            assert result is not None
            assert result.torrent_id.isdigit()
            ids.append(result.torrent_id)
        assert len(set(ids)) == len(ids), "Fixture file has duplicate torrent IDs"

    def test_fixture_info_urls_are_well_formed(self):
        announces = _load_real_announces()
        for line in announces:
            result = parse_announce(line)
            assert result is not None
            assert result.info_url.startswith("https://www.myanonamouse.net/")
            assert result.info_url.endswith(f"/t/{result.torrent_id}")


# ─── Hand-written cases — specific behaviors ─────────────────


class TestParseAnnounce:
    def test_basic_single_author_with_vip(self):
        line = (
            "New Torrent: The Demon King By: Peter V Brett "
            "Category: ( Audiobooks - Fantasy ) Size: ( 921.91 MiB ) "
            "Filetype: ( m4b ) Language: ( English ) "
            "Link: ( https://www.myanonamouse.net/t/1233592 ) VIP"
        )
        result = parse_announce(line)
        assert result is not None
        assert result.torrent_id == "1233592"
        assert result.torrent_name == "The Demon King"
        assert result.title == "The Demon King"
        assert result.author_blob == "Peter V Brett"
        assert result.category == "Audiobooks - Fantasy"
        assert result.size == "921.91 MiB"
        assert result.filetype == "m4b"
        assert result.language == "English"
        assert result.vip is True
        assert result.info_url == "https://www.myanonamouse.net/t/1233592"

    def test_basic_without_vip(self):
        line = (
            "New Torrent: The Path of Ascension 11 By: C Mantis "
            "Category: ( Audiobooks - Fantasy ) Size: ( 761.20 MiB ) "
            "Filetype: ( m4b ) Language: ( English ) "
            "Link: ( https://www.myanonamouse.net/t/1233620 )"
        )
        result = parse_announce(line)
        assert result is not None
        assert result.vip is False
        assert result.torrent_id == "1233620"

    def test_and_n_more_stripped(self):
        # Real-world MAM truncation when there are too many co-authors.
        line = (
            "New Torrent: The Hardboiled Mystery MEGAPACK "
            "By: Stephen Marlowe, John Roeburt, Ed Lacy, and 1 more "
            "Category: ( Ebooks - Mystery ) Size: ( 743.58 KiB ) "
            "Filetype: ( epub ) Language: ( English ) "
            "Link: ( https://www.myanonamouse.net/t/1233596 ) VIP"
        )
        result = parse_announce(line)
        assert result is not None
        # The "and 1 more" suffix must be removed so the splitter
        # produces 3 authors, not 4 with a phantom "1 more".
        assert "more" not in result.author_blob.lower()
        assert result.author_blob == "Stephen Marlowe, John Roeburt, Ed Lacy"

    def test_typographic_apostrophe_in_title_preserved(self):
        # The parser preserves the title verbatim — apostrophe
        # normalization happens in the filter layer when comparing
        # against author lists, not in the parser.
        line = (
            "New Torrent: I Won\u2019t Let Mistress Suck My Blood, Vol. 1 "
            "By: Paderapollonorio Category: ( Ebooks - Comics/Graphic novels ) "
            "Size: ( 62.93 MiB ) Filetype: ( cbz ) Language: ( English ) "
            "Link: ( https://www.myanonamouse.net/t/1233619 ) VIP"
        )
        result = parse_announce(line)
        assert result is not None
        assert "\u2019" in result.title

    def test_title_with_colon(self):
        # "Classroom of the Elite: Year 2, Vol. 12.5" — the colon in the
        # title shouldn't confuse the regex (it's a real fixture).
        line = (
            "New Torrent: Classroom of the Elite: Year 2, Vol. 12.5 "
            "By: Syougo Kinugasa Category: ( Audiobooks - Young Adult ) "
            "Size: ( 472.16 MiB ) Filetype: ( m4b ) Language: ( English ) "
            "Link: ( https://www.myanonamouse.net/t/1233608 ) VIP"
        )
        result = parse_announce(line)
        assert result is not None
        assert result.title == "Classroom of the Elite: Year 2, Vol. 12.5"
        assert result.author_blob == "Syougo Kinugasa"

    def test_title_with_comma(self):
        # "Sea of Wind, Shore of the Labyrinth" — comma in title
        line = (
            "New Torrent: Sea of Wind, Shore of the Labyrinth "
            "By: Fuyumi Ono Category: ( Audiobooks - Fantasy ) "
            "Size: ( 401.33 MiB ) Filetype: ( m4b ) Language: ( English ) "
            "Link: ( https://www.myanonamouse.net/t/1233605 ) VIP"
        )
        result = parse_announce(line)
        assert result is not None
        assert result.title == "Sea of Wind, Shore of the Labyrinth"

    def test_category_with_slash(self):
        # "Ebooks - Action/Adventure", "Ebooks - Crime/Thriller" — slashes
        # in the category are common and shouldn't be eaten by the regex.
        line = (
            "New Torrent: God's Eye By: Robert Rapoza "
            "Category: ( Ebooks - Action/Adventure ) Size: ( 1.49 MiB ) "
            "Filetype: ( epub ) Language: ( English ) "
            "Link: ( https://www.myanonamouse.net/t/1233601 ) VIP"
        )
        result = parse_announce(line)
        assert result is not None
        assert result.category == "Ebooks - Action/Adventure"

    # ─── _strip_irc_formatting direct tests ──────────────────

    def test_strip_irc_formatting_color_with_two_digits(self):
        # The most common shape: \x03 + two-digit color code +
        # actual text. The grey color used by MouseBot is `14`.
        assert _strip_irc_formatting("\x0314hello") == "hello"

    def test_strip_irc_formatting_color_with_one_digit(self):
        # IRC color codes are 1-2 digits. \x031 (color 1) is valid.
        assert _strip_irc_formatting("\x031hi") == "hi"

    def test_strip_irc_formatting_color_with_background(self):
        # \x03NN,MM is "foreground color NN, background color MM".
        assert _strip_irc_formatting("\x0304,00text") == "text"

    def test_strip_irc_formatting_bare_color_reset(self):
        # \x03 alone with no digits resets to default colors.
        assert _strip_irc_formatting("hello\x03world") == "helloworld"

    def test_strip_irc_formatting_bold(self):
        assert _strip_irc_formatting("\x02bold\x02 text") == "bold text"

    def test_strip_irc_formatting_underline(self):
        assert _strip_irc_formatting("\x1funderlined\x1f") == "underlined"

    def test_strip_irc_formatting_italic(self):
        assert _strip_irc_formatting("\x1ditalic\x1d") == "italic"

    def test_strip_irc_formatting_reverse(self):
        assert _strip_irc_formatting("\x16reverse\x16") == "reverse"

    def test_strip_irc_formatting_strikethrough(self):
        assert _strip_irc_formatting("\x1estrikethrough\x1e") == "strikethrough"

    def test_strip_irc_formatting_reset(self):
        assert _strip_irc_formatting("\x0fafter reset") == "after reset"

    def test_strip_irc_formatting_multiple_codes(self):
        # All formatting codes together — common in styled bot output
        assert (
            _strip_irc_formatting("\x02\x0304bold red\x0f normal")
            == "bold red normal"
        )

    def test_strip_irc_formatting_preserves_plain_digits(self):
        # Critical: digits NOT preceded by \x03 must be preserved.
        # The torrent ID 1233678 in a real announce is just digits;
        # if we accidentally consume it, parse_announce dies.
        assert _strip_irc_formatting("torrent 1233678") == "torrent 1233678"

    def test_strip_irc_formatting_empty(self):
        assert _strip_irc_formatting("") == ""

    def test_strip_irc_formatting_no_codes_passthrough(self):
        # Non-colored input should pass through unchanged.
        line = "New Torrent: Foo By: Bar Category: ( Ebooks - Fantasy )"
        assert _strip_irc_formatting(line) == line

    def test_real_colored_privmsg_parses_after_stripping(self):
        # The actual on-the-wire shape MAM IRC sends. Captured live
        # from the first production smoke test once the keepalive
        # fix landed and the listener actually started receiving
        # PRIVMSGs. Color codes (\x03 followed by 1-2 digits) wrap
        # every field — without the formatting stripper the regex
        # silently doesn't match and Seshat looks like it's
        # working but never grabs anything.
        line = (
            "\x0304New Torrent:\x0314 Hello, Melancholic! Vol. 1-3"
            "\x0304 By:\x0303 Yayoi Ohsawa"
            "\x0304 Category: (\x0314 Ebooks - Comics/Graphic novels"
            "\x0304 ) Size: (\x0314 1,011.34 MiB"
            "\x0304 ) Filetype: (\x0314 cbz"
            "\x0304 ) Language: (\x0314 English"
            "\x0304 ) Link: (\x0314 https://www.myanonamouse.net/t/1233678"
            "\x0304 )"
        )
        result = parse_announce(line)
        assert result is not None
        assert result.torrent_id == "1233678"
        assert result.torrent_name == "Hello, Melancholic! Vol. 1-3"
        assert result.author_blob == "Yayoi Ohsawa"
        assert result.category == "Ebooks - Comics/Graphic novels"
        assert result.filetype == "cbz"

    def test_real_colored_privmsg_with_vip(self):
        # VIP variant of the colored line — the trailing `VIP` is
        # OUTSIDE the closing color code, but the regex's optional
        # capture should still match it.
        line = (
            "\x0304New Torrent:\x0314 Test Book"
            "\x0304 By:\x0303 Test Author"
            "\x0304 Category: (\x0314 Ebooks - Fantasy"
            "\x0304 ) Size: (\x0314 1.5 MiB"
            "\x0304 ) Filetype: (\x0314 epub"
            "\x0304 ) Language: (\x0314 English"
            "\x0304 ) Link: (\x0314 https://www.myanonamouse.net/t/9999"
            "\x0304 ) VIP"
        )
        result = parse_announce(line)
        assert result is not None
        assert result.torrent_id == "9999"
        assert result.vip is True

    def test_returns_none_on_unrelated_line(self):
        # The IRC channel emits other PRIVMSGs (status, errors, etc).
        # Anything that doesn't match returns None — never raises.
        assert parse_announce("MouseBot: server restart in 5 minutes") is None
        assert parse_announce("") is None
        assert parse_announce("just some random text") is None

    def test_returns_none_on_partial_match(self):
        # Truncated / malformed announce — must NOT half-fill an Announce.
        line = "New Torrent: The Demon King By: Peter V Brett Category: ("
        assert parse_announce(line) is None


# ─── _strip_and_n_more directly ──────────────────────────────


class TestStripAndNMore:
    def test_no_marker(self):
        assert _strip_and_n_more("A, B, C") == "A, B, C"

    def test_and_n_more(self):
        assert (
            _strip_and_n_more("Stephen Marlowe, John Roeburt, Ed Lacy, and 1 more")
            == "Stephen Marlowe, John Roeburt, Ed Lacy"
        )

    def test_and_2_more(self):
        assert (
            _strip_and_n_more("Author A, Author B, and 2 more")
            == "Author A, Author B"
        )

    def test_n_more_no_and(self):
        # Defensive — handle ", 3 more" without the "and" connector too.
        assert _strip_and_n_more("Author A, Author B, 3 more") == "Author A, Author B"

    def test_case_insensitive(self):
        assert _strip_and_n_more("Author A, AND 5 MORE") == "Author A"


# ─── build_download_url ──────────────────────────────────────


class TestBuildDownloadUrl:
    def test_format(self):
        assert (
            build_download_url("1233592")
            == "https://www.myanonamouse.net/tor/download.php?tid=1233592"
        )

    def test_with_announce_roundtrip(self):
        # The torrent_id captured from a real announce should produce
        # a valid download URL when passed back to build_download_url.
        line = (
            "New Torrent: The Demon King By: Peter V Brett "
            "Category: ( Audiobooks - Fantasy ) Size: ( 921.91 MiB ) "
            "Filetype: ( m4b ) Language: ( English ) "
            "Link: ( https://www.myanonamouse.net/t/1233592 ) VIP"
        )
        result = parse_announce(line)
        assert result is not None
        url = build_download_url(result.torrent_id)
        assert url == "https://www.myanonamouse.net/tor/download.php?tid=1233592"
