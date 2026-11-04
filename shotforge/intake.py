"""Üretimden önce kullanıcıya sorulan alanlar (tek kaynak) ve boş cevapların varsayılanlara çözümü.

CLI, MCP ve /store-shots aynı listeyi kullanır. Boş cevap, "auto" ve "otomatik" varsayılan demektir.
"""
from __future__ import annotations

import re
import unicodedata

from .engine import BANNERS, DEVICES

STORES = ("appstore", "playstore")
HEADER_SIZES = tuple(k for k, b in BANNERS.items() if b["store"] == "app-store")
FEATURE_GRAPHIC = next(k for k, b in BANNERS.items() if b["store"] == "google-play")
APPSTORE_DEVICES = ("iphone-6.9", "iphone-6.9b", "iphone-6.5", "ipad-13", "ipad-12.9")
PLAY_DEVICES = ("android-phone", "android-tablet-7", "android-tablet-10")
DEFAULT_DEVICES = ("iphone-6.9", "android-phone")
DEFAULT_LANGS = ("en", "tr")
OUTPUT_MODES = ("both", "folder", "zip")
PLAY_MAX_SHOTS = 8
TABLET_MIN_SHOTS = 4
# Google Play rehberinin görselde önermediği ifadeler (notes_for ile aynı liste)
PLAY_AVOID_RE = re.compile(r"\b(best|free|download now)\b|#1", re.I)
# Inter fontunda karşılığı olmayan yazılar
NO_GLYPH_LANGS = ("ja", "ko", "zh", "ar", "he", "hi", "th")

_BLANK = {"", "auto", "otomatik", "default", "varsayılan", "varsayilan"}
_NO = {"no", "none", "hayır", "hayir", "false"}
_YES = {"yes", "evet", "true", "all", "hepsi"}
_STORE_ALIASES = {
    "appstore": ("appstore",), "app-store": ("appstore",), "app store": ("appstore",), "ios": ("appstore",),
    "playstore": ("playstore",), "play-store": ("playstore",), "play store": ("playstore",), "play": ("playstore",),
    "google-play": ("playstore",), "google play": ("playstore",), "android": ("playstore",),
    "both": STORES, "ikisi": STORES, "her ikisi": STORES,
}

