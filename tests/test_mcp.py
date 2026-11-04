import asyncio
import json
from pathlib import Path

import pytest

pytest.importorskip("mcp")
from shotforge import cli, mcp_server as S  # noqa: E402


def test_tools_registered():
    names = {t.name for t in asyncio.run(S.mcp.list_tools())}
    assert {"list_templates", "analyze_screenshots", "create_screenshots", "render_screenshots", "convert_app_preview"} <= names


def test_mcp_command_lists_every_tool():
    assert set(cli.MCP_TOOLS) == {t.name for t in asyncio.run(S.mcp.list_tools())}


def test_analyze_screenshots(shots_dir):
    r = S.analyze_screenshots([str(shots_dir)], app="Demo", about="a puzzle game")
    assert r["category"] == "games" and r["recommendation"]["dimension"] == "3d"
    assert len(r["screenshots"]) == 3 and "rules" in r


def test_create_screenshots_separate_files(shots_dir, tmp_path):
    slides = [{"shot": "01_home.png", "title": {"en": "Hello *world*"}, "subtitle": {"en": "Sub"}},
              {"shot": "02_stats.png", "title": {"en": "Charts"}}]
    res = S._create([str(shots_dir)], "Demo", slides, ["en"], "auto", "2d", "#0EA5E9", None, None, None, "", None,
                    "", str(tmp_path / "o"), "iphone-6.9", "flat", "png", [1, 2], 1, None)
    files = res["files"]["en"]
    assert len(files) == 2 and all(Path(f).exists() for f in files) and files[0] != files[1]
    assert res["design"]["dimension"] == "2d" and Path(res["overview"]).exists()
    assert "warning" not in res or "placeholder" in res["warning"]
    assert json.loads((tmp_path / "o" / "shotforge.copy.json").read_text())["generated_with"] == "mcp"


def test_bad_inputs_are_clean_errors(shots_dir, tmp_path):
    with pytest.raises(S.ToolError):
        S.analyze_screenshots([str(tmp_path / "missing")])
    with pytest.raises(ValueError):
        S._create([str(shots_dir)], "Demo", [], ["en"], "nope", "auto", None, None, None, None, "", None, "",
                  str(tmp_path / "o"), "iphone-6.9", "flat", "png", None, 1, None)


def test_intake_questions_tool(shots_dir):
    r = S.get_intake_questions([str(shots_dir)], app="Demo")
    assert {"store", "header_sizes", "langs", "dimension"} <= {q["id"] for q in r["questions"]}
    assert r["screenshot_defaults"]["template"] in {t["name"] for t in r["templates"]}


def test_intake_starts_without_screenshots(shots_dir):
    r = S.get_intake_questions()
    assert [q["id"] for q in r["questions"]] == ["screenshots", "app", "about"] and "next_step" in r
    assert "screenshots" not in {q["id"] for q in S.get_intake_questions([str(shots_dir)])["questions"]}
    assert "ask for it first" in S.store_screenshots_prompt()


def test_create_refuses_until_intake_is_done(shots_dir):
    with pytest.raises(S.ToolError, match="intake"):
        asyncio.run(S.create_screenshots([str(shots_dir)], "Demo"))


def test_create_with_blank_intake_uses_store_defaults(shots_dir, tmp_path):
    res = asyncio.run(S.create_screenshots(
        [str(shots_dir)], "Demo", intake_completed=True, count=1, jobs=2, out_dir=str(tmp_path / "o"), store="playstore",
        banner={"title": {"en": "Hello *there*", "tr": "Merhaba"}}))
    data = json.loads(res[0])
    assert set(data["files"]) == {"en", "tr"} and Path(data["zip"]).name == "demo.zip"
    assert any("google-play/feature-graphic/1024x500.png" in f for f in data["files"]["en"])
    assert any("Google Play" in w for w in data["render_warnings"])


def test_render_keeps_targets_from_copy_file(shots_dir, tmp_path):
    asyncio.run(S.create_screenshots([str(shots_dir)], "Demo", intake_completed=True, count=1, langs=["en"], jobs=1,
                                     out_dir=str(tmp_path / "o"), store="playstore"))
    res = asyncio.run(S.render_screenshots(str(tmp_path / "o" / "shotforge.copy.json"), jobs=1))
    assert any("google-play/phone" in f for f in json.loads(res[0])["files"]["en"])


def test_create_fastlane_with_brand_and_keywords(shots_dir, tmp_path):
    """_render builds its Namespace by hand: new layouts and render fields must also work through MCP."""
    from PIL import Image
    Image.new("RGBA", (200, 100), (255, 0, 0, 255)).save(tmp_path / "logo.png")
    (tmp_path / "shotforge.toml").write_text('[brand]\ntone = "calm"\nlogo = "logo.png"\ntemplate = "bubble"\n[brand.colors]\nbackground = "#123456"\n', encoding="utf-8")
    slides = [{"shot": "01_home.png", "title": {"en": "Build a *habit*"}, "subtitle": {"en": "Sub"}}]
    res = json.loads(asyncio.run(S.create_screenshots(
        [str(shots_dir)], "Demo", slides, ["en"], intake_completed=True, out_dir=str(tmp_path / "o"), layout="fastlane", jobs=1,
        store="both", header_sizes=["no"], keywords=["habit", "focus"], brand_file=str(tmp_path / "shotforge.toml")))[0])
    assert res["design"]["template"] == "bubble" and res["design"]["colors"]["background"] == "#123456"
    assert res["keywords_not_found_word_for_word"] == ["focus"]
    assert (tmp_path / "o" / "fastlane" / "screenshots" / "en-US" / "APP_IPHONE_67_iphone-6.9_01_home.png").exists()
    assert (tmp_path / "o" / "fastlane" / "metadata" / "android" / "en-US" / "images" / "featureGraphic.png").exists()
    copy = json.loads((tmp_path / "o" / "shotforge.copy.json").read_text(encoding="utf-8"))
    assert copy["tone"] == "calm" and copy["keywords"] == ["habit", "focus"]
    res = json.loads(asyncio.run(S.render_screenshots(str(tmp_path / "o" / "shotforge.copy.json"), jobs=1, layout="fastlane",
                                                     brand_file=str(tmp_path / "shotforge.toml")))[0])
    assert res["output_files"] == 3
