"""shotforge — App Store ekran görüntüsü + App Preview CLI (Claude ile tasarım kararı ve metin, 2D/3D şablonlarla render)."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from . import copywriter, palette as P, texts as T
from .engine import BG_PRESETS, DEVICES, FRAMES, PREVIEW_SIZES, STYLES, render_job, text_problems

HERE = Path(__file__).resolve().parent
IMG_EXT = {".png", ".jpg", ".jpeg", ".webp"}
# copy dosyasında slayt başına değiştirilebilen alanlar; yol alan font/image gibi alanlar bilerek yok
SLIDE_OVERRIDES = {"style", "frame", "text_position", "align", "bg_angle", "title_scale", "width", "perspective",
                   "no_grain", "no_glow", "text_color", "accent", "glow", "deco", "frame_rim"}
ASC_LOCALES = {"en": "en-US", "tr": "tr", "de": "de-DE", "fr": "fr-FR", "es": "es-ES", "it": "it", "pt": "pt-BR",
               "ja": "ja", "ko": "ko", "ru": "ru", "nl": "nl-NL", "ar": "ar-SA"}


# --------------------------------------------------------------------------- #
# Şablonlar
# --------------------------------------------------------------------------- #
# Eski şablon adları (geriye uyumluluk): eski copy dosyaları ve komutlar çalışmaya devam eder
TEMPLATE_ALIASES = {"kova": "bubble", "pulse": "heartbeat", "inner-glow": "halo", "astra": "slant"}


def canon_template(name: str | None) -> str | None:
    return TEMPLATE_ALIASES.get(name, name) if name else name


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
        raise SystemExit(f"Şablon yok: {name}. Mevcut: {', '.join(t)}  (kendi .json dosyanızın yolunu da verebilirsiniz)")
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
            raise SystemExit(f"Bulunamadı: {i}")
    if not files:
        raise SystemExit("Hiç ekran görüntüsü bulunamadı.")
    out: dict[str, Path] = {}
    for f in files:
        if f.name in out:
            raise SystemExit(f"Aynı ada sahip iki dosya var: {f.name}")
        out[f.name] = f
    return out


def check_langs(langs: list[str]):
    """Dil kodları klasör adı olur; yol içeremez."""
    for l in langs:
        if not isinstance(l, str) or not T.LANG_RE.fullmatch(l):
            raise SystemExit(f"Geçersiz dil kodu: {l!r}  (örnek: en, tr, pt-BR)")


def count_arg(v: str) -> str:
    if v != "auto" and not (v.isdigit() and 1 <= int(v) <= 10):
        raise argparse.ArgumentTypeError("1–10 arası bir sayı ya da auto olmalı")
    return v


def read_copy(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SystemExit(f"Tasarım/metin dosyası geçerli JSON değil: {path} ({e})")


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
    hint = f" [öneri: {suggestion}]" if suggestion else ""
    for _ in range(2):
        v = _ask(f"{label} (#hex; Enter = otomatik){hint}: ")
        if not v or v.lower() in ("otomatik", "auto", "a"):
            return None
        if P.is_hex(v):
            return "#" + v.lstrip("#")
        print("  Geçersiz renk; örnek: #6D28D9")
    print("  Otomatik seçilecek.")
    return None


def run_interactive(inp: dict, shots: dict[str, Path], profile: dict, templates: dict, langs: list[str]):
    print("\n── Tasarım soruları ─────────────────────────────────────────────")
    print("Ekran görüntülerinden algılanan: ana renk %s, ekran teması %s, yoğunluk %s" %
          (profile.get("brand") or "nötr", profile["ui"], profile["density"]))
    print("Her soruda Enter = otomatik (Claude / kurallar karar verir).\n")

    if not inp.get("app"):
        inp["app"] = _ask("Uygulama adı: ") or "App"
    if not inp.get("about"):
        inp["about"] = _ask("Uygulama ne yapar? (1–2 cümle; Enter = ekranlardan çıkarsansın): ")

    if not inp.get("template") and not inp.get("dimension"):
        v = _ask("Şablon ailesi — 2d / 3d / otomatik (Enter = otomatik): ").lower()
        if v in ("2d", "3d"):
            inp["dimension"] = v
    if not inp.get("template"):
        pool = [n for n, t in templates.items() if not inp.get("dimension") or t["dimension"] == inp["dimension"]]
        v = _ask(f"Şablon — otomatik veya: {', '.join(pool)} (Enter = otomatik): ").lower()
        if v in templates:
            inp["template"] = v

    c = inp["colors"]
    if not c.get("background"):
        c["background"] = _ask_color("1) Ana arka plan rengi", profile.get("brand"))
    if not c.get("text"):
        c["text"] = _ask_color("2) Yazı rengi")
    if not c.get("accent"):
        c["accent"] = _ask_color("   Vurgu rengi")
    inp["colors"] = {k: v for k, v in c.items() if v}

    if not inp.get("count") and not inp.get("locked"):
        v = _ask("3) Kaç sayfa (slayt)? 1–10 (Enter = otomatik, 6–7): ")
        if v.isdigit() and 1 <= int(v) <= 10:
            inp["count"] = int(v)

    if not inp.get("locked"):
        v = _ask("4) Sayfa metinleri — [1] Claude hazırlasın  [2] Ben yazacağım  [3] Dosyadan  (Enter = 1): ")
        if v == "3":
            path = _ask("   Metin dosyası yolu (.txt/.md/.json): ")
            if path:
                inp["locked"] = T.parse_text_file(path)
        elif v == "2":
            n = inp.get("count")
            if not n:
                v = _ask("   Kaç sayfa yazacaksınız? (1–10): ", "7")
                inp["count"] = max(1, min(10, int(v))) if v.isdigit() else 7
            print("   Biçim: 'Başlık | Alt başlık'. *kelime* = vurgu. Boş bırakırsanız o dil için Claude yazar.")
            print("   Ekran görüntüleri: " + ", ".join(shots))
            locked = []
            for i in range(inp["count"]):
                s = {"title": {}, "subtitle": {}}
                sh = _ask(f"   Sayfa {i + 1} — hangi ekran görüntüsü? (Enter = otomatik): ")
                if sh in shots:
                    s["shot"] = sh
                for l in langs:
                    v = _ask(f"   Sayfa {i + 1} [{l}] metin: ")
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
            raise SystemExit(f"Geçersiz renk ({k}): {v}  (örnek: #6D28D9)")
    if inp["template"] and inp["template"] not in templates and not Path(inp["template"]).exists():
        raise SystemExit(f"Şablon yok: {inp['template']}. Mevcut: {', '.join(templates)}")
    ask = True if a.ask else (False if (a.no_ask or a.yes) else sys.stdin.isatty())
    if ask:
        run_interactive(inp, shots, profile, templates, langs)
    if not inp["app"]:
        raise SystemExit("Uygulama adı gerekli: --app \"Ad\" (ya da etkileşimli modda sorulur).")
    if inp["count"] is None and inp["locked"]:
        inp["count"] = len(inp["locked"])  # kullanıcı kaç sayfa yazdıysa o kadar
    for i, s in enumerate(inp["locked"]):
        if s.get("shot") and s["shot"] not in shots:
            raise SystemExit(f"Metin dosyasında slayt {i + 1}: '{s['shot']}' ekran görüntüleri arasında yok ({', '.join(shots)})")
    if inp["template"] and inp["dimension"]:
        t = templates.get(inp["template"])
        if t and t["dimension"] != inp["dimension"]:
            print(f"UYARI: --template {inp['template']} {t['dimension'].upper()}; --dimension {inp['dimension']} yok sayıldı.", file=sys.stderr)
            inp["dimension"] = None
    return inp


def cmd_plan(a, quiet=False) -> dict:
    shots = collect_shots(a.shots)
    langs = [l.strip() for l in a.langs.split(",") if l.strip()]
    check_langs(langs)
    copy_path = Path(a.copy)
    if copy_path.exists() and not a.regen:
        if not quiet:
            print(f"Mevcut tasarım/metin dosyası kullanılıyor: {copy_path}  (yeniden üretmek için --regen)")
        data = read_copy(copy_path)
        if (a.app and a.app != data.get("app")) or set(shots) != set(data.get("shots") or {}):
            print(f"UYARI: {copy_path} başka bir uygulamaya veya ekran görüntülerine ait; "
                  "yeni girdiler kullanılmıyor (--regen ile yeniden üretin).", file=sys.stderr)
        return data

    templates = list_templates()
    profile = P.analyze_shots(list(shots.values()))
    inp = gather_inputs(a, shots, langs, templates, profile)
    if inp["template"] and inp["template"] not in templates:  # --template my.json
        templates[inp["template"]] = load_template(inp["template"])
    ctx = dict(app=inp["app"], about=inp["about"], tone=a.tone, langs=langs, templates=templates, profile=profile,
               locked=inp["locked"],
               fixed=dict(colors=inp["colors"], count=inp["count"], template=inp["template"],
                          dimension=inp["dimension"], category=inp["category"]))
    if (inp["count"] or 0) > 10:
        print("UYARI: App Store dil başına en fazla 10 ekran görüntüsü kabul eder.", file=sys.stderr)
    miss = T.missing(inp["locked"], inp["count"] or len(inp["locked"]), langs) if inp["locked"] else []
    if miss:
        print("Kullanıcı metninde eksikler (Claude tamamlayacak): " + "; ".join(miss[:6]) + (" …" if len(miss) > 6 else ""))
    print(f"Tasarım kararları ve metinler hazırlanıyor ({', '.join(langs)}) …")
    data, used = copywriter.plan(ctx, shots, backend=a.ai, model=a.model)
    d = data["design"]
    out = dict(app=inp["app"], about=inp["about"], tone=a.tone, langs=langs, generated_with=used,
               design=d, shots={n: rel(p, copy_path.parent) for n, p in shots.items()}, slides=data["slides"])
    write_copy(copy_path, out)
    print_design(d)
    print(f"Yazıldı: {copy_path}  (düzenleyip `shotforge render` ile yeniden çizebilirsiniz)")
    if (True if a.ask else (False if (a.no_ask or a.yes) else sys.stdin.isatty())):
        if _ask("Bu tasarımla devam edilsin mi? [E/h]: ", "e").lower().startswith("h"):
            raise SystemExit(f"Durduruldu. {copy_path} dosyasını düzenleyip `shotforge render` çalıştırabilirsiniz.")
    return out


def print_design(d: dict):
    c = d.get("colors") or {}
    print("\nTasarım kararı:")
    print(f"  şablon     : {d['template']}  ({d['dimension'].upper()})   — {d.get('template_reason', '')}")
    print(f"  kategori   : {d.get('category') or '?'}")
    print(f"  sayfa      : {d['count']}")
    print(f"  arka plan  : {c.get('background') or '(şablon varsayılanı)'}   yazı: {c.get('text') or '(otomatik)'}   vurgu: {c.get('accent') or '(otomatik)'}")


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
    for k in ("frame", "text_position", "align", "font"):
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
            warnings.append((idx + 1, f"copy dosyasındaki '{k}' alanı yok sayıldı."))
    if cfg["style"] == "alt":
        cfg["style"] = "tilt-right" if idx % 2 == 0 else "tilt-left"
    if cfg["style"] not in STYLES:
        raise SystemExit(f"Bilinmeyen stil: {cfg['style']} ({', '.join(STYLES)})")
    shot = slide["shot"]
    cfg["image"] = str(shot_paths[shot])
    if cfg["style"] in ("duo", "duo-flat"):
        others = [n for n in names if n != shot]
        cfg["image2"] = str(shot_paths[others[idx % len(others)] if others else shot])
    cfg["title"], cfg["subtitle"] = slide.get("title"), slide.get("subtitle")
    return cfg


def cmd_render(a, data: dict | None = None):
    copy_path = Path(a.copy)
    if data is None:
        if not copy_path.exists():
            raise SystemExit(f"Tasarım/metin dosyası yok: {copy_path}. Önce `shotforge plan` veya `shotforge create` çalıştırın.")
        data = read_copy(copy_path)
    design = data.get("design") or {"template": data.get("template", "classic"), "colors": {}}
    tname = canon_template(a.template if a.template and a.template != "auto" else design.get("template", "classic"))
    tpl = load_template(tname)
    colors = dict(design.get("colors") or {})
    for flag, key in (("bg_color", "background"), ("text_color", "text"), ("accent", "accent")):
        if getattr(a, flag, None):
            colors[key] = getattr(a, flag)
    try:
        pal = P.build_palette(tpl, colors)
    except ValueError as e:
        raise SystemExit(str(e))
    langs = [l.strip() for l in a.langs.split(",")] if getattr(a, "langs", None) else data["langs"]
    check_langs(langs)
    shot_paths = {n: (copy_path.parent / p) for n, p in data["shots"].items()}
    base = copy_path.parent.resolve()
    outside = [n for n, p in shot_paths.items() if not p.resolve().is_relative_to(base)]
    if outside:
        print(f"UYARI: {copy_path} kendi klasörü dışındaki görselleri kullanıyor: {', '.join(outside)}", file=sys.stderr)
    if getattr(a, "shots", None):
        shot_paths.update(collect_shots(a.shots))
    for p in shot_paths.values():
        if not Path(p).exists():
            raise SystemExit(f"Ekran görüntüsü bulunamadı: {p}")
    names = list(shot_paths)
    slides = data["slides"]
    only = {int(x) for x in a.only.split(",")} if getattr(a, "only", None) else None
    out = Path(a.out)
    warnings: list = []
    cfgs = [slide_cfg(a, tpl, pal, i, s, shot_paths, names, warnings) for i, s in enumerate(slides)]
    for lang in langs:
        for i, cfg in enumerate(cfgs):
            if not only or (i + 1) in only:
                warnings += [(i + 1, f"[{lang}] {m}") for m in text_problems(cfg, lang)]
    grouped: dict[str, list[int]] = {}
    for n, msg in warnings:
        grouped.setdefault(msg, []).append(n)
    a.warnings = [f"slayt {', '.join(map(str, nums))}: {msg}" for msg, nums in grouped.items()]
    for w in a.warnings:
        print(f"UYARI: {w}", file=sys.stderr)
    jobs = []
    for lang in langs:
        for idx, s in enumerate(slides):
            if only and (idx + 1) not in only:
                continue
            slug = Path(s["shot"]).stem.lstrip("0123456789_- ") or "screen"
            fname = f"{idx + 1:02d}_{slug}.{'jpg' if a.format == 'jpg' else 'png'}"
            if a.layout == "asc":
                d = out / ASC_LOCALES.get(lang, lang) / DEVICES[a.device]["asc"]
            else:
                d = out / lang
            jobs.append(dict(cfg=cfgs[idx], locale=lang, idx=idx, total=len(slides), path=str(d / fname), fmt=a.format))
    print(f"Çiziliyor: {len(jobs)} görsel, şablon '{tpl['name']}' ({tpl.get('dimension', '?').upper()}), {a.jobs} süreç …")
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
    W, H = DEVICES[a.device]["size"]
    for lang, ps in paths_by_lang.items():
        print(f"{lang}: {len(ps)} AYRI görsel (her biri tek başına {W}x{H}):")
        for p in sorted(ps):
            print(f"   {p}")
    if a.sheet:
        for lang, ps in paths_by_lang.items():
            make_sheet(sorted(ps), out / "_review" / f"overview_{lang}.jpg")
    if a.brief:
        write_brief(out / "_review" / "claude-design-brief.md", data, tpl, langs, a.device, colors)
    return paths_by_lang


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
    print(f"Kontrol önizlemesi (teslim dosyası değil): {dest}")


def pick(v, lang):
    if isinstance(v, dict):
        return v.get(lang) or next(iter(v.values()), "")
    return v or ""


def write_brief(dest: Path, data: dict, tpl: dict, langs: list[str], device: str, colors: dict):
    W, H = DEVICES[device]["size"]
    d = data.get("design") or {}
    L = [f"# Claude Design brief — {data.get('app', '')}", "",
         f"Şablon: **{tpl['name']}** ({tpl.get('dimension', '?').upper()}) — {tpl.get('description', '')}",
         f"Renkler: arka plan {colors.get('background') or 'şablon varsayılanı'}, yazı {colors.get('text') or 'otomatik'}, vurgu {colors.get('accent') or 'otomatik'}",
         (f"İlham: {tpl['inspired_by']}" if tpl.get("inspired_by") else ""), "",
         "Bu dosya, aynı slaytları **Claude Design**'da elle iterasyonla üretmek için hazırlandı "
         "(Claude Design tarayıcı tabanlıdır; shotforge onu komutla sürmez). Her dil için bloğu kopyalayın, "
         "ilgili ekran görüntülerini ekleyip gönderin.", ""]
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
    data = cmd_plan(a, quiet=False)
    cmd_render(a, data)


# --------------------------------------------------------------------------- #
# Diğer komutlar
# --------------------------------------------------------------------------- #
def cmd_templates(a):
    t = list_templates()
    if a.show:
        print(json.dumps(load_template(a.show), ensure_ascii=False, indent=2))
        return
    for dim in ("2d", "3d"):
        print(f"── {dim.upper()} " + ("(telefon düz / düzlem içinde döndürülmüş)" if dim == "2d" else "(perspektifli, kalınlığı görünen eğik telefon)"))
        for k, v in t.items():
            if v.get("dimension") == dim:
                print(f"  {k:11s} [{v.get('theme', '?')}] {v['description'].split('· ', 1)[-1]}")
                print(f"  {'':11s} uygun: {', '.join(v.get('best_for', []))}" + (f"   ↳ {v['inspired_by']}" if v.get("inspired_by") else ""))


def cmd_rules(_):
    print(P.RULES_TEXT)


def cmd_analyze(a):
    shots = collect_shots(a.shots)
    prof = P.analyze_shots(list(shots.values()))
    print(f"Ekran görüntüsü : {prof['count']} dosya")
    print(f"Ana renk (marka): {prof['brand'] or 'nötr / bulunamadı'}   palet: {', '.join(prof['palette'])}")
    print(f"Ekran teması    : {prof['ui']}      yoğunluk: {prof['density']} (puan {prof['density_score']})")
    templates = list_templates()
    cat = a.category or P.infer_category(f"{a.app or ''} {a.about or ''}")
    name, dim, reasons = P.choose_template(templates, dimension=None if a.dimension in (None, "auto") else a.dimension,
                                           category=cat, bg_hex=a.bg_color, profile=prof, tone=a.tone or "")
    print(f"Kategori        : {cat or '(belirsiz; --category veya --about verin)'}")
    print(f"Kural önerisi   : {name} ({dim.upper()})")
    for r in reasons:
        print(f"   • {r}")


def cmd_sizes(_):
    print("Ekran görüntüsü (PNG/JPEG, RGB, alfa yok, dil başına 1–10):")
    for k, d in DEVICES.items():
        print(f"  {k:12s} {d['size'][0]}x{d['size'][1]}   {d['label']}   [{d['asc']}]")
    print("\nApp Preview (H.264, 15–30 sn, ≤30 fps, ≤500 MB, AAC stereo):")
    for k, (w, h) in PREVIEW_SIZES.items():
        print(f"  {k:12s} {w}x{h} (portre)  /  {h}x{w} (yatay)")


def cmd_doctor(_):
    ok = lambda b: "✓" if b else "✗"  # noqa: E731
    print(f"Python        {sys.version.split()[0]}")
    for mod in ("PIL", "numpy"):
        try:
            m = __import__(mod)
            print(f"{ok(True)} {mod:12s} {getattr(m, '__version__', '')}")
        except ImportError:
            print(f"{ok(False)} {mod:12s} kurulu değil")
    print(f"{ok(shutil.which('ffmpeg'))} ffmpeg       {'var' if shutil.which('ffmpeg') else 'yok (yalnızca preview için gerekli)'}")
    c = shutil.which("claude")
    ver = subprocess.run([c, "--version"], capture_output=True, text=True).stdout.strip() if c else ""
    print(f"{ok(c)} claude       {ver or 'yok (Claude Code kurulu değil)'}")
    try:
        import anthropic  # noqa: F401
        sdk = "kurulu"
    except ImportError:
        sdk = "kurulu değil (isteğe bağlı: pip install anthropic)"
    print(f"{ok(os.environ.get('ANTHROPIC_API_KEY'))} API anahtarı {'ANTHROPIC_API_KEY tanımlı' if os.environ.get('ANTHROPIC_API_KEY') else 'ANTHROPIC_API_KEY yok'}; SDK {sdk}")
    fonts = list((HERE / "fonts").glob("Inter-*.otf"))
    print(f"{ok(len(fonts) >= 4)} Inter fontları ({len(fonts)} dosya)")
    print(f"\n--ai auto şu backend'i seçer: {'claude' if c else ('api' if os.environ.get('ANTHROPIC_API_KEY') else 'offline')}")


def cmd_install_claude(a):
    src = (HERE / "data" / "claude_command.md").read_text(encoding="utf-8")
    dest_dir = (Path.home() / ".claude" / "commands") if a.global_ else (Path.cwd() / ".claude" / "commands")
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "store-shots.md"
    dest.write_text(src, encoding="utf-8")
    print(f"Yazıldı: {dest}\nClaude Code içinde:  /store-shots <klasör> \"Uygulama adı\" \"ne yapar\" [şablon|2d|3d]")


def cmd_preview(a):
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        raise SystemExit("ffmpeg/ffprobe bulunamadı. macOS: brew install ffmpeg")
    w, h = PREVIEW_SIZES[a.device]
    if a.landscape:
        w, h = h, w
    src = a.input
    probe = lambda args: subprocess.run(["ffprobe", "-v", "error", *args, src], capture_output=True, text=True).stdout.strip()  # noqa: E731
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
            print(f"UYARI: video {dur:.1f} sn; App Store en az 15 sn ister (--extend son kareyi uzatır).", file=sys.stderr)
    if dur > 30:
        print(f"UYARI: video {dur:.1f} sn; ilk 30 sn alınacak.", file=sys.stderr)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", src]
    if not has_audio:
        cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
    cmd += ["-t", "30", "-vf", vf, "-c:v", "libx264", "-profile:v", "high", "-level", "4.0",
            "-b:v", "11M", "-maxrate", "12M", "-bufsize", "24M", "-c:a", "aac", "-b:a", "256k", "-ar", "44100", "-ac", "2"]
    cmd += ["-map", "0:v:0"] + (["-map", "0:a:0", "-af", "apad"] if has_audio else ["-map", "1:a:0"])
    cmd += ["-shortest", "-movflags", "+faststart", a.out]
    subprocess.run(cmd, check=True)
    print(f"Hazır: {a.out} ({w}x{h}, 30 fps, H.264/AAC)")


# --------------------------------------------------------------------------- #
# Argümanlar
# --------------------------------------------------------------------------- #
def add_render_args(p):
    p.add_argument("--langs", default="en,tr", help="virgülle ayrılmış diller (varsayılan: en,tr)")
    p.add_argument("--copy", default="shotforge.copy.json", help="tasarım + metin dosyası (üretilir/okunur)")
    p.add_argument("-o", "--out", default="out")
    p.add_argument("--device", default="iphone-6.9", choices=DEVICES)
    p.add_argument("--layout", default="flat", choices=["flat", "asc"], help="asc: out/<dil>/<DISPLAY_TYPE>/NN_ad.png")
    p.add_argument("--format", default="png", choices=["png", "jpg"])
    p.add_argument("--jobs", type=int, default=max(1, min(3, os.cpu_count() or 1)), help="paralel süreç sayısı")
    p.add_argument("--style", default=None, help="şablon düzenini geçersiz kıl: " + " | ".join(["alt", *STYLES]))
    p.add_argument("--bg", default=None, help="(ileri) ham arka plan: preset, '#a,#b' degrade veya görsel yolu; renk paletini devre dışı bırakır")
    p.add_argument("--frame", default=None, help="cihaz rengi: %s ya da #hex" % ", ".join(FRAMES))
    p.add_argument("--text-position", default=None, choices=["top", "bottom"])
    p.add_argument("--align", default=None, choices=["center", "left"])
    p.add_argument("--font", default=None)
    p.add_argument("--perspective", type=float, default=None)
    p.add_argument("--no-grain", action="store_true")
    p.add_argument("--only", default=None, help="yalnızca bu slaytlar, ör. 1,3 (hızlı deneme)")
    p.add_argument("--sheet", action="store_true",
                   help="yalnızca gözle kontrol için tüm slaytları yan yana gösteren TEK bir önizleme görseli de yaz "
                        "(out/_review/overview_<dil>.jpg; teslim dosyası değildir)")
    p.add_argument("--brief", action="store_true", help="Claude Design brief (md) de yaz")


def add_design_args(p):
    g = p.add_argument_group("tasarım girdileri (verilmeyen her şeyi Claude / kurallar belirler)")
    g.add_argument("--template", default="auto", help="auto | şablon adı | .json yolu (shotforge templates)")
    g.add_argument("--dimension", default="auto", choices=["auto", "2d", "3d"], help="şablon ailesi; auto = kurallar/Claude seçer")
    g.add_argument("--bg-color", default=None, metavar="#HEX", help="ana arka plan rengi")
    g.add_argument("--text-color", default=None, metavar="#HEX", help="yazı rengi (okunaksızsa slayt başına uyarıyla düzeltilir)")
    g.add_argument("--accent", default=None, metavar="#HEX", help="vurgu rengi")
    g.add_argument("--count", default="auto", type=count_arg, help="sayfa (slayt) sayısı 1–10 | auto (6–7)")
    g.add_argument("--texts", default=None, metavar="DOSYA", help="kendi metinleriniz (.txt/.md/.json); eksikleri Claude tamamlar")
    g.add_argument("--category", default=None, choices=P.CATEGORIES, help="uygulama kategorisi (yoksa çıkarılır)")
    g.add_argument("--app", default=None, help="uygulama adı")
    g.add_argument("--about", default="", help="uygulama ne yapar (1–3 cümle)")
    g.add_argument("--tone", default="confident, friendly, benefit-first", help="metin tonu")
    g.add_argument("--ai", default="auto", choices=["auto", "claude", "api", "offline"])
    g.add_argument("--model", default=None, help="Claude model adı (isteğe bağlı)")
    g.add_argument("--regen", action="store_true", help="tasarım/metin dosyası olsa bile yeniden üret")
    g.add_argument("--ask", action="store_true", help="soruları sor (terminalde varsayılan: eksik girdi varsa sorar)")
    g.add_argument("--no-ask", action="store_true", help="hiç soru sorma")
    g.add_argument("-y", "--yes", action="store_true", help="--no-ask ile aynı; onay da sorma")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="shotforge", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("create", help="tasarım kararı + metin (Claude) + sayfa sayısı kadar AYRI görsel x diller — tek komut")
    c.add_argument("shots", nargs="+", help="ekran görüntüsü dosyaları veya klasör")
    add_design_args(c)
    add_render_args(c)
    c.set_defaults(fn=cmd_create)

    pl = sub.add_parser("plan", help="yalnızca tasarım kararlarını ve metinleri üret (shotforge.copy.json)")
    pl.add_argument("shots", nargs="+")
    add_design_args(pl)
    pl.add_argument("--langs", default="en,tr")
    pl.add_argument("--copy", default="shotforge.copy.json")
    pl.set_defaults(fn=lambda a: cmd_plan(a))

    r = sub.add_parser("render", help="mevcut tasarım/metin dosyasından yeniden çiz")
    r.add_argument("shots", nargs="*", help="(isteğe bağlı) ekran görüntülerini değiştir")
    r.add_argument("--template", default=None, help="dosyadaki şablonu geçersiz kıl")
    r.add_argument("--bg-color", default=None, metavar="#HEX")
    r.add_argument("--text-color", default=None, metavar="#HEX")
    r.add_argument("--accent", default=None, metavar="#HEX")
    add_render_args(r)
    r.set_defaults(fn=lambda a: cmd_render(a), langs=None)  # verilmezse copy dosyasındaki diller

    an = sub.add_parser("analyze", help="ekran görüntülerini analiz et ve kural motorunun 2D/3D şablon önerisini göster (Claude'suz)")
    an.add_argument("shots", nargs="+")
    an.add_argument("--category", default=None, choices=P.CATEGORIES)
    an.add_argument("--dimension", default="auto", choices=["auto", "2d", "3d"])
    an.add_argument("--bg-color", default=None)
    an.add_argument("--app", default=None)
    an.add_argument("--about", default="")
    an.add_argument("--tone", default="")
    an.set_defaults(fn=cmd_analyze)

    ru = sub.add_parser("rules", help="2D/3D ve şablon seçim kurallarını göster")
    ru.set_defaults(fn=cmd_rules)

    t = sub.add_parser("templates", help="şablonları 2D/3D ayrımıyla listele")
    t.add_argument("--show", help="şablon JSON'unu göster")
    t.set_defaults(fn=cmd_templates)

    s = sub.add_parser("sizes", help="Apple boyut tablosu")
    s.set_defaults(fn=cmd_sizes)

    d = sub.add_parser("doctor", help="kurulum/bağımlılık kontrolü")
    d.set_defaults(fn=cmd_doctor)

    ic = sub.add_parser("install-claude", help="Claude Code'a /store-shots slash komutunu kur")
    ic.add_argument("--global", dest="global_", action="store_true", help="~/.claude/commands (tüm projeler)")
    ic.set_defaults(fn=cmd_install_claude)

    pv = sub.add_parser("preview", help="ekran kaydını App Preview şartlarına çevir (ffmpeg)")
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
        a.fn(a)
    except RuntimeError as e:
        raise SystemExit(f"Hata: {e}")
    except subprocess.TimeoutExpired:
        raise SystemExit("Hata: claude zaman aşımına uğradı (600 sn).")


if __name__ == "__main__":
    main()
