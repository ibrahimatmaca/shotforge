import asyncio
import json
from pathlib import Path

import pytest

pytest.importorskip("mcp")
from shotforge import mcp_server as S  # noqa: E402


def test_tools_registered():
    names = {t.name for t in asyncio.run(S.mcp.list_tools())}
    assert {"list_templates", "analyze_screenshots", "create_screenshots", "render_screenshots", "convert_app_preview"} <= names


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
