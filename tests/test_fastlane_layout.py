import json
import zipfile

from PIL import Image

from shotforge.cli import main


def create(shots_dir, out, *extra):
    main(["create", str(shots_dir), "--app", "Demo", "--ai", "offline", "--no-ask", "--no-brand", "--count", "2", "--jobs", "2",
          "--layout", "fastlane", "--langs", "en,tr", "--header-sizes", "1920x1280", "-o", str(out), *extra])


def test_deliver_and_supply_tree(shots_dir, tmp_path):
    out = tmp_path / "o"
    create(shots_dir, out, "--devices", "iphone-6.9,iphone-6.5,android-phone,android-tablet-7")
    ios = out / "fastlane" / "screenshots"
    assert sorted(p.name for p in ios.iterdir()) == ["en-US", "tr"]
    names = sorted(p.name for p in (ios / "en-US").iterdir())
    assert names == ["APP_IPHONE_65_iphone-6.5_01_home.png", "APP_IPHONE_65_iphone-6.5_02_stats.png",
                     "APP_IPHONE_67_iphone-6.9_01_home.png", "APP_IPHONE_67_iphone-6.9_02_stats.png"]
    with Image.open(ios / "en-US" / names[2]) as im:
        assert im.size == (1320, 2868)
    play = out / "fastlane" / "metadata" / "android"
    assert sorted(p.name for p in play.iterdir()) == ["en-US", "tr-TR"]
    images = play / "tr-TR" / "images"
    assert sorted(p.name for p in images.iterdir()) == ["featureGraphic.png", "phoneScreenshots", "sevenInchScreenshots"]
    assert sorted(p.name for p in (images / "phoneScreenshots").iterdir()) == ["01_home.png", "02_stats.png"]
    with Image.open(images / "featureGraphic.png") as im:
        assert im.size == (1024, 500)
    # başlık görselleri deliver klasörüne girmez
    assert (out / "app-store-extra" / "en" / "header-search" / "1920x1280.png").exists()
    assert not list(ios.rglob("1920x1280*"))


def test_manifest_and_zip_cover_the_tree(shots_dir, tmp_path):
    out = tmp_path / "o"
    create(shots_dir, out)
    m = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    by_path = {f["path"]: f for f in m["files"]}
    assert all((out / p).exists() for p in by_path)
    fg = by_path["fastlane/metadata/android/en-US/images/featureGraphic.png"]
    assert fg["store"] == "google-play" and fg["target"] == "feature-graphic" and "feature graphic" in fg["works_with"]
    shot = by_path["fastlane/screenshots/tr/APP_IPHONE_67_iphone-6.9_01_home.png"]
    assert shot["store"] == "app-store" and shot["target"] == "iphone-6.9" and shot["lang"] == "tr"
    assert set(zipfile.ZipFile(out / "demo.zip").namelist()) == set(by_path) | {"manifest.json"}


def test_zip_only_removes_the_tree(shots_dir, tmp_path):
    out = tmp_path / "o"
    create(shots_dir, out, "--zip-only")
    assert not (out / "fastlane").exists() and not (out / "app-store-extra").exists()
    assert (out / "demo.zip").exists() and (out / "shotforge.copy.json").exists()
