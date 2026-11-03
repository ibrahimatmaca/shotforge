"""Copy files, templates and model output are untrusted input: they must not choose paths or render settings."""
import argparse
import asyncio
import json
import sys

import pytest

from shotforge import cli, copywriter, engine, palette as P
from shotforge.cli import main


def render_args(**kw):
    d = dict(copy="c.json", template=None, bg_color=None, text_color=None, accent=None, langs=None, shots=[], only=None,
             out="out", format="png", layout="flat", device="iphone-6.9", jobs=1, sheet=False, brief=False, style=None,
             bg=None, frame=None, text_position=None, align=None, font=None, perspective=None, no_grain=False)
    return argparse.Namespace(**{**d, **kw})


def slide_cfg(slide, warnings):
    tpl = cli.load_template("classic")
    a = render_args()
    return cli.slide_cfg(a, tpl, P.build_palette(tpl, {}), 0, dict(shot="a.png", **slide), {"a.png": "a.png"}, ["a.png"], warnings)


def test_finalize_drops_render_settings_from_model_output():
    ctx = dict(app="A", tone="", langs=["en"], templates=cli.list_templates(), profile=None, locked=[],
               fixed=dict(colors={}, count=None, template="classic", dimension=None, category=None))
    slides = [{"shot": "a.png", "title": {"en": "x"}, "bg": "/secret.png", "font": "/etc/hosts"}] * 30
    d = copywriter.finalize({"slides": slides}, ctx, ["a.png"])
    assert len(d["slides"]) == 10
    assert not {"bg", "font"} & set(d["slides"][0])


@pytest.mark.parametrize("key,value", [("bg", "/secret.png"), ("font", "/etc/hosts"), ("image2", "/secret.png"), ("device", "ipad-13")])
def test_slide_cfg_ignores_unsafe_keys(key, value):
    warnings = []
    cfg = slide_cfg({key: value}, warnings)
    assert cfg.get(key) != value
    assert warnings and key in warnings[-1][1]


@pytest.mark.parametrize("bg", ["ocean", "#000000,#ffffff"])
def test_slide_cfg_keeps_preset_and_gradient_bg(bg):
    warnings = []
    assert slide_cfg({"bg": bg}, warnings)["bg"] == bg and not warnings


@pytest.mark.parametrize("lang", ["/tmp/evil", "../../x", "en\n", "", 3])
def test_bad_langs_rejected_before_any_render(shots_dir, tmp_path, lang):
    data = dict(langs=[lang], design=dict(template="classic", colors={}), shots={"01_home.png": str(shots_dir / "01_home.png")},
                slides=[dict(shot="01_home.png", title={"en": "a"}, subtitle={})])
    copy = tmp_path / "c.json"
    copy.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(SystemExit, match="language code"):
        cli.cmd_render(render_args(copy=str(copy), out=str(tmp_path / "o")))
    assert not (tmp_path / "o").exists()


def test_text_problems():
    cfg = dict(device="iphone-6.9")
    assert engine.text_problems(dict(cfg, title={"tr": "Gününü *planla* şimdi"}), "tr") == []
    assert any("日" in m for m in engine.text_problems(dict(cfg, title={"ja": "日本語"}), "ja"))
    assert any("overflows" in m for m in engine.text_problems(dict(cfg, title={"tr": "Uluslararasılaştırılabilirlikçilerimizden"}), "tr"))


@pytest.mark.parametrize("value", ["abc", "0", "15"])
def test_count_is_validated(value, capsys):
    with pytest.raises(SystemExit) as e:
        main(["plan", "x", "--count", value])
    assert e.value.code == 2 and "--count" in capsys.readouterr().err


def test_custom_template_file_works_with_plan(shots_dir, tmp_path):
    custom = tmp_path / "my.json"
    custom.write_text(json.dumps(cli.list_templates()["bubble"]), encoding="utf-8")
    copy = tmp_path / "c.json"
    main(["plan", str(shots_dir), "--app", "Demo", "--ai", "offline", "--no-ask", "--template", str(custom), "--copy", str(copy)])
    assert json.loads(copy.read_text(encoding="utf-8"))["design"]["template"] == str(custom)


