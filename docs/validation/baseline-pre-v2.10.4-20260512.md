# Source validation — 20260512-112557

Per-author book counts surfaced by each discovery source. Captured manually via `scripts/validate_sources.py`.

| Author | goodreads | hardcover | amazon | google_books |
|---|---|---|---|---|
| J. N. Chaney | FAIL (8.2s) | 0 (0.8s) | 0 (82.5s) | FAIL (1.6s) |
| Marcus Sloss | FAIL (2.0s) | 2 (0.9s) | 2 (80.8s) | FAIL (1.6s) |
| Sabaa Tahir | FAIL (2.0s) | 3 (0.9s) | 11 (86.8s) | 0 (3.5s) |
| James S. A. Corey | FAIL (8.1s) | 5 (1.0s) | 4 (85.6s) | FAIL (1.6s) |
| Jim Butcher | FAIL (2.0s) | 0 (0.7s) | 3 (77.4s) | 0 (3.5s) |
| Brandon Sanderson | FAIL (2.0s) | 3 (0.8s) | 0 (51.4s) | FAIL (1.6s) |
| William D. Arand | FAIL (4.0s) | 0 (0.8s) | FAIL (1.6s) | FAIL (1.6s) |
| Logan Jacobs | FAIL (2.0s) | 0 (0.6s) | FAIL (1.6s) | FAIL (1.6s) |
| Jon Messenger | FAIL (2.0s) | 0 (0.6s) | FAIL (1.6s) | FAIL (1.6s) |
| Karen Traviss | FAIL (2.0s) | 0 (0.7s) | FAIL (1.6s) | FAIL (1.6s) |
| Robyn Bee | FAIL (2.0s) | 2 (1.6s) | FAIL (1.6s) | 0 (3.5s) |
| K.D. Robertson | FAIL (8.1s) | 0 (0.5s) | FAIL (1.6s) | FAIL (1.6s) |
| Asato Asato | FAIL (2.0s) | 0 (0.5s) | FAIL (1.6s) | 20 (3.8s) |
| Isuna Hasekura | FAIL (2.0s) | 0 (0.5s) | FAIL (1.6s) | FAIL (1.6s) |

## Failures (full error strings)

- **goodreads / J. N. Chaney**: search_author returned None
- **goodreads / Marcus Sloss**: search_author returned None
- **goodreads / Sabaa Tahir**: search_author returned None
- **goodreads / James S. A. Corey**: search_author returned None
- **goodreads / Jim Butcher**: search_author returned None
- **goodreads / Brandon Sanderson**: search_author returned None
- **goodreads / William D. Arand**: search_author returned None
- **goodreads / Logan Jacobs**: search_author returned None
- **goodreads / Jon Messenger**: search_author returned None
- **goodreads / Karen Traviss**: search_author returned None
- **goodreads / Robyn Bee**: search_author returned None
- **goodreads / K.D. Robertson**: search_author returned None
- **goodreads / Asato Asato**: search_author returned None
- **goodreads / Isuna Hasekura**: search_author returned None
- **amazon / William D. Arand**: search_author returned None
- **amazon / Logan Jacobs**: search_author returned None
- **amazon / Jon Messenger**: search_author returned None
- **amazon / Karen Traviss**: search_author returned None
- **amazon / Robyn Bee**: search_author returned None
- **amazon / K.D. Robertson**: search_author returned None
- **amazon / Asato Asato**: search_author returned None
- **amazon / Isuna Hasekura**: search_author returned None
- **google_books / J. N. Chaney**: search_author returned None
- **google_books / Marcus Sloss**: search_author returned None
- **google_books / James S. A. Corey**: search_author returned None
- **google_books / Brandon Sanderson**: search_author returned None
- **google_books / William D. Arand**: search_author returned None
- **google_books / Logan Jacobs**: search_author returned None
- **google_books / Jon Messenger**: search_author returned None
- **google_books / Karen Traviss**: search_author returned None
- **google_books / K.D. Robertson**: search_author returned None
- **google_books / Isuna Hasekura**: search_author returned None

## Raw results (JSON)

