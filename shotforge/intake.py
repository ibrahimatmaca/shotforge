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
    dict(id="app", question="Uygulamanın adı ne?", kind="text", required=True),
    dict(id="about", question="Uygulama ne yapar? (1–2 cümle)", kind="text", default="ekranlardan çıkarılır"),
    dict(id="store", question="Görseller hangi mağaza için üretilsin?", kind="choice",
         options=["appstore", "playstore", "both"], default="both"),
    dict(id="header_sizes", question="App Store Header and Search Results görselleri üretilsin mi? Hangi boyutlarda?",
         kind="multi", options=[*HEADER_SIZES, "no"], default="hepsi", when="store includes appstore",
         note="5244x2950 (16:9, PNG) header ve aramada çalışır; 3840x2560 ve 1920x1280 (3:2) yalnızca aramada."),
    dict(id="langs", question="App Preview ve ekran görüntüleri hangi dillerde oluşturulsun? Birden fazla seçilebilir.",
         kind="multi", options=["en", "tr", "de", "fr"], default="en,tr",
         note="Başka bir dil kodu da yazılabilir (ör. pt-BR). ja, ko, ar için fontun karşılığı yok; Arapça sağdan sola dizilmez."),
    dict(id="dimension", question="Şablon ailesi 2D mi 3D mi?", kind="choice", options=["2d", "3d", "auto"], default="auto"),
    dict(id="template", question="Belirli bir şablon ister misiniz?", kind="template", default="kurallar seçer"),
    dict(id="appstore_devices", question="App Store için hangi cihaz boyutları üretilsin?", kind="multi", answer_field="devices",
         options=["iphone-6.9", "iphone-6.5", "ipad-13"], default="iphone-6.9", when="store includes appstore"),
    dict(id="play_devices", question="Google Play için hangi cihaz boyutları üretilsin?", kind="multi", answer_field="devices",
         options=list(PLAY_DEVICES), default="android-phone", when="store includes playstore"),
    dict(id="shot_sets", question="Dile veya platforma göre ayrı ekran görüntüleriniz var mı?", kind="info",
         when="klasörde algılanmadıysa", default="tek set, uyarıyla",
         note="Klasör düzeni: <klasör>/tr/…, <klasör>/android/…, <klasör>/android/tr/…, ipad/…, android-tablet/… (dosya adları aynı olmalı)."),
    dict(id="count", question="Kaç sayfa (slayt) üretilsin? 1–10", kind="number", default="6–7",
         note="Google Play cihaz türü başına en fazla 8 görsel alır."),
    dict(id="texts", question="Başlık metinlerini kim yazsın?", kind="choice", options=["claude", "ben", "dosya"], default="claude"),
    dict(id="tone", question="Metin tonu nasıl olsun?", kind="text", default="confident, friendly, benefit-first"),
    dict(id="background_color", question="Ana arka plan rengi (#hex)", kind="color", default="ekran görüntülerinden"),
    dict(id="text_color", question="Yazı rengi (#hex)", kind="color", default="okunaklı renk otomatik seçilir"),
    dict(id="accent_color", question="Vurgu rengi (#hex)", kind="color", default="ekran görüntülerinden veya otomatik"),
    dict(id="icon", question="Header ve feature graphic için uygulama ikonu dosyası?", kind="path", default="yok, yalnızca metin"),
    dict(id="previews", question="App Preview için ekran kaydınız var mı? (ortak veya dil başına)", kind="path",
         default="atlanır", when="store includes appstore",
         note="Yalnızca verilen kayıt dönüştürülür ve ffmpeg gerekir. Google Play video dosyası değil YouTube bağlantısı alır."),
    dict(id="out", question="Çıktı nereye yazılsın?", kind="path", default="./shotforge-out/<uygulama>"),
    dict(id="output", question="Çıktı klasör mü, zip mi, ikisi mi?", kind="choice", options=list(OUTPUT_MODES), default="both"),
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
        notes.append("Google Play: çerçeve ve ek metin zorunlu kurala aykırı değil, ama yönerge cihaz görseli kullanmamayı, "
                     "sloganın görselin %20'sini geçmemesini ve 'Best', '#1', 'Free', 'Download now' gibi ifadelerden "
                     "kaçınmayı öneriyor. Yönergeye uymayan görseller öneri alanlarında görünmeyebilir.")
    no_glyph = [l for l in langs if l.split("-")[0] in NO_GLYPH_LANGS]
    if no_glyph:
        notes.append(f"{', '.join(no_glyph)}: Inter fontunda bu yazının karakterleri yok; uygun bir font (--font) gerekir.")
    if any(l.split("-")[0] in ("ar", "he") for l in langs):
        notes.append("Sağdan sola yazan diller doğru dizilmez; çıktıyı elle kontrol edin.")
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
