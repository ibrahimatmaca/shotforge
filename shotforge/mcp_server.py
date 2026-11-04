"""
shotforge MCP server (stdio).

Exposes shotforge to any MCP client (Claude Desktop, Claude Code, Cursor, ...). In MCP mode the *host* Claude is the model:
it looks at the screenshots, decides colours / page count / template / copy and passes them to `create_screenshots`.
Anything it leaves out is decided by shotforge's local rules (2D/3D chooser, colour analysis) — no API key needed.

Run:  shotforge-mcp          (or: python -m shotforge.mcp_server)
Install: pip install "shotforge[mcp]"
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import functools
import inspect
import json
import subprocess
import os
import sys
from pathlib import Path
from typing import Any, Optional

try:  # mcp >= 2 renamed FastMCP to MCPServer; support both
    from mcp.server.mcpserver import Image, MCPServer as _Server
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:
    try:
        from mcp.server.fastmcp import FastMCP as _Server, Image
        from mcp.server.fastmcp.exceptions import ToolError
    except ImportError as e:  # pragma: no cover
        raise SystemExit('The MCP server needs the "mcp" package:  pip install "shotforge[mcp]"\nSetup guide:  shotforge mcp') from e

from . import brand as B, cli, copywriter, intake as I, palette as P
from .engine import DEVICES, PREVIEW_SIZES

mcp = _Server(
    "shotforge",
    instructions=(
        "Creates App Store and Google Play images (one separate file per page, per language) from the user's real app screenshots, "
        "with 2D/3D device templates, headline text and colours. Workflow: (1) call get_intake_questions, with no shots if the user "
        "hasn't given any, and ASK THE USER those questions in this conversation before generating anything; a blank or 'auto' answer means the returned default, (2) call analyze_screenshots "
        "and LOOK at the screenshots yourself, (3) call create_screenshots with intake_completed=true, the answers and the slide "
        "copy; everything left out is decided by shotforge's rules, background colour from the screenshots, (4) show the returned "
        "overview image and fix problems with render_screenshots. Output goes under <out>/<lang>/<store>/<target>/, plus a zip. "
        "layout='fastlane' writes fastlane deliver and supply folders instead. For a store listing test, call create_screenshots "
        "once per variant with its own out_dir and a different template or a different messaging angle."
    ),
)


def _safe(fn):
    """Turn expected failures into ToolError so the host Claude sees the reason (mcp 2 masks other exceptions)."""
    errs = (ValueError, SystemExit, RuntimeError, OSError, subprocess.CalledProcessError)

    def msg(e):
        return str(e) or e.__class__.__name__

    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def wrapper(*a, **k):
            try:
                return await fn(*a, **k)
            except ToolError:
                raise
            except errs as e:
                raise ToolError(msg(e)) from None
    else:
        @functools.wraps(fn)
        def wrapper(*a, **k):
            try:
                return fn(*a, **k)
            except ToolError:
                raise
            except errs as e:
                raise ToolError(msg(e)) from None
    return wrapper


def _abs(p: str) -> Path:
    return Path(os.path.expanduser(p)).resolve()


def _collect_sets(shots: list[str], langs=()) -> tuple[dict[str, Path], dict[str, dict[str, Path]]]:
    try:
        return cli.scan_shots([str(_abs(s)) for s in shots], langs)
    except SystemExit as e:  # SystemExit -> clean tool error
        raise ValueError(str(e)) from None


def _collect(shots: list[str]) -> dict[str, Path]:
    return _collect_sets(shots)[0]


class _Quiet(contextlib.AbstractContextManager):
    """CLI functions print progress; stdout is the MCP channel, so route it to stderr."""
    def __enter__(self):
        self._cm = contextlib.redirect_stdout(sys.stderr)
        self._cm.__enter__()
        return self

    def __exit__(self, *a):
        return self._cm.__exit__(*a)


def _templates_info() -> list[dict]:
    return [dict(name=n, dimension=t["dimension"], theme=t.get("theme"), best_for=t.get("best_for", []),
                 description=t["description"]) for n, t in cli.list_templates().items()]


def _valid_hex(v: Optional[str], name: str) -> Optional[str]:
    if not v:
        return None
    if not P.is_hex(v):
        raise ValueError(f"{name} must be a hex colour like #6D28D9, got {v!r}")
    return "#" + v.strip().lstrip("#")


def _builtin_template(name: str) -> str:
    """MCP'de şablon yalnızca adla seçilir; model yerel bir .json yolu yükletemez."""
    name = cli.canon_template(name)
    templates = cli.list_templates()
    if name not in templates:
        raise ValueError(f"unknown template '{name}'. Available: {', '.join(templates)}")
    return name


