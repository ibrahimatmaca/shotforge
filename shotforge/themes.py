"""Seasonal and event themes: a colour set plus decorations drawn behind the phone (snow, confetti, bats, hearts …).

A theme only fills gaps: colours you pass (--bg-color, --accent, shotforge.toml) always win over the theme's colours.
Decoration items use the engine's "scatter" type, so a copy file may also list them per slide under "deco".
"""
from __future__ import annotations

THEMES: dict[str, dict] = {
    "newyear": dict(
        label="New Year", description="Deep navy night with gold, silver and pink confetti and stars.",
        colors=dict(background="#0B1026", accent="#FBBF24"),
        deco=[dict(type="scatter", shape="confetti", colors=["#FBBF24", "#E5E7EB", "#F472B6"], count=36, alpha=0.85),
              dict(type="scatter", shape="star", color="#FDE68A", count=9, alpha=0.8)]),
    "christmas": dict(
        label="Christmas", description="Deep red with falling snow and a few gold stars.",
        colors=dict(background="#7F1D1D", accent="#86EFAC"),
        deco=[dict(type="scatter", shape="snow", color="#FFFFFF", count=40, alpha=0.85),
              dict(type="scatter", shape="star", color="#FDE047", count=6, alpha=0.85)]),
    "halloween": dict(
        label="Halloween", description="Dark violet with orange accents, bats and glowing dots.",
        colors=dict(background="#2E1065", accent="#FB923C"),
        deco=[dict(type="scatter", shape="bat", color="#0B0614", count=9, alpha=0.9, size_min=0.022, size_max=0.045),
              dict(type="scatter", shape="dot", color="#FB923C", count=14, alpha=0.55, size_min=0.004, size_max=0.01)]),
    "valentines": dict(
        label="Valentine's Day", description="Raspberry pink with floating hearts.",
        colors=dict(background="#9D174D", accent="#FBCFE8"),
        deco=[dict(type="scatter", shape="heart", color="#FDA4AF", count=22, alpha=0.6, size_min=0.014, size_max=0.034)]),
    "ramadan": dict(
        label="Ramadan / Eid", description="Emerald green with a gold crescent and stars.",
        colors=dict(background="#064E3B", accent="#FCD34D"),
        deco=[dict(type="scatter", shape="crescent", color="#FCD34D", count=3, alpha=0.75, size_min=0.03, size_max=0.05),
              dict(type="scatter", shape="star", color="#FDE68A", count=12, alpha=0.8)]),
    "summer": dict(
        label="Summer", description="Sky blue with bright bubbles and sun-yellow sparkles.",
        colors=dict(background="#0EA5E9", accent="#FDE047"),
        deco=[dict(type="scatter", shape="dot", color="#FFFFFF", count=26, alpha=0.35, size_min=0.008, size_max=0.03),
              dict(type="scatter", shape="star", color="#FEF08A", count=7, alpha=0.9)]),
    "blackfriday": dict(
        label="Black Friday", description="Near-black with electric yellow sparks.",
        colors=dict(background="#0A0A0A", accent="#FACC15"),
        deco=[dict(type="scatter", shape="star", color="#FACC15", count=12, alpha=0.85),
              dict(type="scatter", shape="confetti", colors=["#FACC15", "#FFFFFF"], count=22, alpha=0.7)]),
}


def names() -> list[str]:
    return list(THEMES)


def get(name: str) -> dict:
    """Theme by name (case-insensitive). Raises ValueError listing the valid names."""
    key = str(name or "").strip().lower().replace("-", "").replace("_", "").replace(" ", "")
    key = {"yilbasi": "newyear", "cadilarbayrami": "halloween", "sevgililergunu": "valentines"}.get(key, key)
    if key not in THEMES:
        raise ValueError(f"Unknown theme: {name}. Available: {', '.join(THEMES)}")
    return dict(THEMES[key], name=key)


def is_none(name) -> bool:
    return name is None or str(name).strip().lower() in {"", "none", "no", "off", "auto", "default"}
