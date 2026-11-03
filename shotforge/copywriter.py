"""
shotforge.copywriter — tasarım kararlarını (renk, sayfa sayısı, 2D/3D şablon) ve slayt metinlerini Claude ile hazırlar.

Kullanıcının verdiği her şey KESİN kabul edilir (renkler, sayfa sayısı, şablon/boyut, kendi yazdığı metinler);
yalnızca eksik olanları Claude (veya kural motoru) tamamlar.

Backend'ler:
  claude   : yerel `claude -p` (Claude Code, headless). Ekran görüntülerini Read aracıyla görür.
  api      : Anthropic Python SDK (ANTHROPIC_API_KEY). Görseller base64 olarak gönderilir.
  offline  : Claude'suz; yerel kurallar + yer tutucu metin.
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from . import palette as P

LANG_NAMES = {
    "en": "English", "tr": "Turkish (Türkçe, natural and idiomatic, correct diacritics ç ğ ı İ ö ş ü)",
    "de": "German", "fr": "French", "es": "Spanish", "it": "Italian", "pt": "Portuguese (Brazil)",
    "ja": "Japanese", "ko": "Korean", "ar": "Arabic", "ru": "Russian", "nl": "Dutch",
}
DEFAULT_MODEL_API = "claude-sonnet-5-5"
AUTO_COUNT_RANGE = (6, 7)

PROMPT = """You are an expert App Store (ASO) copywriter AND visual designer. Look at the real app screenshots listed below
(read every one of them), then make the design decisions and write the text for a set of App Store screenshot slides.

APP NAME: {app}
WHAT IT DOES: {about}
CATEGORY (may be empty, then infer one of: {categories}): {category}
TONE: {tone}

SCREENSHOT FILES (the text of each slide must describe what is ACTUALLY visible on the screenshot paired with it):
{shot_list}

FACTS MEASURED LOCALLY FROM THE SCREENSHOTS (use as hints):
{facts}

AVAILABLE TEMPLATES (2D = phone flat or rotated only in the plane; 3D = phone tilted in perspective with visible thickness):
{templates}

TEMPLATE SELECTION RULES (follow them unless the user fixed the template or dimension):
{rules}

USER-FIXED CHOICES (keep EXACTLY, do not change them):
{fixed}

YOU DECIDE EVERYTHING THAT IS NOT FIXED ABOVE:
1. Number of slides: {count_rule}
2. Template + dimension: apply the rules above; give a one-sentence reason.
3. Colours (hex #RRGGBB): "background" = ONE main brand-fitting colour derived from the app's own look (the templates build
   gradients/tints from it); "accent" = a highlight colour with strong contrast to the background; "text" = headline colour
   with contrast >= 4.5:1 against the background (for 'alternate-tint'/'light-gradient' templates give the colour the headline
   should have on the main-colour slides; the tool adapts it per slide).
4. Slide text, in storytelling order: slide 1 = main promise (hook), middle slides = one concrete benefit each, last slide =
   reassurance / call to action. Each slide uses ONE screenshot (exact file name); reuse is allowed when there are fewer
   screenshots than slides, but every screenshot must be used at least once.
{locked}
TEXT RULES
- Languages: {lang_list}. Write every text in every language. Do NOT translate word for word; write natural, persuasive
  marketing copy in each language (Turkish must sound native).
- Title: benefit-first, max 5 words, max ~34 characters per language. Wrap the 1-3 most important words in *asterisks* for
  accent colour (e.g. "Plan your *day*"). "\\n" may be used for a deliberate line break.
- Subtitle: one short supporting line, max ~60 characters, no asterisks.
- Never invent facts, awards, rankings, user counts, prices or claims that are not in the information above.
- No emojis, no hashtags, no quotation marks around the text.

