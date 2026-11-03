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
        raise SystemExit('The MCP server needs the "mcp" package:  pip install "shotforge[mcp]"') from e

from . import cli, copywriter, palette as P
from .engine import DEVICES, PREVIEW_SIZES

mcp = _Server(
    "shotforge",
    instructions=(
        "Creates App Store screenshots (one separate image file per page, per language) from the user's real app screenshots, "
        "with 2D/3D device templates, headline text and colours. Workflow: (1) call analyze_screenshots with the screenshot "
        "folder/files, (2) LOOK at the screenshots yourself, (3) ask the user for anything they want to fix (main background "
        "colour, text colour, accent colour, number of pages, their own headline text, 2D or 3D) — everything not given is decided "
        "by you or by shotforge's rules, (4) call create_screenshots with your design decisions and the slide copy, (5) show the "
        "returned overview image and fix problems with render_screenshots. Output files are separate PNGs, never one combined image."
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


def _collect(shots: list[str]) -> dict[str, Path]:
    try:
        return cli.collect_shots([str(_abs(s)) for s in shots])
    except SystemExit as e:  # SystemExit -> clean tool error
        raise ValueError(str(e)) from None


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
def analyze_screenshots(shots: list[str], app: str = "", about: str = "", category: Optional[str] = None,
                        dimension: str = "auto", background_color: Optional[str] = None, tone: str = "") -> dict:
    """Analyse app screenshots locally (dominant brand colour, light/dark UI, visual density) and return shotforge's
    rule-based 2D/3D template recommendation with reasons, plus the rules and template catalogue.
    Use this first; then look at the screenshots yourself and decide the design.

    shots: absolute paths of image files or of a folder containing them.
    category: one of fitness, health, finance, social, education, productivity, games, photo, travel, shopping,
              entertainment, lifestyle, utilities (inferred from app/about when omitted)."""
    sm = _collect(shots)
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
                       "Write natural marketing copy per language, never word-for-word translation. Do not invent claims."),
    )


def _create(shots, app, slides, langs, template, dimension, background_color, text_color, accent_color, count, about,
            category, tone, out_dir, device, layout, fmt, only, jobs, style):
    sm = _collect(shots)
    templates = cli.list_templates()
    langs = [l.strip() for l in langs if l.strip()] or ["en"]
    cli.check_langs(langs)
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
    cli.write_copy(copy_path, full)
    result = _render(copy_path, out, device, layout, fmt, only, jobs, style, langs)
    result["design"] = data["design"]
    if placeholder:
        result["warning"] = ("Some slide text was missing and was filled with generic placeholder copy. "
                             "Write the missing titles/subtitles yourself and call create_screenshots again (or edit "
                             f"{copy_path} and call render_screenshots).")
    result["copy_file"] = str(copy_path)
    return result


