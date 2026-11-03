import json
import shutil
import zipfile
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from shotforge import cli, engine
from shotforge.cli import main


def run(*args):
    main(list(map(str, args)))


@pytest.fixture(scope="module")
def full(shots_dir, tmp_path_factory):
    out = tmp_path_factory.mktemp("run") / "demo"
    run("create", shots_dir, "--app", "Kışlık Demo", "--ai", "offline", "--no-ask", "--count", 2, "--langs", "en,tr", "--jobs", 2,
        "--sheet", "--header-sizes", "5244x2950,1920x1280", "-o", out)
    return out


def test_tree_per_language(full):
    for lang in ("en", "tr"):
        assert sorted(p.name for p in (full / lang / "app-store" / "iphone-6.9").iterdir()) == ["01_home.png", "02_stats.png"]
        assert sorted(p.name for p in (full / lang / "google-play" / "phone").iterdir()) == ["01_home.png", "02_stats.png"]
        assert sorted(p.name for p in (full / lang / "app-store" / "header-search").iterdir()) == ["1920x1280.png", "5244x2950.png"]
        assert (full / lang / "google-play" / "feature-graphic" / "1024x500.png").exists()


def test_every_file_has_exact_size_and_no_alpha(full):
    want = {"iphone-6.9": (1320, 2868), "phone": (1080, 1920), "header-search": None, "feature-graphic": (1024, 500)}
    for p in full.glob("*/*/*/*.png"):
        with Image.open(p) as im:
            size = want[p.parent.name] or tuple(map(int, p.stem.split("x")))
            assert im.size == size and im.mode == "RGB", p


def test_zip_matches_manifest_and_excludes_review(full):
    manifest = json.loads((full / "manifest.json").read_text(encoding="utf-8"))
    names = set(zipfile.ZipFile(full / "kislik-demo.zip").namelist())
    assert names == {f["path"] for f in manifest["files"]} | {"manifest.json"}
    assert not any(n.startswith("_review") or "copy.json" in n for n in names)
    assert (full / "_review" / "overview_en.jpg").exists()
    assert any("Google Play" in w for w in manifest["warnings"])
    assert any("App Store: search results only" in f.get("works_with", "") for f in manifest["files"])


def test_render_keeps_store_targets_and_stale_files_stay_out_of_zip(full, tmp_path):
    work = tmp_path / "demo"
    shutil.copytree(full, work)
    stale = work / "en" / "google-play" / "phone" / "99_old.png"
    Image.new("RGB", (10, 10)).save(stale)
    copy = work / "shotforge.copy.json"
    d = json.loads(copy.read_text(encoding="utf-8"))
    d["shots"] = {k: str((full / v).resolve()) for k, v in d["shots"].items()}
    d["targets"]["banners"] = []
    d["targets"]["devices"] = ["android-phone"]
    copy.write_text(json.dumps(d), encoding="utf-8")
    run("render", "--copy", copy, "--langs", "en", "--jobs", 1, "-o", work)
    names = zipfile.ZipFile(work / "kislik-demo.zip").namelist()
    assert "en/google-play/phone/01_home.png" in names and not any("99_old" in n for n in names)


def test_only_skips_zip_and_banners(full, tmp_path):
    work = tmp_path / "demo"
    work.mkdir()
    d = json.loads((full / "shotforge.copy.json").read_text(encoding="utf-8"))
    d["shots"] = {k: str((full / v).resolve()) for k, v in d["shots"].items()}
    (work / "c.json").write_text(json.dumps(d), encoding="utf-8")
    run("render", "--copy", work / "c.json", "--only", 1, "--langs", "en", "--jobs", 1, "-o", work)
    assert not (work / "manifest.json").exists() and not list(work.glob("*.zip"))
    assert not (work / "en" / "app-store" / "header-search").exists()


def test_play_is_capped_at_eight_pages(shots_dir, tmp_path):
    copy = tmp_path / "c.json"
    run("plan", shots_dir, "--app", "D", "--ai", "offline", "--no-ask", "--copy", copy, "--count", 9, "--langs", "en")
    d = json.loads(copy.read_text(encoding="utf-8"))
    assert len(d["slides"]) == 9
    d["targets"] = dict(devices=["android-phone"], banners=[], output="folder")
    a = cli.argparse.Namespace(copy=str(copy), out=str(tmp_path / "o"), device=None, layout=None, format="jpg", jobs=2, style=None,
                               bg=None, frame=None, text_position=None, align=None, font=None, perspective=None, no_grain=True,
                               only=None, sheet=False, brief=False, shots=[], template=None, bg_color=None, text_color=None,
                               accent=None, langs="en")
    paths = cli.cmd_render(a, d)
    assert len(paths["en"]) == 8 and any("at most 8" in w for w in a.warnings)


def test_zip_only_leaves_no_language_folders(shots_dir, tmp_path):
    out = tmp_path / "demo"
    run("create", shots_dir, "--app", "Demo", "--ai", "offline", "--no-ask", "--count", 1, "--langs", "en", "--store", "playstore",
        "--zip-only", "--jobs", 1, "-o", out)
    assert not (out / "en").exists() and (out / "shotforge.copy.json").exists()
    assert "en/google-play/phone/01_home.png" in zipfile.ZipFile(out / "demo.zip").namelist()


