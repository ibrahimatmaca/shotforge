from shotforge import palette as P
from shotforge.cli import list_templates, load_template


def test_contrast_helpers():
    assert P.contrast((255, 255, 255), (0, 0, 0)) > 20
    assert P.best_text(P.hex2rgb("#0b1026")) == P.WHITE
    assert P.best_text(P.hex2rgb("#fde68a")) == P.INK
    assert P.is_hex("#6D28D9") and P.is_hex("fff") and not P.is_hex("#12")


def test_unreadable_text_color_is_fixed_and_warned():
    tpl = load_template("heartbeat")
    pal = P.build_palette(tpl, {"background": "#0B1026", "text": "#111111"})
    warnings = []
    st = P.slide_style(tpl, pal, 0, warnings)
    assert st["text_color"] == "#ffffff"
    assert warnings and warnings[0][0] == 1


def test_readable_user_text_color_is_kept():
    tpl = load_template("prism3d")
    pal = P.build_palette(tpl, {"background": "#F97316", "text": "#7C2D12"})
    warnings = []
    st = P.slide_style(tpl, pal, 0, warnings)
    assert st["text_color"] == "#7c2d12" and not warnings


def test_placeholders_resolve_per_slide():
    tpl = load_template("heartbeat")
    pal = P.build_palette(tpl, {"accent": "#FF00AA"})
    st = P.slide_style(tpl, pal, 0, [])
    out = P.resolve_placeholders(tpl["defaults"], st.pop("tokens"))
    assert out["glow"]["color"].lower() == "#ff00aa" or out["glow"]["color"].startswith("#")
    assert "$" not in str(out)


def test_chooser_dimension_rules():
    t = list_templates()
    assert P.choose_template(t, category="finance")[1] == "2d"
    assert P.choose_template(t, category="games")[1] == "3d"
    assert P.choose_template(t, category="games", dimension="2d")[1] == "2d"
    name, dim, reasons = P.choose_template(t, category="health", dimension="3d", bg_hex="#0B1026")
    assert dim == "3d" and t[name]["theme"] == "dark" and reasons


def test_every_template_is_consistent():
    for name, t in list_templates().items():
        assert t["dimension"] in ("2d", "3d"), name
        three_d = {"tilt-right", "tilt-left", "duo", "peek"}
        uses_3d = any(l in three_d for l in t["layouts"])
        assert uses_3d == (t["dimension"] == "3d"), f"{name}: layouts must match dimension"


def test_analyze_shots(shots_dir):
    prof = P.analyze_shots(sorted(shots_dir.glob("*.png")))
    assert prof["count"] == 3 and prof["ui"] in ("light", "dark") and prof["density"] in ("low", "medium", "high")