def test_render_uses_langs_from_copy_file(shots_dir, tmp_path):
    copy = tmp_path / "c.json"
    main(["plan", str(shots_dir), "--app", "Demo", "--ai", "offline", "--no-ask", "--langs", "de", "--copy", str(copy)])
    main(["render", "--copy", str(copy), "--only", "1", "--jobs", "1", "-o", str(tmp_path / "o")])
    assert [p.name for p in (tmp_path / "o").iterdir()] == ["de"]


def test_reusing_copy_for_other_app_warns(shots_dir, tmp_path, capsys):
    copy = tmp_path / "c.json"
    base = ["plan", str(shots_dir), "--ai", "offline", "--no-ask", "--copy", str(copy)]
    main(base + ["--app", "Demo"])
    capsys.readouterr()
    main(base + ["--app", "Demo"])
    assert "WARNING" not in capsys.readouterr().err
    main(base + ["--app", "Other"])
    assert "--regen" in capsys.readouterr().err


def test_mcp_rejects_unsafe_arguments(shots_dir, tmp_path):
    pytest.importorskip("mcp")
    from shotforge import mcp_server as S
    copy = tmp_path / "c.json"
    data = dict(langs=["en"], design=dict(template="classic", colors={}), shots={}, slides=[])
    copy.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(S.ToolError, match="unknown template"):
        asyncio.run(S.render_screenshots(str(copy), template=str(copy)))
    with pytest.raises(S.ToolError, match="device"):
        asyncio.run(S.render_screenshots(str(copy), device="nope"))
    copy.write_text(json.dumps(dict(data, design=dict(template=str(copy), colors={}))), encoding="utf-8")
    with pytest.raises(S.ToolError, match="unknown template"):
        asyncio.run(S.render_screenshots(str(copy)))

    existing = tmp_path / "v.mp4"
    existing.write_bytes(b"keep")
    with pytest.raises(S.ToolError, match="overwrite"):
        asyncio.run(S.convert_app_preview("in.mov", str(existing)))
    with pytest.raises(S.ToolError, match="must end"):
        asyncio.run(S.convert_app_preview("in.mov", str(tmp_path / ".zshrc")))
    with pytest.raises(S.ToolError, match="device"):
        asyncio.run(S.convert_app_preview("in.mov", str(tmp_path / "o.mp4"), device="iphone-6.9b"))
    assert existing.read_bytes() == b"keep"


def _copy_with(tmp_path, shots_dir, **extra):
    copy = tmp_path / "c.json"
    main(["plan", str(shots_dir), "--app", "D", "--ai", "offline", "--no-ask", "--copy", str(copy), "--count", "1", "--langs", "en"])
    d = json.loads(copy.read_text(encoding="utf-8"))
    d.update(extra)
    copy.write_text(json.dumps(d), encoding="utf-8")
    return copy


@pytest.mark.parametrize("targets", [{"devices": ["../../evil"]}, {"banners": ["../x"]}, {"output": "rm"}, {"devices": "iphone-6.9"}])
def test_copy_file_targets_are_allowlisted(tmp_path, shots_dir, targets):
    copy = _copy_with(tmp_path, shots_dir, targets=targets)
    with pytest.raises(SystemExit):
        main(["render", "--copy", str(copy), "--jobs", "1", "-o", str(tmp_path / "o")])


def test_copy_file_cannot_set_icon_or_font_through_banner(tmp_path, shots_dir):
    data = dict(app="D", banner={"title": {"en": 5}, "subtitle": ["x"], "icon": "/etc/passwd", "font": "/etc/hosts"})
    title, subtitle = cli.banner_text(data, {"title": {"en": "T"}})
    assert title == {"en": "T"} and subtitle == "D"


def test_bad_shot_set_key_is_ignored(tmp_path, shots_dir, capsys):
    copy = _copy_with(tmp_path, shots_dir, shots_by_set={"../../x/": {"01_home.png": "01_home.png"}})
    main(["render", "--copy", str(copy), "--jobs", "1", "--only", "1", "-o", str(tmp_path / "o")])
    assert "unknown shots_by_set" in capsys.readouterr().err


