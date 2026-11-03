import pytest
from shotforge import texts as T


def test_parse_blocks(tmp_path):
    f = tmp_path / "t.txt"
    f.write_text("""# slide 1
shot: 01_home.png
en: Build *habits* | One calm screen
tr: *Alışkanlık* kur | Tek sakin ekran

# slide 2
tr: Gününü *planla*
""", encoding="utf-8")
    s = T.parse_text_file(f)
    assert len(s) == 2
    assert s[0]["shot"] == "01_home.png" and s[0]["title"]["en"] == "Build *habits*" and s[0]["subtitle"]["tr"] == "Tek sakin ekran"
    assert s[1]["title"] == {"tr": "Gününü *planla*"}
    assert T.missing(s, 2, ["en", "tr"]) == ["slayt 2: 'en' başlığı eksik"]


def test_parse_json_and_errors(tmp_path):
    j = tmp_path / "t.json"
    j.write_text('{"slides":[{"title":{"en":"A"}}]}', encoding="utf-8")
    assert T.parse_text_file(j)[0]["title"] == {"en": "A"}
    bad = tmp_path / "bad.txt"
    bad.write_text("this line has no colon", encoding="utf-8")
    with pytest.raises(SystemExit):
        T.parse_text_file(bad)
