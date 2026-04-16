"""
Goodreads HTML parser tests.

Drives `_parse_search_results` and `_merge_detail_page` directly with
inline HTML fixtures so we don't need real Goodreads or an httpx
fake transport. The fixtures are small on purpose — the point is
pinning the CSS selectors, not reproducing the full page.

If Goodreads changes their markup, one of these tests will fail and
tell us exactly which selector to update.
"""
from bs4 import BeautifulSoup

from app.metadata.record import MetaRecord
from app.metadata.sources.goodreads import (
    _merge_detail_page,
    _parse_search_results,
    _parse_series_string,
)


_SEARCH_HTML = """
<html><body>
<table>
  <tr itemtype="http://schema.org/Book">
    <td>
      <a class="bookTitle" href="/book/show/7235533.The_Way_of_Kings">
        <span>The Way of Kings</span>
      </a>
      <a class="authorName" href="/author/show/38550.Brandon_Sanderson">
        <span>Brandon Sanderson</span>
      </a>
      <img class="bookCover" src="https://i.gr-assets.com/images/S/way.jpg" />
    </td>
  </tr>
  <tr itemtype="http://schema.org/Book">
    <td>
      <a class="bookTitle" href="/book/show/9999999">
        <span>An Unrelated Book</span>
      </a>
      <a class="authorName"><span>Other Author</span></a>
      <img class="bookCover" src="https://example.com/nophoto.png" />
    </td>
  </tr>
</table>
</body></html>
"""


class TestParseSearchResults:
    def test_extracts_primary_row(self):
        soup = BeautifulSoup(_SEARCH_HTML, "lxml")
        results = _parse_search_results(soup)
        assert len(results) == 2
        first = results[0]
        assert first["book_id"] == "7235533"
        assert first["title"] == "The Way of Kings"
        assert first["author"] == "Brandon Sanderson"
        assert first["cover_url"] == "https://i.gr-assets.com/images/S/way.jpg"

    def test_nophoto_cover_dropped(self):
        soup = BeautifulSoup(_SEARCH_HTML, "lxml")
        results = _parse_search_results(soup)
        assert results[1]["cover_url"] is None


_DETAIL_HTML = """
<html><head>
<meta property="og:description" content="A sweeping epic fantasy." />
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "Book",
  "name": "The Way of Kings",
  "author": [{"@type": "Person", "name": "Brandon Sanderson"}],
  "datePublished": "2010-08-31",
  "inLanguage": "en",
  "image": "https://i.gr-assets.com/books/detail.jpg",
  "numberOfPages": 1007,
  "isbn": "978-0-7653-2635-5"
}
</script>
</head><body>
<h3 class="Text__title3">
  <a href="/series/49075-the-stormlight-archive">The Stormlight Archive #1</a>
</h3>
</body></html>
"""


class TestMergeDetailPage:
    def test_jsonld_fields_populate(self):
        record = MetaRecord(title="The Way of Kings", source="goodreads")
        _merge_detail_page(record, _DETAIL_HTML)
        assert record.pub_date == "2010-08-31"
        assert record.language == "en"
        assert record.page_count == 1007
        assert record.isbn == "9780765326355"
        assert record.cover_url == "https://i.gr-assets.com/books/detail.jpg"
        assert record.authors == ["Brandon Sanderson"]
        assert record.description == "A sweeping epic fantasy."
        assert record.series == "The Stormlight Archive"
        assert record.series_index == 1.0

    def test_does_not_overwrite_existing_fields(self):
        record = MetaRecord(
            title="The Way of Kings",
            description="Existing blurb",
            cover_url="existing.jpg",
            source="goodreads",
        )
        _merge_detail_page(record, _DETAIL_HTML)
        assert record.description == "Existing blurb"
        assert record.cover_url == "existing.jpg"


class TestParseSeriesString:
    def test_simple_split(self):
        name, idx = _parse_series_string("Stormlight Archive #1")
        assert name == "Stormlight Archive"
        assert idx == 1.0

    def test_decimal_index(self):
        name, idx = _parse_series_string("Mistborn #2.5")
        assert name == "Mistborn"
        assert idx == 2.5

    def test_no_index(self):
        name, idx = _parse_series_string("Series Name")
        assert name == "Series Name"
        assert idx is None
