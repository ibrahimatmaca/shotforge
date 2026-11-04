from PIL import ImageFont

from shotforge import engine as E

FONT = ImageFont.truetype(E.resolve_font("Bold", None), 80)


def test_accent_keeps_neighbouring_punctuation_and_suffixes_attached():
    assert E.parse_markup("Your *daily routine*, done") == [
        [("Your", False, False), ("daily", True, False), ("routine", True, False), (",", False, True), ("done", False, False)]]
    assert E.parse_markup("*Habita*'yı (*bugün*) dene")[0] == [
        ("Habita", True, False), ("'yı", False, True), ("(", False, False), ("bugün", True, True), (")", False, True), ("dene", False, False)]


def test_real_spaces_around_accent_are_kept():
    assert E.parse_markup("a *b* c **d") == [[("a", False, False), ("b", True, False), ("c", False, False), ("d", False, False)]]


def test_accent_does_not_change_width():
    lines, _ = E.wrap(E.parse_markup("Your *routine*, done"), FONT, 10_000)
    plain, _ = E.wrap(E.parse_markup("Your routine, done"), FONT, 10_000)
    assert abs(lines[0][1] - plain[0][1]) < 4


def test_line_break_never_separates_attached_parts():
    width = FONT.getlength("Your routine") + 5
    lines, _ = E.wrap(E.parse_markup("Your *routine*, done"), FONT, width)
    text = [[w[0] for w in words] for words, _ in lines]
    assert len(text) > 1 and any(l[:2] == ["routine", ","] for l in text)
