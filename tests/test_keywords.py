import json

import pytest

from shotforge import copywriter as C, cli
from shotforge.cli import main


def ctx(**kw):
    return dict(app="A", tone="", langs=["en"], templates=cli.list_templates(), profile=None, locked=[],
                fixed=dict(colors={}, count=None, template=None, dimension=None, category=None), **kw)


def test_parse_keywords_cleans_dedupes_and_limits():
    assert C.parse_keywords(" habit  tracker, Streak,streak,, ") == ["habit tracker", "Streak"]
    assert C.parse_keywords(None) == []
    with pytest.raises(ValueError):
        C.parse_keywords(",".join(f"k{i}" for i in range(C.MAX_KEYWORDS + 1)))
    with pytest.raises(ValueError):
        C.parse_keywords("x" * (C.MAX_KEYWORD_LEN + 1))


def test_prompt_has_keywords_only_when_given():
    assert "TARGET SEARCH KEYWORDS" not in C.build_prompt(ctx(), ["a.png"])
    # süslü parantez format dizgesini bozmamalı
    assert "TARGET SEARCH KEYWORDS (ASO): {habit}, streak" in C.build_prompt(ctx(keywords=["{habit}", "streak"]), ["a.png"])


def test_keyword_gaps_ignores_accent_marks_and_case():
    slides = [dict(title={"en": "Build a *Habit* tracker"}, subtitle={"en": "Keep your streak"})]
    assert C.keyword_gaps(slides, ["habit tracker", "STREAK", "focus"]) == ["focus"]
    assert C.keyword_gaps([dict(title={"en": "Meet Habita"})], ["habit"]) == ["habit"]  # kelimenin parçası sayılmaz


def test_offline_run_warns_and_records_keywords(shots_dir, tmp_path, capsys):
    copy = tmp_path / "c.json"
    args = ["plan", str(shots_dir), "--app", "Demo", "--ai", "offline", "--no-ask", "--copy", str(copy), "--keywords", "habit,best app"]
    main(args)
    err = capsys.readouterr().err
    assert "needs an AI backend" in err and "best app" in err
    assert json.loads(copy.read_text(encoding="utf-8"))["keywords"] == ["habit", "best app"]
    main([*args[:-1], "other"])
    assert "--keywords was not applied" in capsys.readouterr().err