def _render(copy_path: Path, out: Path, device, layout, fmt, only, jobs, style, langs=None, overrides=None):
    import argparse as ap
    ov = overrides or {}
    a = ap.Namespace(
        copy=str(copy_path), out=str(out), device=device, layout=layout, format=fmt, jobs=jobs, style=style or None, bg=None,
        frame=None, text_position=None, align=None, font=None, perspective=None, no_grain=False,
        only=",".join(map(str, only)) if only else None, sheet=False, brief=False, shots=[],
        template=ov.get("template"), bg_color=ov.get("background"), text_color=ov.get("text"), accent=ov.get("accent"),
        langs=",".join(langs) if langs else None)
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
    tone: str = "confident, friendly, benefit-first",
    out_dir: str = "~/shotforge-out",
    device: str = "iphone-6.9",
    layout: str = "flat",
    format: str = "png",
    only: Optional[list[int]] = None,
    jobs: int = 2,
    style: Optional[str] = None,
) -> list:
    """Create App Store screenshots: ONE SEPARATE IMAGE FILE per page per language (1320x2868 for iphone-6.9).

    shots: absolute paths of the real app screenshots (files or a folder).
    slides: the page texts you (the host Claude) or the user decided, in order. Each item:
        {"shot": "01_home.png"  (file name, optional), "title": {"en": "Plan your *day*", "tr": "Gününü *planla*"},
         "subtitle": {"en": "...", "tr": "..."}}      (*word* = accent colour)
        Missing pages/languages are filled with generic placeholder copy and reported as a warning.
    langs: e.g. ["en","tr"] (default ["en"]).
    template/dimension: 'auto' lets shotforge's rules choose (2D = flat/in-plane phone, 3D = perspective phone). Use
        list_templates for names. background_color/text_color/accent_color: '#RRGGBB' (optional; text colour is auto-fixed if unreadable).
    count: number of pages 1-10 (defaults to len(slides), else 6-7).
    layout: 'flat' (out/<lang>/NN_name.png) or 'asc' (out/<locale>/<DISPLAY_TYPE>/NN_name.png for App Store Connect uploaders).
    only: render only these 1-based page numbers (faster iteration). Returns file paths and a small overview image."""
    res = await asyncio.to_thread(_create, shots, app, slides, langs or ["en"], template, dimension, background_color,
                                  text_color, accent_color, count, about, category, tone, out_dir, device, layout,
                                  format, only, jobs, style)
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
    device: str = "iphone-6.9",
    layout: str = "flat",
    format: str = "png",
    jobs: int = 2,
) -> list:
    """Re-render from a shotforge.copy.json (written by create_screenshots, editable by hand): use after fixing text or
    trying another template/colours. Overrides are optional. Returns file paths and a small overview image."""
    cp = _abs(copy_file)
    if not cp.exists():
        raise ValueError(f"copy file not found: {cp}")
    _valid_device(device, DEVICES)
    _builtin_template((cli.read_copy(cp).get("design") or {}).get("template", "classic"))
    if template:
        template = _builtin_template(template)
    out = _abs(out_dir) if out_dir else cp.parent
    ov = dict(template=template, background=_valid_hex(background_color, "background_color"),
              text=_valid_hex(text_color, "text_color"), accent=_valid_hex(accent_color, "accent_color"))
    res = await asyncio.to_thread(_render, cp, out, device, layout, format, only, jobs, None, None, ov)
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


@mcp.prompt(name="store_screenshots", description="Guided workflow: analyse screenshots, ask for design choices, create separate 2D/3D App Store images")
def store_screenshots_prompt(folder: str, app: str, about: str = "") -> str:
    return (
        f"Create App Store screenshots for the app '{app}' ({about or 'infer from the screenshots'}) from the real screenshots in {folder}.\n"
        "1. Call analyze_screenshots(shots=[folder], app, about).\n"
        "2. Open and LOOK at every screenshot yourself.\n"
        "3. In ONE message ask the user which of these they want to fix (answer 'auto' = you decide): main background colour (#hex), "
        "text colour, accent colour, number of pages (1-10, auto = 6-7), 2D or 3D (or a template name), languages, and whether they "
        "will write the headline text or you should.\n"
        "4. Decide the rest (colours derived from the app's look; template per the returned rules; page copy in each language: benefit-first, "
        "max 5 words, *accent* words, subtitle one line). Keep any text the user wrote exactly.\n"
        "5. Call create_screenshots with your decisions. Show the returned overview image, check text overflow / wrong translation / "
        "contrast / screenshot-headline mismatch, fix with render_screenshots (edit the copy file) and tell the user where the separate "
        "image files are. Do not upload anything unless asked."
    )


def main(argv=None):
    ap = argparse.ArgumentParser(prog="shotforge-mcp", description="shotforge MCP server (stdio)")
    ap.add_argument("--version", action="store_true")
    a = ap.parse_args(argv)
    if a.version:
        from . import __version__
        print(__version__)
        return
    mcp.run()  # stdio


if __name__ == "__main__":
    main()