def _valid_device(device: str, allowed) -> str:
    if device not in allowed:
        raise ValueError(f"device must be one of {', '.join(allowed)}")
    return device


# --------------------------------------------------------------------------- #
@mcp.tool()
@_safe
def list_templates() -> list[dict]:
    """List the available templates. dimension '2d' = phone flat or rotated only in the plane; '3d' = phone tilted in perspective
    with visible thickness. theme = dark | light | colorful. best_for = app categories the template suits."""
    return _templates_info()


@mcp.tool()
@_safe
def get_intake_questions(shots: Optional[list[str]] = None, app: str = "", about: str = "") -> dict:
    """Call this FIRST, even before the user has given screenshots. Without shots it returns only the questions needed to find
    them; ask those, then call it again. With shots it returns the questions to ask before generating, with defaults computed from them.
    Ask them in your host's question UI (several short groups if it limits questions per call), else in one message. Skip
    anything the user already told you and questions whose `when` doesn't apply. A blank or 'auto' answer means the default.
    Each question has at most 4 options; offer other values through free text. answer_field names the create_screenshots
    argument when it differs from id. A default run renders many large images and can take a few minutes."""
    if not shots:
        first = ("screenshots", "app", "about")
        return dict(questions=[q for q in I.QUESTIONS if q["id"] in first],
                    next_step="Ask these, then call get_intake_questions again with shots set to the paths the user gave.")
    sm, sets = _collect_sets(shots)
    prof = P.analyze_shots(list(sm.values()))
    cat = P.infer_category(f"{app} {about}")
    name, dim, reasons = P.choose_template(cli.list_templates(), category=cat, profile=prof)
    return dict(
        questions=[q for q in I.QUESTIONS if q["id"] != "screenshots"], templates=_templates_info(), detected_shot_sets=sorted(sets),
        screenshot_defaults=dict(colors=P.default_colors(prof), ui=prof["ui"], template=name, dimension=dim, reasons=reasons),
        output_layout="<out>/<lang>/app-store/{iphone-6.9,ipad-13,header-search,previews}/… and <out>/<lang>/google-play/"
                      "{phone,tablet-7,tablet-10,feature-graphic}/…, plus manifest.json and <app>.zip. layout='fastlane': "
                      "<out>/fastlane/screenshots/<locale>/… and <out>/fastlane/metadata/android/<locale>/images/…",
    )


