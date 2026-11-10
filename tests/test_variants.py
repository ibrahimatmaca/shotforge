import json

import pytest

from shotforge import copywriter as C, cli
from shotforge.cli import main

BASE = ["--app", "Demo", "--ai", "offline", "--no-ask", "--no-brand", "--count", "1", "--jobs", "2", "--store", "appstore",
        "--header-sizes", "no", "--langs", "en"]


def copies(out, n):
    return [json.loads((out / f"variant-{l}" / "shotforge.copy.json").read_text(encoding="utf-8")) for l in "abc"[:n]]


def test_design_variants_share_text_and_colours(shots_dir, tmp_path, capsys):
    out = tmp_path / "o"
    main(["create", str(shots_dir), *BASE, "--variants", "3", "--variant-by", "design", "-o", str(out)])
    a, b, c = copies(out, 3)
    assert len({a["design"]["template"], b["design"]["template"], c["design"]["template"]}) == 3
    assert a["design"]["dimension"] != b["design"]["dimension"]  # boyut serbestken B, 2D/3D farkını dener
    assert a["slides"][0]["title"] == b["slides"][0]["title"] == c["slides"][0]["title"]
    assert a["design"]["colors"]["background"] == b["design"]["colors"]["background"]
    for l in "abc":
        assert (out / f"variant-{l}" / "en" / "app-store" / "iphone-6.9" / "01_en.png").exists()
        assert (out / f"variant-{l}" / "demo.zip").exists()
    assert "same headlines" not in capsys.readouterr().err


def test_design_variant_respects_dimension(shots_dir, tmp_path):
    out = tmp_path / "o"
    main(["create", str(shots_dir), *BASE, "--variants", "2", "--variant-by", "design", "--dimension", "2d", "-o", str(out)])
    a, b = copies(out, 2)
    assert a["design"]["dimension"] == b["design"]["dimension"] == "2d"
    assert a["design"]["template"] != b["design"]["template"]


def test_offline_copy_variants_warn_about_identical_text(shots_dir, tmp_path, capsys):
    out = tmp_path / "o"
    main(["create", str(shots_dir), *BASE, "--variants", "2", "--variant-by", "copy", "-o", str(out)])
    a, b = copies(out, 2)
    assert a["design"]["template"] == b["design"]["template"]
    assert "same headlines" in capsys.readouterr().err


def test_each_variant_gets_its_own_fastlane_tree(shots_dir, tmp_path):
    out = tmp_path / "o"
    main(["create", str(shots_dir), *BASE, "--variants", "2", "--layout", "fastlane", "-o", str(out)])
    for l in "ab":
        assert (out / f"variant-{l}" / "fastlane" / "screenshots" / "en-US" / "APP_IPHONE_67_iphone-6.9_01_en.png").exists()


def test_copy_flag_gets_a_suffix_per_variant(shots_dir, tmp_path):
    out = tmp_path / "o"
    main(["create", str(shots_dir), *BASE, "--variants", "2", "--copy", str(tmp_path / "c.json"), "-o", str(out)])
    assert (tmp_path / "c.a.json").exists() and (tmp_path / "c.b.json").exists()


def test_variants_need_an_app_name(shots_dir, tmp_path):
    with pytest.raises(SystemExit, match="App name"):
        main(["create", str(shots_dir), "--ai", "offline", "--no-ask", "--no-brand", "--variants", "2", "-o", str(tmp_path / "o")])


def test_prompt_carries_the_variant_angle():
    ctx = dict(app="A", tone="", langs=["en"], templates=cli.list_templates(), profile=None, locked=[],
               fixed=dict(colors={}, count=None, template=None, dimension=None, category=None),
               variant=dict(letter="b", total=2, angle=cli.VARIANT_ANGLES[1]))
    assert "VARIANT B of 2" in C.build_prompt(ctx, ["a.png"]) and cli.VARIANT_ANGLES[1] in C.build_prompt(ctx, ["a.png"])
