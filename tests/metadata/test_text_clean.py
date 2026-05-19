"""Unit tests for description_to_plain_text.

Covers the three encodings the util handles in one pass:
  - MAM-legacy BBCode (`[b]`, `[i]`, `[hr]`)
  - Publisher-paste HTML (`<p>`, `<br>`, `<b>`)
  - HTML entities (`&#8212;` em-dash, `&amp;`)

Plus invariants: idempotence on clean text, None on empty.
"""

from app.metadata.text_clean import description_to_plain_text


def test_empty_inputs_return_none():
    assert description_to_plain_text("") is None
    assert description_to_plain_text(None) is None
    assert description_to_plain_text("   \n\n  ") is None


def test_plain_text_passes_through_unchanged():
    src = "A simple description with no markup."
    assert description_to_plain_text(src) == src


def test_strips_bbcode_inline_tags():
    src = "[b]Bold[/b] and [i]italic[/i] text"
    assert description_to_plain_text(src) == "Bold and italic text"


def test_bbcode_hr_becomes_newline():
    src = "Part one[hr]Part two"
    assert description_to_plain_text(src) == "Part one\nPart two"


def test_strips_html_tags():
    src = "<p>First paragraph.</p><p>Second paragraph.</p>"
    out = description_to_plain_text(src)
    # `</p>` becomes a paragraph break, then trailing whitespace gets
    # collapsed by .strip().
    assert "First paragraph." in out
    assert "Second paragraph." in out
    assert "<" not in out and ">" not in out


def test_html_br_becomes_newline():
    src = "Line one<br>Line two<br/>Line three"
    assert description_to_plain_text(src) == "Line one\nLine two\nLine three"


def test_decodes_html_entities():
    # The actual Mortedant's Peril case: em-dash entity inside HTML.
    src = "dead&#8212;though no one thanks him &amp; the rest."
    out = description_to_plain_text(src)
    assert "—" in out
    assert "&" in out and "&amp;" not in out
    assert "&#8212;" not in out


def test_mortedant_case_full_chain():
    """The screenshot's actual broken input — must come out readable."""
    src = (
        "<p><b>In a city of ancient automata, strange spirits, and "
        "sleeping gods, a cleric of death finds his own life on the "
        "line</b> of <i>The Bone Ships</i>.<br>Irody Hasp is a "
        "Mortedant&#8212;though no one thanks him for it.</p>"
    )
    out = description_to_plain_text(src)
    assert "<" not in out and ">" not in out
    assert "&#8212;" not in out
    assert "—" in out
    assert "In a city of ancient automata" in out
    assert "Mortedant—though no one thanks him for it" in out


def test_mixed_bbcode_and_html_one_pass():
    src = "[b]Title:[/b] <p>The synopsis &amp; details.</p>"
    out = description_to_plain_text(src)
    assert "[b]" not in out and "[/b]" not in out
    assert "<p>" not in out and "</p>" not in out
    assert "&amp;" not in out
    assert "Title:" in out
    assert "synopsis & details." in out


def test_idempotent_on_already_clean_output():
    src = "<p>Run twice<br>should be stable.</p>"
    once = description_to_plain_text(src)
    twice = description_to_plain_text(once)
    assert once == twice


def test_collapses_excess_blank_lines():
    src = "Para one.\n\n\n\n\nPara two."
    out = description_to_plain_text(src)
    assert "\n\n\n" not in out
    assert "Para one." in out and "Para two." in out
