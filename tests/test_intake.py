import builtins

import pytest
from PIL import Image

from shotforge import copywriter, cli, intake as I, palette as P


def test_blank_answers_become_defaults():
    r = I.resolve({})
    assert r["stores"] == ["appstore", "playstore"] and r["langs"] == ["en", "tr"]
    assert r["targets"] == dict(devices=["iphone-6.9", "android-phone"],
                                banners=["5244x2950", "3840x2560", "1920x1280", "1024x500"], output="both")
    assert r["previews"] == {}
    assert I.resolve(dict(store="auto", header_sizes="", langs="otomatik", output=None))["targets"] == r["targets"]


def test_store_choice_limits_targets():
    play = I.resolve(dict(store="playstore", header_sizes="5244x2950", previews="a.mov"))
    assert play["targets"]["devices"] == ["android-phone"] and play["targets"]["banners"] == ["1024x500"]
    assert play["previews"] == {}
    apple = I.resolve(dict(store="appstore", header_sizes="hayır", devices="iphone-6.9,ipad-13,android-phone"))
    assert apple["targets"]["devices"] == ["iphone-6.9", "ipad-13"] and apple["targets"]["banners"] == []
    assert not any("Google Play" in n for n in apple["notes"])


def test_play_note_is_a_recommendation_not_a_ban():
    note = next(n for n in I.resolve(dict(store="both"))["notes"] if "Google Play" in n)
    assert "zorunlu kurala aykırı değil" in note


@pytest.mark.parametrize("answers", [dict(store="windows"), dict(header_sizes="100x100"), dict(devices="pixel"), dict(output="tar")])
def test_bad_answers_are_rejected(answers):
    with pytest.raises(ValueError):
        I.resolve(answers)


def test_previews_shared_or_per_language():
    assert I.parse_previews("a.mov") == {"*": "a.mov"}
    assert I.parse_previews(["tr=t.mov", "en=e.mov"]) == {"tr": "t.mov", "en": "e.mov"}


@pytest.mark.parametrize("name,want", [("Kışlık Çağrı", "kislik-cagri"), ("../../etc", "etc"), ("///", "app"), (None, "app")])
def test_slug_cannot_escape(name, want):
    assert I.slug(name) == want


def test_every_requested_question_exists():
    ids = {q["id"] for q in I.QUESTIONS}
    assert {"store", "header_sizes", "langs", "dimension", "template", "output", "appstore_devices", "play_devices", "count", "texts", "background_color",
            "icon", "previews", "shot_sets"} <= ids


def test_questions_fit_a_four_option_picker():
    assert all(len(q.get("options", [])) <= 4 for q in I.QUESTIONS)


def _ctx(profile, template=None, colors=None):
    return dict(app="A", tone="", langs=["en"], templates=cli.list_templates(), profile=profile, locked=[],
                fixed=dict(colors=colors or {}, count=1, template=template, dimension=None, category=None))


def test_background_comes_from_screenshots():
    d = copywriter.finalize(None, _ctx(dict(brand="#123abc", ui="light", density="low")), ["a.png"])["design"]
    assert d["colors"]["background"] == "#123abc" and d["colors_source"] == "screenshots"


def test_user_colour_wins_and_chosen_dark_template_keeps_its_background():
    prof = dict(brand="#123abc", ui="light", density="low")
    d = copywriter.finalize(None, _ctx(prof, colors=dict(background="#ff0000")), ["a.png"])["design"]
    assert d["colors"]["background"] == "#ff0000" and d["colors_source"] == "user"
    d = copywriter.finalize(None, _ctx(prof, template="heartbeat"), ["a.png"])["design"]
    assert "background" not in d["colors"] and d["colors"]["accent"] == "#123abc"


def test_neutral_screens_keep_template_default():
    d = copywriter.finalize(None, _ctx(dict(brand=None, ui="light", density="low")), ["a.png"])["design"]
    assert d["colors"] == {} and d["colors_source"] == "template"


def test_brand_found_when_screens_have_different_colours(tmp_path):
    """Farklı renkli ekranlar birlikte griye ortalanabilir; marka rengi yine bulunmalı."""
    paths = []
    for i, c in enumerate([(200, 20, 180), (20, 110, 200), (20, 160, 90), (200, 90, 20)]):
        im = Image.new("RGB", (300, 600), (246, 247, 251))
        im.paste(c, (0, 0, 300, 120))
        paths.append(tmp_path / f"{i}.png")
        im.save(paths[-1])
    assert P.analyze_shots(paths)["brand"]


def test_interactive_skips_header_question_for_play(monkeypatch, shots_dir, tmp_path):
    prompts = []

    def fake_input(prompt=""):
        prompts.append(prompt)
        return "playstore" if "mağaza" in prompt else ""
    monkeypatch.setattr(builtins, "input", fake_input)
    cli.main(["create", str(shots_dir), "--app", "Demo", "--ai", "offline", "--ask", "--count", "1", "--jobs", "1",
              "--langs", "en", "-o", str(tmp_path / "o")])
    assert any("mağaza" in p for p in prompts) and not any("Header" in p and "üretilsin" in p for p in prompts)
    assert (tmp_path / "o" / "en" / "google-play" / "phone" / "01_home.png").exists()
    assert not (tmp_path / "o" / "en" / "app-store").exists()
