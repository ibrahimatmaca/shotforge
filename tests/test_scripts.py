import pytest
from PIL import ImageFont

from shotforge import engine as E
from shotforge import scripts as S

INTER = E.resolve_font("Bold", None)


def test_direction_and_breaks():
    assert S.is_rtl("مرحبا بالعالم") and S.is_rtl("שלום") and not S.is_rtl("Hello مرحبا")
    assert S.split_breaks("日本語、です。") == ["日", "本", "語、", "で", "す。"]
    assert S.split_breaks("App Store") == ["App Store"]


def test_arabic_is_joined_and_reordered():
    shaped = S.shape("بيت", True)
    assert shaped != "بيت" and all(0xFB50 <= ord(c) <= 0xFEFF for c in shaped)  # presentation forms, not isolated letters
    assert S.shape("2024", True) == "2024" and S.shape("abc", False) == "abc"


@pytest.mark.parametrize("text", ["日本語のスクリーンショット", "한국어 스크린샷", "المهام اليومية بسهولة", "פשוט ויומי"])
def test_non_latin_gets_a_covering_font(text):
    path, idx, missing = S.choose_font(text, INTER, True, S.is_rtl(text))
    if missing:
        pytest.skip(f"no system font covers {text!r} here")
    assert path != INTER  # Inter would draw empty boxes


def test_latin_keeps_the_primary_font():
    assert S.choose_font("Plan your day", INTER, True, False) == (INTER, 0, ())


def test_cjk_wraps_without_spaces():
    path, idx, missing = S.choose_font("日本語", INTER, True, False)
    if missing:
        pytest.skip("no CJK font")
    font = S.load(path, idx, 80)
    lines, _ = E.wrap(E.parse_markup("毎日の習慣をかんたんに記録して続けましょう"), font, 400)
    assert len(lines) > 1 and all(w <= 400 for _, w in lines)
    assert not any(words[0][0][0] in S.NO_START for words, _ in lines)
    # nothing is lost, and no spaces are invented between characters
    assert "".join(w for words, _ in lines for w, *_ in words) == "毎日の習慣をかんたんに記録して続けましょう"
    assert all(g for words, _ in lines for _, _, _, g in words[1:])


@pytest.mark.parametrize("text", ["日本語の *簡単* な習慣", "تتبع *عاداتك* اليومية بسهولة تامة"])
def test_text_renders_visible_glyphs(text):
    cfg = dict(device="iphone-6.9", title={"x": text})
    if E.text_problems(cfg, "x") and any("no installed font" in m for m in E.text_problems(cfg, "x")):
        pytest.skip("no system font for this script")
    W, H = E.DEVICES["iphone-6.9"]["size"]
    from PIL import Image
    canvas = Image.new("RGB", (W, H), "white")
    tb = E.TextBlock(text, "", W, H, INTER, INTER, (0, 0, 0, 255), (200, 0, 0))
    tb.draw(canvas, 100)
    assert canvas.convert("L").getextrema()[0] < 100  # dark pixels were drawn