QUESTIONS = [
    dict(id="screenshots", question="Where are your app screenshots? (a folder or image files)", kind="path", required=True,
         note="You can paste the path or drag the folder in. Separate sets go in <folder>/tr/, <folder>/android/ and so on."),
    dict(id="app", question="What is the app called?", kind="text", required=True),
    dict(id="about", question="What does the app do? (1–2 sentences)", kind="text", default="inferred from the screens"),
    dict(id="store", question="Which store should the images be for?", kind="choice",
         options=["appstore", "playstore", "both"], default="both"),
    dict(id="header_sizes", question="Create App Store Header and Search Results images? In which sizes?",
         kind="multi", options=[*HEADER_SIZES, "no"], default="all", when="store includes appstore",
         note="5244x2950 (16:9, PNG) works for the header and search; 3840x2560 and 1920x1280 (3:2) only for search."),
    dict(id="langs", question="Which languages should the App Preview and screenshots use? You can choose several.",
         kind="multi", options=["en", "tr", "de", "fr"], default="en,tr",
         note="Other language codes work too (e.g. pt-BR). The font has no glyphs for ja, ko or ar; Arabic is not laid out right to left."),
    dict(id="dimension", question="Should the template family be 2D or 3D?", kind="choice", options=["2d", "3d", "auto"], default="auto"),
    dict(id="template", question="Do you want a specific template?", kind="template", default="the rules choose"),
    dict(id="appstore_devices", question="Which device sizes for the App Store?", kind="multi", answer_field="devices",
         options=["iphone-6.9", "iphone-6.5", "ipad-13"], default="iphone-6.9", when="store includes appstore"),
    dict(id="play_devices", question="Which device sizes for Google Play?", kind="multi", answer_field="devices",
         options=list(PLAY_DEVICES), default="android-phone", when="store includes playstore"),
    dict(id="shot_sets", question="Do you have separate screenshots per language or platform?", kind="info",
         when="not detected in the folder", default="one set, with a warning",
         note="Folder layout: <folder>/tr/…, <folder>/android/…, <folder>/android/tr/…, ipad/…, android-tablet/… (file names must match)."),
    dict(id="count", question="How many pages (slides)? 1–10", kind="number", default="6–7",
         note="Google Play takes at most 8 images per device type."),
    dict(id="texts", question="Who should write the headlines?", kind="choice", options=["ai", "me", "file"], default="ai"),
    dict(id="keywords", question="Which search keywords should the headlines target? (comma-separated)", kind="text", default="none"),
    dict(id="tone", question="What tone should the copy have?", kind="text", default="confident, friendly, benefit-first"),
    dict(id="background_color", question="Main background colour (#hex)", kind="color", default="from the screenshots"),
    dict(id="text_color", question="Text colour (#hex)", kind="color", default="a readable colour is chosen automatically"),
    dict(id="accent_color", question="Accent colour (#hex)", kind="color", default="from the screenshots or automatic"),
    dict(id="icon", question="App icon file for the header and feature graphic?", kind="path", default="none, text only"),
    dict(id="previews", question="Do you have a screen recording for the App Preview? (shared or per language)", kind="path",
         default="skipped", when="store includes appstore",
         note="Only a recording you provide is converted, and it needs ffmpeg. Google Play takes a YouTube link, not a video file."),
    dict(id="out", question="Where should the output go?", kind="path", default="./shotforge-out/<app>"),
    dict(id="output", question="Output as a folder, a zip, or both?", kind="choice", options=list(OUTPUT_MODES), default="both"),
]


def is_blank(v) -> bool:
    if v is None:
        return True
    if isinstance(v, str):
        return v.strip().lower() in _BLANK
    if isinstance(v, (list, tuple, set, dict)):
        return not v
    return False


def _items(v) -> list[str]:
    parts = v if isinstance(v, (list, tuple)) else str(v).split(",")
    return [str(p).strip() for p in parts if str(p).strip()]


def parse_stores(v) -> tuple[str, ...]:
    if is_blank(v):
        return STORES
    key = str(v).strip().lower()
    if key not in _STORE_ALIASES:
        raise ValueError(f"store must be appstore, playstore or both, got {v!r}")
    return _STORE_ALIASES[key]


def parse_header_sizes(v) -> list[str]:
    if is_blank(v):
        return list(HEADER_SIZES)
    if isinstance(v, bool):
        return list(HEADER_SIZES) if v else []
    items = [i.lower().replace("×", "x").replace(" ", "") for i in _items(v)]
    if any(i in _NO for i in items):
        return []
    if any(i in _YES for i in items):
        return list(HEADER_SIZES)
    bad = [i for i in items if i not in HEADER_SIZES]
    if bad:
        raise ValueError(f"header_sizes must be from {', '.join(HEADER_SIZES)} or no, got {', '.join(bad)}")
    return [s for s in HEADER_SIZES if s in items]


def parse_devices(v, stores) -> list[str]:
    allowed = [d for s, ds in (("appstore", APPSTORE_DEVICES), ("playstore", PLAY_DEVICES)) if s in stores for d in ds]
    if is_blank(v):
        return [d for d in DEFAULT_DEVICES if d in allowed]
    items = _items(v)
    bad = [i for i in items if i not in DEVICES]
    if bad:
        raise ValueError(f"unknown device: {', '.join(bad)}. Available: {', '.join(DEVICES)}")
    chosen = [d for d in items if d in allowed]
    # Yalnızca bir mağazanın cihazı seçildiyse diğer mağaza boş kalmasın
    for store, ds, default in (("appstore", APPSTORE_DEVICES, "iphone-6.9"), ("playstore", PLAY_DEVICES, "android-phone")):
        if store in stores and not any(d in ds for d in chosen):
            chosen.append(default)
    return list(dict.fromkeys(chosen))