Return ONLY valid JSON (no markdown fence, no commentary) with exactly this shape:
{{
  "design": {{
    "category": "<one category>",
    "dimension": "2d | 3d",
    "template": "<template name from the list>",
    "template_reason": "<one sentence>",
    "colors": {{"background": "#RRGGBB", "text": "#RRGGBB", "accent": "#RRGGBB"}},
    "count": <number of slides>
  }},
  "slides": [
    {{
      "shot": "<exact file name>",
      "role": "hook | feature | proof | cta",
      "title":    {{{lang_keys}}},
      "subtitle": {{{lang_keys}}}
    }}
  ]
}}
"""


# --------------------------------------------------------------------------- #
# Bağlam (ctx) ve istem
# --------------------------------------------------------------------------- #
def templates_block(templates: dict) -> str:
    return "\n".join(
        f"- {n} [{t['dimension'].upper()}, {t.get('theme', '?')}] best for: {', '.join(t.get('best_for', []))} — {t['description']}"
        for n, t in templates.items())


def fixed_block(ctx) -> str:
    f = ctx["fixed"]
    items = []
    c = f.get("colors") or {}
    for k, label in (("background", "main background colour"), ("text", "text colour"), ("accent", "accent colour")):
        if c.get(k):
            items.append(f"- {label} = {c[k]}")
    if f.get("count"):
        items.append(f"- number of slides = {f['count']}")
    if f.get("template"):
        items.append(f"- template = {f['template']}")
    if f.get("dimension"):
        items.append(f"- dimension = {f['dimension'].upper()}")
    if f.get("category"):
        items.append(f"- category = {f['category']}")
    return "\n".join(items) or "- (nothing fixed: decide everything yourself)"


def facts_block(profile: dict | None) -> str:
    if not profile:
        return "- (none)"
    return (f"- screenshots: {profile['count']}\n- dominant brand-like colour: {profile.get('brand') or 'none (mostly neutral)'}\n"
            f"- main palette: {', '.join(profile.get('palette', []))}\n- UI theme of the screenshots: {profile['ui']}\n"
            f"- visual density (small text / busy UI): {profile['density']} (score {profile['density_score']})")


def locked_block(ctx) -> str:
    locked = ctx.get("locked") or []
    if not any(locked):
        return ""
    d = {str(i + 1): s for i, s in enumerate(locked) if s}
    return ("5. The user ALREADY WROTE text for these slides (FINAL: copy it verbatim, never change a character; slide N of "
            "your output corresponds to the user's slide N). Write only the missing fields/languages, and still return the "
            "complete list:\n" + json.dumps(d, ensure_ascii=False))


def build_prompt(ctx, shots, shot_paths=None, claude_mode=False) -> str:
    langs = ctx["langs"]
    lines = []
    for s in shots:
        lines.append(f"- {s}  ->  read it with the Read tool at: {shot_paths[s]}" if claude_mode and shot_paths else f"- {s}")
    cnt = ctx["fixed"].get("count")
    lo, hi = AUTO_COUNT_RANGE
    count_rule = (f"exactly {cnt}." if cnt else
                  f"choose {lo} or {hi} (never more than 10), based on how many distinct features the screenshots show.")
    return PROMPT.format(
        app=ctx["app"], about=ctx.get("about") or "(not provided; infer from the screenshots)",
        categories=", ".join(P.CATEGORIES), category=ctx["fixed"].get("category") or "",
        tone=ctx["tone"], shot_list="\n".join(lines), facts=facts_block(ctx.get("profile")),
        templates=templates_block(ctx["templates"]), rules=P.RULES_TEXT, fixed=fixed_block(ctx),
        count_rule=count_rule, locked=locked_block(ctx),
        lang_list=", ".join(f"{l} = {LANG_NAMES.get(l, l)}" for l in langs),
        lang_keys=", ".join(f'"{l}": "..."' for l in langs))


# --------------------------------------------------------------------------- #
# Ayrıştırma / doğrulama
# --------------------------------------------------------------------------- #
def extract_json(text: str) -> dict:
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.S)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        a, b = t.find("{"), t.rfind("}")
        if a >= 0 and b > a:
            return json.loads(t[a:b + 1])
        raise


def validate(data: dict, ctx, shots: list[str]) -> list[str]:
    probs = []
    langs = ctx["langs"]
    slides = data.get("slides")
    if not isinstance(slides, list) or not slides:
        return ["'slides' listesi yok"]
    want = ctx["fixed"].get("count")
    if want and len(slides) != want:
        probs.append(f"{want} slayt bekleniyordu, {len(slides)} geldi")
    if not want and not (1 <= len(slides) <= 10):
        probs.append(f"slayt sayısı 1-10 olmalı ({len(slides)})")
    used = set()
    for i, s in enumerate(slides, 1):
        if s.get("shot") not in shots:
            probs.append(f"slayt {i}: bilinmeyen shot '{s.get('shot')}'")
        else:
            used.add(s["shot"])
        for key in ("title", "subtitle"):
            v = s.get(key)
            if not isinstance(v, dict):
                probs.append(f"slayt {i}: {key} bir sözlük olmalı")
            elif key == "title":
                probs += [f"slayt {i}: title.{l} boş" for l in langs if not str(v.get(l, "")).strip()]
    missing = [s for s in shots if s not in used]
    if missing and len(slides) >= len(shots):
        probs.append("kullanılmayan ekran görüntüleri: " + ", ".join(missing))
    d = data.get("design")
    if not isinstance(d, dict):
        probs.append("'design' bloğu yok")
    else:
        t = d.get("template")
        if t and t not in ctx["templates"]:
            probs.append(f"bilinmeyen şablon '{t}' (seçenekler: {', '.join(ctx['templates'])})")
        for k, v in (d.get("colors") or {}).items():
            if v and not P.is_hex(v if isinstance(v, str) else (v[0] if v else "")):
                probs.append(f"design.colors.{k} geçerli hex değil: {v}")
    return probs


# --------------------------------------------------------------------------- #
# Backend'ler
# --------------------------------------------------------------------------- #
def _claude_cli(prompt: str, add_dirs: list[str], model: str | None, timeout=600) -> str:
    exe = shutil.which("claude")
    if not exe:
        raise RuntimeError("`claude` komutu bulunamadı (Claude Code kurulu değil). --ai api veya --ai offline deneyin.")
    # İstem stdin'den gider (ps'te görünmesin). Ekran görüntüleri güvenilmez girdi: yalnızca Read aracı açık.
    cmd = [exe, "-p", "--output-format", "json", "--tools", "Read", "--allowedTools", "Read",
           "--strict-mcp-config", "--max-turns", "25"]
    for d in add_dirs:
        cmd += ["--add-dir", d]
    if model:
        cmd += ["--model", model]
    r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0 and not r.stdout.strip():
        raise RuntimeError(f"claude hata verdi ({r.returncode}): {r.stderr.strip()[:500]}")
    try:
        env = json.loads(r.stdout)
    except json.JSONDecodeError:
        return r.stdout
    if isinstance(env, dict):
        if env.get("is_error"):
            raise RuntimeError(f"claude hata döndürdü: {str(env.get('result'))[:500]}")
        return str(env.get("result", ""))
    return r.stdout


def _shrink_b64(path: Path, max_side=1100) -> tuple[str, str]:
    from PIL import Image
    im = Image.open(path).convert("RGB")
    im.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode(), "image/jpeg"


def _api(prompt: str, shot_paths: dict[str, Path], model: str | None) -> str:
    try:
        import anthropic
    except ImportError:
        raise RuntimeError("anthropic paketi yok: pip install anthropic")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY tanımlı değil.")
    content = []
    for name, p in shot_paths.items():
        data, mt = _shrink_b64(p)
        content.append({"type": "text", "text": f"Screenshot file: {name}"})
        content.append({"type": "image", "source": {"type": "base64", "media_type": mt, "data": data}})
    content.append({"type": "text", "text": prompt})
    msg = anthropic.Anthropic().messages.create(model=model or DEFAULT_MODEL_API, max_tokens=5000,
                                                messages=[{"role": "user", "content": content}])
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


# --------------------------------------------------------------------------- #
# Offline plan (kural motoru + yer tutucu metin)
# --------------------------------------------------------------------------- #
def _humanize(stem: str) -> str:
    s = re.sub(r"^\d+[_\-\s]*", "", stem).replace("_", " ").replace("-", " ").strip()
    return s[:1].upper() + s[1:] if s else stem


def offline_slides(app, shots, count, langs):
    roles = ["hook"] + ["feature"] * max(0, count - 2) + ["cta"]
    if count == 1:
        roles = ["hook"]
    tpl = {
        "hook": {"en": ("Meet *{app}*", "Everything you need in one app"), "tr": ("*{app}* ile tanış", "İhtiyacın olan her şey tek uygulamada")},
        "feature": {"en": ("{f} made *simple*", "Fast, clear and easy to use"), "tr": ("{f} artık *çok kolay*", "Hızlı, net ve kullanımı kolay")},
        "cta": {"en": ("Start *today*", "Download {app} and get going"), "tr": ("Hemen *başla*", "{app}'i indir ve başla")},
    }
    slides = []
    for i in range(count):
        shot = shots[i % len(shots)]
        title, sub = {}, {}
        for l in langs:
            t, s = tpl[roles[i]].get(l, tpl[roles[i]]["en"])
            title[l] = t.format(app=app, f=_humanize(Path(shot).stem))
            sub[l] = s.format(app=app)
        slides.append(dict(shot=shot, role=roles[i], title=title, subtitle=sub))
    return slides


def auto_count(profile, shots) -> int:
    lo, hi = AUTO_COUNT_RANGE
    return max(lo, min(hi, len(shots) + 2))


# --------------------------------------------------------------------------- #
# Son hâl: kullanıcı kararları > Claude > kurallar
# --------------------------------------------------------------------------- #
def finalize(data: dict | None, ctx, shots: list[str]) -> dict:
    data = data or {}
    langs, templates, fixed = ctx["langs"], ctx["templates"], ctx["fixed"]
    d = dict(data.get("design") or {})
    profile = ctx.get("profile")

    category = fixed.get("category") or (d.get("category") if d.get("category") in P.CATEGORIES else None) \
        or P.infer_category(f"{ctx['app']} {ctx.get('about', '')}")

    colors = {}
    fc, dc = fixed.get("colors") or {}, d.get("colors") or {}
    for k in ("background", "text", "accent"):
        v = fc.get(k) or dc.get(k)
        if isinstance(v, list):
            v = v[0] if v else None
        if v and P.is_hex(v):
            colors[k] = "#" + v.strip().lstrip("#").lower() if len(v.strip().lstrip("#")) == 6 else P.rgb2hex(P.hex2rgb(v))

    reason = None
    tname = fixed.get("template")
    if tname:
        reason = "kullanıcı seçti"
    else:
        cand = d.get("template")
        user_bg = (fixed.get("colors") or {}).get("background")
        ok = cand in templates and (not fixed.get("dimension") or templates[cand]["dimension"] == fixed["dimension"])
        theme_clash = bool(ok and user_bg and templates[cand].get("theme") != P.desired_theme(user_bg, profile))
        if ok and not theme_clash:
            tname, reason = cand, d.get("template_reason") or "Claude seçti"
        else:
            keep_dim = fixed.get("dimension") or (templates[cand]["dimension"] if ok else None)
            tname, _, rs = P.choose_template(templates, dimension=keep_dim, category=category,
                                             bg_hex=colors.get("background") or (profile or {}).get("brand"), profile=profile, tone=ctx["tone"])
            pre = (f"Claude'un önerdiği '{cand}' ({templates[cand].get('theme')}), verdiğiniz arka plan rengiyle "
                   f"({user_bg}) uyumsuz; " if theme_clash else "")
            reason = pre + "kural motoru: " + "; ".join(rs)
    dimension = templates[tname]["dimension"]

    source = "user" if fc.get("background") else ("claude" if colors.get("background") else None)
    if "background" not in colors:
        derived = {k: v for k, v in P.default_colors(profile, templates[tname].get("theme") if fixed.get("template") else None).items() if k not in colors}
        colors.update(derived)
        source = "screenshots" if derived else "template"

    # model çıktısı güvenilmez: yalnızca metin alanları geçer, çizim ayarları geçmez
    slides = [{k: s[k] for k in ("shot", "role", "title", "subtitle") if k in s} for s in (data.get("slides") or [])]
    count = fixed.get("count") or (min(len(slides), 10) if slides else auto_count(profile, shots))
    if len(slides) < count:
        fill = offline_slides(ctx["app"], shots, count, langs)
        slides += fill[len(slides):]
    slides = slides[:count]
    for i, s in enumerate(slides):
        if s.get("shot") not in shots:
            s["shot"] = shots[i % len(shots)]  # kullanıcı bir shot yazdıysa aşağıda üzerine yazılır
        for key in ("title", "subtitle"):
            v = dict(s.get(key) or {})
            first = next((x for x in v.values() if x), "")
            for l in langs:
                if not v.get(l):
                    v[l] = first if key == "title" else v.get(l, "")
            s[key] = v
        lk = (ctx.get("locked") or [])
        lk = lk[i] if i < len(lk) else None
        if lk:  # kullanıcının yazdığı metin kesindir
            if lk.get("shot") in shots:
                s["shot"] = lk["shot"]
            for key in ("title", "subtitle"):
                for l, v in (lk.get(key) or {}).items():
                    if v:
                        s[key][l] = v
    design = dict(category=category, dimension=dimension, template=tname, template_reason=reason,
                  colors=colors, colors_source=source, count=count)
    return {"design": design, "slides": slides}


def needs_ai(ctx, shots) -> bool:
    f = ctx["fixed"]
    c = f.get("colors") or {}
    colors_done = all(c.get(k) for k in ("background", "text", "accent"))
    count = f.get("count")
    locked = ctx.get("locked") or []
    texts_done = bool(count) and not _locked_missing(locked, count, ctx["langs"]) and all(
        (locked[i].get("shot") if i < len(locked) and locked[i] else None) for i in range(count))
    return not (colors_done and count and f.get("template") and texts_done)


def _locked_missing(locked, count, langs):
    miss = []
    for i in range(count):
        s = locked[i] if i < len(locked) else None
        if not s or any(not (s.get("title") or {}).get(l) for l in langs):
            miss.append(i + 1)
    return miss


def plan(ctx, shot_paths: dict[str, Path], backend="auto", model=None, log=print):
    """ctx: app, about, tone, langs, fixed{colors,count,template,dimension,category}, locked[], profile, templates.
    Dönüş: ({'design':…, 'slides':[…]}, kullanılan_backend)"""
    shots = list(shot_paths)
    if backend == "auto":
        backend = "claude" if shutil.which("claude") else ("api" if os.environ.get("ANTHROPIC_API_KEY") else "offline")
        log(f"AI backend: {backend}")
    if not needs_ai(ctx, shots):
        log("Tüm tasarım kararları ve metinler kullanıcıdan geldi; Claude çağrılmadı.")
        return finalize(None, ctx, shots), "user"
    if backend == "offline":
        log("UYARI: offline mod — tasarım kararları yerel kurallarla, metinler yer tutucu olarak üretilir.")
        count = ctx["fixed"].get("count") or max(len(ctx.get("locked") or []), auto_count(ctx.get("profile"), shots))
        return finalize({"slides": offline_slides(ctx["app"], shots, count, ctx["langs"])}, ctx, shots), "offline"

    feedback, data, last_err = "", None, None
    for attempt in (1, 2):
        if backend == "claude":
            prompt = build_prompt(ctx, shots, {k: str(v.resolve()) for k, v in shot_paths.items()}, claude_mode=True) + feedback
            dirs = sorted({str(p.resolve().parent) for p in shot_paths.values()})
            raw = _claude_cli(prompt, dirs, model)
        elif backend == "api":
            raw = _api(build_prompt(ctx, shots) + feedback, shot_paths, model)
        else:
            raise RuntimeError(f"Bilinmeyen backend: {backend}")
        try:
            data = extract_json(raw)
            probs = validate(data, ctx, shots)
        except Exception as e:  # noqa: BLE001
            probs, data, last_err = [f"JSON ayrıştırılamadı: {e}"], None, raw[:300]
        if not probs:
            return finalize(data, ctx, shots), backend
        log(f"Claude çıktısı düzeltilecek (deneme {attempt}): " + "; ".join(probs[:4]))
        feedback = "\n\nYour previous answer had problems: " + "; ".join(probs) + ". Fix them and return ONLY the JSON."
    if data and data.get("slides"):
        log("UYARI: doğrulama sorunları kaldı; çıktı kurallarla düzeltilerek kullanılıyor.")
        return finalize(data, ctx, shots), backend
    raise RuntimeError(f"Claude geçerli JSON döndürmedi. Son çıktı: {last_err}")