@mcp.tool()
@_safe
def analyze_screenshots(shots: list[str], app: str = "", about: str = "", category: Optional[str] = None,
                        dimension: str = "auto", background_color: Optional[str] = None, tone: str = "",
                        keywords: Optional[list[str]] = None) -> dict:
    """Analyse app screenshots locally (dominant brand colour, light/dark UI, visual density) and return shotforge's
    rule-based 2D/3D template recommendation with reasons, plus the rules and template catalogue.
    Use this first; then look at the screenshots yourself and decide the design.

    shots: absolute paths of image files or of a folder containing them.
    category: one of fitness, health, finance, social, education, productivity, games, photo, travel, shopping,
              entertainment, lifestyle, utilities (inferred from app/about when omitted).
    keywords: search keywords the slide copy should target; they are added to copy_guidance."""
    sm = _collect(shots)
    keywords = copywriter.parse_keywords(keywords)
    prof = P.analyze_shots(list(sm.values()))
    cat = category or P.infer_category(f"{app} {about}")
    if cat and cat not in P.CATEGORIES:
        raise ValueError(f"category must be one of {', '.join(P.CATEGORIES)}")
    bg = _valid_hex(background_color, "background_color")
    name, dim, reasons = P.choose_template(cli.list_templates(), dimension=None if dimension == "auto" else dimension,
                                           category=cat, bg_hex=bg, profile=prof, tone=tone)
    return dict(
        screenshots=[dict(file=n, path=str(p)) for n, p in sm.items()], profile=prof, category=cat,
        recommendation=dict(template=name, dimension=dim, reasons=reasons),
        rules=P.RULES_TEXT, templates=_templates_info(),
        copy_guidance=("Titles: benefit-first, max 5 words (~34 chars); wrap 1-3 key words in *asterisks* for the accent colour; "
                       "subtitle: one short line (~60 chars). Slide 1 = main promise, middle = one benefit each, last = reassurance/CTA. "
                       "Write natural marketing copy per language, never word-for-word translation. Do not invent claims."
                       + (f" Work in these search keywords where they read naturally, at most one or two per slide, using the term people "
                          f"really search for in each language: {', '.join(keywords)}." if keywords else "")),
    )


def _brand(path: Optional[str]) -> dict:
    return B.load(_abs(path)) if path else {}


