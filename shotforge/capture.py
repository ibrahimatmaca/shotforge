"""Maestro akışlarıyla dil başına ekran görüntüsü alır ve mevcut girdi düzenine yazar: <out>/[<platform>/]<lang>/NN_ad.png

shotforge cihazı açmaz, uygulamayı kurmaz ve cihaz dilini değiştirmez: açık cihazdaki kurulu uygulamayı akış sürer.
Maestro dili akışta değil cihazda ayarlar: ya her dile bir cihaz kullanılır ya da uygulama ${LANG} değerini açılışta okur.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

from .engine import DEVICES

SHOT_PLATFORMS = ("ios", "ipad", "android", "android-tablet")
DEFAULT_TIMEOUT = 900
_PLATFORM_DEVICES = {"ios": "iphone", "ipad": "ipad", "android": "android-phone", "android-tablet": "android-tablet"}
_ASPECT_TOLERANCE = 0.10


def _new_screens(run: Path) -> list[Path]:
    """Maestro 2.11: artifacts/<zaman>/<akış>/takeScreenshot/<ad>.png. Eski sürümler çalışma klasörüne yazar."""
    shots = [*run.glob("*.png"), *(p for p in (run / "artifacts").rglob("*.png") if "takeScreenshot" in p.parts)]
    shots = [p for p in shots if p.is_file() and not p.is_symlink()]
    names = [p.name for p in shots]
    dup = sorted({n for n in names if names.count(n) > 1})
    if dup:
        raise ValueError(f"Two screenshots have the same name: {', '.join(dup)}. Give each takeScreenshot its own name.")
    return shots


def _run_flows(exe: str, flows: Path, lang: str, device: str | None, run: Path, timeout: int):
    cmd = [exe, "test", "--no-ansi", "-e", f"LANG={lang}", "--test-output-dir", str(run / "artifacts")]
    if device:
        cmd += ["--device", device]
    cmd.append(str(flows))
    try:
        r = subprocess.run(cmd, cwd=run, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Maestro did not finish in {timeout} s for [{lang}].") from None
    if r.returncode != 0:
        # Maestro hatanın ardından çerçeveli bir tanıtım kutusu basar; asıl neden kutunun üstünde kalır
        lines = [l for l in f"{r.stdout}\n{r.stderr}".splitlines() if l.strip() and l.strip()[0] not in "╭│╰"]
        tail = "\n".join(lines)[-800:]
        raise RuntimeError(f"Maestro failed for [{lang}] (exit {r.returncode}): {tail}")


def _aspect_warnings(files: list[Path], platform: str | None) -> list[str]:
    prefix = _PLATFORM_DEVICES.get(platform, "")
    aspects = [d.get("screen_aspect") or d["size"][1] / d["size"][0] for k, d in DEVICES.items() if k.startswith(prefix)]
    bad = []
    for f in files:
        with Image.open(f) as im:
            w, h = im.size
        if not any(abs(h / w - x) / x <= _ASPECT_TOLERANCE for x in aspects):
            bad.append(f"{f.name} ({w}x{h})")
    return [f"These screenshots don't fit the device screen shape and will be cropped: {', '.join(bad)}"] if bad else []


def capture(flows, langs: list[str], out, platform: str | None = None, device: str | None = None,
            timeout: int = DEFAULT_TIMEOUT, log=print) -> list[str]:
    """Dilleri sırayla çalıştırır ve uyarı listesi döndürür. Bir dil başarısız olursa durur; bitmiş diller yerinde kalır."""
    exe = shutil.which("maestro")
    if not exe:
        raise RuntimeError("`maestro` command not found. Install it from https://docs.maestro.dev, or put your screenshots in the folder yourself.")
    flows = Path(flows).expanduser().resolve()  # mutlak yol: "-" ile başlayan ad bayrak sanılmasın
    if not flows.exists():
        raise ValueError(f"Maestro flows not found: {flows}")
    if device and device.startswith("-"):
        raise ValueError(f"Invalid device ID: {device}")
    if platform and platform not in SHOT_PLATFORMS:
        raise ValueError(f"platform must be one of {', '.join(SHOT_PLATFORMS)}")
    out = Path(out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    names: dict[str, set[str]] = {}
    for lang in langs:
        log(f"Capturing [{lang}] with Maestro …")
        dest = out / platform / lang if platform else out / lang
        # Geçici klasör: yalnızca bu çalıştırmanın görüntüleri taşınır, hata olursa yarım set kalmaz
        with tempfile.TemporaryDirectory(dir=out, prefix=".capture-") as tmp:
            run = Path(tmp)
            _run_flows(exe, flows, lang, device, run, timeout)
            shots = _new_screens(run)
            if not shots:
                raise RuntimeError(f"Maestro finished for [{lang}] but wrote no PNG. Add takeScreenshot steps to your flows.")
            warnings += [f"[{lang}] {w}" for w in _aspect_warnings(shots, platform)]
            dest.mkdir(parents=True, exist_ok=True)
            # silmek kullanıcının dosyasına dokunur; ama kalan görüntü create'te slayt olur, bu yüzden söylenir
            stale = sorted(p.name for p in dest.glob("*.png") if p.name not in {s.name for s in shots})
            if stale:
                warnings.append(f"[{lang}] {dest} has older screenshots this run didn't take; delete them if those screens are gone, "
                                f"or they become slides: {', '.join(stale)}")
            for s in shots:
                os.replace(s, dest / s.name)
        names[lang] = {s.name for s in shots}
        log(f"  {len(shots)} screenshots: {dest}")
    all_names = set().union(*names.values())
    for lang, have in names.items():
        if have != all_names:
            warnings.append(f"[{lang}] is missing screenshots the other languages have: {', '.join(sorted(all_names - have))}. "
                            "shotforge matches screens by file name, so that language shows another language's UI there.")
    return warnings


def booted_devices() -> list[str]:
    """Açık simülatör ve emülatörler; yalnızca listeler, hiçbir cihazı başlatmaz."""
    found: list[str] = []
    try:
        if shutil.which("xcrun"):
            r = subprocess.run(["xcrun", "simctl", "list", "devices", "booted", "-j"], capture_output=True, text=True, timeout=10)
            found += [d["name"] for ds in json.loads(r.stdout or "{}").get("devices", {}).values() for d in ds]
        if shutil.which("adb"):
            r = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=10)
            found += [l.split()[0] for l in r.stdout.splitlines()[1:] if l.endswith("device")]
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return found