def test_zip_never_touches_files_next_to_the_output_folder(shots_dir, tmp_path):
    neighbour = tmp_path / "demo.zip"
    neighbour.write_text("kullanıcının dosyası", encoding="utf-8")
    run("create", shots_dir, "--app", "Demo", "--ai", "offline", "--no-ask", "--count", 1, "--langs", "en", "--store", "playstore",
        "--jobs", 1, "-o", tmp_path / "demo")
    assert neighbour.read_text(encoding="utf-8") == "kullanıcının dosyası"
    assert (tmp_path / "demo" / "demo.zip").exists()


def test_universal_header_content_survives_both_store_crops(tmp_path):
    """5244x2950 hem 21:9 hem 3:2 kırpılır; kesilen şeritlerde yalnızca arka plan kalmalı."""
    shot = tmp_path / "s.png"
    Image.new("RGB", (400, 860), (255, 0, 0)).save(shot)
    cfg = dict(banner="5244x2950", device="iphone-6.9", bg="#000000,#000000", no_glow=True, frame="silver", dimension="3d",
               image=str(shot), title="Hello there", subtitle="Sub", text_color="#ffffff", accent="#ffffff")
    a = np.asarray(engine.render_banner(cfg, "en"))
    h, w = a.shape[:2]
    keep_y, keep_x = round(w * 9 / 21), round(h * 3 / 2)
    y0, x0 = (h - keep_y) // 2, (w - keep_x) // 2
    assert a[:y0].max() < 40 and a[y0 + keep_y:].max() < 40  # 21:9 kırpma
    assert a[:, :x0].max() < 40 and a[:, x0 + keep_x:].max() < 40  # 3:2 kırpma


def _solid(path, colour):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (400, 860), colour).save(path)


def test_language_and_platform_folders_are_used(tmp_path):
    root = tmp_path / "shots"
    _solid(root / "en" / "01_a.png", (200, 20, 20))
    _solid(root / "tr" / "01_a.png", (20, 200, 20))
    _solid(root / "android" / "tr" / "01_a.png", (20, 20, 200))
    _solid(root / "old" / "01_a.png", (0, 0, 0))  # dil değil
    base, sets = cli.scan_shots([str(root)], ["en", "tr"])
    assert base["01_a.png"].parent.name == "en" and set(sets) == {"/en", "/tr", "android/tr"}
    notes = []
    assert cli.shots_for("iphone-6.9", "tr", base, sets, notes)["01_a.png"].parent.name == "tr"
    assert cli.shots_for("android-phone", "tr", base, sets, notes)["01_a.png"].parts[-3:-1] == ("android", "tr")
    assert not notes
    assert cli.shots_for("iphone-6.9", "de", base, sets, notes)["01_a.png"].parent.name == "en"
    assert any("[de]" in n for n in notes)
    cli.shots_for("android-phone", "en", base, sets, notes)
    assert any("no Android screenshots" in n for n in notes)

    out = tmp_path / "o"
    run("create", root, "--app", "D", "--ai", "offline", "--no-ask", "--count", 1, "--langs", "en,tr", "--store", "appstore",
        "--header-sizes", "no", "--jobs", 1, "-o", out, "--template", "classic")
    pixel = lambda lang: Image.open(out / lang / "app-store" / "iphone-6.9" / "01_a.png").getpixel((660, 2000))  # noqa: E731
    assert pixel("en")[0] > pixel("en")[1] and pixel("tr")[1] > pixel("tr")[0]


def test_android_frame_uses_tall_screen_without_island():
    d = engine.DEVICES["android-phone"]
    assert engine.screen_ratio(d) > 2 and d["island"] is False
    assert engine.screen_ratio(engine.DEVICES["iphone-6.9"]) == 2868 / 1320


def test_previews_warn_instead_of_failing(tmp_path, monkeypatch):
    warnings = []
    monkeypatch.setattr(cli.shutil, "which", lambda _: None)
    assert cli.convert_previews(tmp_path, {"*": "a.mov"}, ["en"], ["iphone-6.9"], warnings) == []
    assert "ffmpeg" in warnings[0]
    monkeypatch.setattr(cli.shutil, "which", lambda _: "/bin/x")
    warnings.clear()
    assert cli.convert_previews(tmp_path, {"*": "/etc/hosts"}, ["en"], ["iphone-6.9"], warnings) == []
    assert "skipped" in warnings[0]


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg is not installed")
def test_preview_conversion_goes_to_language_folder(tmp_path):
    src = tmp_path / "rec.mp4"
    cli.subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=blue:s=320x640:d=16", str(src)], check=True)
    warnings = []
    files = cli.convert_previews(tmp_path / "o", {"*": str(src)}, ["en", "tr"], ["iphone-6.9"], warnings)
    assert not warnings and {Path(f).parts[-4] for f in files} == {"en", "tr"} and all(Path(f).exists() for f in files)
