"""
shotforge.texts — kullanıcının kendi yazdığı slayt metinlerini okur (JSON veya düz metin blokları).

Düz metin biçimi (.txt / .md), slaytlar boş satırla veya '#' satırıyla ayrılır:

    # slayt 1
    shot: 01_home.png            (isteğe bağlı; yoksa sırayla atanır / Claude seçer)
    en: Build habits that *stick* | Your whole day on one calm screen
    tr: Kalıcı *alışkanlıklar* edin | Tüm günün tek sakin ekranda

    # slayt 2
    en: Plan your *day*
    tr: Gününü *planla*

'|' işaretinden sonrası alt başlıktır (isteğe bağlı). Eksik dil/slayt varsa Claude tamamlar.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

LANG_RE = re.compile(r"^[a-z]{2,3}(-[A-Za-z]{2,4})?$")


def _split(value: str) -> tuple[str, str]:
    t, _, s = value.partition("|")
    return t.strip().replace("\\n", "\n"), s.strip().replace("\\n", "\n")


def parse_text_file(path: str | Path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"Metin dosyası bulunamadı: {p}")
    raw = p.read_text(encoding="utf-8")
    if p.suffix.lower() == ".json":
        data = json.loads(raw)
        slides = data["slides"] if isinstance(data, dict) else data
        return [normalize(s) for s in slides]
    blocks: list[list[str]] = [[]]
    for line in raw.splitlines():
        if line.strip().startswith("#"):
            if blocks[-1]:
                blocks.append([])
            continue
        if not line.strip():
            if blocks[-1]:
                blocks.append([])
            continue
        blocks[-1].append(line.strip())
    out = []
    for b in blocks:
        if not b:
            continue
        s: dict = {"title": {}, "subtitle": {}}
        for line in b:
            if ":" not in line:
                raise SystemExit(f"Metin dosyasında anlaşılmayan satır: '{line}'  (biçim: 'tr: Başlık | Alt başlık')")
            k, _, v = line.partition(":")
            k = k.strip()
            if k.lower() == "shot":
                s["shot"] = v.strip()
            elif LANG_RE.match(k):
                t, st = _split(v)
                s["title"][k] = t
                if st:
                    s["subtitle"][k] = st
            else:
                raise SystemExit(f"Bilinmeyen anahtar '{k}' (dil kodu veya 'shot' bekleniyordu)")
        out.append(s)
    if not out:
        raise SystemExit(f"Metin dosyasında slayt bulunamadı: {p}")
    return out


def normalize(s: dict) -> dict:
    out = {"title": dict(s.get("title") or {}), "subtitle": dict(s.get("subtitle") or {})}
    if s.get("shot"):
        out["shot"] = s["shot"]
    return out


def missing(locked: list[dict | None], count: int, langs: list[str]) -> list[str]:
    """Kullanıcı metninde eksik olanları insan okuyabilir biçimde listeler."""
    notes = []
    for i in range(count):
        s = locked[i] if i < len(locked) else None
        if not s:
            notes.append(f"slayt {i + 1}: tamamen eksik")
            continue
        for l in langs:
            if not s["title"].get(l):
                notes.append(f"slayt {i + 1}: '{l}' başlığı eksik")
    return notes
