import json

import numpy as np
import pytest
from PIL import Image

from shotforge import brand as B, themes as TH
from shotforge.cli import main


def run(*args):
    main(list(map(str, args)))


def plan(shots_dir, copy, *extra):
    run("plan", shots_dir, "--app", "Demo", "--ai", "offline", "--no-ask", "--copy", copy, *extra)
    return json.loads(copy.read_text(encoding="utf-8"))


def test_every_theme_is_well_formed():
    from shotforge import engine, palette as P
    for name, t in TH.THEMES.items():
        assert all(P.is_hex(c) for c in t["colors"].values()), name
        assert t["deco"] and all(d["type"] == "scatter" for d in t["deco"]), name
        for d in t["deco"]:
            img = Image.new("RGBA", (400, 800))
            engine.deco_scatter(img, d, 0, dict(text_band=(0, 100)))  # raises on an unknown shape


def test_get_accepts_loose_names_and_rejects_unknown():
    assert TH.get("New-Year")["name"] == "newyear" and TH.get("Halloween")["name"] == "halloween"
    with pytest.raises(ValueError, match="Available"):
        TH.get("easter-bunny")


def test_theme_fills_colours_but_user_colours_win(shots_dir, tmp_path):
    d = plan(shots_dir, tmp_path / "a.json", "--theme", "halloween")
    assert d["design"]["theme"] == "halloween"
    assert d["design"]["colors"]["background"] == TH.THEMES["halloween"]["colors"]["background"].lower()
    d = plan(shots_dir, tmp_path / "b.json", "--theme", "halloween", "--accent", "#00ff00")
    assert d["design"]["colors"]["accent"] == "#00ff00"


def test_no_theme_leaves_design_untouched(shots_dir, tmp_path):
    assert "theme" not in plan(shots_dir, tmp_path / "c.json")["design"]


def test_unknown_theme_is_a_clean_error(shots_dir, tmp_path):
    with pytest.raises(SystemExit, match="Unknown theme"):
        plan(shots_dir, tmp_path / "d.json", "--theme", "nope")


def _render(copy, out, *extra):
    run("render", "--copy", copy, "--only", "1", "--langs", "en", "--jobs", 1, "-o", out, *extra)
    return np.asarray(Image.open(next(out.rglob("*.png"))).convert("RGB"), dtype=int)


def test_render_draws_theme_decorations_and_can_restyle(shots_dir, tmp_path):
    copy = tmp_path / "c.json"
    plan(shots_dir, copy, "--template", "halo", "--no-ask")
    plain = _render(copy, tmp_path / "plain")
    themed = _render(copy, tmp_path / "themed", "--theme", "christmas")
    assert np.abs(plain - themed).mean() > 5  # new colours and snow
    # restyle from the file's own theme: plan with a theme, render without a flag
    t = plan(shots_dir, tmp_path / "t.json", "--theme", "newyear", "--template", "halo")
    assert t["design"]["theme"] == "newyear"
    assert np.abs(plain - _render(tmp_path / "t.json", tmp_path / "ny")).mean() > 5


def test_brand_file_theme(tmp_path):
    f = tmp_path / B.FILE_NAME
    f.write_text('[brand]\ntheme = "valentines"\n', encoding="utf-8")
    assert B.load(f)["theme"] == "valentines"
    f.write_text('[brand]\ntheme = "nope"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="brand.theme"):
        B.load(f)


def test_themes_command_lists_all(capsys):
    run("themes")
    out = capsys.readouterr().out
    assert all(n in out for n in TH.names())
