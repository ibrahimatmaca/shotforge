"""Non-Latin text: font fallback, Arabic/Hebrew shaping and CJK line breaking.

The bundled Inter font only has Latin, Greek and Cyrillic glyphs, and Pillow often ships without Raqm, so it neither
shapes Arabic nor reorders right-to-left text. This module fills those gaps in pure Python.
"""
from __future__ import annotations

import glob
import os
import sys
import unicodedata
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

# Never break before these (closing brackets, small kana, punctuation) or after the opening brackets.
NO_START = set("、。，．・：；？！ー）」』】〕〉》〗〙〟’”)]}!?,.:;ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮヵヶ々")
NO_END = set("（「『【〔〈《〖〘〝‘“([{")

# Best candidates first; the broad scan below only runs when none of these cover the text.
_PREFERRED = [
    "/System/Library/Fonts/*W[3-7].ttc", "/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/Hiragino*.ttc",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc", "/System/Library/Fonts/GeezaPro.ttc",
    "/System/Library/Fonts/SFHebrew.ttf", "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "C:/Windows/Fonts/msyh*.ttc", "C:/Windows/Fonts/YuGoth*.ttc", "C:/Windows/Fonts/meiryo*.ttc",
    "C:/Windows/Fonts/malgun*.ttf", "C:/Windows/Fonts/tahoma*.ttf", "C:/Windows/Fonts/segoeui*.ttf",
    "/usr/share/fonts/**/NotoSans*CJK*", "/usr/share/fonts/**/NotoSansArabic*", "/usr/share/fonts/**/NotoSansHebrew*",
    "/usr/share/fonts/**/DejaVuSans*.ttf", "/usr/share/fonts/**/wqy*",
]
_BROAD = ["/System/Library/Fonts/*.tt[cf]", "/System/Library/Fonts/Supplemental/*.tt[cf]", "/Library/Fonts/*.tt[cf]",
          "~/Library/Fonts/*.tt[cf]", "C:/Windows/Fonts/*.tt[cf]", "/usr/share/fonts/**/*.tt[cf]",
          "/usr/share/fonts/**/*.otf", "~/.fonts/**/*.tt[cf]"]
_BOLD = ("bold", "w6", "w7", "w8", "semibold", "heavy", "black", "demi")


def is_rtl(text: str) -> bool:
    """Standard rule: the first strong character decides the base direction."""
    for c in text:
        d = unicodedata.bidirectional(c)
        if d in ("R", "AL"):
            return True
        if d == "L":
            return False
    return False


def shape(word: str, rtl: bool) -> str:
    """Display form of one word: Arabic letters joined, right-to-left runs reordered for left-to-right drawing."""
    if not rtl or not any(unicodedata.bidirectional(c) in ("R", "AL") for c in word):
        return word  # digits and Latin words inside Arabic text stay as they are
    try:
        import arabic_reshaper
        try:
            from bidi import get_display
        except ImportError:  # python-bidi < 0.5
            from bidi.algorithm import get_display
    except ImportError:
        raise SystemExit("Arabic and Hebrew text needs: pip install arabic-reshaper python-bidi")
    return get_display(arabic_reshaper.reshape(word), base_dir="R")


def is_cjk(c: str) -> bool:
    o = ord(c)
    return (0x3000 <= o <= 0x30FF or 0x3400 <= o <= 0x4DBF or 0x4E00 <= o <= 0x9FFF or 0xF900 <= o <= 0xFAFF
            or 0xFF00 <= o <= 0xFFEF or 0x20000 <= o <= 0x2FFFF)


def split_breaks(word: str) -> list[str]:
    """CJK has no spaces, so a line may break between characters (never before closing marks or after opening ones)."""
    out = [word[:1]]
    for prev, c in zip(word, word[1:]):
        if (is_cjk(prev) or is_cjk(c)) and c not in NO_START and prev not in NO_END:
            out.append(c)
        else:
            out[-1] += c
    return out


def load(path: str, index: int, size: int, rtl: bool = False) -> ImageFont.FreeTypeFont:
    # Basic layout: we shape ourselves, and Raqm would reorder and join a second time.
    kw = {"layout_engine": ImageFont.Layout.BASIC} if rtl else {}
    return ImageFont.truetype(path, size, index=index, **kw)


def _chars(text: str) -> set[str]:
    return {c for c in text if not c.isspace() and c != "*" and unicodedata.category(c) != "Cf"}


def _missing(font: ImageFont.FreeTypeFont, chars: set[str]) -> set[str]:
    notdef = bytes(font.getmask("\U000F0000"))  # private use area: the font's "missing character" shape
    return {c for c in chars if bytes(font.getmask(c)) == notdef}


def _faces(path: str):
    for i in range(32):
        try:
            f = ImageFont.truetype(path, 64, index=i)
        except (OSError, ValueError):
            return
        name = " ".join(f.getname()).lower()
        if not name.startswith("."):  # Apple's hidden "interface" and PUA variants
            yield i, f, name
        if not path.lower().endswith((".ttc", ".otc")):
            return


def _files(patterns: list[str], bold: bool) -> list[str]:
    seen, out = set(), []
    for pat in patterns:
        for p in sorted(glob.glob(os.path.expanduser(pat), recursive=True)):
            if p not in seen:
                seen.add(p)
                out.append(p)
    # separate bold files (msyhbd.ttc) come first for titles, last for body text
    return sorted(out, key=lambda p: (any(k in Path(p).stem.lower() for k in _BOLD + ("bd",)) != bold))


@lru_cache(maxsize=256)
def choose_font(text: str, primary: str, bold: bool, rtl: bool) -> tuple[str, int, tuple[str, ...]]:
    """(path, face index, missing characters) of the first font that covers the text; the primary font wins if it does.
    When nothing covers everything, the font missing the fewest characters is returned."""
    chars = _chars("".join(shape(w, rtl) for w in text.split()) if rtl else text)
    if not chars:
        return primary, 0, ()
    best = (primary, 0, _missing(ImageFont.truetype(primary, 64), chars))
    if not best[2]:
        return primary, 0, ()
    for patterns in (_PREFERRED, _BROAD):
        for path in _files(patterns, bold):
            try:
                faces = list(_faces(path))
            except Exception:
                continue
            scored = []
            for i, f, name in faces:
                try:
                    miss = _missing(f, chars)
                except OSError:  # a few system fonts crash FreeType on odd code points
                    continue
                is_b = any(k in name for k in _BOLD)
                scored.append((len(miss), is_b != bold, i, miss))
            if not scored:
                continue
            n, _, i, miss = min(scored)
            if n < len(best[2]):
                best = (path, i, miss)
            if not best[2]:
                return best[0], best[1], ()
    return best[0], best[1], tuple(sorted(best[2]))
