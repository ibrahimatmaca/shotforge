"""shotforge.toml: her çalıştırmada aynı görünüm için marka ayarları.

Öncelik: CLI bayrağı > bu dosya > model çıktısı > kurallar. Dosya bir depoyla birlikte gelebilir, bu yüzden font ve logo
yolları dosyanın kendi klasörünün dışına çıkamaz: başka bir yerdeki görsel çıktıya gömülmesin.
"""
from __future__ import annotations

from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

from . import palette as P

FILE_NAME = "shotforge.toml"
FONT_EXT = {".ttf", ".otf", ".ttc"}
LOGO_EXT = {".png", ".jpg", ".jpeg", ".webp"}
COLOR_KEYS = ("background", "text", "accent")
BRAND_KEYS = {"colors", "font", "logo", "tone", "langs", "template"}


def find(start: Path) -> Path | None:
    """Başlangıç klasöründen yukarı arar; depo kökünde veya ev klasöründe durur."""
    home = Path.home().resolve()
    for d in (start.resolve(), *start.resolve().parents):
        if (d / FILE_NAME).is_file():
            return d / FILE_NAME
        if d == home or (d / ".git").exists():
            return None
    return None


def _inside(root: Path, value, exts: set[str], label: str, path: Path) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{path}: brand.{label} must be a path")
    f = (root / value).resolve()
    if not f.is_relative_to(root):
        raise ValueError(f"{path}: brand.{label} must stay inside the folder of {FILE_NAME}: {value}")
    if f.suffix.lower() not in exts or not f.is_file():
        raise ValueError(f"{path}: brand.{label} must be an existing {'/'.join(sorted(exts))} file: {value}")
    return str(f)


def load(path) -> dict:
    """Doğrulanmış marka ayarları: colors, tone, langs, template, font (mutlak yol), logo (mutlak yol)."""
    path = Path(path).expanduser()
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"{path} is not valid TOML: {e}") from None
    b = raw.get("brand")
    if not isinstance(b, dict) or set(raw) != {"brand"}:
        raise ValueError(f"{path}: expected a single [brand] table")
    unknown = set(b) - BRAND_KEYS
    if unknown:
        raise ValueError(f"{path}: unknown brand keys: {', '.join(sorted(unknown))}. Allowed: {', '.join(sorted(BRAND_KEYS))}")
    colors = b.get("colors", {})
    if not isinstance(colors, dict) or set(colors) - set(COLOR_KEYS):
        raise ValueError(f"{path}: brand.colors may only have {', '.join(COLOR_KEYS)}")
    for k, v in colors.items():
        if not isinstance(v, str) or not P.is_hex(v):
            raise ValueError(f"{path}: brand.colors.{k} must be a hex colour like #6D28D9")
    for k in ("tone", "template"):
        if k in b and not isinstance(b[k], str):
            raise ValueError(f"{path}: brand.{k} must be text")
    langs = b.get("langs", [])
    if not isinstance(langs, list) or not all(isinstance(l, str) for l in langs):
        raise ValueError(f"{path}: brand.langs must be a list like [\"en\", \"tr\"]")
    root = path.resolve().parent
    out = dict(colors=colors, tone=b.get("tone"), template=b.get("template"), langs=langs, font=None, logo=None)
    if "font" in b:
        out["font"] = _inside(root, b["font"], FONT_EXT, "font", path)
    if "logo" in b:
        out["logo"] = _inside(root, b["logo"], LOGO_EXT, "logo", path)
    return out