def parse_langs(v) -> list[str]:
    return list(DEFAULT_LANGS) if is_blank(v) else list(dict.fromkeys(_items(v)))


def parse_previews(v) -> dict[str, str]:
    """'kayit.mov' = her dil; 'tr=kayit_tr.mov' = yalnızca o dil. Dönüş: {'*'|dil: yol}"""
    if is_blank(v):
        return {}
    if isinstance(v, dict):
        return {str(k): str(p) for k, p in v.items() if p}
    out: dict[str, str] = {}
    for item in _items(v):
        lang, sep, path = item.partition("=")
        out.update({lang: path} if sep and path else {"*": item})
    return out


def parse_output(v) -> str:
    if is_blank(v):
        return "both"
    key = str(v).strip().lower()
    if key not in OUTPUT_MODES:
        raise ValueError(f"output must be one of {', '.join(OUTPUT_MODES)}, got {v!r}")
    return key


def resolve(answers: dict | None) -> dict:
    """Mağaza ve çıktı cevaplarını çözer. Tasarım alanları (şablon, renk, metin) copywriter.finalize'da çözülür."""
    a = answers or {}
    stores = parse_stores(a.get("store"))
    headers = parse_header_sizes(a.get("header_sizes")) if "appstore" in stores else []
    langs = parse_langs(a.get("langs"))
    devices = parse_devices(a.get("devices"), stores)
    banners = [*headers, *([FEATURE_GRAPHIC] if "playstore" in stores else [])]
    previews = parse_previews(a.get("previews")) if "appstore" in stores else {}
    return dict(stores=list(stores), langs=langs, previews=previews, notes=notes_for(stores, devices, langs),
                targets=dict(devices=devices, banners=banners, output=parse_output(a.get("output"))))


def notes_for(stores, devices, langs) -> list[str]:
    notes = []
    if "playstore" in stores:
        notes.append("Google Play: device frames and extra text don't break a hard rule, but the guidelines recommend no device images, "
                     "a slogan under 20% of the image, and avoiding phrases like 'Best', '#1', 'Free' and 'Download now'. "
                     "Images that don't follow them may not appear in recommended placements.")
    no_glyph = [l for l in langs if l.split("-")[0] in NO_GLYPH_LANGS]
    if no_glyph:
        notes.append(f"{', '.join(no_glyph)}: the Inter font has no characters for this script; a suitable font (--font) is needed.")
    if any(l.split("-")[0] in ("ar", "he") for l in langs):
        notes.append("Right-to-left languages are not laid out correctly; check the output by hand.")
    return notes


def check_targets(t) -> dict:
    """Copy dosyası güvenilmezdir: hedefler yalnızca bilinen cihaz, görsel ve çıktı modlarından oluşabilir."""
    if not isinstance(t, dict):
        raise ValueError("targets must be an object")
    devices, banners = t.get("devices") or [], t.get("banners") or []
    if not isinstance(devices, list) or any(d not in DEVICES for d in devices):
        raise ValueError(f"targets.devices must be from: {', '.join(DEVICES)}")
    if not isinstance(banners, list) or any(b not in BANNERS for b in banners):
        raise ValueError(f"targets.banners must be from: {', '.join(BANNERS)}")
    output = t.get("output", "both")
    if output not in OUTPUT_MODES:
        raise ValueError(f"targets.output must be one of {', '.join(OUTPUT_MODES)}")
    return dict(devices=devices, banners=banners, output=output)


_TR_FOLD = str.maketrans({"ı": "i", "İ": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g", "ç": "c", "Ç": "c",
                          "ö": "o", "Ö": "o", "ü": "u", "Ü": "u"})


def slug(name: str | None) -> str:
    """Uygulama adından klasör ve zip adı. Yol ayırıcı veya '..' içeremez."""
    s = unicodedata.normalize("NFKD", str(name or "").translate(_TR_FOLD)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "app"
