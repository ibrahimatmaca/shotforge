import json

import numpy as np
import pytest
from PIL import Image

from shotforge import brand as B
from shotforge.cli import main

TOML = """
[brand]
tone = "calm"
langs = ["de"]
template = "bubble"
logo = "logo.png"
[brand.colors]
background = "#123456"
text = "#FFFFFF"
accent = "#FDE047"
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    Image.new("RGBA", (400, 100), (255, 0, 0, 255)).save(tmp_path / "logo.png")
    (tmp_path / B.FILE_NAME).write_text(TOML, encoding="utf-8")
    (tmp_path / ".git").mkdir()
    sub = tmp_path / "work"
    sub.mkdir()
    monkeypatch.chdir(sub)
    return tmp_path


def plan(shots_dir, *extra):
    main(["plan", str(shots_dir), "--app", "Demo", "--ai", "offline", "--no-ask", "--copy", "c.json", *extra])
    return json.load(open("c.json", encoding="utf-8"))


def test_found_in_parent_and_applied(project, shots_dir):
    d = plan(shots_dir)
    assert d["tone"] == "calm" and d["langs"] == ["de"] and d["design"]["template"] == "bubble"
    assert d["design"]["colors"]["background"] == "#123456"


def test_flags_beat_the_file(project, shots_dir):
    d = plan(shots_dir, "--tone", "bold", "--langs", "en", "--template", "classic", "--bg-color", "#000000")
    assert d["tone"] == "bold" and d["langs"] == ["en"] and d["design"]["template"] == "classic"
    assert d["design"]["colors"]["background"] == "#000000"


def test_dimension_flag_beats_brand_template(project, shots_dir):
    assert plan(shots_dir, "--dimension", "3d")["design"]["dimension"] == "3d"


def test_no_brand_ignores_the_file(project, shots_dir):
    d = plan(shots_dir, "--no-brand")
    assert d["tone"] != "calm" and d["langs"] == ["en", "tr"]


def test_search_stops_at_repo_root(tmp_path):
    (tmp_path / B.FILE_NAME).write_text("[brand]\n", encoding="utf-8")
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    assert B.find(repo) is None and B.find(tmp_path) == tmp_path / B.FILE_NAME


@pytest.mark.parametrize("body, msg", [
    ('[brand]\nlogo = "../secret.png"\n', "must stay inside"),
    ('[brand]\nfont = "/etc/hosts"\n', "must stay inside"),
    ('[brand]\nlogo = "x.png"\n', "existing"),
    ('[brand]\ncolour = "#fff"\n', "unknown brand keys"),
    ('[brand.colors]\nbackground = "red"\n', "hex colour"),
    ('title = "x"\n[brand]\n', "single [brand]"),
    ('[brand\n', "not valid TOML"),
])
def test_rejects_bad_files(tmp_path, body, msg):
    d = tmp_path / "p"
    d.mkdir()
    Image.new("RGB", (10, 10)).save(tmp_path / "secret.png")
    (d / B.FILE_NAME).write_text(body, encoding="utf-8")
    with pytest.raises(ValueError, match=msg.replace("[", r"\[")):
        B.load(d / B.FILE_NAME)


def test_symlink_cannot_escape(tmp_path):
    d = tmp_path / "p"
    d.mkdir()
    Image.new("RGB", (10, 10)).save(tmp_path / "secret.png")
    (d / "logo.png").symlink_to(tmp_path / "secret.png")
    (d / B.FILE_NAME).write_text('[brand]\nlogo = "logo.png"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="must stay inside"):
        B.load(d / B.FILE_NAME)


def _red_box(path):
    a = np.asarray(Image.open(path).convert("RGB")).astype(int)
    ys, xs = np.nonzero((a[..., 0] > 200) & (a[..., 1] < 60) & (a[..., 2] < 60))
    return a.shape, ys, xs


@pytest.mark.parametrize("position", ["top", "bottom"])
def test_logo_is_drawn_in_the_free_band_on_slides_only_via_flag_or_brand(project, shots_dir, position):
    main(["create", str(shots_dir), "--app", "Demo", "--ai", "offline", "--no-ask", "--count", "1", "--device", "iphone-6.9",
          "--text-position", position, "--align", "center", "--jobs", "1", "--no-grain", "-o", "out", "--copy", "c.json"])
    (h, w, _), ys, xs = _red_box("out/de/01_de.png")
    assert len(ys), "logo not found"
    band = h * 0.06
    assert (ys.max() < band) if position == "top" else (ys.min() > h - band)
    assert abs((xs.min() + xs.max()) / 2 - w / 2) < w * 0.02  # metinle aynı hizada


def test_copy_file_cannot_set_a_logo(project, shots_dir, tmp_path, capsys):
    main(["plan", str(shots_dir), "--app", "Demo", "--ai", "offline", "--no-ask", "--copy", "c.json", "--no-brand", "--count", "1"])
    d = json.load(open("c.json", encoding="utf-8"))
    d["slides"][0]["logo"] = str(project / "logo.png")
    json.dump(d, open("c.json", "w", encoding="utf-8"))
    main(["render", "--copy", "c.json", "-o", "out2", "--jobs", "1", "--no-brand", "--langs", "en", "--no-grain"])
    assert "'logo' field in the copy file was ignored" in capsys.readouterr().err
    assert not len(_red_box("out2/en/01_en.png")[1])
