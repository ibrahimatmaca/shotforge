"""The OpenAI backend is tested against a fake `openai` module, so no network, key or SDK is needed."""
import json
import sys
from types import SimpleNamespace

import pytest

from shotforge.cli import main

KEY = "sk-test-SECRET-do-not-print"
SHOTS = ["01_home.png", "02_stats.png", "03_goals.png"]


def answer(count=6, **extra_slide):
    slides = [{"shot": SHOTS[i % 3], "role": "feature", "title": {"en": f"Title {i} *x*", "tr": f"Başlık {i} *x*"},
               "subtitle": {"en": "sub", "tr": "alt"}, **extra_slide} for i in range(count)]
    return json.dumps({"design": {"category": "productivity", "dimension": "3d", "template": "prism3d",
                                  "template_reason": "fake", "count": count,
                                  "colors": {"background": "#F97316", "text": "#111111", "accent": "#0EA5E9"}},
                       "slides": slides})


def completed(text):
    return SimpleNamespace(status="completed", incomplete_details=None, output_text=text, output=[])


class FakeError(Exception):
    pass


@pytest.fixture
def fake(monkeypatch):
    """Installs a fake SDK; put responses (or exceptions to raise) in `replies`, read requests from `calls`."""
    state = SimpleNamespace(replies=[], calls=[])

    def create(**kw):
        state.calls.append(kw)
        reply = state.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    mod = SimpleNamespace(OpenAIError=FakeError,
                          OpenAI=lambda **_: SimpleNamespace(responses=SimpleNamespace(create=create)))
    monkeypatch.setitem(sys.modules, "openai", mod)
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    return state


def plan(shots_dir, tmp_path, *extra, ai="openai"):
    copy = tmp_path / "c.json"
    main(["plan", str(shots_dir), "--app", "Demo", "--ai", ai, "--no-ask", "--count", "6", "--copy", str(copy), *extra])
    return json.loads(copy.read_text(encoding="utf-8"))


def test_openai_backend_with_retry(shots_dir, tmp_path, fake):
    fake.replies = [completed(answer(3)), completed(answer(6))]     # wrong slide count first -> retry path
    d = plan(shots_dir, tmp_path)
    assert len(fake.calls) == 2 and "Your previous answer had problems" in fake.calls[1]["input"][0]["content"][-1]["text"]
    assert d["generated_with"] == "openai" and len(d["slides"]) == 6
    assert d["design"]["template"] == "prism3d" and d["design"]["colors"]["accent"] == "#0ea5e9"
    assert d["design"]["colors_source"] == "openai"


def test_request_shape(shots_dir, tmp_path, fake):
    fake.replies = [completed(answer())]
    plan(shots_dir, tmp_path)
    kw = fake.calls[0]
    assert kw["model"] == "gpt-6.1-sol" and kw["store"] is False and "temperature" not in kw
    content = kw["input"][0]["content"]
    images = [c for c in content if c["type"] == "input_image"]
    assert len(images) == 3 and all(c["image_url"].startswith("data:image/jpeg;base64,") for c in images)
    assert content[-1]["type"] == "input_text" and "APP NAME" in content[-1]["text"]
    fmt = kw["text"]["format"]
    assert fmt["type"] == "json_schema" and fmt["strict"] is True


def test_schema_is_strict_at_every_level(shots_dir, tmp_path, fake):
    fake.replies = [completed(answer())]
    plan(shots_dir, tmp_path, "--langs", "en,tr")

    def check(node):
        if node.get("type") == "object":
            assert node["additionalProperties"] is False and set(node["required"]) == set(node["properties"])
            for child in node["properties"].values():
                check(child)
        elif node.get("type") == "array":
            check(node["items"])

    schema = fake.calls[0]["text"]["format"]["schema"]
    check(schema)
    slide = schema["properties"]["slides"]["items"]["properties"]
    assert set(slide) == {"shot", "role", "title", "subtitle"} and slide["shot"]["enum"] == SHOTS
    assert set(slide["title"]["properties"]) == {"en", "tr"}


def test_model_flag(shots_dir, tmp_path, fake):
    fake.replies = [completed(answer())]
    plan(shots_dir, tmp_path, "--model", "gpt-6.1-terra")
    assert fake.calls[0]["model"] == "gpt-6.1-terra"


def test_extra_model_keys_are_dropped(shots_dir, tmp_path, fake):
    fake.replies = [completed(answer(bg="/etc/passwd", font="/tmp/evil.otf", layout="flat"))]
    d = plan(shots_dir, tmp_path)
    assert all(set(s) == {"shot", "role", "title", "subtitle"} for s in d["slides"])


def test_auth_error_never_prints_the_key(shots_dir, tmp_path, fake, capfd):
    err = FakeError(f"Incorrect API key provided: {KEY}")
    err.status_code, err.code = 401, "invalid_api_key"
    fake.replies = [err]
    with pytest.raises(SystemExit) as e:
        plan(shots_dir, tmp_path)
    assert "401" in str(e.value) and "invalid_api_key" in str(e.value) and KEY not in str(e.value)
    out = capfd.readouterr()
    assert KEY not in out.out + out.err


def test_doctor_reports_presence_only(fake, capfd):
    main(["doctor"])
    out = capfd.readouterr()
    assert "OPENAI_API_KEY tanımlı" in out.out and KEY not in out.out + out.err


def test_missing_key(shots_dir, tmp_path, fake, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY")
    with pytest.raises(SystemExit, match="OPENAI_API_KEY"):
        plan(shots_dir, tmp_path)
    assert not fake.calls


def test_missing_sdk(shots_dir, tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "openai", None)
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    with pytest.raises(SystemExit, match="pip install openai"):
        plan(shots_dir, tmp_path)


def test_incomplete_response_is_reported(shots_dir, tmp_path, fake):
    fake.replies = [SimpleNamespace(status="incomplete", output_text="", output=[],
                                    incomplete_details=SimpleNamespace(reason="max_output_tokens"))]
    with pytest.raises(SystemExit, match="max_output_tokens"):
        plan(shots_dir, tmp_path)
    assert len(fake.calls) == 1


def test_refusal_is_reported(shots_dir, tmp_path, fake):
    refusal = SimpleNamespace(type="refusal", refusal="I can't help with that")
    fake.replies = [SimpleNamespace(status="completed", incomplete_details=None, output_text="",
                                    output=[SimpleNamespace(type="message", content=[refusal])])]
    with pytest.raises(SystemExit, match="reddetti"):
        plan(shots_dir, tmp_path)


@pytest.mark.parametrize("anthropic_key, want", [(None, "offline"), ("x", "api")])
def test_auto_never_picks_openai(shots_dir, tmp_path, fake, monkeypatch, anthropic_key, want):
    # empty PATH: this must not depend on a real `claude` being installed
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    if anthropic_key:
        monkeypatch.setenv("ANTHROPIC_API_KEY", anthropic_key)
        monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(Anthropic=lambda: SimpleNamespace(
            messages=SimpleNamespace(create=lambda **_: SimpleNamespace(
                content=[SimpleNamespace(type="text", text=answer())])))))
    d = plan(shots_dir, tmp_path, ai="auto")
    assert d["generated_with"] == want and not fake.calls