def test_icon_must_be_an_image(tmp_path, shots_dir):
    copy = _copy_with(tmp_path, shots_dir)
    with pytest.raises(SystemExit, match="Icon"):
        main(["render", "--copy", str(copy), "--jobs", "1", "--icon", "/etc/hosts", "-o", str(tmp_path / "o")])


def test_mcp_tool_rejects_bad_store_cleanly(shots_dir, tmp_path):
    pytest.importorskip("mcp")
    from shotforge import mcp_server as S
    with pytest.raises(S.ToolError):
        asyncio.run(S.create_screenshots([str(shots_dir)], "D", intake_completed=True, store="windows", out_dir=str(tmp_path)))


@pytest.mark.parametrize("change,message", [
    (dict(shots=["a.png"]), "'shots' must map"),
    (dict(slides=[]), "'slides' must be"),
    (dict(slides=[dict(shot="other.png")]), "unknown screenshot"),
    (dict(shots_by_set={"android/": "x"}), "'shots_by_set' must map"),
    (dict(langs="en"), "language code"),
])
def test_malformed_copy_file_is_a_clear_error(shots_dir, tmp_path, change, message):
    data = dict(langs=["en"], design=dict(template="classic", colors={}), shots={"01_home.png": str(shots_dir / "01_home.png")},
                slides=[dict(shot="01_home.png", title={"en": "a"}, subtitle={})])
    copy = tmp_path / "c.json"
    copy.write_text(json.dumps({**data, **change}), encoding="utf-8")
    with pytest.raises(SystemExit, match=message):
        cli.cmd_render(render_args(copy=str(copy), out=str(tmp_path / "o")))


def test_copy_file_loading_a_local_template_warns(shots_dir, tmp_path, capsys, monkeypatch):
    custom = tmp_path / "my.json"
    custom.write_text(json.dumps(cli.list_templates()["bubble"]), encoding="utf-8")
    data = dict(langs=["en"], design=dict(template=str(custom), colors={}), shots={"01_home.png": str(shots_dir / "01_home.png")},
                slides=[dict(shot="01_home.png", title={"en": "a"}, subtitle={})])
    copy = tmp_path / "c.json"
    copy.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(cli, "render_job", lambda j: j["path"])
    cli.cmd_render(render_args(copy=str(copy), out=str(tmp_path / "o")))
    assert "loads a local template file" in capsys.readouterr().err


def test_ffmpeg_and_ffprobe_only_read_local_files(tmp_path, monkeypatch):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return argparse.Namespace(stdout="20\n")
    monkeypatch.setattr(cli.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    cli.cmd_preview(argparse.Namespace(input="in.mov", out=str(tmp_path / "o.mp4"), device="iphone-6.9", landscape=False,
                                       fit="cover", pad_color="black", extend=False))
    assert calls and all(c[c.index("-protocol_whitelist") + 1] == "file" for c in calls)
    assert calls[-1].index("-protocol_whitelist") < calls[-1].index("-i")


def test_anthropic_error_text_is_not_printed(tmp_path, monkeypatch, shots_dir):
    class AnthropicError(Exception):
        status_code = 401

    class Messages:
        def create(self, **kw):
            raise AnthropicError("key sk-secret rejected")
    fake = argparse.Namespace(AnthropicError=AnthropicError, Anthropic=lambda: argparse.Namespace(messages=Messages()))
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    with pytest.raises(RuntimeError) as e:
        copywriter._api("p", {"01_home.png": shots_dir / "01_home.png"}, None)
    assert "sk-secret" not in str(e.value) and "401" in str(e.value)


def test_cli_reports_runtime_errors_without_traceback(tmp_path):
    bad = tmp_path / "bad.png"
    bad.write_text("not an image", encoding="utf-8")
    with pytest.raises(SystemExit, match="Error:"):
        main(["analyze", str(bad)])
