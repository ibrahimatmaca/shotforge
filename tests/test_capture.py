"""capture is tested against a fake `maestro` executable. Real Maestro needs a booted device with the app installed."""
import os
import stat
import sys

import pytest
from PIL import Image

from shotforge import capture as C
from shotforge.cli import main

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX shim")

FAKE = '''#!/usr/bin/env python3
import os, sys
from pathlib import Path
from PIL import Image
a = sys.argv[1:]
assert a[0] == "test" and "--no-ansi" in a
lang = next(x for x in a if x.startswith("LANG=")).split("=")[1]
assert os.path.isabs(a[-1])
Path(os.environ["FAKE_LOG"]).open("a").write(" ".join(a) + "\\n")
if lang == os.environ.get("FAKE_FAIL"):
    print("flow failed"); print("╭──╮\\n│ Run on Maestro Cloud │\\n╰──╯", file=sys.stderr); sys.exit(3)
size = tuple(map(int, os.environ.get("FAKE_SIZE", "1320x2868").split("x")))
d = Path(a[a.index("--test-output-dir") + 1]) / "2026-11-04_013605" / "shots" / "takeScreenshot"  # Maestro 2.11 ile doğrulandı
d.mkdir(parents=True)
if lang != os.environ.get("FAKE_EMPTY"):
    Image.new("RGB", size, (20, 90, 200)).save(d / "01_home.png")
    Image.new("RGB", size, (90, 20, 200)).save((d if os.environ.get("FAKE_LAYOUT") != "cwd" else Path.cwd()) / "02_stats.png")
    if lang != os.environ.get("FAKE_SHORT"):
        Image.new("RGB", size, (90, 200, 20)).save(d / "03_goals.png")
'''


@pytest.fixture
def maestro(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    exe = bindir / "maestro"
    exe.write_text(FAKE.replace("#!/usr/bin/env python3", f"#!{sys.executable}"))
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    log = tmp_path / "log"
    monkeypatch.setenv("FAKE_LOG", str(log))
    (tmp_path / "flows").mkdir()
    return tmp_path


def test_writes_the_folder_layout_create_reads(maestro):
    out = maestro / "shots"
    assert C.capture(maestro / "flows", ["en", "tr"], out, device="SIM-1") == []
    for lang in ("en", "tr"):
        assert sorted(p.name for p in (out / lang).iterdir()) == ["01_home.png", "02_stats.png", "03_goals.png"]
    assert not list(out.glob(".capture-*"))
    log = (maestro / "log").read_text()
    assert "LANG=tr" in log and "--device SIM-1" in log


def test_platform_folder_and_screenshots_in_cwd(maestro, monkeypatch):
    monkeypatch.setenv("FAKE_LAYOUT", "cwd")  # eski Maestro çalışma klasörüne yazar
    C.capture(maestro / "flows", ["tr"], maestro / "s", platform="android")
    assert sorted(p.name for p in (maestro / "s" / "android" / "tr").iterdir()) == ["01_home.png", "02_stats.png", "03_goals.png"]


def test_failure_stops_and_reports(maestro, monkeypatch):
    monkeypatch.setenv("FAKE_FAIL", "tr")
    with pytest.raises(RuntimeError, match=r"failed for \[tr\] \(exit 3\): flow failed$"):
        C.capture(maestro / "flows", ["en", "tr"], maestro / "s")
    assert (maestro / "s" / "en" / "01_home.png").exists() and not (maestro / "s" / "tr").exists()
    assert not list((maestro / "s").glob(".capture-*"))


def test_no_screenshots_is_an_error(maestro, monkeypatch):
    monkeypatch.setenv("FAKE_EMPTY", "en")
    with pytest.raises(RuntimeError, match="takeScreenshot"):
        C.capture(maestro / "flows", ["en"], maestro / "s")


def test_warns_on_missing_names_and_wrong_shape(maestro, monkeypatch):
    monkeypatch.setenv("FAKE_SHORT", "tr")
    monkeypatch.setenv("FAKE_SIZE", "1000x1000")
    w = "\n".join(C.capture(maestro / "flows", ["en", "tr"], maestro / "s", platform="ios"))
    assert "[tr] is missing" in w and "03_goals.png" in w and "cropped" in w


def test_warns_about_older_screenshots_and_keeps_them(maestro):
    old = maestro / "s" / "en" / "99_old.png"
    old.parent.mkdir(parents=True)
    Image.new("RGB", (10, 10)).save(old)
    w = "\n".join(C.capture(maestro / "flows", ["en"], maestro / "s"))
    assert "99_old.png" in w and "01_home.png" not in w and old.exists()
    assert C.capture(maestro / "flows", ["en"], maestro / "s") != []  # hâlâ duruyor, hâlâ uyarır
    old.unlink()
    assert C.capture(maestro / "flows", ["en"], maestro / "s") == []  # aynı adların üzerine yazmak uyarı değil


def test_missing_maestro_and_bad_inputs(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(RuntimeError, match="not found"):
        C.capture(tmp_path, ["en"], tmp_path / "s")


def test_bad_device_and_language_are_refused(maestro):
    with pytest.raises(ValueError, match="Invalid device"):
        C.capture(maestro / "flows", ["en"], maestro / "s", device="--evil")
    with pytest.raises(SystemExit, match="Invalid language"):
        main(["capture", "--flows", str(maestro / "flows"), "--langs", "../x", "-o", str(maestro / "s")])


def test_create_with_maestro_uses_localized_screens(maestro):
    shots, out = maestro / "shots", maestro / "o"
    main(["create", str(shots), "--maestro", str(maestro / "flows"), "--app", "Demo", "--ai", "offline", "--no-ask", "--no-brand",
          "--count", "1", "--jobs", "1", "--store", "appstore", "--header-sizes", "no", "--langs", "en,tr", "-o", str(out)])
    assert (out / "tr" / "app-store" / "iphone-6.9" / "01_tr.png").exists()
    with Image.open(shots / "tr" / "01_home.png") as im:
        assert im.size == (1320, 2868)