def _create(shots, app, slides, langs, template, dimension, background_color, text_color, accent_color, count, about,
            category, tone, out_dir, device, layout, fmt, only, jobs, style, *, targets=None, previews=None, icon=None, banner=None,
            keywords=None, font=None, logo=None):
    keywords = copywriter.parse_keywords(keywords)
    langs = [l.strip() for l in langs if l.strip()] or ["en"]
    cli.check_langs(langs)
    sm, sets = _collect_sets(shots, langs)
    templates = cli.list_templates()
    if device:
        _valid_device(device, DEVICES)
    colors ={k: v for k, v in (("background", _valid_hex(background_color, "background_color")),
                                ("text", _valid_hex(text_color, "text_color")),
                                ("accent", _valid_hex(accent_color, "accent_color"))) if v}
    locked = []
    for i, s in enumerate(slides or [], 1):
        s = dict(s)
        if s.get("shot") and s["shot"] not in sm:
            raise ValueError(f"slides[{i}].shot '{s['shot']}' is not among the screenshots: {', '.join(sm)}")
        locked.append(dict(shot=s.get("shot"), title=dict(s.get("title") or {}), subtitle=dict(s.get("subtitle") or {}),
                           **({"role": s["role"]} if s.get("role") else {})))
        if not locked[-1]["shot"]:
            locked[-1].pop("shot")
    tname = None if template in (None, "", "auto") else _builtin_template(template)
    dim =None if dimension in (None, "", "auto") else dimension.lower()
    if dim not in (None, "2d", "3d"):
        raise ValueError("dimension must be auto, 2d or 3d")
    if category and category not in P.CATEGORIES:
        raise ValueError(f"category must be one of {', '.join(P.CATEGORIES)}")
    n = int(count) if count else (len(locked) or None)
    if n and not 1 <= n <= 10:
        raise ValueError("count must be 1-10 (App Store allows at most 10 screenshots per language)")
    prof = P.analyze_shots(list(sm.values()))
    ctx = dict(app=app, about=about, tone=tone, langs=langs, templates=templates, profile=prof, locked=locked,
               fixed=dict(colors=colors, count=n, template=tname, dimension=dim, category=category))
    placeholder = not locked or copywriter._locked_missing(locked, n or len(locked), langs)
    data = copywriter.finalize(None, ctx, list(sm))
    out = _abs(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    copy_path = out / "shotforge.copy.json"
    full = dict(app=app, about=about, tone=tone, langs=langs, generated_with="mcp", design=data["design"],
                shots={k: cli.rel(p, copy_path.parent) for k, p in sm.items()}, slides=data["slides"])
    if sets:
        full["shots_by_set"] = {k: {n: cli.rel(p, copy_path.parent) for n, p in m.items()} for k, m in sets.items()}
    if targets:
        full["targets"] = targets
    if banner:
        full["banner"] = {k: banner[k] for k in ("title", "subtitle") if banner.get(k)}
    if keywords:
        full["keywords"] = keywords
    cli.write_copy(copy_path, full)
    result = _render(copy_path, out, device, layout, fmt, only, jobs, style, langs, previews=previews, icon=icon, font=font, logo=logo)
    result["design"] = data["design"]
    if keywords:
        result["keywords_not_found_word_for_word"] = copywriter.keyword_gaps(data["slides"], keywords)
    if placeholder:
        result["warning"] = ("Some slide text was missing and was filled with generic placeholder copy. "
                             "Write the missing titles/subtitles yourself and call create_screenshots again (or edit "
                             f"{copy_path} and call render_screenshots).")
    result["copy_file"] = str(copy_path)
    return result


def _render(copy_path: Path, out: Path, device, layout, fmt, only, jobs, style, langs=None, overrides=None, previews=None, icon=None,
            font=None, logo=None):
    import argparse as ap
    ov = overrides or {}
    a = ap.Namespace(
        copy=str(copy_path), out=str(out), device=device, layout=layout, format=fmt, jobs=jobs, style=style or None, bg=None,
        frame=None, text_position=None, align=None, font=font, logo=logo, perspective=None, no_grain=False,
        only=",".join(map(str, only)) if only else None, sheet=False, brief=False, shots=[],
        template=ov.get("template"), bg_color=ov.get("background"), text_color=ov.get("text"), accent=ov.get("accent"),
        langs=",".join(langs) if langs else None, previews=previews, icon=str(_abs(icon)) if icon else None)
    with _Quiet():
        by_lang = cli.cmd_render(a)
    files = {l: sorted(ps) for l, ps in by_lang.items()}
    # small overview image for the host to inspect (not a deliverable)
    review = out / "_review"
    review.mkdir(exist_ok=True)
    first = next(iter(files))
    ov_path = review / f"overview_{first}.jpg"
    with _Quiet():
        cli.make_sheet(files[first], ov_path, h=640, gap=20)
    res = dict(files=files, overview=str(ov_path), note="Each file is a separate, upload-ready image (not combined).")
    with _Quiet():
        res.update(cli.package_output(a, by_lang))
    if a.warnings:
        res["render_warnings"] = a.warnings
    return res


@mcp.tool()
@_safe
async def create_screenshots(
    shots: list[str],
    app: str,
    slides: Optional[list[dict[str, Any]]] = None,
    langs: Optional[list[str]] = None,
    template: str = "auto",
    dimension: str = "auto",
    background_color: Optional[str] = None,
    text_color: Optional[str] = None,
    accent_color: Optional[str] = None,
    count: Optional[int] = None,
    about: str = "",
    category: Optional[str] = None,
    tone: str = cli.DEFAULT_TONE,
    out_dir: Optional[str] = None,
    device: Optional[str] = None,
    layout: Optional[str] = None,
    format: str = "png",
    only: Optional[list[int]] = None,
    jobs: int = 2,
    style: Optional[str] = None,
    intake_completed: bool = False,
    store: Optional[str] = None,
    header_sizes: Optional[list[str]] = None,
    devices: Optional[list[str]] = None,
    previews: Optional[dict[str, str]] = None,
    icon: Optional[str] = None,
    banner: Optional[dict[str, Any]] = None,
    output: Optional[str] = None,
    keywords: Optional[list[str]] = None,
    brand_file: Optional[str] = None,
) -> list:
    """Create store images: ONE SEPARATE IMAGE FILE per page per language, for every store target.

    intake_completed: set true only after asking the user the get_intake_questions questions. Blank answers are fine.
    store: appstore | playstore | both (default both). header_sizes: 5244x2950, 3840x2560, 1920x1280 or ["no"] (default all).
    devices: from DEVICES (default iphone-6.9, android-phone). previews: {"*": path} or {"tr": path} screen recordings.
    icon: app icon image for header/feature graphic. output: both | folder | zip (default both). A device argument draws
    one device only, with no store folders, header or zip.
    banner: {"title": {"en": "..."}, "subtitle": {"en": "..."}} for header/feature graphic (default: first slide text).
    Output: <out_dir>/<lang>/<store>/<target>/…, default out_dir ~/shotforge-out/<app>.

    shots: absolute paths of the real app screenshots (files or a folder).
    slides: the page texts you (the host Claude) or the user decided, in order. Each item:
        {"shot": "01_home.png"  (file name, optional), "title": {"en": "Plan your *day*", "tr": "Gününü *planla*"},
         "subtitle": {"en": "...", "tr": "..."}}      (*word* = accent colour)
        Missing pages/languages are filled with generic placeholder copy and reported as a warning.
    langs: e.g. ["en","tr"] (default ["en","tr"]).
    template/dimension: 'auto' lets shotforge's rules choose (2D = flat/in-plane phone, 3D = perspective phone). Use
        list_templates for names. background_color/text_color/accent_color: '#RRGGBB' (optional; text colour is auto-fixed if unreadable).
    count: number of pages 1-10 (defaults to len(slides), else 6-7).
    layout: 'store' (default), 'flat' (out/<lang>/NN_name.png, one device), 'asc' (out/<locale>/<DISPLAY_TYPE>/NN_name.png) or
        'fastlane' (out/fastlane/screenshots/<locale>/… and out/fastlane/metadata/android/<locale>/images/…).
    keywords: the search keywords your slide copy targets; the result lists any not found word for word.
    brand_file: path to a shotforge.toml; it fills template, colours, tone and langs you left out, and adds its font and logo.
    only: render only these 1-based page numbers (faster iteration). Returns file paths and a small overview image."""
    if not intake_completed:
        raise ToolError("Ask the user the get_intake_questions questions first, then call again with intake_completed=true. "
                        "Blank answers are fine: they become defaults.")
    targets = resolved_previews = None
    b = _brand(brand_file)
    colors = b.get("colors") or {}
    background_color, text_color = background_color or colors.get("background"), text_color or colors.get("text")
    accent_color = accent_color or colors.get("accent")
    if template in (None, "", "auto") and b.get("template") and dimension in (None, "", "auto"):
        template = b["template"]
    if tone == cli.DEFAULT_TONE:
        tone = b.get("tone") or tone
    resolved_langs = langs or b.get("langs") or list(I.DEFAULT_LANGS)
    if not device:
        r = I.resolve(dict(store=store, header_sizes=header_sizes, langs=resolved_langs, devices=devices, previews=previews, output=output))
        targets, resolved_previews = r["targets"], r["previews"]
    out_dir = out_dir or (f"~/shotforge-out/{I.slug(app)}" if not device else "~/shotforge-out")
    res = await asyncio.to_thread(_create, shots, app, slides, resolved_langs, template, dimension, background_color,
                                  text_color, accent_color, count, about, category, tone, out_dir, device, layout,
                                  format, only, jobs, style, targets=targets, previews=resolved_previews, icon=icon or b.get("logo"), banner=banner,
                                  keywords=keywords, font=b.get("font"), logo=b.get("logo"))
    content: list = [json.dumps(res, ensure_ascii=False, indent=2)]
    if Path(res["overview"]).exists():
        content.append(Image(path=res["overview"]))
    return content


@mcp.tool()
@_safe
async def render_screenshots(
    copy_file: str,
    out_dir: Optional[str] = None,
    only: Optional[list[int]] = None,
    template: Optional[str] = None,
    background_color: Optional[str] = None,
    text_color: Optional[str] = None,
    accent_color: Optional[str] = None,
    device: Optional[str] = None,
    layout: Optional[str] = None,
    format: str = "png",
    jobs: int = 2,
    icon: Optional[str] = None,
    brand_file: Optional[str] = None,
) -> list:
    """Re-render from a shotforge.copy.json (written by create_screenshots, editable by hand): use after fixing text or
    trying another template/colours. Overrides are optional. Pass brand_file again if the images use its font and logo.
    Returns file paths and a small overview image."""
    cp = _abs(copy_file)
    if not cp.exists():
        raise ValueError(f"copy file not found: {cp}")
    if device:
        _valid_device(device, DEVICES)
    _builtin_template((cli.read_copy(cp).get("design") or {}).get("template", "classic"))
    if template:
        template = _builtin_template(template)
    out = _abs(out_dir) if out_dir else cp.parent
    ov = dict(template=template, background=_valid_hex(background_color, "background_color"),
              text=_valid_hex(text_color, "text_color"), accent=_valid_hex(accent_color, "accent_color"))
    b = _brand(brand_file)
    res = await asyncio.to_thread(_render, cp, out, device, layout, format, only, jobs, None, None, ov, None, icon or b.get("logo"),
                                  b.get("font"), b.get("logo"))
    content: list = [json.dumps(res, ensure_ascii=False, indent=2)]
    if Path(res["overview"]).exists():
        content.append(Image(path=res["overview"]))
    return content


@mcp.tool()
@_safe
async def convert_app_preview(input_video: str, output_video: str, device: str = "iphone-6.9", landscape: bool = False,
                              fit: str = "cover", extend: bool = False, overwrite: bool = False) -> str:
    """Convert a screen recording to App Store Connect app-preview specs with ffmpeg (886x1920 portrait for iPhone 6.9",
    30 fps, H.264 High@4.0, AAC stereo, max 30 s; adds silent audio if missing). `extend` freezes the last frame to reach 15 s.
    output_video must end in .mp4, .mov or .m4v; an existing file is replaced only with overwrite=true.
    Requires ffmpeg on PATH. The video is not framed or decorated."""
    import argparse as ap
    _valid_device(device, PREVIEW_SIZES)
    out = _abs(output_video)
    if out.suffix.lower() not in (".mp4", ".mov", ".m4v"):
        raise ValueError("output_video must end in .mp4, .mov or .m4v")
    if out.exists() and not overwrite:
        raise ValueError(f"{out} already exists; pass overwrite=true to replace it")
    a = ap.Namespace(input=str(_abs(input_video)), out=str(out), device=device, landscape=landscape,
                     fit=fit, pad_color="black", extend=extend)

    def run():
        with _Quiet():
            cli.cmd_preview(a)
    await asyncio.to_thread(run)
    return f"Wrote {a.out}"


@mcp.prompt(name="store_screenshots", description="Guided workflow: ask the intake questions, then create separate store images per language")
def store_screenshots_prompt(folder: str = "", app: str = "", about: str = "") -> str:
    return (
        f"Create store images for the app '{app or 'ask the user'}' ({about or 'infer from the screenshots'}) from the real screenshots in {folder or 'a folder the user gives you; ask for it first'}.\n"
        "1. Call get_intake_questions(shots=[folder], app, about), leaving shots out if there is no folder yet, and ask the user those questions in this conversation BEFORE generating anything. "
        "Skip what is already known. Blank or 'auto' means the returned default.\n"
        "2. Call analyze_screenshots and LOOK at every screenshot yourself.\n"
        "3. Write page copy in each language unless the user wrote it: benefit-first, max 5 words, *accent* words, subtitle one line. "
        "Keep any text the user wrote exactly. For Google Play avoid words like Best, #1, Free and calls like Download now.\n"
        "4. Call create_screenshots with intake_completed=true and the answers. Leave colours out when the user left them blank: "
        "shotforge derives them from the screenshots.\n"
        "5. Show the returned overview image, check text overflow / wrong translation / contrast / screenshot-headline mismatch, "
        "fix with render_screenshots (edit the copy file). Report the output folder, zip path and every warning. Do not upload anything unless asked."
    )


def main(argv=None):
    ap = argparse.ArgumentParser(prog="shotforge-mcp", description="shotforge MCP server (stdio)",
                                 epilog="Your MCP client starts this command. For setup steps, run: shotforge mcp")
    ap.add_argument("--version", action="store_true")
    a = ap.parse_args(argv)
    if a.version:
        from . import __version__
        print(__version__)
        return
    mcp.run()  # stdio


if __name__ == "__main__":
    main()
