"""shotforge: App Store and Google Play images plus App Preview conversion (AI design decisions and copy, 2D/3D templates)."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from . import brand as B, capture as C, copywriter, intake as I, palette as P, texts as T
from .engine import BANNERS, BG_PRESETS, DEVICES, FRAMES, PREVIEW_SIZES, STYLES, render_job, text_problems

HERE = Path(__file__).resolve().parent
IMG_EXT = {".png", ".jpg", ".jpeg", ".webp"}
# copy dosyasında slayt başına değiştirilebilen alanlar; yol alan font/image gibi alanlar bilerek yok
SLIDE_OVERRIDES = {"style", "frame", "text_position", "align", "bg_angle", "title_scale", "width", "perspective",
                   "no_grain", "no_glow", "text_color", "accent", "glow", "deco", "frame_rim"}
ASC_LOCALES = {"en": "en-US", "tr": "tr", "de": "de-DE", "fr": "fr-FR", "es": "es-ES", "it": "it", "pt": "pt-BR",
               "ja": "ja", "ko": "ko", "ru": "ru", "nl": "nl-NL", "ar": "ar-SA"}
PLAY_LOCALES = {"en": "en-US", "tr": "tr-TR", "de": "de-DE", "fr": "fr-FR", "es": "es-ES", "it": "it-IT", "pt": "pt-BR",
                "ja": "ja-JP", "ko": "ko-KR", "ru": "ru-RU", "nl": "nl-NL", "ar": "ar"}
PLAY_IMAGE_DIRS = {"android-phone": "phoneScreenshots", "android-tablet-7": "sevenInchScreenshots", "android-tablet-10": "tenInchScreenshots"}
SHOT_PLATFORMS = C.SHOT_PLATFORMS
# shots_by_set anahtarı: "<platform>/<dil>", ikisinden biri boş olabilir ("android/", "/tr")
SET_KEY_RE = re.compile(r"(ios|ipad|android|android-tablet)?/([a-z]{2,3}(-[A-Za-z]{2,4})?)?")
VIDEO_EXT = {".mp4", ".mov", ".m4v"}
DEFAULT_TONE = "confident, friendly, benefit-first"
VARIANT_ANGLES = ("the outcome the user gains", "the problem the app removes", "the concrete features that set it apart")


# --------------------------------------------------------------------------- #
# Şablonlar
# --------------------------------------------------------------------------- #
# Eski şablon adları (geriye uyumluluk): eski copy dosyaları ve komutlar çalışmaya devam eder
TEMPLATE_ALIASES = {"kova": "bubble", "pulse": "heartbeat", "inner-glow": "halo", "astra": "slant"}


def canon_template(name: str | None) -> str | None:
    return TEMPLATE_ALIASES.get(name, name) if name else name


def apply_brand(a):
    """shotforge.toml değerlerini yalnızca bayrakla verilmemiş alanlara yazar. render tasarımı copy dosyasından alır,
    bu yüzden orada yalnızca copy dosyasında olmayan font ve logo uygulanır."""
    path = a.brand or (None if a.no_brand else B.find(Path.cwd()))
    b = B.load(path) if path else None
    if b:
        print(f"Brand file: {path}")
        a.font = getattr(a, "font", None) or b["font"]
        a.logo = getattr(a, "logo", None) or b["logo"]
        a.icon = getattr(a, "icon", None) or b["logo"]
        if a.cmd != "render":
            c = b["colors"]
            a.bg_color, a.text_color = a.bg_color or c.get("background"), a.text_color or c.get("text")
            a.accent = a.accent or c.get("accent")
            a.tone = a.tone or b["tone"]
            a.langs = a.langs or ",".join(b["langs"]) or None
            t = canon_template(b["template"])
            if t and t not in list_templates():
                raise SystemExit(f"{path}: unknown template: {t}. Available: {', '.join(list_templates())}")
            if t and a.template in (None, "auto") and a.dimension in (None, "auto", list_templates()[t]["dimension"]):
                a.template = t
    if a.cmd != "render":
        a.tone = a.tone or DEFAULT_TONE


def list_templates() -> dict[str, dict]:
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted((HERE / "templates").glob("*.json"))}


def load_template(name: str) -> dict:
    name = canon_template(name)
    p = Path(name)
    if p.suffix == ".json" and p.exists():
        t = json.loads(p.read_text(encoding="utf-8"))
        t.setdefault("dimension", "3d" if any(l in ("tilt-right", "tilt-left", "duo", "peek") for l in t.get("layouts", [])) else "2d")
        return t
    t = list_templates()
    if name not in t:
        raise SystemExit(f"Unknown template: {name}. Available: {', '.join(t)}  (a path to your own .json also works)")
    return t[name]


# --------------------------------------------------------------------------- #
# Girdi / copy dosyası
# --------------------------------------------------------------------------- #
def collect_shots(inputs) -> dict[str, Path]:
    files: list[Path] = []
    for i in inputs:
        p = Path(i)
        if p.is_dir():
            files += sorted(q for q in p.iterdir() if q.suffix.lower() in IMG_EXT)
        elif p.exists():
            files.append(p)
        else:
            raise SystemExit(f"Not found: {i}")
    if not files:
        raise SystemExit("No screenshots found.")
    out: dict[str, Path] = {}
    for f in files:
        if f.name in out:
            raise SystemExit(f"Two files have the same name: {f.name}")
        out[f.name] = f
    return out


def _images(d: Path) -> list[Path]:
    return sorted(q for q in d.iterdir() if q.is_file() and q.suffix.lower() in IMG_EXT)


def scan_shots(inputs, langs=()) -> tuple[dict[str, Path], dict[str, dict[str, Path]]]:
    """Temel ekran görüntüsü seti + dile/platforma göre ayrılmış setler (<klasör>/tr, <klasör>/android/tr …)."""
    root = Path(inputs[0]) if len(inputs) == 1 else None
    if root is None or not root.is_dir():
        return collect_shots(inputs), {}
    known = {*ASC_LOCALES, *langs}  # 'old', 'out' gibi klasörler dil sanılmasın
    sets: dict[str, list[Path]] = {}
    for d in sorted(q for q in root.iterdir() if q.is_dir()):
        if d.name in SHOT_PLATFORMS:
            sets[f"{d.name}/"] = _images(d)
            for sub in sorted(q for q in d.iterdir() if q.is_dir() and q.name in known):
                sets[f"{d.name}/{sub.name}"] = _images(sub)
        elif d.name in known:
            sets[f"/{d.name}"] = _images(d)
    sets = {k: v for k, v in sets.items() if v}
    if _images(root):
        base = collect_shots(inputs)
    else:
        order = [*(f"/{l}" for l in langs), *sorted(k for k in sets if k.startswith("/")), "ios/", *sorted(sets)]
        first = next((k for k in order if k in sets), None)
        if first is None:
            return collect_shots(inputs), {}  # anlaşılır hata verir
        base = {f.name: f for f in sets[first]}
    return base, {k: {f.name: f for f in v} for k, v in sets.items()}


def _shot_platforms(device: str) -> tuple[str, ...]:
    if device.startswith("android-tablet"):
        return ("android-tablet", "android")
    if device.startswith("android"):
        return ("android",)
    return ("ipad", "ios") if device.startswith("ipad") else ("ios",)


def shots_for(device: str, lang: str, base: dict[str, Path], sets: dict[str, dict[str, Path]], notes: list[str]) -> dict[str, Path]:
    """Her slayt görseli için en uygun set: platform+dil → platform → dil → temel."""
    chain = [k for p in _shot_platforms(device) for k in (f"{p}/{lang}", f"{p}/")] + [f"/{lang}"]
    out, used = {}, set()
    for name, path in base.items():
        key = next((k for k in chain if name in sets.get(k, {})), None)
        out[name] = sets[key][name] if key else path
        if key:
            used.add(key)
    if device.startswith("android") and not any(k.startswith("android") for k in used):
        notes.append(f"{device}: no Android screenshots, iOS screenshots were used (the status bar won't look like Android).")
    if DEVICES[device]["kind"] == "tablet" and not any(k.startswith(("ipad/", "android-tablet/")) for k in used):
        notes.append(f"{device}: no tablet screenshots, phone screenshots were cropped to the tablet screen.")
    if any(k.split("/")[1] for k in sets) and not any(k.endswith(f"/{lang}") for k in used):
        notes.append(f"[{lang}] has no screenshots of its own; another language's UI is shown.")
    return out


def check_langs(langs: list[str]):
    """Dil kodları klasör adı olur; yol içeremez."""
    if not isinstance(langs, list) or not langs:
        raise SystemExit("At least one language code is required (example: en, tr, pt-BR)")
    for l in langs:
        if not isinstance(l, str) or not T.LANG_RE.fullmatch(l):
            raise SystemExit(f"Invalid language code: {l!r}  (example: en, tr, pt-BR)")


def count_arg(v: str) -> str:
    if v != "auto" and not (v.isdigit() and 1 <= int(v) <= 10):
        raise argparse.ArgumentTypeError("must be a number from 1 to 10, or auto")
    return v


def read_copy(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SystemExit(f"Copy file is not valid JSON: {path} ({e})")


def check_copy(data, path: Path):
    """Copy dosyası elle düzenlenir ve paylaşılır: çizimden önce yapısını doğrula."""
    shots = data.get("shots") if isinstance(data, dict) else None
    if not isinstance(shots, dict) or not all(isinstance(p, str) for p in shots.values()):
        raise SystemExit(f"{path}: 'shots' must map file names to paths")
    sets = data.get("shots_by_set") or {}
    if not isinstance(sets, dict) or not all(isinstance(m, dict) and all(isinstance(p, str) for p in m.values()) for m in sets.values()):
        raise SystemExit(f"{path}: 'shots_by_set' must map set names to file maps")
    slides = data.get("slides")
    if not isinstance(slides, list) or not slides or not all(isinstance(s, dict) for s in slides):
        raise SystemExit(f"{path}: 'slides' must be a non-empty list of objects")
    for i, s in enumerate(slides, 1):
        if s.get("shot") not in shots:
            raise SystemExit(f"{path}: slide {i} uses an unknown screenshot: {s.get('shot')!r}")


def write_copy(path: Path, data: dict):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def rel(p: Path, base: Path) -> str:
    try:
        return os.path.relpath(p.resolve(), base.resolve())
    except ValueError:
        return str(p.resolve())


# --------------------------------------------------------------------------- #
# Etkileşimli sorular
# --------------------------------------------------------------------------- #
def _ask(prompt: str, default: str = "") -> str:
    try:
        v = input(prompt)
    except EOFError:
        v = ""
    return v.strip() or default


def _ask_color(label: str, suggestion: str | None = None) -> str | None:
    hint = f" [suggested: {suggestion}]" if suggestion else ""
    for _ in range(2):
        v = _ask(f"{label} (#hex; Enter = auto){hint}: ")
        if not v or v.lower() in ("otomatik", "auto", "a"):
            return None
        if P.is_hex(v):
            return "#" + v.lstrip("#")
        print("  Invalid colour; example: #6D28D9")
    print("  It will be chosen automatically.")
    return None


def _ask_valid(q: dict, parse):
    """Soruyu sorar; geçersiz cevapta bir kez daha sorar, yine olmazsa varsayılana düşer."""
    opts = f" [{', '.join(q['options'])}]" if q.get("options") else ""
    if q.get("note"):
        print(f"  ({q['note']})")
    for _ in range(2):
        v = _ask(f"{q['question']}{opts} (Enter = {q.get('default', 'auto')}): ")
        try:
            parse(v)
            return v
        except ValueError as e:
            print(f"  Invalid: {e}")
    print("  The default will be used.")
    return ""


def ask_store_questions(a, answers: dict) -> dict:
    """Mağaza, boyut, dil ve çıktı soruları. Tasarım soruları sonra run_interactive içinde sorulur."""
    qs = {q["id"]: q for q in I.QUESTIONS}
    print("\n── Store and output questions ───────────────────────────────────")
    print("Press Enter on any question for the default.\n")
    if not a.app:
        a.app = _ask(qs["app"]["question"] + " ") or None
    if not answers.get("store"):
        answers["store"] = _ask_valid(qs["store"], I.parse_stores)
    stores = I.parse_stores(answers["store"])
    if "appstore" in stores and not answers.get("header_sizes"):
        answers["header_sizes"] = _ask_valid(qs["header_sizes"], I.parse_header_sizes)
    if not answers.get("langs"):
        def langs_ok(v):
            try:
                check_langs(I.parse_langs(v))
            except SystemExit as e:
                raise ValueError(str(e)) from None
        answers["langs"] = _ask_valid(qs["langs"], langs_ok)
    if not answers.get("devices"):
        picked = [_ask_valid(qs[k], lambda v: I.parse_devices(v, stores))
                  for k, st in (("appstore_devices", "appstore"), ("play_devices", "playstore")) if st in stores]
        answers["devices"] = ",".join(p for p in picked if p and not I.is_blank(p))
    if "appstore" in stores and not answers.get("previews"):
        answers["previews"] = _ask_valid(qs["previews"], I.parse_previews)
    if not a.keywords:
        a.keywords = _ask(f"{qs['keywords']['question']} (Enter = {qs['keywords']['default']}): ") or None
    if not a.icon:
        a.icon = _ask(f"{qs['icon']['question']} (Enter = {qs['icon']['default']}): ") or None
    if not a.out:
        a.out = _ask(f"{qs['out']['question']} (Enter = {qs['out']['default']}): ") or None
    if not answers.get("output"):
        answers["output"] = _ask_valid(qs["output"], I.parse_output)
    return answers


def should_ask(a) -> bool:
    return True if a.ask else (False if (a.no_ask or a.yes) else sys.stdin.isatty())


def run_interactive(inp: dict, shots: dict[str, Path], profile: dict, templates: dict, langs: list[str]):
    print("\n── Design questions ─────────────────────────────────────────────")
    print("Detected from the screenshots: main colour %s, UI theme %s, density %s" %
          (profile.get("brand") or "neutral", profile["ui"], profile["density"]))
    print("Press Enter on any question for auto (the AI or the rules decide).\n")

    if not inp.get("app"):
        inp["app"] = _ask("App name: ") or "App"
    if not inp.get("about"):
        inp["about"] = _ask("What does the app do? (1–2 sentences; Enter = infer from the screens): ")

    if not inp.get("template") and not inp.get("dimension"):
        v = _ask("Template family: 2d / 3d / auto (Enter = auto): ").lower()
        if v in ("2d", "3d"):
            inp["dimension"] = v
    if not inp.get("template"):
        pool = [n for n, t in templates.items() if not inp.get("dimension") or t["dimension"] == inp["dimension"]]
        v = _ask(f"Template: auto or {', '.join(pool)} (Enter = auto): ").lower()
        if v in templates:
            inp["template"] = v

    c = inp["colors"]
    if not c.get("background"):
        c["background"] = _ask_color("1) Main background colour", profile.get("brand"))
    if not c.get("text"):
        c["text"] = _ask_color("2) Text colour")
    if not c.get("accent"):
        c["accent"] = _ask_color("   Accent colour")
    inp["colors"] = {k: v for k, v in c.items() if v}

    if not inp.get("count") and not inp.get("locked"):
        v = _ask("3) How many pages (slides)? 1–10 (Enter = auto, 6–7): ")
        if v.isdigit() and 1 <= int(v) <= 10:
            inp["count"] = int(v)

    if not inp.get("locked"):
        v = _ask("4) Page text: [1] AI writes it  [2] I will write it  [3] From a file  (Enter = 1): ")
        if v == "3":
            path = _ask("   Text file path (.txt/.md/.json): ")
            if path:
                inp["locked"] = T.parse_text_file(path)
        elif v == "2":
            n = inp.get("count")
            if not n:
                v = _ask("   How many pages will you write? (1–10): ", "7")
                inp["count"] = max(1, min(10, int(v))) if v.isdigit() else 7
            print("   Format: 'Title | Subtitle'. *word* = accent. Leave blank and the AI writes that language.")
            print("   Screenshots: " + ", ".join(shots))
            locked = []
            for i in range(inp["count"]):
                s = {"title": {}, "subtitle": {}}
                sh = _ask(f"   Page {i + 1}: which screenshot? (Enter = auto): ")
                if sh in shots:
                    s["shot"] = sh
                for l in langs:
                    v = _ask(f"   Page {i + 1} [{l}] text: ")
                    if v:
                        t, st = T._split(v)
                        s["title"][l] = t
                        if st:
                            s["subtitle"][l] = st
                locked.append(s)
            inp["locked"] = locked
    print("─" * 63 + "\n")


# --------------------------------------------------------------------------- #
# Plan: tasarım kararları + metinler
# --------------------------------------------------------------------------- #
def gather_inputs(a, shots: dict[str, Path], langs: list[str], templates: dict, profile: dict) -> dict:
    inp = dict(
        app=a.app, about=a.about or "", category=a.category,
        count=None if a.count in (None, "auto") else int(a.count),
        dimension=None if a.dimension in (None, "auto") else a.dimension,
        template=None if a.template in (None, "auto") else canon_template(a.template),
        colors={k: v for k, v in (("background", a.bg_color), ("text", a.text_color), ("accent", a.accent)) if v},
        locked=T.parse_text_file(a.texts) if a.texts else [],
    )
    for k, v in inp["colors"].items():
        if not P.is_hex(v):
            raise SystemExit(f"Invalid colour ({k}): {v}  (example: #6D28D9)")
    if inp["template"] and inp["template"] not in templates and not Path(inp["template"]).exists():
        raise SystemExit(f"Unknown template: {inp['template']}. Available: {', '.join(templates)}")
    if should_ask(a):
        run_interactive(inp, shots, profile, templates, langs)
    if not inp["app"]:
        raise SystemExit("App name required: --app \"Name\" (or answer it in interactive mode).")
    if inp["count"] is None and inp["locked"]:
        inp["count"] = len(inp["locked"])  # kullanıcı kaç sayfa yazdıysa o kadar
    for i, s in enumerate(inp["locked"]):
        if s.get("shot") and s["shot"] not in shots:
            raise SystemExit(f"Text file, slide {i + 1}: '{s['shot']}' is not among the screenshots ({', '.join(shots)})")
    if inp["template"] and inp["dimension"]:
        t = templates.get(inp["template"])
        if t and t["dimension"] != inp["dimension"]:
            print(f"WARNING: --template {inp['template']} is {t['dimension'].upper()}; --dimension {inp['dimension']} was ignored.", file=sys.stderr)
            inp["dimension"] = None
    return inp


def cmd_plan(a, quiet=False) -> dict:
    langs = [l.strip() for l in a.langs.split(",") if l.strip()]
    check_langs(langs)
    shots, sets = scan_shots(a.shots, langs)
    copy_path = Path(a.copy)
    keywords = copywriter.parse_keywords(getattr(a, "keywords", None))
    if copy_path.exists() and not a.regen:
        if not quiet:
            print(f"Using the existing copy file: {copy_path}  (use --regen to create it again)")
        data = read_copy(copy_path)
        if keywords and keywords != data.get("keywords"):
            print("WARNING: --keywords was not applied because the copy file already exists (use --regen).", file=sys.stderr)
        if (a.app and a.app != data.get("app")) or set(shots) != set(data.get("shots") or {}):
            print(f"WARNING: {copy_path} belongs to another app or other screenshots; "
                  "the new input is not used (create it again with --regen).", file=sys.stderr)
        return data

    templates = list_templates()
    profile = P.analyze_shots(list(shots.values()))
    inp = gather_inputs(a, shots, langs, templates, profile)
    if inp["template"] and inp["template"] not in templates:  # --template my.json
        templates[inp["template"]] = load_template(inp["template"])
    ctx = dict(app=inp["app"], about=inp["about"], tone=a.tone, langs=langs, templates=templates, profile=profile,
               locked=inp["locked"], keywords=keywords, variant=getattr(a, "variant", None),
               fixed=dict(colors=inp["colors"], count=inp["count"], template=inp["template"],
                          dimension=inp["dimension"], category=inp["category"]))
    if (inp["count"] or 0) > 10:
        print("WARNING: App Store accepts at most 10 screenshots per language.", file=sys.stderr)
    miss = T.missing(inp["locked"], inp["count"] or len(inp["locked"]), langs) if inp["locked"] else []
    if miss:
        print("Missing from your text (the AI will complete it): " + "; ".join(miss[:6]) + (" …" if len(miss) > 6 else ""))
    print(f"Preparing design decisions and copy ({', '.join(langs)}) …")
    if getattr(a, "variant_slides", None):  # yalnızca tasarımı değişen varyant: metin aynı kalır, model çağrılmaz
        data, used = copywriter.finalize({"slides": a.variant_slides}, ctx, list(shots)), "variant"
    else:
        data, used = copywriter.plan(ctx, shots, backend=a.ai, model=a.model)
    d = data["design"]
    out = dict(app=inp["app"], about=inp["about"], tone=a.tone, langs=langs, generated_with=used,
               design=d, shots={n: rel(p, copy_path.parent) for n, p in shots.items()}, slides=data["slides"])
    if sets:
        out["shots_by_set"] = {k: {n: rel(p, copy_path.parent) for n, p in m.items()} for k, m in sets.items()}
    if keywords:
        out["keywords"] = keywords
        warn_keywords(keywords, data["slides"], used)
    write_copy(copy_path, out)
    print_design(d)
    print(f"Wrote: {copy_path}  (edit it and redraw with `shotforge render`)")
    if should_ask(a):
        if _ask("Continue with this design? [Y/n]: ", "y").lower().startswith("n"):
            raise SystemExit(f"Stopped. Edit {copy_path} and run `shotforge render`.")
    return out


def warn_keywords(keywords: list[str], slides: list[dict], used: str):
    if used in ("offline", "user"):
        print(f"WARNING: --keywords needs an AI backend to shape the copy; this run's text came from {used} and was not changed.", file=sys.stderr)
    gaps = copywriter.keyword_gaps(slides, keywords)
    if gaps:
        print(f"Keywords not found word for word in any slide (a localized term may have been used): {', '.join(gaps)}")
    risky = [k for k in keywords if I.PLAY_AVOID_RE.search(k)]
    if risky:
        print(f"WARNING: Google Play guidelines advise against these words in store images: {', '.join(risky)}", file=sys.stderr)


def cmd_plan_only(a):
    a.langs = a.langs or ",".join(I.DEFAULT_LANGS)
    return cmd_plan(a)


def print_design(d: dict):
    c = d.get("colors") or {}
    print("\nDesign decision:")
    print(f"  template   : {d['template']}  ({d['dimension'].upper()})   — {d.get('template_reason', '')}")
    print(f"  category   : {d.get('category') or '?'}")
    print(f"  pages      : {d['count']}")
    print(f"  background : {c.get('background') or '(template default)'}   text: {c.get('text') or '(auto)'}   accent: {c.get('accent') or '(auto)'}")
    if d.get("colors_source"):
        print(f"  colours from: {d['colors_source']}")


# --------------------------------------------------------------------------- #
# Render
# --------------------------------------------------------------------------- #
def slide_cfg(a, tpl, pal, idx, slide, shot_paths, names, warnings) -> dict:
    layouts = tpl.get("layouts") or ["flat"]
    st = P.slide_style(tpl, pal, idx, warnings)
    tokens = st.pop("tokens")
    cfg: dict = dict(device=a.device, frame="black", text_position="top")
    cfg.update(P.resolve_placeholders(tpl.get("defaults", {}), tokens))
    cfg.update(st)
    cfg["style"] = layouts[idx % len(layouts)]
    if a.style:
        cfg["style"] = a.style
    if a.bg:  # ileri düzey: ham arka plan (preset / degrade / görsel)
        cfg["bg"] = a.bg
        cfg.pop("bg_angle", None)
    for k in ("frame", "text_position", "align", "font", "logo"):
        v = getattr(a, k, None)
        if v:
            cfg[k] = v
    if getattr(a, "perspective", None):
        cfg["perspective"] = a.perspective
    if getattr(a, "no_grain", False):
        cfg["no_grain"] = True
    for k, v in slide.items():  # copy dosyasında slayta elle yazılmış değerler
        if k in ("shot", "role", "title", "subtitle"):
            continue
        # bg yalnızca preset/degrade olabilir: görsel yolu dosya içeriğini çıktıya gömer
        if k in SLIDE_OVERRIDES or (k == "bg" and (v in BG_PRESETS or all(P.is_hex(c) for c in str(v).split(",")))):
            cfg[k] = v
        else:
            warnings.append((idx + 1, f"the '{k}' field in the copy file was ignored."))
    if cfg["style"] == "alt":
        cfg["style"] = "tilt-right" if idx % 2 == 0 else "tilt-left"
    if cfg["style"] not in STYLES:
        raise SystemExit(f"Unknown style: {cfg['style']} ({', '.join(STYLES)})")
    shot = slide["shot"]
    cfg["image"] = str(shot_paths[shot])
    if cfg["style"] in ("duo", "duo-flat"):
        others = [n for n in names if n != shot]
        cfg["image2"] = str(shot_paths[others[idx % len(others)] if others else shot])
    cfg["title"], cfg["subtitle"] = slide.get("title"), slide.get("subtitle")
    return cfg


def cmd_render(a, data: dict | None = None):
    copy_path = Path(a.copy)
    from_file = data is None
    if from_file:
        if not copy_path.exists():
            raise SystemExit(f"Copy file not found: {copy_path}. Run `shotforge plan` or `shotforge create` first.")
        data = read_copy(copy_path)
        check_copy(data, copy_path)
    design = data.get("design") or {"template": data.get("template", "classic"), "colors": {}}
    tname = canon_template(a.template if a.template and a.template != "auto" else design.get("template", "classic"))
    if from_file and not a.template and tname not in list_templates():
        print(f"WARNING: {copy_path} loads a local template file: {tname}", file=sys.stderr)
    tpl = load_template(tname)
    colors = dict(design.get("colors") or {})
    for flag, key in (("bg_color", "background"), ("text_color", "text"), ("accent", "accent")):
        if getattr(a, flag, None):
            colors[key] = getattr(a, flag)
    try:
        pal = P.build_palette(tpl, colors)
    except ValueError as e:
        raise SystemExit(str(e))
    langs = [l.strip() for l in a.langs.split(",")] if getattr(a, "langs", None) else data.get("langs")
    check_langs(langs)
    shot_paths = {n: (copy_path.parent / p) for n, p in data["shots"].items()}
    base = copy_path.parent.resolve()
    sets: dict[str, dict[str, Path]] = {}
    for key, m in (data.get("shots_by_set") or {}).items():
        if not SET_KEY_RE.fullmatch(key):
            print(f"WARNING: {copy_path}: unknown shots_by_set key ignored: {key!r}", file=sys.stderr)
            continue
        sets[key] = {n: copy_path.parent / p for n, p in m.items() if (copy_path.parent / p).exists()}
    all_paths = [*shot_paths.items(), *(i for m in sets.values() for i in m.items())]
    outside = sorted({n for n, p in all_paths if not p.resolve().is_relative_to(base)})
    if outside and from_file:  # create az önce kendi yazdı; uyarı dışarıdan gelen copy dosyaları için
        print(f"WARNING: {copy_path} uses images outside its own folder: {', '.join(outside)}", file=sys.stderr)
    if getattr(a, "shots", None):
        shot_paths.update(scan_shots(a.shots, langs)[0])
    for p in shot_paths.values():
        if not Path(p).exists():
            raise SystemExit(f"Screenshot not found: {p}")
    icon = getattr(a, "icon", None)
    if icon:
        icon = os.path.expanduser(str(icon))
        if Path(icon).suffix.lower() not in IMG_EXT or not Path(icon).is_file():
            raise SystemExit(f"Icon must be an image file: {icon}")
    if getattr(a, "logo", None):
        a.logo = os.path.expanduser(str(a.logo))
        if Path(a.logo).suffix.lower() not in IMG_EXT or not Path(a.logo).is_file():
            raise SystemExit(f"Logo must be an image file: {a.logo}")
    try:
        targets = I.check_targets(data["targets"]) if data.get("targets") else None
    except ValueError as e:
        raise SystemExit(f"{copy_path}: {e}")
    device = getattr(a, "device", None)
    devices = [device] if device else (targets["devices"] if targets else ["iphone-6.9"])
    banners = targets["banners"] if targets and not device else []
    layout = getattr(a, "layout", None) or ("store" if targets and not device else "flat")
    if layout == "flat" and len(devices) > 1:
        raise SystemExit("--layout flat works with one device only (files would overwrite each other); choose store or asc.")
    names = list(shot_paths)
    slides = data["slides"]
    only = {int(x) for x in a.only.split(",")} if getattr(a, "only", None) else None
    out = Path(a.out)
    warnings: list = []
    notes: list[str] = []
    jobs = []
    for dev in devices:
        ns = argparse.Namespace(**{**vars(a), "device": dev})
        limit = I.PLAY_MAX_SHOTS if dev.startswith("android") else len(slides)
        if len(slides) > limit:
            notes.append(f"{dev}: Google Play takes at most {limit} images; the first {limit} pages were created.")
        if dev.startswith("android-tablet") and len(slides) < I.TABLET_MIN_SHOTS:
            notes.append(f"{dev}: Google Play needs at least {I.TABLET_MIN_SHOTS} tablet images; there are {len(slides)} pages.")
        for lang in langs:
            paths = shots_for(dev, lang, shot_paths, sets, notes)
            for idx, s in enumerate(slides[:limit]):
                if only and (idx + 1) not in only:
                    continue
                cfg = slide_cfg(ns, tpl, pal, idx, s, paths, names, warnings)
                warnings += [(idx + 1, f"[{lang}] {m}") for m in text_problems(cfg, lang)]
                slug = Path(s["shot"]).stem.lstrip("0123456789_- ") or "screen"
                fname = f"{idx + 1:02d}_{slug}.{'jpg' if a.format == 'jpg' else 'png'}"
                store, target = store_dir(dev).parts
                if layout == "store":
                    path = out / lang / store / target / fname
                elif layout == "fastlane":
                    path = fastlane_shot(out, dev, lang, fname)
                elif layout == "asc":
                    path = out / ASC_LOCALES.get(lang, lang) / DEVICES[dev]["asc"] / fname
                else:
                    path = out / lang / fname
                jobs.append(dict(cfg=cfg, locale=lang, idx=idx, total=len(slides), path=str(path), fmt=a.format,
                                 meta=dict(store=store, target=target)))
    for key in [] if only else banners:
        b = BANNERS[key]
        ns = argparse.Namespace(**{**vars(a), "device": b["device"]})
        for lang in langs:
            paths = shots_for(b["device"], lang, shot_paths, sets, notes)
            cfg = slide_cfg(ns, tpl, pal, 0, slides[0], paths, names, warnings)
            for k, i in (("image", 0), ("image2", 1), ("image3", 2)):
                cfg[k] = str(paths[slides[i % len(slides)]["shot"]])
            title, subtitle = banner_text(data, slides[0])
            cfg.update(banner=key, dimension=tpl.get("dimension"), title=title, subtitle=subtitle, icon=icon)
            path = out / lang / b["store"] / b["folder"] / f"{key}.png"
            if layout == "fastlane":
                path = fastlane_banner(out, b, lang, key)
            jobs.append(dict(cfg=cfg, locale=lang, idx=0, total=1, path=str(path), fmt="png",
                             meta=dict(store=b["store"], target=b["folder"], banner=key)))
    grouped: dict[str, list[int]] = {}
    for n, msg in warnings:
        grouped.setdefault(msg, [])
        if n not in grouped[msg]:
            grouped[msg].append(n)
    if targets and not device:
        stores = [st for st, pre in (("appstore", ("iphone", "ipad")), ("playstore", ("android",)))
                  if any(d.startswith(pre) for d in devices)]
        notes = [*I.notes_for(stores, devices, langs), *notes]
    if len(langs) > 1 and not any(k.split("/")[1] for k in sets):
        notes.append("The same screenshots were used for every language. For a localized UI, put them in <folder>/<lang>/.")
    a.warnings = [f"slide {', '.join(map(str, nums))}: {msg}" for msg, nums in grouped.items()] + list(dict.fromkeys(notes))
    for w in a.warnings:
        print(f"WARNING: {w}", file=sys.stderr)
    print(f"Rendering {len(jobs)} images, template '{tpl['name']}' ({tpl.get('dimension', '?').upper()}), {a.jobs} processes …")
    done = 0
    paths_by_lang: dict[str, list[str]] = {l: [] for l in langs}
    if a.jobs <= 1:
        for j in jobs:
            p = render_job(j)
            done += 1
            print(f"[{done}/{len(jobs)}] {p}")
            paths_by_lang[j["locale"]].append(p)
    else:
        with ProcessPoolExecutor(max_workers=a.jobs) as ex:
            futs = {ex.submit(render_job, j): j for j in jobs}
            for f in as_completed(futs):
                p = f.result()
                done += 1
                print(f"[{done}/{len(jobs)}] {p}")
                paths_by_lang[futs[f]["locale"]].append(p)
    print()
    for lang, ps in paths_by_lang.items():
        print(f"{lang}: {len(ps)} separate image(s):")
        for p in sorted(ps):
            print(f"   {p}")
    if a.sheet:
        for lang, ps in paths_by_lang.items():
            make_sheet(sorted(ps), out / "_review" / f"overview_{lang}.jpg")
    if a.brief:
        write_brief(out / "_review" / "claude-design-brief.md", data, tpl, langs, devices[0], colors)
    a.render_state = dict(data=data, langs=langs, layout=layout, targets=targets, devices=devices, partial=bool(only),
                          meta={j["path"]: j["meta"] for j in jobs})
    return paths_by_lang


def store_dir(device: str) -> Path:
    if device.startswith("android-"):
        return Path("google-play") / device.removeprefix("android-")
    return Path("app-store") / device


def fastlane_shot(out: Path, device: str, lang: str, fname: str) -> Path:
    """deliver tek dil klasörü bekler ve cihazı boyuttan anlar: ad cihazlar arasında çakışmamalı, sıra NN ile korunmalı.
    asc adı dosya adında kalır; fastlane aynı boyutlu iPad nesillerini bununla ayırır."""
    if device.startswith("android"):
        return out / "fastlane" / "metadata" / "android" / PLAY_LOCALES.get(lang, lang) / "images" / PLAY_IMAGE_DIRS[device] / fname
    return out / "fastlane" / "screenshots" / ASC_LOCALES.get(lang, lang) / f"{DEVICES[device]['asc']}_{device}_{fname}"


def fastlane_banner(out: Path, banner: dict, lang: str, key: str) -> Path:
    if banner["store"] == "google-play":
        return out / "fastlane" / "metadata" / "android" / PLAY_LOCALES.get(lang, lang) / "images" / "featureGraphic.png"
    # deliver ekran görüntüsü yüklüyor; başlık görselleri onun klasörüne girerse boyutu reddedilir
    return out / "app-store-extra" / lang / banner["folder"] / f"{key}.png"


def banner_text(data: dict, first_slide: dict) -> tuple:
    """Copy dosyasındaki banner metni; yoksa ilk slaytın metni, alt başlık yoksa uygulama adı. Yalnızca metin alanları geçer."""
    b = data.get("banner") if isinstance(data.get("banner"), dict) else {}

    def text(v):
        if isinstance(v, dict):
            v = {k: x for k, x in v.items() if isinstance(x, str) and x}
        return v if v and isinstance(v, (str, dict)) else None
    return (text(b.get("title")) or first_slide.get("title"),
            text(b.get("subtitle")) or text(first_slide.get("subtitle")) or str(data.get("app") or ""))


def convert_previews(out: Path, previews: dict, langs: list[str], devices: list[str], warnings: list[str]) -> list[str]:
    """Verilen ekran kayıtlarını dil klasörlerine dönüştürür. Sorun görsel üretimini durdurmaz, uyarı olur."""
    if not previews:
        return []
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        warnings.append("App Preview skipped: ffmpeg/ffprobe not found (macOS: brew install ffmpeg).")
        return []
    device = next((d for d in devices if d in PREVIEW_SIZES), "iphone-6.9")
    done: dict[str, Path] = {}
    written = []
    for lang in langs:
        src = previews.get(lang) or previews.get("*")
        if not src:
            continue
        src = os.path.abspath(os.path.expanduser(str(src)))  # "-" ile başlayan ad ffmpeg bayrağı sanılmasın
        if Path(src).suffix.lower() not in VIDEO_EXT or not Path(src).is_file():
            warnings.append(f"App Preview [{lang}] skipped: {src} is not a .mp4/.mov/.m4v file.")
            continue
        dest = out / lang / "app-store" / "previews" / f"{device}.mp4"
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            if src in done:
                shutil.copy2(done[src], dest)
            else:
                cmd_preview(argparse.Namespace(input=src, out=str(dest), device=device, landscape=False, fit="cover",
                                               pad_color="black", extend=False))
                done[src] = dest
            written.append(str(dest))
        except (subprocess.CalledProcessError, OSError, ValueError, SystemExit) as e:
            warnings.append(f"App Preview [{lang}] could not be converted: {e}")
    return written


def package_output(a, paths_by_lang: dict[str, list[str]]) -> dict:
    """Mağaza düzeninde manifest ve zip yazar. Zip yalnızca bu çalıştırmanın dosyalarını alır, eski dosyalar girmez."""
    st = a.render_state
    if st["layout"] not in ("store", "fastlane") or st["partial"] or not st["targets"]:
        return {}
    from PIL import Image
    out = Path(a.out)
    warnings = a.warnings
    videos = convert_previews(out, getattr(a, "previews", None) or {}, st["langs"], st["devices"], warnings)
    files = []
    for lang, ps in paths_by_lang.items():
        for p in sorted(ps):
            with Image.open(p) as im:
                w, h = im.size
            rp = Path(p).relative_to(out)
            meta = st["meta"][p]
            entry = dict(path=rp.as_posix(), lang=lang, store=meta["store"], target=meta["target"], width=w, height=h)
            if meta.get("banner"):
                entry["works_with"] = BANNERS[meta["banner"]]["works_with"]
            files.append(entry)
    for v in videos:
        rp = Path(v).relative_to(out)
        files.append(dict(path=rp.as_posix(), lang=rp.parts[0], store=rp.parts[1], target=rp.parts[2]))
    data = st["data"]
    manifest = out / "manifest.json"
    manifest.write_text(json.dumps(dict(app=data.get("app"), langs=st["langs"], design=data.get("design"), targets=st["targets"],
                                        files=files, warnings=warnings), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    res = dict(manifest=str(manifest), output_files=len(files))
    mode = getattr(a, "output_mode", None) or st["targets"]["output"]
    if mode != "folder":
        zip_path = out / f"{I.slug(data.get('app'))}.zip"  # klasör içinde: yanındaki başka bir dosyanın üzerine yazmaz
        tmp = zip_path.with_suffix(".zip.tmp")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            for f in files:
                z.write(out / f["path"], f["path"])
            z.write(manifest, "manifest.json")
        os.replace(tmp, zip_path)
        res["zip"] = str(zip_path)
        print(f"Zip: {zip_path}")
    if mode == "zip":
        for f in files:
            path = out / f["path"]
            path.unlink(missing_ok=True)
            for d in path.parents:  # boşalan klasörler; içinde başka dosya olan klasör kalır
                if d == out or any(d.iterdir()):
                    break
                d.rmdir()
    else:
        print(f"Folder: {out}")
    return res


def render_and_package(a, data: dict | None = None) -> dict:
    paths = cmd_render(a, data)
    return package_output(a, paths)


def make_sheet(paths: list[str], dest: Path, h=900, gap=28):
    from PIL import Image
    ims = []
    for p in paths:
        im = Image.open(p).convert("RGB")
        ims.append(im.resize((round(im.width * h / im.height), h), Image.LANCZOS))
    W = sum(i.width for i in ims) + gap * (len(ims) + 1)
    sheet = Image.new("RGB", (W, h + 2 * gap), (236, 236, 240))
    x = gap
    for im in ims:
        sheet.paste(im, (x, gap))
        x += im.width + gap
    dest.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(dest, quality=88)
    print(f"Review image (not a deliverable): {dest}")


def pick(v, lang):
    if isinstance(v, dict):
        return v.get(lang) or next(iter(v.values()), "")
    return v or ""


def write_brief(dest: Path, data: dict, tpl: dict, langs: list[str], device: str, colors: dict):
    W, H = DEVICES[device]["size"]
    d = data.get("design") or {}
    L = [f"# Claude Design brief — {data.get('app', '')}", "",
         f"Template: **{tpl['name']}** ({tpl.get('dimension', '?').upper()}) — {tpl.get('description', '')}",
         f"Colours: background {colors.get('background') or 'template default'}, text {colors.get('text') or 'auto'}, accent {colors.get('accent') or 'auto'}",
         (f"Inspired by: {tpl['inspired_by']}" if tpl.get("inspired_by") else ""), "",
         "Use this file to iterate on the same slides by hand in **Claude Design** "
         "(Claude Design runs in the browser; shotforge doesn't drive it). For each language, copy the block, "
         "attach the matching screenshots and send it.", ""]
    for lang in langs:
        L += [f"## {lang.upper()}", "", "```text",
              f"Design {len(data['slides'])} App Store screenshots, each {W}x{H}px, portrait, one consistent design system.",
              f"App: {data.get('app', '')}. {data.get('about', '')}",
              f"Style: {'3D (phone tilted in perspective)' if tpl.get('dimension') == '3d' else '2D (phone flat or rotated in the plane)'}.",
              f"Colours: main background {colors.get('background') or 'your choice'}, text {colors.get('text') or 'high-contrast auto'}, accent {colors.get('accent') or 'your choice'}.",
              f"Visual direction: {tpl.get('design_notes', '')}",
              "Use ONLY the attached real app screenshots inside the device mockups (do not redraw their UI).",
              "In headlines, words wrapped in *asterisks* get the accent colour (do not print the asterisks).",
              "Keep text inside a 7% safe margin, max 2-3 headline lines, high contrast, no overlap with the phone.", ""]
        for i, s in enumerate(data["slides"], 1):
            L.append(f"Slide {i} [{s.get('role', '')}] — screenshot: {s['shot']}")
            L.append(f"  Headline: {pick(s.get('title'), lang)}")
            if pick(s.get("subtitle"), lang):
                L.append(f"  Subtitle: {pick(s.get('subtitle'), lang)}")
        L += ["```", ""]
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(l for l in L) + "\n", encoding="utf-8")
    print(f"Claude Design brief: {dest}")


def cmd_create(a):
    r = None
    if a.device:  # tek cihaz, eski düzen
        a.langs = a.langs or "en,tr"
    else:
        output = {True: "both", False: "folder", None: None}[a.zip]
        answers = dict(store=a.store, header_sizes=a.header_sizes, langs=a.langs, devices=a.devices, previews=a.preview,
                       output="zip" if a.zip_only else output)
        if should_ask(a):
            answers = ask_store_questions(a, answers)
        try:
            r = I.resolve(answers)
        except ValueError as e:
            raise SystemExit(str(e))
        check_langs(r["langs"])
        a.langs, a.previews = ",".join(r["langs"]), r["previews"]
    if a.maestro:
        if len(a.shots) != 1:
            raise SystemExit("--maestro writes screenshots into one folder; give a single folder as the input.")
        run_capture(a.maestro, a.langs, a.shots[0], a.capture_platform, a.capture_device)
    if a.variants > 1:
        create_variants(a, r)
    else:
        create_one(a, r)


def create_one(a, r) -> dict:
    if a.device:
        a.out, a.copy = a.out or "out", a.copy or "shotforge.copy.json"
        data = cmd_plan(a, quiet=False)
        cmd_render(a, data)
        return data
    app = a.app or (read_copy(Path(a.copy)).get("app") if a.copy and Path(a.copy).exists() else None)
    a.out = a.out or str(Path("shotforge-out") / I.slug(app))
    Path(a.out).mkdir(parents=True, exist_ok=True)
    a.copy = a.copy or str(Path(a.out) / "shotforge.copy.json")
    data = cmd_plan(a, quiet=False)
    data["targets"] = r["targets"]
    write_copy(Path(a.copy), data)
    render_and_package(a, data)
    return data


def next_template(used: set[str], first: dict, i: int, a, profile: dict) -> str:
    """Tasarım varyantı için kullanılmamış şablon. Boyut serbestse B, 2D/3D farkını da dener."""
    pool = {n: t for n, t in list_templates().items() if n not in used}
    dim = first["dimension"]
    if a.dimension in (None, "auto") and i % 2:
        dim = "2d" if dim == "3d" else "3d"
    return P.choose_template(pool, dimension=dim, category=first.get("category"),
                             bg_hex=(first.get("colors") or {}).get("background"), profile=profile, tone=a.tone or "")[0]


def create_variants(a, r):
    """Her varyant kendi klasöründe tam bir çıktıdır: render, manifest, zip ve fastlane ağacı ayrı çalışır."""
    if not a.app:
        raise SystemExit("App name required with --variants: --app \"Name\".")
    if should_ask(a):
        print("Design questions are skipped with --variants; pass colours and the template as flags.")
    a.ask, a.no_ask = False, True
    root = Path(a.out or Path("shotforge-out") / I.slug(a.app))
    by = a.variant_by
    results: list[dict] = []
    for i in range(a.variants):
        letter = "abc"[i]
        ns = argparse.Namespace(**vars(a))
        ns.out = str(root / f"variant-{letter}")
        if a.copy:
            ns.copy = str(Path(a.copy).with_suffix(f".{letter}{Path(a.copy).suffix}"))
        if by != "design":
            ns.variant = dict(letter=letter, total=a.variants, angle=VARIANT_ANGLES[i])
        if i:
            first = results[0]["design"]
            if by == "copy":
                ns.template = first["template"]
            else:
                profile = P.analyze_shots(list(scan_shots(a.shots, a.langs.split(","))[0].values()))
                ns.template = next_template({d["design"]["template"] for d in results}, first, i, a, profile)
            ns.dimension = "auto"
            if by != "both":  # tek fark test edilsin: diğer yarı A'dan kalır
                c = first.get("colors") or {}
                ns.bg_color, ns.text_color, ns.accent = c.get("background"), c.get("text"), c.get("accent")
            if by == "design":
                ns.variant_slides, ns.count = results[0]["slides"], str(len(results[0]["slides"]))
        print(f"\n── Variant {letter.upper()} ─────────────────────────────────────────────")
        results.append(create_one(ns, r))
    titles = [[s.get("title") for s in d["slides"]] for d in results]
    if by != "design" and any(t == titles[0] for t in titles[1:]):
        print("WARNING: some variants have the same headlines (placeholder copy, or text you fixed); "
              "only an AI backend writes different angles.", file=sys.stderr)
    print(f"\n{a.variants} variants: " + ", ".join(str(root / f"variant-{l}") for l in "abc"[:a.variants]))


# --------------------------------------------------------------------------- #
# Diğer komutlar
# --------------------------------------------------------------------------- #
def run_capture(flows, langs: str, out, platform, device, timeout=C.DEFAULT_TIMEOUT):
    langs = [l.strip() for l in langs.split(",") if l.strip()]
    check_langs(langs)
    for w in C.capture(flows, langs, out, platform, device, timeout):
        print(f"WARNING: {w}", file=sys.stderr)


def cmd_capture(a):
    run_capture(a.flows, a.langs, a.out, a.platform, a.device, a.timeout)
    print(f"Next: shotforge create {a.out} --app \"Name\" --langs {a.langs}")


def cmd_templates(a):
    t = list_templates()
    if a.show:
        print(json.dumps(load_template(a.show), ensure_ascii=False, indent=2))
        return
    for dim in ("2d", "3d"):
        print(f"── {dim.upper()} " + ("(phone flat or rotated in the plane)" if dim == "2d" else "(phone tilted in perspective, thickness visible)"))
        for k, v in t.items():
            if v.get("dimension") == dim:
                print(f"  {k:11s} [{v.get('theme', '?')}] {v['description'].split('· ', 1)[-1]}")
                print(f"  {'':11s} best for: {', '.join(v.get('best_for', []))}" + (f"   ↳ {v['inspired_by']}" if v.get("inspired_by") else ""))


def cmd_rules(_):
    print(P.RULES_TEXT)


def cmd_analyze(a):
    shots = collect_shots(a.shots)
    prof = P.analyze_shots(list(shots.values()))
    print(f"Screenshots     : {prof['count']} files")
    print(f"Brand colour    : {prof['brand'] or 'neutral / not found'}   palette: {', '.join(prof['palette'])}")
    print(f"UI theme        : {prof['ui']}      density: {prof['density']} (score {prof['density_score']})")
    templates = list_templates()
    cat = a.category or P.infer_category(f"{a.app or ''} {a.about or ''}")
    name, dim, reasons = P.choose_template(templates, dimension=None if a.dimension in (None, "auto") else a.dimension,
                                           category=cat, bg_hex=a.bg_color, profile=prof, tone=a.tone or "")
    print(f"Category        : {cat or '(unknown; pass --category or --about)'}")
    print(f"Rule suggestion : {name} ({dim.upper()})")
    for r in reasons:
        print(f"   • {r}")


def cmd_sizes(_):
    print("Screenshots (PNG/JPEG, RGB, no alpha, 1–10 per language):")
    for k, d in DEVICES.items():
        print(f"  {k:12s} {d['size'][0]}x{d['size'][1]}   {d['label']}   [{d['asc']}]")
    print("\nLandscape store images (PNG/JPEG, no alpha):")
    for k, b in BANNERS.items():
        print(f"  {k:12s} {b['works_with']}")
    print("\nApp Preview (H.264, 15–30 s, ≤30 fps, ≤500 MB, AAC stereo):")
    for k, (w, h) in PREVIEW_SIZES.items():
        print(f"  {k:12s} {w}x{h} (portrait)  /  {h}x{w} (landscape)")


def cmd_doctor(_):
    ok = lambda b: "✓" if b else "✗"  # noqa: E731
    print(f"Python        {sys.version.split()[0]}")
    for mod in ("PIL", "numpy"):
        try:
            m = __import__(mod)
            print(f"{ok(True)} {mod:12s} {getattr(m, '__version__', '')}")
        except ImportError:
            print(f"{ok(False)} {mod:12s} not installed")
    print(f"{ok(shutil.which('ffmpeg'))} ffmpeg       {'found' if shutil.which('ffmpeg') else 'missing (only needed for preview)'}")
    c = shutil.which("claude")
    ver = subprocess.run([c, "--version"], capture_output=True, text=True).stdout.strip() if c else ""
    print(f"{ok(c)} claude       {ver or 'missing (Claude Code is not installed)'}")
    try:
        import anthropic  # noqa: F401
        sdk = "installed"
    except ImportError:
        sdk = "not installed (optional: pip install anthropic)"
    print(f"{ok(os.environ.get('ANTHROPIC_API_KEY'))} API key      {'ANTHROPIC_API_KEY set' if os.environ.get('ANTHROPIC_API_KEY') else 'ANTHROPIC_API_KEY not set'}; SDK {sdk}")
    try:
        import openai  # noqa: F401
        sdk = "installed"
    except ImportError:
        sdk = "not installed (optional: pip install openai)"
    print(f"{ok(os.environ.get('OPENAI_API_KEY'))} OpenAI       {'OPENAI_API_KEY set' if os.environ.get('OPENAI_API_KEY') else 'OPENAI_API_KEY not set'}; SDK {sdk} (only with --ai openai)")
    m = shutil.which("maestro")
    print(f"{ok(m)} maestro      {'found' if m else 'missing (only needed for capture)'}")
    booted = C.booted_devices()
    print(f"{ok(booted)} devices      {', '.join(booted) if booted else 'no booted simulator or emulator (only needed for capture)'}")
    fonts = list((HERE / "fonts").glob("Inter-*.otf"))
    print(f"{ok(len(fonts) >= 4)} Inter fonts ({len(fonts)} files)")
    print(f"\n--ai auto selects this backend: {'claude' if c else ('api' if os.environ.get('ANTHROPIC_API_KEY') else 'offline')}")


def cmd_install_claude(a):
    src = (HERE / "data" / "claude_command.md").read_text(encoding="utf-8")
    dest_dir = (Path.home() / ".claude" / "commands") if a.global_ else (Path.cwd() / ".claude" / "commands")
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "store-shots.md"
    dest.write_text(src, encoding="utf-8")
    print(f"Wrote: {dest}\nIn Claude Code:  /store-shots <folder> \"App name\" \"what it does\" [template|2d|3d]")


def cmd_preview(a):
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        raise SystemExit("ffmpeg/ffprobe not found. macOS: brew install ffmpeg")
    w, h = PREVIEW_SIZES[a.device]
    if a.landscape:
        w, h = h, w
    src = a.input
    probe = lambda args: subprocess.run(["ffprobe", "-v", "error", "-protocol_whitelist", "file", *args, src], capture_output=True, text=True).stdout.strip()  # noqa: E731
    dur = float(probe(["-show_entries", "format=duration", "-of", "csv=p=0"]) or 0)
    has_audio = bool(probe(["-select_streams", "a", "-show_entries", "stream=codec_type", "-of", "csv=p=0"]))
    if a.fit == "cover":
        vf = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
    else:
        vf = f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color={a.pad_color}"
    vf += ",fps=30,format=yuv420p"
    if dur and dur < 15:
        if a.extend:
            vf += f",tpad=stop_mode=clone:stop_duration={15 - dur + 0.2:.2f}"
        else:
            print(f"WARNING: the video is {dur:.1f} s; App Store needs at least 15 s (--extend holds the last frame).", file=sys.stderr)
    if dur > 30:
        print(f"WARNING: the video is {dur:.1f} s; only the first 30 s are used.", file=sys.stderr)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-protocol_whitelist", "file", "-i", src]
    if not has_audio:
        cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
    cmd += ["-t", "30", "-vf", vf, "-c:v", "libx264", "-profile:v", "high", "-level", "4.0",
            "-b:v", "11M", "-maxrate", "12M", "-bufsize", "24M", "-c:a", "aac", "-b:a", "256k", "-ar", "44100", "-ac", "2"]
    cmd += ["-map", "0:v:0"] + (["-map", "0:a:0", "-af", "apad"] if has_audio else ["-map", "1:a:0"])
    cmd += ["-shortest", "-movflags", "+faststart", a.out]
    subprocess.run(cmd, check=True)
    print(f"Ready: {a.out} ({w}x{h}, 30 fps, H.264/AAC)")


# --------------------------------------------------------------------------- #
# Argümanlar
# --------------------------------------------------------------------------- #
def add_render_args(p):
    p.add_argument("--langs", default="en,tr", help="comma-separated languages (default: en,tr)")
    p.add_argument("--copy", default="shotforge.copy.json", help="design and copy file (created or read)")
    p.add_argument("-o", "--out", default="out")
    p.add_argument("--device", default=None, choices=DEVICES,
                   help="one device (old layout); otherwise the store targets are used, or iphone-6.9 without them")
    p.add_argument("--layout", default=None, choices=["flat", "asc", "store", "fastlane"],
                   help="store: out/<lang>/<store>/<target>/… | flat: out/<lang>/NN_name.png | asc: out/<lang>/<DISPLAY_TYPE>/NN_name.png | "
                        "fastlane: out/fastlane/screenshots/<locale>/… and out/fastlane/metadata/android/<locale>/images/…")
    p.add_argument("--icon", default=None, metavar="FILE", help="app icon for the header and feature graphic")
    p.add_argument("--format", default="png", choices=["png", "jpg"])
    p.add_argument("--jobs", type=int, default=max(1, min(3, os.cpu_count() or 1)), help="number of parallel processes")
    p.add_argument("--style", default=None, help="override the template layout: " + " | ".join(["alt", *STYLES]))
    p.add_argument("--bg", default=None, help="(advanced) raw background: preset, '#a,#b' gradient or image path; disables the colour palette")
    p.add_argument("--frame", default=None, help="device colour: %s or #hex" % ", ".join(FRAMES))
    p.add_argument("--text-position", default=None, choices=["top", "bottom"])
    p.add_argument("--align", default=None, choices=["center", "left"])
    p.add_argument("--font", default=None)
    p.add_argument("--logo", default=None, metavar="FILE", help="small logo on every slide, in the free band beside the headline")
    p.add_argument("--perspective", type=float, default=None)
    p.add_argument("--no-grain", action="store_true")
    p.add_argument("--only", default=None, help="only these slides, e.g. 1,3 (for quick tests)")
    p.add_argument("--sheet", action="store_true",
                   help="also write ONE review image with all slides side by side "
                        "(out/_review/overview_<lang>.jpg; not a deliverable)")
    p.add_argument("--brief", action="store_true", help="also write a Claude Design brief (md)")


def add_brand_args(p):
    p.add_argument("--brand", default=None, metavar="FILE", help="brand file (default: shotforge.toml in this folder or a parent, up to the repository root)")
    p.add_argument("--no-brand", action="store_true", help="ignore shotforge.toml")


def add_design_args(p):
    g = p.add_argument_group("design inputs (the AI or the rules decide anything left out)")
    g.add_argument("--template", default="auto", help="auto | template name | .json path (see shotforge templates)")
    g.add_argument("--dimension", default="auto", choices=["auto", "2d", "3d"], help="template family; auto = the rules or the AI choose")
    g.add_argument("--bg-color", default=None, metavar="#HEX", help="main background colour")
    g.add_argument("--text-color", default=None, metavar="#HEX", help="text colour (corrected per slide, with a warning, if unreadable)")
    g.add_argument("--accent", default=None, metavar="#HEX", help="accent colour")
    g.add_argument("--count", default="auto", type=count_arg, help="number of pages (slides) 1–10 | auto (6–7)")
    g.add_argument("--texts", default=None, metavar="FILE", help="your own text (.txt/.md/.json); the AI completes what is missing")
    g.add_argument("--category", default=None, choices=P.CATEGORIES, help="app category (inferred if omitted)")
    g.add_argument("--app", default=None, help="app name")
    g.add_argument("--about", default="", help="what the app does (1–3 sentences)")
    g.add_argument("--tone", default=None, help=f"copy tone (default: {DEFAULT_TONE})")
    g.add_argument("--keywords", default=None, metavar="A,B,C", help="search keywords the AI should work into the headlines where they read naturally")
    g.add_argument("--ai", default="auto", choices=["auto", "claude", "api", "openai", "offline"])
    g.add_argument("--model", default=None, help="model name for the chosen backend (optional)")
    g.add_argument("--regen", action="store_true", help="create the copy file again even if it exists")
    g.add_argument("--ask", action="store_true", help="ask the questions (default in a terminal when input is missing)")
    g.add_argument("--no-ask", action="store_true", help="never ask questions")
    g.add_argument("-y", "--yes", action="store_true", help="same as --no-ask, and skip the confirmation")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="shotforge", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("create", help="design decision + copy (AI) + separate images per page and language, in one command")
    c.add_argument("shots", nargs="+", help="screenshot files or a folder")
    add_design_args(c)
    add_render_args(c)
    add_brand_args(c)
    g = c.add_argument_group("store targets (anything left out uses the default)")
    g.add_argument("--store", default=None, help="appstore | playstore | both (default: both)")
    g.add_argument("--header-sizes", default=None, help=f"{','.join(I.HEADER_SIZES)} or no (default: all)")
    g.add_argument("--devices", default=None, help=f"comma-separated: {', '.join(DEVICES)} (default: iphone-6.9, android-phone)")
    g.add_argument("--preview", action="append", default=None, metavar="[LANG=]FILE",
                   help="screen recording to convert to an App Preview; without a language it applies to all (repeatable)")
    g.add_argument("--zip", action=argparse.BooleanOptionalAction, default=None, help="also create a zip (default: yes)")
    g.add_argument("--zip-only", action="store_true", help="keep the images only inside the zip")
    g = c.add_argument_group("store tests")
    g.add_argument("--variants", type=int, default=1, choices=[1, 2, 3], help="write 2 or 3 complete sets to <out>/variant-a, variant-b, …")
    g.add_argument("--variant-by", default="both", choices=["copy", "design", "both"],
                   help="what differs between variants; design uses a different template, also when --template sets variant A's")
    g = c.add_argument_group("capture screenshots first")
    g.add_argument("--maestro", default=None, metavar="FLOWS", help="run these Maestro flows per language and write into the input folder")
    g.add_argument("--capture-platform", default=None, choices=C.SHOT_PLATFORMS, help="write to <folder>/<platform>/<lang>/ instead of <folder>/<lang>/")
    g.add_argument("--capture-device", default=None, metavar="ID", help="device ID for Maestro (default: its own choice)")
    c.set_defaults(fn=cmd_create, langs=None, out=None, copy=None)

    cp = sub.add_parser("capture", help="take screenshots per language by running your Maestro flows on a booted device")
    cp.add_argument("--flows", required=True, help="Maestro flow file or folder; each takeScreenshot is one page, and ${LANG} holds the language")
    cp.add_argument("--langs", default="en,tr", help="comma-separated languages (default: en,tr)")
    cp.add_argument("--platform", default=None, choices=C.SHOT_PLATFORMS, help="write to <out>/<platform>/<lang>/ instead of <out>/<lang>/")
    cp.add_argument("--device", default=None, metavar="ID", help="device ID for Maestro")
    cp.add_argument("--timeout", type=int, default=C.DEFAULT_TIMEOUT, help="seconds per language")
    cp.add_argument("-o", "--out", default="shots")
    cp.set_defaults(fn=cmd_capture)

    pl = sub.add_parser("plan", help="create only the design decisions and copy (shotforge.copy.json)")
    pl.add_argument("shots", nargs="+")
    add_design_args(pl)
    add_brand_args(pl)
    pl.add_argument("--langs", default=None, help="comma-separated languages (default: en,tr)")
    pl.add_argument("--copy", default="shotforge.copy.json")
    pl.set_defaults(fn=cmd_plan_only)

    r = sub.add_parser("render", help="redraw from an existing copy file")
    r.add_argument("shots", nargs="*", help="(optional) replace the screenshots")
    r.add_argument("--template", default=None, help="override the template in the file")
    r.add_argument("--bg-color", default=None, metavar="#HEX")
    r.add_argument("--text-color", default=None, metavar="#HEX")
    r.add_argument("--accent", default=None, metavar="#HEX")
    add_render_args(r)
    add_brand_args(r)
    r.set_defaults(fn=lambda a: render_and_package(a), langs=None)  # verilmezse copy dosyasındaki diller

    an = sub.add_parser("analyze", help="analyse screenshots and show the rule-based 2D/3D template suggestion (no AI)")
    an.add_argument("shots", nargs="+")
    an.add_argument("--category", default=None, choices=P.CATEGORIES)
    an.add_argument("--dimension", default="auto", choices=["auto", "2d", "3d"])
    an.add_argument("--bg-color", default=None)
    an.add_argument("--app", default=None)
    an.add_argument("--about", default="")
    an.add_argument("--tone", default="")
    an.set_defaults(fn=cmd_analyze)

    ru = sub.add_parser("rules", help="show the 2D/3D and template selection rules")
    ru.set_defaults(fn=cmd_rules)

    t = sub.add_parser("templates", help="list templates by 2D and 3D")
    t.add_argument("--show", help="show a template's JSON")
    t.set_defaults(fn=cmd_templates)

    s = sub.add_parser("sizes", help="show store image sizes")
    s.set_defaults(fn=cmd_sizes)

    d = sub.add_parser("doctor", help="check the installation and dependencies")
    d.set_defaults(fn=cmd_doctor)

    ic = sub.add_parser("install-claude", help="install the /store-shots slash command for Claude Code")
    ic.add_argument("--global", dest="global_", action="store_true", help="~/.claude/commands (all projects)")
    ic.set_defaults(fn=cmd_install_claude)

    pv = sub.add_parser("preview", help="convert a screen recording to App Preview specs (ffmpeg)")
    pv.add_argument("input")
    pv.add_argument("-o", "--out", default="preview.mp4")
    pv.add_argument("--device", default="iphone-6.9", choices=PREVIEW_SIZES)
    pv.add_argument("--landscape", action="store_true")
    pv.add_argument("--fit", default="cover", choices=["cover", "contain"])
    pv.add_argument("--pad-color", default="black")
    pv.add_argument("--extend", action="store_true")
    pv.set_defaults(fn=cmd_preview)

    a = ap.parse_args(argv)
    try:
        if hasattr(a, "brand"):
            apply_brand(a)
        a.fn(a)
    except subprocess.TimeoutExpired:
        raise SystemExit("Error: claude timed out (600 s).")
    except (RuntimeError, ValueError, OSError, subprocess.CalledProcessError) as e:
        raise SystemExit(f"Error: {e}")


if __name__ == "__main__":
    main()
