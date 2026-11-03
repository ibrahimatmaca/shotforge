import pytest
from PIL import Image, ImageDraw


def _fake_screen(path, accent, title):
    im = Image.new("RGB", (1320, 2868), (246, 247, 251))
    d = ImageDraw.Draw(im)
    d.rectangle((0, 180, 1320, 900), fill=accent)
    d.text((80, 300), title, fill="white")
    for i in range(6):
        y = 1000 + i * 260
        d.rounded_rectangle((60, y, 1260, y + 210), radius=50, fill="white", outline=(225, 228, 238), width=3)
        d.rounded_rectangle((100, y + 40, 210, y + 150), radius=30, fill=accent)
    im.save(path)


@pytest.fixture(scope="session")
def shots_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("shots")
    _fake_screen(d / "01_home.png", (109, 40, 217), "Home")
    _fake_screen(d / "02_stats.png", (14, 165, 233), "Stats")
    _fake_screen(d / "03_goals.png", (16, 185, 129), "Goals")
    return d
