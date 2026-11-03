import json

from PIL import Image

from shotforge.cli import main


def run(*args):
    main(list(map(str, args)))


def test_plan_offline_then_render_one_slide(shots_dir, tmp_path):
    copy = tmp_path / "c.json"
    run("plan", shots_dir, "--app", "Demo", "--ai", "offline", "--no-ask", "--copy", copy, "--category", "games")
    data = json.loads(copy.read_text(encoding="utf-8"))
    assert data["design"]["dimension"] == "3d" and 6 <= data["design"]["count"] <= 7
    out = tmp_path / "out"
    run("render", "--copy", copy, "--only", "1", "--langs", "en", "--jobs", 1, "-o", out)
    files = list(out.rglob("*.png"))
    assert len(files) == 1
    im = Image.open(files[0])
    assert im.size == (1320, 2868) and im.mode == "RGB"
    assert not list(out.rglob("*.jpg")), "overview must be opt-in"


def test_user_texts_and_colors_skip_ai(shots_dir, tmp_path, capsys):
    texts = tmp_path / "t.txt"
    texts.write_text("shot: 02_stats.png\nen: Hello *world* | Sub\ntr: Merhaba *dünya* | Alt\n", encoding="utf-8")
    copy = tmp_path / "c.json"
    run("plan", shots_dir, "--app", "Demo", "--texts", texts, "--template", "bubble", "--bg-color", "#6D3AE8",
        "--text-color", "#FFFFFF", "--accent", "#DDD6FE", "--no-ask", "--ai", "claude", "--copy", copy)
    assert "Claude çağrılmadı" in capsys.readouterr().out
    d = json.loads(copy.read_text(encoding="utf-8"))
    assert d["design"]["template"] == "bubble" and d["design"]["count"] == 1
    assert d["slides"][0]["shot"] == "02_stats.png" and d["generated_with"] == "user"


def test_old_template_names_still_work(shots_dir, tmp_path):
    copy = tmp_path / "c.json"
    run("plan", shots_dir, "--app", "Demo", "--ai", "offline", "--no-ask", "--copy", copy, "--template", "kova")
    assert json.loads(copy.read_text(encoding="utf-8"))["design"]["template"] == "bubble"


def test_templates_and_rules_commands(capsys):
    run("templates")
    out = capsys.readouterr().out
    assert "2D" in out and "3D" in out and "heartbeat" in out
    run("rules")
    assert "2D / 3D" in capsys.readouterr().out