```json
[
  {
    "source": "goodreads",
    "author": "J. N. Chaney",
    "found_id": null,
    "book_count": 0,
    "seconds": 8.196986894996371,
    "error": "search_author returned None"
  },
  {
    "source": "goodreads",
    "author": "Marcus Sloss",
    "found_id": null,
    "book_count": 0,
    "seconds": 2.0187901180470362,
    "error": "search_author returned None"
  },
  {
    "source": "goodreads",
    "author": "Sabaa Tahir",
    "found_id": null,
    "book_count": 0,
    "seconds": 2.0192762429942377,
    "error": "search_author returned None"
  },
  {
    "source": "goodreads",
    "author": "James S. A. Corey",
    "found_id": null,
    "book_count": 0,
    "seconds": 8.06953534996137,
    "error": "search_author returned None"
  },
  {
    "source": "goodreads",
    "author": "Jim Butcher",
    "found_id": null,
    "book_count": 0,
    "seconds": 2.0183025139849633,
    "error": "search_author returned None"
  },
  {
    "source": "goodreads",
    "author": "Brandon Sanderson",
    "found_id": null,
    "book_count": 0,
    "seconds": 2.0169288470060565,
    "error": "search_author returned None"
  },
  {
    "source": "goodreads",
    "author": "William D. Arand",
    "found_id": null,
    "book_count": 0,
    "seconds": 4.035817280004267,
    "error": "search_author returned None"
  },
  {
    "source": "goodreads",
    "author": "Logan Jacobs",
    "found_id": null,
    "book_count": 0,
    "seconds": 2.0183431379846297,
    "error": "search_author returned None"
  },
  {
    "source": "goodreads",
    "author": "Jon Messenger",
    "found_id": null,
    "book_count": 0,
    "seconds": 2.020034115004819,
    "error": "search_author returned None"
  },
  {
    "source": "goodreads",
    "author": "Karen Traviss",
    "found_id": null,
    "book_count": 0,
    "seconds": 2.020324298995547,
    "error": "search_author returned None"
  },
  {
    "source": "goodreads",
    "author": "Robyn Bee",
    "found_id": null,
    "book_count": 0,
    "seconds": 2.01908001198899,
    "error": "search_author returned None"
  },
  {
    "source": "goodreads",
    "author": "K.D. Robertson",
    "found_id": null,
    "book_count": 0,
    "seconds": 8.073123367968947,
    "error": "search_author returned None"
  },
  {
    "source": "goodreads",
    "author": "Asato Asato",
    "found_id": null,
    "book_count": 0,
    "seconds": 2.0192577409907244,
    "error": "search_author returned None"
  },
  {
    "source": "goodreads",
    "author": "Isuna Hasekura",
    "found_id": null,
    "book_count": 0,
    "seconds": 2.018638189008925,
    "error": "search_author returned None"
  },
  {
    "source": "hardcover",
    "author": "J. N. Chaney",
    "found_id": "search",
    "book_count": 0,
    "seconds": 0.7644329680479132,
    "error": null
  },
  {
    "source": "hardcover",
    "author": "Marcus Sloss",
    "found_id": "664777",
    "book_count": 2,
    "seconds": 0.8510859119705856,
    "error": null
  },
  {
    "source": "hardcover",
    "author": "Sabaa Tahir",
    "found_id": "224360",
    "book_count": 3,
    "seconds": 0.8719377410016023,
    "error": null
  },
  {
    "source": "hardcover",
    "author": "James S. A. Corey",
    "found_id": "85052",
    "book_count": 5,
    "seconds": 1.0251732349861413,
    "error": null
  },
  {
    "source": "hardcover",
    "author": "Jim Butcher",
    "found_id": "109593",
    "book_count": 0,
    "seconds": 0.6539257679833099,
    "error": null
  },
  {
    "source": "hardcover",
    "author": "Brandon Sanderson",
    "found_id": "204214",
    "book_count": 3,
    "seconds": 0.7718957739998586,
    "error": null
  },
  {
    "source": "hardcover",
    "author": "William D. Arand",
    "found_id": "259414",
    "book_count": 0,
    "seconds": 0.8245341849979013,
    "error": null
  },
  {
    "source": "hardcover",
    "author": "Logan Jacobs",
    "found_id": "233928",
    "book_count": 0,
    "seconds": 0.6333390980144031,
    "error": null
  },
  {
    "source": "hardcover",
    "author": "Jon Messenger",
    "found_id": "177975",
    "book_count": 0,
    "seconds": 0.6245078590000048,
    "error": null
  },
  {
    "source": "hardcover",
    "author": "Karen Traviss",
    "found_id": "146141",
    "book_count": 0,
    "seconds": 0.7011402010102756,
    "error": null
  },
  {
    "source": "hardcover",
    "author": "Robyn Bee",
    "found_id": "1327883",
    "book_count": 2,
    "seconds": 1.5849998890189454,
    "error": null
  },
  {
    "source": "hardcover",
    "author": "K.D. Robertson",
    "found_id": "374788",
    "book_count": 0,
    "seconds": 0.5205956779536791,
    "error": null
  },
  {
    "source": "hardcover",
    "author": "Asato Asato",
    "found_id": "241157",
    "book_count": 0,
    "seconds": 0.543427397031337,
    "error": null
  },
  {
    "source": "hardcover",
    "author": "Isuna Hasekura",
    "found_id": "75516",
    "book_count": 0,
    "seconds": 0.5482399089960381,
    "error": null
  },
  {
    "source": "amazon",
    "author": "J. N. Chaney",
    "found_id": "J. N. Chaney",
    "book_count": 0,
    "seconds": 82.51188468100736,
    "error": null
  },
  {
    "source": "amazon",
    "author": "Marcus Sloss",
    "found_id": "Marcus Sloss",
    "book_count": 2,
    "seconds": 80.79153346002568,
    "error": null
  },
  {
    "source": "amazon",
    "author": "Sabaa Tahir",
    "found_id": "Sabaa Tahir",
    "book_count": 11,
    "seconds": 86.77341502800118,
    "error": null
  },
  {
    "source": "amazon",
    "author": "James S. A. Corey",
    "found_id": "James S. A. Corey",
    "book_count": 4,
    "seconds": 85.55758523300756,
    "error": null
  },
  {
    "source": "amazon",
    "author": "Jim Butcher",
    "found_id": "Jim Butcher",
    "book_count": 3,
    "seconds": 77.39561996003613,
    "error": null
  },
  {
    "source": "amazon",
    "author": "Brandon Sanderson",
    "found_id": "Brandon Sanderson",
    "book_count": 0,
    "seconds": 51.39788076200057,
    "error": null
  },
  {
    "source": "amazon",
    "author": "William D. Arand",
    "found_id": null,
    "book_count": 0,
    "seconds": 1.5714475580025464,
    "error": "search_author returned None"
  },
  {
    "source": "amazon",
    "author": "Logan Jacobs",
    "found_id": null,
    "book_count": 0,
    "seconds": 1.5758869299897924,
    "error": "search_author returned None"
  },
  {
    "source": "amazon",
    "author": "Jon Messenger",
    "found_id": null,
    "book_count": 0,
    "seconds": 1.5687580330413766,
    "error": "search_author returned None"
  },
  {
    "source": "amazon",
    "author": "Karen Traviss",
    "found_id": null,
    "book_count": 0,
    "seconds": 1.5689013280207291,
    "error": "search_author returned None"
  },
  {
    "source": "amazon",
    "author": "Robyn Bee",
    "found_id": null,
    "book_count": 0,
    "seconds": 1.568733591993805,
    "error": "search_author returned None"
  },
  {
    "source": "amazon",
    "author": "K.D. Robertson",
    "found_id": null,
    "book_count": 0,
    "seconds": 1.567871593986638,
    "error": "search_author returned None"
  },
  {
    "source": "amazon",
    "author": "Asato Asato",
    "found_id": null,
    "book_count": 0,
    "seconds": 1.573341126961168,
    "error": "search_author returned None"
  },
  {
    "source": "amazon",
    "author": "Isuna Hasekura",
    "found_id": null,
    "book_count": 0,
    "seconds": 1.5657869299757294,
    "error": "search_author returned None"
  },
  {
    "source": "google_books",
    "author": "J. N. Chaney",
    "found_id": null,
    "book_count": 0,
    "seconds": 1.6058160109678283,
    "error": "search_author returned None"
  },
  {
    "source": "google_books",
    "author": "Marcus Sloss",
    "found_id": null,
    "book_count": 0,
    "seconds": 1.564639512973372,
    "error": "search_author returned None"
  },
  {
    "source": "google_books",
    "author": "Sabaa Tahir",
    "found_id": "Sabaa Tahir",
    "book_count": 0,
    "seconds": 3.533223514968995,
    "error": null
  },
  {
    "source": "google_books",
    "author": "James S. A. Corey",
    "found_id": null,
    "book_count": 0,
    "seconds": 1.5584328370168805,
    "error": "search_author returned None"
  },
  {
    "source": "google_books",
    "author": "Jim Butcher",
    "found_id": "Jim Butcher",
    "book_count": 0,
    "seconds": 3.458989584003575,
    "error": null
  },
  {
    "source": "google_books",
    "author": "Brandon Sanderson",
    "found_id": null,
    "book_count": 0,
    "seconds": 1.569796903990209,
    "error": "search_author returned None"
  },
  {
    "source": "google_books",
    "author": "William D. Arand",
    "found_id": null,
    "book_count": 0,
    "seconds": 1.62519562500529,
    "error": "search_author returned None"
  },
  {
    "source": "google_books",
    "author": "Logan Jacobs",
    "found_id": null,
    "book_count": 0,
    "seconds": 1.5919431649963371,
    "error": "search_author returned None"
  },
  {
    "source": "google_books",
    "author": "Jon Messenger",
    "found_id": null,
    "book_count": 0,
    "seconds": 1.5687589209992439,
    "error": "search_author returned None"
  },
  {
    "source": "google_books",
    "author": "Karen Traviss",
    "found_id": null,
    "book_count": 0,
    "seconds": 1.5746953939669766,
    "error": "search_author returned None"
  },
  {
    "source": "google_books",
    "author": "Robyn Bee",
    "found_id": "Robyn Bee",
    "book_count": 0,
    "seconds": 3.4824201950104907,
    "error": null
  },
  {
    "source": "google_books",
    "author": "K.D. Robertson",
    "found_id": null,
    "book_count": 0,
    "seconds": 1.5691581420251168,
    "error": "search_author returned None"
  },
  {
    "source": "google_books",
    "author": "Asato Asato",
    "found_id": "Asato Asato",
    "book_count": 20,
    "seconds": 3.786504242045339,
    "error": null
  },
  {
    "source": "google_books",
    "author": "Isuna Hasekura",
    "found_id": null,
    "book_count": 0,
    "seconds": 1.5590511460322887,
    "error": "search_author returned None"
  }
]
```